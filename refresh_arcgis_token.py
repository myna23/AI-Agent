"""
Refresh the ArcGIS token for the Zambia GeoHub AI app.

Run this on your WB desktop whenever the app shows a token error
(roughly every 2 weeks if you tick "Remember Me" at login).

Usage:
    python refresh_arcgis_token.py                      # update .env only (local dev)
    python refresh_arcgis_token.py YOUR_POSIT_API_KEY   # also push to Posit Connect

This script does NOT ask the Hub admin for anything — it logs in with
YOUR OWN World Bank account via a browser window.
"""

import os
import sys
import warnings

warnings.filterwarnings("ignore")

POSIT_SERVER  = "https://datanalytics-int.worldbank.org"
CONTENT_GUID  = "900cfa7d-9dde-443a-b75f-f4b5cad7bfb6"  # Zambia GeoHub AI app


# ── Step 1: Get a fresh ArcGIS token via browser login ────────────────────────

def _save_token_to_env(token: str):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    try:
        with open(env_path) as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    if "ARCGIS_TOKEN=" in content:
        lines = [
            f"ARCGIS_TOKEN={token}" if l.startswith("ARCGIS_TOKEN=") else l
            for l in content.splitlines()
        ]
        content = "\n".join(lines) + "\n"
    else:
        content = content.rstrip() + f"\nARCGIS_TOKEN={token}\n"
    with open(env_path, "w") as f:
        f.write(content)


print("=" * 60)
print("Zambia GeoHub AI — ArcGIS Token Refresh")
print("=" * 60)
print()
print("Step 1: Get a fresh token from your World Bank account.")
print("        A browser window will open — sign in normally.")
print("        Tick 'Keep me signed in' / 'Remember me' if shown.")
print()

token = None
from arcgis.gis import GIS

portals = [
    ("Zambia GeoHub (World Bank)", "https://zmb-geowb.hub.arcgis.com"),
    ("ArcGIS Online",              "https://www.arcgis.com"),
]

for label, portal_url in portals:
    print(f"  Trying: {label} ...")
    try:
        gis = GIS(portal_url, client_id="arcgisapp")
        token = gis._con.token
        if token:
            user = (gis.properties.get("fullName") or
                    (gis.users.me.username if gis.users.me else "unknown"))
            print(f"  Logged in as: {user}")
            break
    except Exception as e:
        print(f"  {label} failed: {e}")
        continue

# Manual fallback
if not token:
    print()
    print("Browser login did not work. Do this manually:")
    print()
    print("  1. Open: https://zmb-geowb.hub.arcgis.com  (log in if needed)")
    print("  2. Press F12 → Console tab → paste this and press Enter:")
    print()
    print('     require(["esri/identity/IdentityManager"],function(im){var c=im.credentials;c&&c.length?console.log("TOKEN:",c[0].token):console.log("none");});')
    print()
    print("  3. Copy the long string after TOKEN:")
    print()
    token = input("Paste token here: ").strip()

if not token:
    print("\nNo token obtained. Try again.")
    sys.exit(1)

_save_token_to_env(token)
print(f"\n  Token saved to .env  (first 30 chars: {token[:30]}...)")


# ── Step 2: Push to Posit Connect (optional) ──────────────────────────────────

posit_key = sys.argv[1] if len(sys.argv) > 1 else ""

if not posit_key:
    print()
    print("Step 2 skipped — no Posit API key provided.")
    print("  If the app is running on Posit Connect, run:")
    print(f"  python refresh_arcgis_token.py YOUR_POSIT_API_KEY")
    print()
    print("Done. Restart the Streamlit app locally to use the new token.")
    sys.exit(0)

print()
print("Step 2: Push token to Posit Connect ...")

import requests

headers = {"Authorization": f"Key {posit_key}", "Content-Type": "application/json"}

resp = requests.patch(
    f"{POSIT_SERVER}/__api__/v1/content/{CONTENT_GUID}/environment",
    headers=headers,
    json=[{"name": "ARCGIS_TOKEN", "value": token}],
    verify=False,
)
if not resp.ok:
    print(f"  ERROR updating env var: {resp.status_code} {resp.text[:200]}")
    sys.exit(1)
print("  ARCGIS_TOKEN updated on Posit Connect")

resp2 = requests.post(
    f"{POSIT_SERVER}/__api__/v1/content/{CONTENT_GUID}/restart",
    headers=headers,
    verify=False,
)
if resp2.ok:
    print("  App restarting — wait ~20 seconds then refresh your browser.")
else:
    print(f"  Restart returned {resp2.status_code} (refresh the browser manually)")

print()
print("Done. Token is good for ~2 weeks.")
print(f"App: {POSIT_SERVER}/connect/#/apps/{CONTENT_GUID}")
