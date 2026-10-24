
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://api.5dollarfootballapi.com/v1"
STATE_FILE = "odds_state.json"

DROP_LIMIT = 10.0
WINDOW_SECONDS = 300
MIN_WINDOW_SECONDS = 240
MAX_WINDOW_SECONDS = 360
MAX_MATCHES = 3
HISTORY_RETENTION = 900
ALERT_COOLDOWN = 1800

API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def get_json(url, headers=None, data=None):
    request = urllib.request.Request(
        url,
        headers=headers or {},
        data=data,
        method="POST" if data is not None else "GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)

    except urllib.error.HTTPError as error:
        # Do not print URLs, headers, tokens, or secret values.
        raise RuntimeError(
            f"HTTP error {error.code}"
        ) from None

    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Network error: {type(error.reason).__name__}"
        ) from None

    except (TimeoutError, json.JSONDecodeError):
        raise RuntimeError(
            "Request timed out or returned invalid JSON"
        ) from None


def api_get(path):
    result = get_json(
        BASE_URL + path,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
        },
    )

    if not isinstance(result, dict):
        raise RuntimeError("Unexpected odds API response")

    if result.get("success") is False:
        raise RuntimeError("Odds API returned success=false")

    return result.get("data", result)


def send_telegram(message):
    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    body = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    result = get_json(url, data=body)

    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError("Telegram rejected the message")

    return True


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

        if isinstance(state, dict):
            state.setdefault("history", {})
            state.setdefault("alerts", {})
            return state

    except (OSError, json.JSONDecodeError):
        pass

    return {"history": {}, "alerts": {}}


def save_state(state):
    temporary = STATE_FILE + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False)

    os.replace(temporary, STATE_FILE)


