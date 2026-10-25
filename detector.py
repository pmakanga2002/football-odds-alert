
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
MAX_MATCHES = 500


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
            body = json.loads(error.read().decode("utf-8"))
            message = body.get("error", {}).get(
                "message",
                body.get("message", body.get("description", "Erreur API")),
            )
        except Exception:
            message = "Réponse d'erreur illisible"

        raise RuntimeError(f"HTTP {error.code}: {message}") from None

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

    result = request_json(
        BASE_URL + path,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
        },
    )

    if isinstance(result, dict) and result.get("success") == 0:
        error = result.get("error", {})
        raise RuntimeError(
            error.get("message", "L'API a signalé une erreur")
        )

    return result


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant"
        )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

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


def get_live_fixtures():
    print("=== Recherche des matchs en direct ===")

    response = api_get("/fixtures?status=live&per_page=500")

    if not isinstance(response, dict):
        raise RuntimeError("Réponse fixtures invalide")

    fixtures = response.get("data", [])
    pagination = response.get("pagination", {})

    if not isinstance(fixtures, list):
        raise RuntimeError("Le champ data n'est pas une liste")

    print("Pagination :", pagination)
    print("Matchs reçus par l'API :", len(fixtures))

    # Le filtre status=live doit normalement renvoyer in_play.
    live = [
        fixture for fixture in fixtures
        if isinstance(fixture, dict)
        and fixture.get("status") == "in_play"
    ]

    print("Matchs confirmés en direct :", len(live))

    for fixture in live:
        print(
            fixture.get("teams", {}).get("home", {}).get("name", "?"),
            "-",
            fixture.get("teams", {}).get("away", {}).get("name", "?"),
            "| minute/statut :",
            fixture.get("status_code"),
        )

    return live


def fixture_name(fixture, side):
    teams = fixture.get("teams", {})
    team = teams.get(side, {}) if isinstance(teams, dict) else {}
    return team.get("name", "Équipe inconnue")


def add_price(prices, market, side, line, value):
    try:
        price = float(value)
        if price > 1:
            key = f"{market}|{side}|{line}"
            prices[key] = {
                "market": market,
                "side": side,
                "line": line,
                "price": price,
            }
    except (TypeError, ValueError):
        pass


def extract_prices(response):
    """Lit les cotes Bet365 du marché goal_line et goal_line_fixed."""
    if not isinstance(response, dict):
        return []

    data = response.get("data", response)
    if not isinstance(data, dict):
        return []

    bookmakers = data.get("bookmakers", [])
    if not isinstance(bookmakers, list):
        return []

    prices = {}

    for bookmaker in bookmakers:
        slug = str(bookmaker.get("slug", "")).lower()
        if slug != "bet365":
            continue

        odds = bookmaker.get("odds", {})
        if not isinstance(odds, dict):
            continue

        # Marché classique Over/Under
        market = odds.get("goal_line", {})
        if isinstance(market, dict):
            inplay = market.get("inplay")

            if isinstance(inplay, dict):
                line = inplay.get("line")
                add_price(prices, "goal_line", "over", line,
                           inplay.get("over"))
                add_price(prices, "goal_line", "under", line,
                           inplay.get("under"))

        # Échelle de lignes fixes : Over/Under 0.5, 1.5, 2.5, etc.
        fixed = odds.get("goal_line_fixed", [])

        if isinstance(fixed, list):
            for entry in fixed:
                if not isinstance(entry, dict):
                    continue

                line = entry.get("line")
                inplay = entry.get("inplay")

                if isinstance(inplay, dict):
                    add_price(prices, "goal_line_fixed", "over",
                              line, inplay.get("over"))
                    add_price(prices, "goal_line_fixed", "under",
                              line, inplay.get("under"))

                # Tolérance aux réponses où les prix sont directement
                # placés dans chaque élément de la liste.
                elif isinstance(entry, dict):
                    add_price(prices, "goal_line_fixed", "over",
                              line, entry.get("over"))
                    add_price(prices, "goal_line_fixed", "under",
                              line, entry.get("under"))

    return list(prices.values())


def main():
    print("=== Vérification de la configuration ===")
    print("Clé API :", "présente" if API_KEY else "MANQUANTE")
    print("Token Telegram :", "présent" if BOT_TOKEN else "MANQUANT")
    print("Chat ID :", "présent" if CHAT_ID else "MANQUANT")

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Configuration Telegram incomplète")

    fixtures = get_live_fixtures()

    if not fixtures:
        print(
            "Aucun match en direct fourni par l'API. "
            "Nouvel essai au prochain lancement."
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
        status_code = fixture.get("status_code", "?")

        try:
            response = api_get(f"/fixtures/{fixture_id}/odds")
            prices = extract_prices(response)
            checked += 1

            print(
                f"{home} - {away} | Statut {status_code} | "
                f"Cotes Bet365 reconnues : {len(prices)}"
            )

            for item in prices:
                key = (
                    f"{fixture_id}|{item['market']}|"
                    f"{item['side']}|{item['line']}"
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
                                f"Statut : {status_code}\n"
                                f"Marché : {item['market']}\n"
                                f"Choix : {item['side']} {item['line']}\n"
                                f"Ancienne cote : {old_price:.2f}\n"
                                f"Nouvelle cote : {item['price']:.2f}\n"
                                f"Baisse : {drop * 100:.1f}%\n"
                                f"Intervalle : {elapsed} secondes\n\n"
                                "Vérifie la cote actuelle chez le bookmaker."
                            )

                            send_telegram(message)
                            alerts += 1
                            print("Alerte Telegram envoyée.")

                # Enregistre le dernier prix observé.
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
    
