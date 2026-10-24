
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
MAX_HISTORY_SECONDS = 360
MAX_MATCHES = 100


def get_json(url, headers=None):
    request = urllib.request.Request(
        url,
        headers=headers or {},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)

    except urllib.error.HTTPError as error:
        try:
            details = error.read().decode("utf-8")
            result = json.loads(details)
            description = result.get(
                "description",
                result.get("message", "Erreur API sans description"),
            )
        except Exception:
            description = "Impossible de lire la réponse d'erreur"

        raise RuntimeError(
            f"HTTP {error.code}: {description}"
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

    return get_json(
        BASE_URL + path,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
        },
    )


def telegram_send(message):
    if not BOT_TOKEN:
        raise RuntimeError("Secret TELEGRAM_BOT_TOKEN manquant")

    if not CHAT_ID:
        raise RuntimeError("Secret TELEGRAM_CHAT_ID manquant")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    result = get_json(request.full_url, headers={})
    # Cette fonction GET ne convient pas à l'envoi POST.
    return result


def send_telegram(message):
    if not BOT_TOKEN:
        raise RuntimeError("Secret TELEGRAM_BOT_TOKEN manquant")

    if not CHAT_ID:
        raise RuntimeError("Secret TELEGRAM_CHAT_ID manquant")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            raise RuntimeError(
                result.get("description", "Telegram a refusé le message")
            )

        return True

    except urllib.error.HTTPError as error:
        try:
            details = json.loads(error.read().decode("utf-8"))
            description = details.get(
                "description", "Erreur Telegram sans description"
            )
        except Exception:
            description = "Réponse Telegram illisible"

        raise RuntimeError(
            f"Telegram HTTP {error.code}: {description}"
        ) from None

    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Connexion Telegram impossible : "
            f"{type(error.reason).__name__}"
        ) from None


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def extract_fixtures(response):
    if not isinstance(response, dict):
        return []

    data = response.get("data", [])

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        fixtures = data.get("fixtures", [])
        return fixtures if isinstance(fixtures, list) else []

    return []


def get_current_prices(response):
    """Extract Bet365 in-play goal-line over/under prices."""
    data = response.get("data", {})
    if not isinstance(data, dict):
        return []

    bookmakers = data.get("bookmakers", [])
    if not isinstance(bookmakers, list):
        return []

    prices = []

    for bookmaker in bookmakers:
        if bookmaker.get("slug") != "bet365":
            continue

        odds = bookmaker.get("odds", {})
        if not isinstance(odds, dict):
            continue

        market = odds.get("goal_line", {})
        if not isinstance(market, dict):
            continue

        inplay = market.get("inplay")
        if not isinstance(inplay, dict):
            continue

        for side in ("over", "under"):
            try:
                price = float(inplay.get(side))
                line = inplay.get("line")
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

    print("=== Recherche des matchs en direct ===")
    response = api_get("/fixtures?status=live&per_page=100")
    fixtures = extract_fixtures(response)

    print(f"Matchs en direct reçus : {len(fixtures)}")

    state = load_state()
    now = int(time.time())
    checked = 0
    errors = 0

    for fixture in fixtures[:MAX_MATCHES]:
        fixture_id = fixture.get("id")
        teams = fixture.get("teams", {})
        home = teams.get("home", {}).get("name", "Équipe domicile")
        away = teams.get("away", {}).get("name", "Équipe extérieure")

        if fixture_id is None:
            continue

        try:
            odds_response = api_get(f"/fixtures/{fixture_id}/odds")
            prices = get_current_prices(odds_response)
            checked += 1

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
                    ):
                        elapsed = now - old_time

                        if (
                            MIN_HISTORY_SECONDS <= elapsed
                            <= MIN_HISTORY_SECONDS + 180
                            and old_price > 0
                        ):
                            drop = (old_price - item["price"]) / old_price

                            if drop >= DROP_THRESHOLD:
                                message = (
                                    "📉 BAISSE DE COTE DÉTECTÉE\n\n"
                                    f"Match : {home} - {away}\n"
                                    f"Marché : Total buts, "
                                    f"{item['side']} {item['line']}\n"
                                    f"Ancienne cote : {old_price:.2f}\n"
                                    f"Nouvelle cote : {item['price']:.2f}\n"
                                    f"Baisse : {drop * 100:.1f}%\n"
                                    f"Intervalle : {elapsed} secondes\n"
                                    "⚠️ Vérifie la cote chez ton bookmaker."
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
    print(f"Matchs avec cotes vérifiés : {checked}")
    print(f"Erreurs rencontrées : {errors}")
    print(
        "Terminé à :",
        datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"ERREUR FATALE : {type(error).__name__}: {error}"
        )
        raise SystemExit(1)
