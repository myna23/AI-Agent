"""
Refresh MAI_FACTORY_TOKEN on Posit Connect.

Run this script on your WB desktop before each usage session.
It gets a fresh token via DesktopToken and pushes it to Posit Connect
so the Zambia GeoHub AI app can call the mAI Factory.

Usage:
    python refresh_mai_token.py YOUR_POSIT_API_KEY

The token typically lasts 1 hour. Re-run if the app gives auth errors.
"""
import sys
import requests
import warnings

warnings.filterwarnings("ignore", message="Unverified HTTPS")

SERVER       = "https://datanalytics-int.worldbank.org"
CONTENT_GUID = "900cfa7d-9dde-443a-b75f-f4b5cad7bfb6"

if len(sys.argv) < 2:
    print("Usage: python refresh_mai_token.py YOUR_POSIT_API_KEY")
    sys.exit(1)

POSIT_KEY = sys.argv[1]
AUTH = {"Authorization": f"Key {POSIT_KEY}"}

# Step 1 — get fresh token via DesktopToken
print("Getting fresh mAI Factory token via DesktopToken...")
try:
    from itsai.platform.authentication import DesktopToken
    token = DesktopToken().token_provider(env="DEV")
    print(f"  Token obtained ({len(token)} chars)")
except Exception as e:
    print(f"  ERROR: {e}")
    print("  Make sure you are on a WB machine with itsai SDK installed.")
    sys.exit(1)

# Step 2 — push token to Posit Connect environment
print("Pushing token to Posit Connect...")
resp = requests.patch(
    f"{SERVER}/__api__/v1/content/{CONTENT_GUID}/environment",
    headers={**AUTH, "Content-Type": "application/json"},
    json=[{"name": "MAI_FACTORY_TOKEN", "value": token}],
    verify=False,
)
if not resp.ok:
    print(f"  ERROR setting env var: {resp.status_code} {resp.text[:200]}")
    sys.exit(1)
print("  MAI_FACTORY_TOKEN updated on Posit Connect")

# Step 3 — restart the app so it picks up the new token
print("Restarting app on Posit Connect...")
resp = requests.post(
    f"{SERVER}/__api__/v1/content/{CONTENT_GUID}/restart",
    headers=AUTH,
    verify=False,
)
if resp.ok:
    print("  App restarting — wait ~20 seconds then refresh the browser.")
else:
    print(f"  Restart returned {resp.status_code} (may still work — try refreshing the app in ~30s)")

print("\nDone. The app will use the new token for ~1 hour.")
print(f"App: {SERVER}/connect/#/apps/{CONTENT_GUID}")
