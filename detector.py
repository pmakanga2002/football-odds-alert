
import os
import sys
import requests
from collections import Counter
from urllib.parse import urljoin

API_BASE_URL = "https://api.5dollarfootballapi.com/v1"
TIMEOUT = 20


def api_get(path):
    """Appelle l'API sans afficher la clé secrète."""
    api_key = os.getenv("ODDS_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError(
            "Le secret GitHub ODDS_API_KEY est absent ou vide."
        )

    url = urljoin(API_BASE_URL.rstrip("/") + "/", path.lstrip("/"))

    # On teste les en-têtes d'authentification courants sans révéler la clé.
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "x-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "Football-Odds-Alert/1.0",
        },
        timeout=TIMEOUT,
    )

    if response.status_code >= 400:
        print(f"Erreur API HTTP {response.status_code} sur {path}")
        print("Réponse :", response.text[:500])
        response.raise_for_status()

    return response.json()


def get_fixtures(path):
    """Récupère les matchs et vérifie la structure de la réponse."""
    result = api_get(path)

    if not isinstance(result, dict):
        print("Réponse inattendue :", type(result).__name__)
        return []

    fixtures = result.get("data", [])
    pagination = result.get("pagination", {})

    if not isinstance(fixtures, list):
        print("Le champ data n'est pas une liste.")
        return []

    print("Nombre de résultats :", len(fixtures))
    print("Pagination :", pagination)

    statuses = Counter(
        str(fixture.get("status", "absent"))
        for fixture in fixtures
        if isinstance(fixture, dict)
    )
    status_codes = Counter(
        str(fixture.get("status_code", "absent"))
        for fixture in fixtures
        if isinstance(fixture, dict)
    )

    print("Statuts trouvés :", dict(statuses))
    print("Codes de statut :", dict(status_codes))

    for fixture in fixtures[:5]:
        if not isinstance(fixture, dict):
            continue

        home = fixture.get("home_team") or fixture.get("home") or {}
        away = fixture.get("away_team") or fixture.get("away") or {}

        if isinstance(home, dict):
            home = home.get("name", str(home))
        if isinstance(away, dict):
            away = away.get("name", str(away))

        print(
            "Exemple :",
            f"id={fixture.get('id')}",
            f"{home} - {away}",
            f"status={fixture.get('status')}",
            f"status_code={fixture.get('status_code')}",
        )

    return fixtures


def main():
    print("========================================")
    print(" FOOTBALL ODDS ALERT - DIAGNOSTIC API")
    print("========================================")

    print("Clé API configurée :", bool(os.getenv("ODDS_API_KEY")))
    print("Jeton Telegram configuré :", bool(
        os.getenv("TELEGRAM_BOT_TOKEN")
    ))
    print("Identifiant Telegram configuré :", bool(
        os.getenv("TELEGRAM_CHAT_ID")
    ))

    tests = [
        "/fixtures?status=live&per_page=100",
        "/fixtures?status=in_play&per_page=100",
        "/fixtures?status=playing&per_page=100",
        "/fixtures?per_page=100",
    ]

    seen = {}

    for path in tests:
        print("\n----------------------------------------")
        print("TEST :", path)
        print("----------------------------------------")

        try:
            fixtures = get_fixtures(path)

            for fixture in fixtures:
                if isinstance(fixture, dict) and fixture.get("id") is not None:
                    seen[str(fixture["id"])] = fixture

        except requests.RequestException as error:
            print("Erreur réseau/API :", str(error))
        except (ValueError, RuntimeError) as error:
            print("Erreur :", str(error))

    live_statuses = {
        "in_play",
        "live",
        "inplay",
        "playing",
        "1h",
        "2h",
        "ht",
        "half_time",
        "extra_time",
        "penalties",
    }

    live = []
    for fixture in seen.values():
        status = str(fixture.get("status", "")).strip().lower()
        status = status.replace(" ", "_").replace("-", "_")

        if status in live_statuses:
            live.append(fixture)

    print("\n========================================")
    print(" BILAN DU DIAGNOSTIC")
    print("========================================")
    print("Matchs distincts récupérés :", len(seen))
    print("Matchs avec statut live reconnu :", len(live))

    if seen:
        print("Répartition finale des statuts :")
        print(dict(Counter(
            str(f.get("status", "absent"))
            for f in seen.values()
        )))

    print("\nDiagnostic terminé.")
    print("Aucune alerte de pari n'a été envoyée.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERREUR FATALE :", type(error).__name__, str(error))
        sys.exit(1)
