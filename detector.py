
import os
import sys
import requests

API_BASE_URL = "https://api.5dollarfootballapi.com/v1"
TIMEOUT = 20


def api_get(path):
    key = os.getenv("ODDS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Le secret ODDS_API_KEY est absent.")

    response = requests.get(
        API_BASE_URL + path,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        timeout=TIMEOUT,
    )

    print(f"HTTP {response.status_code} - {path}")
    response.raise_for_status()
    return response.json()


def get_fixtures(status):
    result = api_get(f"/fixtures?status={status}&per_page=100")
    data = result.get("data", [])
    return data if isinstance(data, list) else []


def main():
    print("=== TEST DE RECUPERATION DES COTES ===")
    print("Clé API configurée :", bool(os.getenv("ODDS_API_KEY")))

    live = get_fixtures("live")
    print("Matchs en direct :", len(live))

    fixture = None

    if live:
        fixture = live[0]
        print("Test des cotes sur un match en direct.")
    else:
        print("Aucun match en direct. Recherche d'un match programmé.")
        scheduled = get_fixtures("scheduled")
        print("Matchs programmés :", len(scheduled))

        if scheduled:
            fixture = scheduled[0]
            print("Test des cotes sur un match programmé.")

    if not fixture:
        print("Aucun match disponible pour tester les cotes.")
        return

    fixture_id = fixture.get("id")
    teams = fixture.get("teams") or {}
    home = (teams.get("home") or {}).get("name", "?")
    away = (teams.get("away") or {}).get("name", "?")

    print("Match :", home, "-", away)
    print("ID du match :", fixture_id)

    result = api_get(f"/fixtures/{fixture_id}/odds")
    data = result.get("data", {})

    if not isinstance(data, dict):
        print("Format inattendu pour les cotes.")
        return

    bookmakers = data.get("bookmakers", [])
    print("Nombre de bookmakers :", len(bookmakers))

    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue

        print("\nBookmaker :", bookmaker.get("name", "?"))
        odds = bookmaker.get("odds") or {}

        if not isinstance(odds, dict):
            continue

        print("Marchés disponibles :", list(odds.keys()))

        for market_name, market_data in odds.items():
            if not isinstance(market_data, dict):
                continue

            print("Marché :", market_name)

            for period in ("opening", "closing", "inplay"):
                values = market_data.get(period)
                if isinstance(values, dict):
                    print(f"  {period} :", values)

    print("\n=== FIN DU TEST ===")
    print("Aucune alerte de pari n'a été envoyée.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERREUR :", type(error).__name__, str(error))
        sys.exit(1)
        
