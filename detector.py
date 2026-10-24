import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

BASE_URL = "https://api.5dollarfootballapi.com/v1"
STATE_FILE = "odds_state.json"

API_KEY = os.getenv("ODDS_API_KEY", "").strip()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

DROP_THRESHOLD = 0.10
MIN_HISTORY_SECONDS = 240
MAX_HISTORY_SECONDS = 420
MAX_MATCHES = 100


def request_json(url, method="GET", headers=None, data=None):
    request = urllib.request.Request(
        url,
        headers=headers or {},
        data=data,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)

    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8")
            details = json.loads(body)
            message = details.get(
                "message",
                details.get("description", body[:300]),
            )
        except Exception:
            message = "Réponse d'erreur illisible"

        raise RuntimeError(
            f"HTTP {error.code}: {message}"
        ) from None

    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Erreur réseau : {type(error.reason).__name__}"
        ) from None

    except (TimeoutError, json.JSONDecodeError):
        raise RuntimeError(
            "Délai dépassé ou réponse JSON invalide"
        ) from None


def api_get(path):
    if not API_KEY:
        raise RuntimeError("Secret ODDS_API_KEY manquant")

    return request_json(
        BASE_URL + path,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
        },
    )


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant"
        )

    url = (
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    result = request_json(
        url,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=payload,
    )

    if not result.get("ok"):
        raise RuntimeError(
            result.get("description", "Erreur Telegram")
        )


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            result = json.load(file)
            return result if isinstance(result, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def extract_fixtures(response):
    """Accepte plusieurs structures de réponse JSON."""
    if not isinstance(response, dict):
        print("Réponse API inattendue :", type(response).__name__)
        return []

    print("Clés principales de la réponse :", list(response.keys()))

    # Formats courants : {"data": [...]} ou {"data": {"fixtures": [...]}}
    for container in (response, response.get("data")):
        if isinstance(container, list):
            return container

        if isinstance(container, dict):
            for key in ("fixtures", "data", "results", "items"):
                value = container.get(key)
                if isinstance(value, list):
                    print(f"Matchs trouvés dans la clé : {key}")
                    return value

    print(
        "Structure API non reconnue :",
        json.dumps(response, ensure_ascii=False)[:1500],
    )
    return []


def fixture_name(fixture, side):
    teams = fixture.get("teams", {})
    if isinstance(teams, dict):
        team = teams.get(side, {})
        if isinstance(team, dict) and team.get("name"):
            return str(team["name"])

    # Autre format parfois utilisé par les API
    value = fixture.get(f"{side}_team")
    if isinstance(value, dict):
        return str(value.get("name", side))
    if isinstance(value, str):
        return value

    return "Équipe inconnue"


def extract_prices(response):
    """Extrait les cotes Over/Under si elles sont présentes."""
    if not isinstance(response, dict):
        return []

    containers = [response]
    data = response.get("data")
    if isinstance(data, dict):
        containers.append(data)

    prices = []

    for container in containers:
        bookmakers = container.get("bookmakers", [])

        if not isinstance(bookmakers, list):
            continue

        for bookmaker in bookmakers:
            name = str(
                bookmaker.get("slug", bookmaker.get("name", ""))
            ).lower()

            if "bet365" not in name:
                continue

            odds = bookmaker.get("odds", {})
            if not isinstance(odds, dict):
                continue

            goal_line = odds.get("goal_line", {})
            if not isinstance(goal_line, dict):
                continue

            inplay = goal_line.get("inplay", {})
            if not isinstance(inplay, dict):
                continue

            line = inplay.get("line")

            for side in ("over", "under"):
                try:
                    price = float(inplay.get(side))
                    if price > 1:
                        prices.append({
                            "side": side,
                            "line": line,
                            "price": price,
                        })
                except (TypeError, ValueError):
                    pass

    return prices


def main():
    print("=== Vérification de la configuration ===")
    print("Clé API :", "présente" if API_KEY else "MANQUANTE")
    print("Token Telegram :", "présent" if BOT_TOKEN else "MANQUANT")
    print("Chat ID :", "présent" if CHAT_ID else "MANQUANT")

    print("=== Test Telegram ===")
    send_telegram(
        "✅ Test réussi : le détecteur de cotes est connecté à Telegram."
    )
    print("Telegram OK : message de test envoyé.")

    print("=== Recherche des matchs en direct ===")
    response = api_get("/fixtures?status=live&per_page=100")
    fixtures = extract_fixtures(response)

    print("Matchs en direct reçus :", len(fixtures))

    if not fixtures:
        print(
            "Aucun match récupéré. Vérifie ci-dessus les clés et "
            "la structure de réponse de l'API."
        )
        # Essai complémentaire : récupérer des fixtures sans filtre.
        print("=== Essai complémentaire sans filtre live ===")
        try:
            alternate = api_get("/fixtures?per_page=10")
            print(
                "Réponse sans filtre :",
                json.dumps(alternate, ensure_ascii=False)[:1500],
            )
        except Exception as error:
            print(
                "Erreur de l'essai complémentaire :",
                type(error).__name__,
                str(error),
            )

    state = load_state()
    now = int(time.time())
    checked = 0
    errors = 0

    for fixture in fixtures[:MAX_MATCHES]:
        fixture_id = fixture.get("id", fixture.get("fixture_id"))

        if fixture_id is None:
            print("Match ignoré : identifiant absent")
            continue

        home = fixture_name(fixture, "home")
        away = fixture_name(fixture, "away")

        try:
            odds_response = api_get(
                f"/fixtures/{fixture_id}/odds"
            )
            prices = extract_prices(odds_response)
            checked += 1

            if not prices:
                print(
                    f"Aucune cote Bet365 Over/Under reconnue "
                    f"pour le match {fixture_id}"
                )

            for item in prices:
                key = (
                    f"{fixture_id}|{item['side']}|{item['line']}"
                )
                previous = state.get(key)

                if isinstance(previous, dict):
                    old_price = previous.get("price")
                    old_time = previous.get("time")

                    if (
                        isinstance(old_price, (int, float))
                        and isinstance(old_time, int)
                        and old_price > 0
                    ):
                        elapsed = now - old_time
                        drop = (old_price - item["price"]) / old_price

                        if (
                            MIN_HISTORY_SECONDS <= elapsed
                            <= MAX_HISTORY_SECONDS
                            and drop >= DROP_THRESHOLD
                        ):
                            message = (
                                "📉 BAISSE DE COTE DÉTECTÉE\n\n"
                                f"Match : {home} - {away}\n"
                                f"Marché : Total buts, "
                                f"{item['side']} {item['line']}\n"
                                f"Ancienne cote : {old_price:.2f}\n"
                                f"Nouvelle cote : {item['price']:.2f}\n"
                                f"Baisse : {drop * 100:.1f}%\n"
                                f"Intervalle : {elapsed} secondes\n\n"
                                "Vérifie la cote chez ton bookmaker."
                            )
                            send_telegram(message)
                            print(
                                f"Alerte envoyée : {home} - {away}"
                            )

                state[key] = {
                    "price": item["price"],
                    "time": now,
                }

        except Exception as error:
            errors += 1
            print(
                f"Erreur match {fixture_id}: "
                f"{type(error).__name__}: {error}"
            )

        time.sleep(0.1)

    save_state(state)

    print("=== Résultat ===")
    print("Matchs avec cotes vérifiés :", checked)
    print("Erreurs rencontrées :", errors)
    print("Terminé à :", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"ERREUR FATALE : {type(error).__name__}: {error}"
        )
        raise SystemExit(1)
