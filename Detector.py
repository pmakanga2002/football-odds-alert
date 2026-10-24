
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://api.5dollarfootballapi.com/v1"

API_KEY = os.environ["ODDS_API_KEY"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = "odds_state.json"

DROP_LIMIT = 10.0
WINDOW_SECONDS = 300

# GitHub Actions runs every 5 minutes.
# Check only a few matches to limit API usage.
MAX_MATCHES = 3

# Retain history for comparisons and alert deduplication.
HISTORY_RETENTION = 900
ALERT_COOLDOWN = 1800


def get_json(url, headers=None, data=None):
    request = urllib.request.Request(
        url,
        headers=headers or {},
        data=data,
        method="POST" if data is not None else "GET",
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def api_get(path):
    result = get_json(
        BASE_URL + path,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
        },
    )

    if not result.get("success"):
        raise RuntimeError("The odds API returned an unsuccessful response")

    return result.get("data")


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    body = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    result = get_json(url, data=body)

    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message")


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
            if isinstance(state, dict):
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
        return "score indisponible"

    return f"{home}-{away}"


def find_goal_market(odds_data):
    if not isinstance(odds_data, dict):
        return []

    bookmakers = odds_data.get("bookmakers", [])

    results = []

    for bookmaker in bookmakers:
        # The free plan's default bookmaker is Bet365.
        if bookmaker.get("slug") != "bet365":
            continue

        odds = bookmaker.get("odds") or {}
        market = odds.get("goalline") or {}
        live = market.get("inplay")

        if isinstance(live, dict):
            results.append({
                "bookmaker": bookmaker.get("name", "Bet365"),
                "line": live.get("line"),
                "over": live.get("over"),
                "under": live.get("under"),
            })

        # Also inspect the fixed goal-line ladder, when provided.
        fixed = odds.get("goalline_fixed") or {}
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
        if 0 <= now - item["t"] <= HISTORY_RETENTION
    ]

    # Select the closest stored price near five minutes ago.
    candidates = [
        item for item in history
        if 240 <= now - item["t"] <= 360
    ]

    reference = None

    if candidates:
        reference = min(
            candidates,
            key=lambda item: abs((now - item["t"]) - WINDOW_SECONDS),
        )

    if reference:
        old_price = reference["p"]
        drop = (old_price - price) / old_price * 100

        if drop >= DROP_LIMIT:
            alert_key = (
                f"{market_key}|{reference['t']}"
            )

            last_alert = state["alerts"].get(market_key, 0)

            if (
                alert_key not in state["alerts"]
                and now - last_alert >= ALERT_COOLDOWN
            ):
                teams = match.get("teams") or {}
                home = (teams.get("home") or {}).get(
                    "name", "Domicile"
                )
                away = (teams.get("away") or {}).get(
                    "name", "Extérieur"
                )

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
                    "Signal informatif, pas une garantie de gain."
                )

                send_telegram(message)
                state["alerts"][alert_key] = now
                state["alerts"][market_key] = now
                print(f"Alert sent: {home} - {away}, {side} {line}")

    history.append({"t": now, "p": price})


def main():
    now = int(time.time())
    state = load_state()

    state.setdefault("history", {})
    state.setdefault("alerts", {})

    # Remove expired alert deduplication records.
    state["alerts"] = {
        key: timestamp
        for key, timestamp in state["alerts"].items()
        if isinstance(timestamp, (int, float))
        and now - timestamp <= 86400
    }

    data = api_get("/fixtures?status=live&per_page=100")
    fixtures = extract_fixtures(data)

    checked = 0
    errors = 0

    for match in fixtures:
        if checked >= MAX_MATCHES:
            break

        fixture_id = match.get("id")
        if fixture_id is None:
            continue

        checked += 1

        try:
            odds_data = api_get(
                f"/fixtures/{fixture_id}/odds?market=goalline"
            )

            # Some responses wrap the payload in another data object.
            if (
                isinstance(odds_data, dict)
                and isinstance(odds_data.get("data"), dict)
            ):
                odds_data = odds_data["data"]

            markets = find_goal_market(odds_data)

            for market in markets:
                line = market.get("line")
                bookmaker = market["bookmaker"]

                if line is None:
                    continue

                for side in ("over", "under"):
                    try:
                        price = float(market[side])
                    except (TypeError, ValueError):
                        continue

                    record_price(
                        state=state,
                        match=match,
                        bookmaker=bookmaker,
                        line=line,
                        side=side,
                        price=price,
                        now=now,
                    )

        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            RuntimeError,
        ) as error:
            errors += 1
            # Do not print URLs, request headers, or secret values.
            print(
                f"Could not check fixture {fixture_id}: "
                f"{type(error).__name__}"
            )

    save_state(state)

    print(
        f"Finished at {datetime.now(timezone.utc).isoformat()}: "
        f"{checked} matches checked; {errors} errors."
    )


if __name__ == "__main__":
    main()
                      
