
import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# =========================
# CONFIGURATION
# =========================

BASE = "https://api.5dollarfootballapi.com/v1"
STATE_FILE = "odds_state.json"

MAX_FIXTURES_PER_RUN = 3
DROP_THRESHOLD = 10.0
MAX_BASELINE_AGE = 30 * 60
STATE_MAX_AGE = 24 * 3600
TIMEOUT = 15

API_KEY = os.getenv("ODDS_API_KEY", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
})


# =========================
# API
# =========================

def api_get(path):
    try:
        response = session.get(
            BASE + path,
            timeout=TIMEOUT
        )

        print(f"API HTTP {response.status_code}: {path}")

        if response.status_code == 429:
            print("Quota API atteint. Arrêt de la requête.")
            return None

        response.raise_for_status()
        result = response.json()

        if result.get("success") != 1:
            print("La réponse API indique un échec.")
            return None

        return result.get("data")

    except requests.RequestException as error:
        print("Erreur API :", type(error).__name__)
        return None

    except ValueError:
        print("Réponse API JSON invalide.")
        return None


def parse_time(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


# =========================
# MATCHS CANDIDATS
# =========================

def get_candidate_fixtures():
    data = api_get(
        "/fixtures?status=all&per_page=100"
    )

    if not isinstance(data, list):
        print("Impossible de récupérer les matchs.")
        return None

    now = datetime.now(timezone.utc)
    candidates = []

    for fixture in data:
        if not isinstance(fixture, dict):
            continue

        status = str(
            fixture.get("status", "")
        ).lower()

        kickoff = parse_time(
            fixture.get("kickoff_utc")
        )

        if status in ("in_play", "live"):
            candidates.append((0, fixture))

        elif status == "scheduled" and kickoff:
            delta = kickoff - now

            if timedelta(0) <= delta <= timedelta(hours=8):
                candidates.append((1, fixture))

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1].get("kickoff_utc", "")
        )
    )

    print("Matchs candidats :", len(candidates))

    return [
        fixture
        for _, fixture in candidates[:MAX_FIXTURES_PER_RUN]
    ]


# =========================
# HISTORIQUE
# =========================

def load_state():
    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            state = json.load(file)

        return state if isinstance(state, dict) else {}

    except (FileNotFoundError, json.JSONDecodeError):
        print("Aucun historique valide trouvé.")
        return {}


def save_state(state):
    temporary_file = STATE_FILE + ".tmp"

    with open(
        temporary_file,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temporary_file, STATE_FILE)


# =========================
# TELEGRAM
# =========================

def send_telegram(message):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("Telegram non configuré.")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TG_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            json={
                "chat_id": TG_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            print("Telegram n'a pas confirmé l'envoi.")
            return False

        print("Alerte Telegram envoyée.")
        return True

    except (requests.RequestException, ValueError) as error:
        print("Erreur Telegram :", type(error).__name__)
        return False


# =========================
# EXTRACTION DES COTES
# =========================

def get_prices(fixture):
    fixture_id = fixture.get("id")

    if fixture_id is None:
        return None

    data = api_get(
        f"/fixtures/{fixture_id}/odds"
    )

    if not isinstance(data, dict):
        return None

    bookmakers = data.get("bookmakers", [])

    if not isinstance(bookmakers, list):
        return None

    bookmaker_found = False
    prices = []

    teams = fixture.get("teams") or {}
    home = (teams.get("home") or {}).get(
        "name", "Équipe domicile"
    )
    away = (teams.get("away") or {}).get(
        "name", "Équipe extérieure"
    )

    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue

        if bookmaker.get("slug") != "bet365":
            continue

        bookmaker_found = True
        odds = bookmaker.get("odds", {})

        if not isinstance(odds, dict):
            continue

        for market, details in odds.items():
            if not isinstance(details, dict):
                continue

            # Cotes explicitement identifiées comme live.
            # Ne pas utiliser opening ou closing ici.
            current = details.get("inplay")

            if not isinstance(current, dict):
                continue

            line = current.get("line", "")

            for outcome, value in current.items():
                if outcome in ("line", "opened_at"):
                    continue

                if isinstance(value, bool):
                    continue

                if not isinstance(value, (int, float)):
                    continue

                if value <= 1.0:
                    continue

                # Nouvelle version de clé :
                # ignore les anciennes références historiques.
                key = "inplay-v2|" + "|".join([
                    str(fixture_id),
                    str(market),
                    str(line),
                    str(outcome),
                ])

                prices.append({
                    "key": key,
                    "fixture_id": fixture_id,
                    "match": f"{home} - {away}",
                    "market": market,
                    "line": line,
                    "outcome": outcome,
                    "price": float(value),
                })

    if not bookmaker_found:
        print("Bet365 absent pour ce match.")

    elif not prices:
        print("Aucune cote inplay exploitable pour ce match.")

    return prices


# =========================
# DÉTECTION DES BAISSES
# =========================

def main():
    if not API_KEY:
        raise SystemExit("Secret ODDS_API_KEY absent.")

    print("=== BET365 ODDS ALERT ===")
    print("Seuil de baisse :", DROP_THRESHOLD, "%")
    print("Maximum de matchs :", MAX_FIXTURES_PER_RUN)
    print("Historique : inplay-v2")

    state = load_state()
    now = time.time()

    fixtures = get_candidate_fixtures()

    if fixtures is None:
        print("Arrêt : liste de matchs indisponible.")
        return

    alerts = []
    observed = 0

    for fixture in fixtures:
        prices = get_prices(fixture)

        if prices is None:
            print("Cotes indisponibles pour ce match.")
            continue

        for item in prices:
            observed += 1
            key = item["key"]
            old = state.get(key)

            previous_price = None
            previous_time = None

            if isinstance(old, dict):
                try:
                    previous_price = float(old["price"])
                    previous_time = float(old["time"])
                except (KeyError, TypeError, ValueError):
                    pass

            # Comparaison uniquement avec une observation
            # récente de la même cote.
            if (
                previous_price is not None
                and previous_time is not None
                and 0 < now - previous_time <= MAX_BASELINE_AGE
                and item["price"] < previous_price
            ):
                drop = (
                    (previous_price - item["price"])
                    / previous_price
                    * 100
                )

                if drop >= DROP_THRESHOLD:
                    alerts.append(
                        "📉 BAISSE DE COTE BET365\n"
                        f"Match : {item['match']}\n"
                        f"Marché : {item['market']}\n"
                        f"Choix : {item['outcome']}\n"
                        f"Ligne : {item['line']}\n"
                        f"Ancienne cote : {previous_price:.3f}\n"
                        f"Nouvelle cote : {item['price']:.3f}\n"
                        f"Baisse : {drop:.1f}%\n"
                        "Attention : mouvement de cote, "
                        "pas une garantie de gain."
                    )

            # Actualiser la référence, même si aucune
            # alerte n'est déclenchée.
            state[key] = {
                "price": item["price"],
                "time": now,
            }

    # Nettoyage de l'historique trop ancien.
    state = {
        key: value
        for key, value in state.items()
        if isinstance(value, dict)
        and isinstance(value.get("time"), (int, float))
        and 0 <= now - value["time"] <= STATE_MAX_AGE
    }

    try:
        save_state(state)
        print("Historique sauvegardé dans", STATE_FILE)

    except OSError as error:
        print(
            "Impossible de sauvegarder l'historique :",
            type(error).__name__
        )

    print("Cotes en direct analysées :", observed)
    print("Alertes détectées :", len(alerts))

    for message in alerts:
        send_telegram(message)

    print("=== FIN ===")


if __name__ == "__main__":
    main()
        
