
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
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8"))
            message = details.get(
                "message",
                details.get("description", "Erreur API"),
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
    if not isinstance(response, dict):
        return []

    print("Clés principales de la réponse :", list(response.keys()))

    data = response.get("data", [])

    if isinstance(data, list):
        print("Matchs trouvés dans la clé : data")
        return data

    if isinstance(data, dict):
        print("Clés de data :", list(data.keys()))
        for key in ("fixtures", "data", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                print(f"Matchs trouvés dans la clé : {key}")
                return value

    print(
        "Structure API non reconnue :",
        json.dumps(response, ensure_ascii=False)[:1000],
    )
    return []


def get_live_fixtures():
    print("=== Essai 1 : filtre live ===")

    try:
        response = api_get("/fixtures?status=live&per_page=500")
        fixtures = extract_fixtures(response)
        print("Matchs reçus avec filtre live :", len(fixtures))

        if fixtures:
            return fixtures

    except Exception as error:
        print(
            "Échec du filtre live :",
            type(error).__name__,
            str(error),
        )

    print("=== Essai 2 : récupération sans filtre ===")

    response = api_get("/fixtures?per_page=500")
    fixtures = extract_fixtures(response)
    print("Matchs reçus sans filtre :", len(fixtures))

    live_statuses = {
        "live",
        "in_play",
        "inplay",
        "1h",
        "2h",
        "ht",
        "half_time",
        "extra_time",
        "penalties",
        "playing",
    }

    live = []

    for fixture in fixtures:
        status = str(
            fixture.get("status", "")
        ).strip().lower().replace(" ", "_")

        status_code = str(
            fixture.get("status_code", "")
        ).strip().lower().replace(" ", "_")

        if status in live_statuses or status_code in live_statuses:
            live.append(fixture)

    print("Matchs identifiés comme en cours :", len(live))

    if not live:
        print("Aucun statut live reconnu dans la première page.")
        print("Exemples de statuts reçus :")

        for fixture in fixtures[:10]:
            print(
                "ID :", fixture.get("id"),
                "| Statut :", fixture.get("status"),
                "| Code :", fixture.get("status_code"),
                "| Coup d'envoi :", fixture.get("kickoff_utc"),
            )

    return live


def fixture_name(fixture, side):
    teams = fixture.get("teams", {})

    if isinstance(teams, dict):
        team = teams.get(side, {})
        if isinstance(team, dict):
            return str(team.get("name", "Équipe inconnue"))

    return "Équipe inconnue"


def extract_prices(response):
    if not isinstance(response, dict):
        return []

    containers = [response]

    if isinstance(response.get("data"), dict):
        containers.append(response["data"])

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
                    continue

    return prices


def main():
    print("=== Configuration ===")
    print("Clé API :", "présente" if API_KEY else "MANQUANTE")
    print("Token Telegram :", "présent" if BOT_TOKEN else "MANQUANT")
    print("Chat ID :", "présent" if CHAT_ID else "MANQUANT")

    print("=== Test Telegram ===")
    send_telegram(
        "✅ Test réussi : le détecteur de cotes est connecté à Telegram."
    )
    print("Telegram OK : message de test envoyé.")

    fixtures = get_live_fixtures()
    print("Matchs en direct retenus :", len(fixtures))

    if not fixtures:
        print(
            "Aucun match en direct trouvé. "
            "Aucune cote ne sera analysée pendant cette exécution."
        )
        return

    state = load_state()
    now = int(time.time())
    checked = 0
    errors = 0
    alerts = 0

    for fixture in fixtures[:MAX_MATCHES]:
        fixture_id = fixture.get("id")

        if fixture_id is None:
            continue

        home = fixture_name(fixture, "home")
        away = fixture_name(fixture, "away")
        status = fixture.get("status", "inconnu")

        try:
            response = api_get(
                f"/fixtures/{fixture_id}/odds"
            )
            prices = extract_prices(response)
            checked += 1

            print(
                f"Match : {home} - {away} | "
                f"Statut : {status} | Cotes reconnues : {len(prices)}"
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
                            MIN_HISTORY_SECONDS <= elapsed <= MAX_HISTORY_SECONDS
                            and drop >= DROP_THRESHOLD
                        ):
                            send_telegram(
                                "📉 BAISSE DE COTE DÉTECTÉE\n\n"
                                f"Match : {home} - {away}\n"
                                f"Statut : {status}\n"
                                f"Marché : Total buts, "
                                f"{item['side']} {item['line']}\n"
                                f"Ancienne cote : {old_price:.2f}\n"
                                f"Nouvelle cote : {item['price']:.2f}\n"
                                f"Baisse : {drop * 100:.1f}%\n"
                                f"Intervalle : {elapsed} secondes\n\n"
                                "Vérifie la cote chez ton bookmaker."
                            )
                            alerts += 1
                            print("Alerte Telegram envoyée.")

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
    print("Alertes envoyées :", alerts)
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
        