def extract_fixtures(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        fixtures = data.get("data", [])
        if isinstance(fixtures, list):
            return fixtures

    return []


def extract_score(match):
    goals = match.get("goals") or {}
    home = goals.get("home")
    away = goals.get("away")

    if home is None or away is None:
        return "indisponible"

    return f"{home}-{away}"


def team_name(match, side):
    teams = match.get("teams") or {}
    team = teams.get(side) or {}

    if isinstance(team, dict):
        return team.get("name", side)

    return side


def find_goal_market(odds_data):
    if not isinstance(odds_data, dict):
        return []

    bookmakers = odds_data.get("bookmakers", [])
    if not isinstance(bookmakers, list):
        return []

    results = []

    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue

        if bookmaker.get("slug") != "bet365":
            continue

        odds = bookmaker.get("odds") or {}
        if not isinstance(odds, dict):
            continue

        market = odds.get("goalline") or {}
        if isinstance(market, dict):
            live = market.get("inplay")

            if isinstance(live, dict):
                results.append({
                    "bookmaker": bookmaker.get("name", "Bet365"),
                    "line": live.get("line"),
                    "over": live.get("over"),
                    "under": live.get("under"),
                })

        fixed = odds.get("goalline_fixed") or {}
        if isinstance(fixed, dict):
            fixed_live = fixed.get("inplay")

            if isinstance(fixed_live, list):
                for item in fixed_live:
                    if isinstance(item, dict):
                        results.append({
                            "bookmaker": bookmaker.get("name", "Bet365"),
                            "line": item.get("line"),
                            "over": item.get("over"),
                            "under": item.get("under"),
                        })

    return results


def record_price(state, match, bookmaker, line, side, price, now):
    if price <= 1.0:
        return

    fixture_id = str(match.get("id", ""))
    if not fixture_id:
        return

    market_key = f"{fixture_id}|{bookmaker}|{side}|{line}"
    history = state["history"].setdefault(market_key, [])

    history[:] = [
        item for item in history
        if isinstance(item, dict)
        and isinstance(item.get("t"), (int, float))
        and isinstance(item.get("p"), (int, float))
        and 0 <= now - item["t"] <= HISTORY_RETENTION
    ]

    candidates = [
        item for item in history
        if MIN_WINDOW_SECONDS <= now - item["t"]
        <= MAX_WINDOW_SECONDS
    ]

    if candidates:
        reference = min(
            candidates,
            key=lambda item: abs(
                (now - item["t"]) - WINDOW_SECONDS
            ),
        )

        old_price = float(reference["p"])
        drop = (old_price - price) / old_price * 100

        if drop >= DROP_LIMIT:
            alert_key = f"{market_key}|{reference['t']}"
            last_alert = state["alerts"].get(market_key, 0)

            if (
                alert_key not in state["alerts"]
                and now - last_alert >= ALERT_COOLDOWN
            ):
                home = team_name(match, "home")
                away = team_name(match, "away")

                message = (
                    "⚠️ BAISSE DE COTE DÉTECTÉE\n\n"
                    f"Match : {home} - {away}\n"
                    f"Score : {extract_score(match)}\n"
                    f"Bookmaker : {bookmaker}\n"
                    f"Marché : {side.upper()} {line} buts\n"
                    f"Cote précédente : {old_price:.2f}\n"
                    f"Cote actuelle : {price:.2f}\n"
                    f"Baisse : {drop:.1f}%\n"
                    "Intervalle : environ 5 minutes\n\n"
                    "Signal informatif, sans garantie de gain."
                )

                send_telegram(message)
                state["alerts"][alert_key] = now
                state["alerts"][market_key] = now

                print(
                    f"ALERTE ENVOYÉE : {home} - {away}, "
                    f"{side} {line}, baisse {drop:.1f}%"
                )

    history.append({"t": now, "p": price})


def main():
    if not API_KEY:
        raise RuntimeError("Secret ODDS_API_KEY absent")

    if not BOT_TOKEN:
        raise RuntimeError("Secret TELEGRAM_BOT_TOKEN absent")

    if not CHAT_ID:
        raise RuntimeError("Secret TELEGRAM_CHAT_ID absent")

    print("=== Test de connexion Telegram ===")
    send_telegram(
        "✅ Test réussi : le détecteur de cotes "
        "est connecté à Telegram."
    )
    print("Telegram OK : message de test envoyé.")

    now = int(time.time())
    state = load_state()

    state["alerts"] = {
        key: timestamp
        for key, timestamp in state["alerts"].items()
        if isinstance(timestamp, (int, float))
        and 0 <= now - timestamp <= 86400
    }

    print("=== Recherche des matchs en direct ===")

    data = api_get(
        "/fixtures?status=live&per_page=100"
    )
    fixtures = extract_fixtures(data)

    print(f"Matchs en direct reçus : {len(fixtures)}")

    checked = 0
    errors = 0
    prices_found = 0

    for match in fixtures:
        if checked >= MAX_MATCHES:
            break

        if not isinstance(match, dict) or match.get("id") is None:
            continue

        fixture_id = match["id"]
        checked += 1

        try:
            odds_data = api_get(
                f"/fixtures/{fixture_id}/odds?market=goalline"
            )

            if (
                isinstance(odds_data, dict)
                and isinstance(odds_data.get("data"), dict)
            ):
                odds_data = odds_data["data"]

            markets = find_goal_market(odds_data)

            for market in markets:
                line = market.get("line")
                if line is None:
                    continue

                for side in ("over", "under"):
                    try:
                        price = float(market.get(side))
                    except (TypeError, ValueError):
                        continue

                    prices_found += 1

                    record_price(
                        state=state,
                        match=match,
                        bookmaker=market["bookmaker"],
                        line=line,
                        side=side,
                        price=price,
                        now=now,
                    )

        except (RuntimeError, ValueError, TypeError) as error:
            errors += 1
            print(
                f"Match {fixture_id} : "
                f"{type(error).__name__}: {error}"
            )

    save_state(state)

    print(
        f"Terminé : {checked} matchs vérifiés, "
        f"{prices_found} cotes lues, {errors} erreurs."
    )
    print(
        "Les alertes de baisse nécessitent un historique "
        "de prix couvrant environ cinq minutes."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"ERREUR FATALE : {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
    
