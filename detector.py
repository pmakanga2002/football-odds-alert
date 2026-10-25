
import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

BASE = "https://api.5dollarfootballapi.com/v1"
STATE_FILE = "odds_state.json"
MAX_FIXTURES_PER_RUN = 3
DROP_THRESHOLD = 10.0
MAX_BASELINE_AGE = 30 * 60
TIMEOUT = 15

API_KEY = os.getenv("ODDS_API_KEY", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
})


def api_get(path):
    response = session.get(BASE + path, timeout=TIMEOUT)
    print(f"API HTTP {response.status_code}: {path}")

    if response.status_code == 429:
        print("Quota API atteint. Arrêt sans nouvelle tentative.")
        return None

    response.raise_for_status()
    result = response.json()

    if result.get("success") != 1:
        print("Réponse API non réussie.")
        return None

    return result.get("data")


def send_telegram(message):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("Telegram non configuré : alerte non envoyée.")
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            json={
                "chat_id": TG_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )
        print("Telegram HTTP :", response.status_code)
        response.raise_for_status()
    except requests.RequestException as error:
        print("Erreur Telegram :", type(error).__name__)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
            return state if isinstance(state, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def parse_time(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return None


def get_candidate_fixtures():
    data = api_get("/fixtures?status=all&per_page=100")

    if not isinstance(data, list):
        print("Liste de matchs indisponible.")
        return None

    now = datetime.now(timezone.utc)
    candidates = []

    for fixture in data:
        if not isinstance(fixture, dict):
            continue

        status = str(fixture.get("status", "")).lower()
        kickoff = parse_time(fixture.get("kickoff_utc"))

        is_live = status in ("in_play", "live")

        if is_live:
            candidates.append((0, fixture))
        elif status == "scheduled" and kickoff:
            delta = kickoff - now

            if timedelta(0) <= delta <= timedelta(hours=8):
                candidates.append((1, fixture))

    candidates.sort(key=lambda item: (
        item[0],
        item[1].get("kickoff_utc", "")
    ))

    print("Matchs candidats :", len(candidates))
    return [item[1] for item in candidates[:MAX_FIXTURES_PER_RUN]]


def get_prices(fixture):
    fixture_id = fixture.get("id")
    if fixture_id is None:
        return None

    data = api_get(f"/fixtures/{fixture_id}/odds")

    if not isinstance(data, dict):
        return None

    bookmakers = data.get("bookmakers", [])
    if not isinstance(bookmakers, list):
        return None

    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue

        if bookmaker.get("slug") != "bet365":
            continue

        odds = bookmaker.get("odds", {})
        if not isinstance(odds, dict):
            return None

        teams = fixture.get("teams") or {}
        home = (teams.get("home") or {}).get("name", "?")
        away = (teams.get("away") or {}).get("name", "?")
        prices = []

        for market, details in odds.items():
            if not isinstance(details, dict):
                continue

            # Préférer les cotes en direct ; sinon les dernières
            # cotes pré-match disponibles (closing).
            current = details.get("inplay")
            if not isinstance(current, dict):
                current = details.get("closing")

            if not isinstance(current, dict):
                continue

            line = current.get("line", "")
            for outcome, value in current.items():
                if outcome in ("line", "opened_at"):
                    continue

                if isinstance(value, bool) or not isinstance(
                    value, (int, float)
                ):
                    continue

                if value <= 1.0:
                    continue

                key = "|".join([
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

        return prices

    print("Bet365 absent des cotes du match.")
    return []


def main():
    if not API_KEY:
        raise SystemExit("Secret ODDS_API_KEY absent.")

    print("=== BET365 ODDS ALERT ===")
    print("Seuil de baisse :", DROP_THRESHOLD, "%")
    print("Maximum de matchs :", MAX_FIXTURES_PER_RUN)

    state = load_state()
    now = time.time()

    fixtures = get_candidate_fixtures()
    if fixtures is None:
        print("Arrêt : impossible de lire les matchs.")
        return

    alerts = []
    observed = 0

    for fixture in fixtures:
        prices = get_prices(fixture)

        if prices is None:
            print("Cotes indisponibles pour le match.")
            continue

        for item in prices:
            observed += 1
            key = item["key"]
            old = state.get(key)

            previous_price = None
            previous_time = None

            if isinstance(old, dict):
                try:
                    previous_price = float(old.get("price"))
                    previous_time = float(old.get("time"))
                except (TypeError, ValueError):
                    pass

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

            # Une baisse détectée ne doit pas être répétée à
            # chaque passage : le nouveau prix devient la référence.
            state[key] = {
                "price": item["price"],
                "time": now,
            }

    # Supprimer les anciennes références pour garder un fichier léger.
    state = {
        key: value for key, value in state.items()
        if isinstance(value, dict)
        and isinstance(value.get("time"), (int, float))
        and now - value["time"] <= 24 * 3600
    }

    save_state(state)

    print("Cotes analysées :", observed)
    print("Alertes détectées :", len(alerts))
    print("Historique sauvegardé dans", STATE_FILE)

    for message in alerts:
        send_telegram(message)

    print("=== FIN ===")


if __name__ == "__main__":
    main()
    
