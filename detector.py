
import os
import requests

BASE = "https://api.5dollarfootballapi.com/v1"
KEY = os.getenv("ODDS_API_KEY", "").strip()
FIXTURE_ID = 3715878313

if not KEY:
    raise SystemExit("Secret ODDS_API_KEY absent.")

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {KEY}",
    "Accept": "application/json",
})

# On teste uniquement la route de cotes déjà connue,
# puis on affiche les noms des champs disponibles.
url = f"{BASE}/fixtures/{FIXTURE_ID}/odds"
response = session.get(url, timeout=20)
print("HTTP :", response.status_code)
response.raise_for_status()

result = response.json()
data = result.get("data", {})

print("Clés de la réponse :", list(result.keys()))
print("Type de data :", type(data).__name__)

if isinstance(data, dict):
    for key, value in data.items():
        if key == "bookmakers" and isinstance(value, list):
            print("Nombre de bookmakers :", len(value))
            for bookmaker in value:
                if isinstance(bookmaker, dict):
                    print("Bookmaker :", bookmaker.get("name"))
                    odds = bookmaker.get("odds", {})
                    if isinstance(odds, dict):
                        for market, details in odds.items():
                            print("Marché :", market)
                            if isinstance(details, dict):
                                print("Champs :", list(details.keys()))
        else:
            print("Champ :", key, "| Type :", type(value).__name__)

print("Diagnostic terminé.")
            
