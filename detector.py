
import os
import sys
import requests
from collections import Counter

API_BASE_URL = "https://api.5dollarfootballapi.com/v1"
TIMEOUT = 20


def api_get(path):
    api_key = os.getenv("ODDS_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("Le secret ODDS_API_KEY est absent.")

    response = requests.get(
        API_BASE_URL + path,
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-API-Key": api_key,
            "Accept": "application/json",
        },
        timeout=TIMEOUT,
    )

    print(f"HTTP {response.status_code} - {path}")

    if response.status_code >= 400:
        print("Réponse API :", response.text[:600])
        response.raise_for_status()

    return response.json()


def extract_list(result):
    if not isinstance(result, dict):
        print("Format inattendu :", type(result).__name__)
        return []

    data = result.get("data", [])

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        # Certaines routes peuvent renvoyer un objet contenant
        # une liste de données ou les détails d'un compte.
        print("Le champ data est un objet.")
        print("Clés de data :", list(data.keys()))
        return []

    print("Type du champ data :", type(data).__name__)
    return []


def show_fixtures(path):
    print("\n" + "=" * 50)
    print("REQUÊTE :", path)
    print("=" * 50)

    result = api_get(path)

    if isinstance(result, dict):
        print("Succès API :", result.get("success"))
        print("Pagination :", result.get("pagination"))

    fixtures = extract_list(result)
    print("Nombre de matchs :", len(fixtures))

    statuses = Counter()
    status_codes = Counter()

    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue

        statuses[str(fixture.get("status", "absent"))] += 1
        status_codes[str(fixture.get("status_code", "absent"))] += 1

    print("Répartition des statuts :", dict(statuses))
    print("Répartition des codes :", dict(status_codes))

    for fixture in fixtures[:5]:
        if not isinstance(fixture, dict):
            continue

        teams = fixture.get("teams") or {}
        home = teams.get("home", {}) if isinstance(teams, dict) else {}
        away = teams.get("away", {}) if isinstance(teams, dict) else {}
        league = fixture.get("league") or {}

        if not isinstance(home, dict):
            home = {}
        if not isinstance(away, dict):
            away = {}
        if not isinstance(league, dict):
            league = {}

        print(
            "Match :",
            home.get("name", "?"),
            "-",
            away.get("name", "?"),
            "| Ligue :", league.get("name", "?"),
            "| Coup d'envoi UTC :", fixture.get("kickoff_utc", "?"),
            "| Statut :", fixture.get("status", "?"),
            "| Code :", fixture.get("status_code", "?"),
            "| ID :", fixture.get("id", "?"),
        )

    return fixtures


def main():
    print("FOOTBALL ODDS ALERT - DIAGNOSTIC COMPLET")
    print("Clé API configurée :", bool(os.getenv("ODDS_API_KEY")))
    print("Telegram configuré :", bool(
        os.getenv("TELEGRAM_BOT_TOKEN")
        and os.getenv("TELEGRAM_CHAT_ID")
    ))

    try:
        print("\n=== ÉTAT DU SERVICE ET DU COMPTE ===")
        status_result = api_get("/status")
        if isinstance(status_result, dict):
            # Ne jamais afficher d'éventuelles données d'authentification.
            print("Clés de réponse :", list(status_result.keys()))
            print("Réponse de statut :", str(status_result)[:1500])

    except Exception as error:
        print("Impossible de lire /status :",
              type(error).__name__, str(error))

    results = {}

    for path in (
        "/fixtures?status=live&per_page=100",
        "/fixtures?status=scheduled&per_page=100",
        "/fixtures?status=unknown&per_page=100",
        "/fixtures?status=all&per_page=100",
    ):
        try:
            results[path] = show_fixtures(path)
        except Exception as error:
            print(
                "ÉCHEC DE LA REQUÊTE :",
                path,
                "|",
                type(error).__name__,
                str(error),
            )

    live_path = "/fixtures?status=live&per_page=100"
    live_count = len(results.get(live_path, []))

    print("\n" + "=" * 50)
    print("BILAN FINAL")
    print("=" * 50)
    print("Matchs live renvoyés par l'API :", live_count)
    print("Matchs programmés :", len(results.get(
        "/fixtures?status=scheduled&per_page=100", []
    )))
    print("Matchs inconnus :", len(results.get(
        "/fixtures?status=unknown&per_page=100", []
    )))
    print("Aucune alerte de pari n'a été envoyée.")
    print("Fin du diagnostic.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERREUR FATALE :", type(error).__name__, str(error))
        sys.exit(1)
        
