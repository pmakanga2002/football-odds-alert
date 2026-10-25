
import os
import sys
import requests

BASE = "https://api.5dollarfootballapi.com/v1"
KEY = os.getenv("ODDS_API_KEY", "").strip()

if not KEY:
    sys.exit("ERREUR : secret ODDS_API_KEY absent.")

headers = {
    "Authorization": f"Bearer {KEY}",
    "Accept": "application/json",
}

def test_endpoint(path):
    print(f"\n=== TEST {path} ===")
    try:
        r = requests.get(BASE + path, headers=headers, timeout=20)
        print("HTTP :", r.status_code)

        try:
            result = r.json()
        except ValueError:
            print("Réponse non JSON.")
            return

        if r.status_code != 200:
            error = result.get("error", {})
            if isinstance(error, dict):
                print("Code erreur :", error.get("code", "non précisé"))
                print("Message :", error.get("message", "non précisé"))
            else:
                print("Erreur API :", str(error)[:200])
            return

        data = result.get("data", [])

        if path == "/status":
            print("Forfait :", data.get("plan", "non indiqué"))
            usage = data.get("usage", {})
            limits = data.get("limits", {})
            print("Utilisation actuelle :", usage.get("today", "non indiquée"))
            print("Limite :", limits.get("rate_limit", "non indiquée"))
            return

        if isinstance(data, list):
            print("Nombre de bookmakers :", len(data))
            for item in data:
                if isinstance(item, dict):
                    print(
                        "-",
                        item.get("name", "?"),
                        "| slug :",
                        item.get("slug", "?")
                    )
        else:
            print("Format inattendu :", type(data).__name__)

    except requests.RequestException as e:
        print("Erreur réseau :", type(e).__name__)

test_endpoint("/status")
test_endpoint("/bookmakers")

print("\n=== FIN DU TEST ===")
        
