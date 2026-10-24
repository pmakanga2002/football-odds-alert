
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

BASE = "https://api.5dollarfootballapi.com/v1"
API_KEY = os.environ["ODDS_API_KEY"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = "odds_state.json"
DROP_LIMIT = 10.0
WINDOW_SECONDS = 300
MAX_MATCHES = 3  # Limite pour rester prudent avec le quota gratuit


def api_get(path):
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("success"):
        raise RuntimeError(f"Erreur API : {result}")
    return result.get("data", [])


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message
    }).encode()
    req = urllib.request.Request(url, data=body)
    with urllib.request.urlopen(req, timeout=20) as response:
        response.read()


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    now = int(datetime.now(timezone.utc).timestamp())
    state = load_state()
    fixtures = api_get("/fixtures?status=live&per_page=100")

    if isinstance(fixtures, dict):
        fixtures = fixtures.get("data", [])

    checked = 0
    alerts = 0

    for match in fixtures:
        if checked >= MAX_MATCHES:
            break

        fixture_id = match.get("id")
        teams = match.get("teams", {})
        home = teams.get("home", {}).get("name", "Équipe domicile")
        away = teams.get("away", {}).get("name", "Équipe extérieure")
        score = match.get("goals", {})
        score_text = f'{score.get("home", "?")}-{score.get("away", "?")}'

        if not fixture_id:
            continue

        checked += 1

        try:
            odds_data = api_get(
                f"/fixtures/{fixture_id}/odds?market=goalline"
            )
        except Exception as e:
            print(f"Cotes indisponibles pour {fixture_id}: {e}")
            continue

        books = odds_data.get("bookmakers", []) if isinstance(
            odds_data, dict
        ) else []

        for book in books:
            if book.get("slug") != "bet365":
                continue

            market = book.get("odds", {}).get("goalline", {})
            live = market.get("inplay")
            if not isinstance(live, dict):
                continue

            line = live.get("line")
            for side in ("over", "under"):
                try:
                    price = float(live.get(side))
                    if price <= 1.0:
                        continue
                except (TypeError, ValueError):
                    continue

                key = f"{fixture_id}|{side}|{line}"
                history = state.get(key, [])
                history = [
                    item for item in history
                    if now - item["t"] <= WINDOW_SECONDS
                ]

                old_prices = [
                    item["p"] for item in history
                    if now - item["t"] >= WINDOW_SECONDS - 30
                ]

                if old_prices:
                    old_price = old_prices[0]
                    drop = (old_price - price) / old_price * 100

                    if drop >= DROP_LIMIT:
                        send_telegram(
                            "⚠️ CHUTE DE COTE DÉTECTÉE\n"
                            f"Match : {home} - {away}\n"
                            f"Score : {score_text}\n"
                            f"Marché : {side.upper()} {line} buts\n"
                            f"Bookmaker : Bet365\n"
                            f"Cote : {old_price:.2f} → {price:.2f}\n"
                            f"Baisse : {drop:.1f}% en environ 5 min\n"
                            "À vérifier : ce signal ne garantit pas un gain."
                        )
                        alerts += 1
                        history = []

                history.append({"t": now, "p": price})
                state[key] = history

    save_state(state)
    print(
        f"Contrôle terminé : {checked} matchs examinés, "
        f"{alerts} alertes envoyées."
    )


if __name__ == "__main__":
    main()
  
