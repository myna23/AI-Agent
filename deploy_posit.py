"""
Deploy to Posit Connect using the REST API directly.
Usage: python deploy_posit.py <API_KEY>
"""
import os
import sys
import json
import tarfile
import tempfile
import hashlib
import requests
from pathlib import Path

SERVER      = "https://datanalytics-int.worldbank.org/"
CONTENT_GUID = "900cfa7d-9dde-443a-b75f-f4b5cad7bfb6"

API_KEY = sys.argv[1] if len(sys.argv) > 1 else ""
if not API_KEY:
    print("Usage: python deploy_posit.py <API_KEY>")
    print("       python deploy_posit.py <API_KEY> activate <BUNDLE_ID>")
    sys.exit(1)

# Activate-only mode: python deploy_posit.py <KEY> activate <BUNDLE_ID>
if len(sys.argv) == 4 and sys.argv[2] == "activate":
    bundle_id = int(sys.argv[3])
    AUTH2 = {"Authorization": f"Key {API_KEY}", "Content-Type": "application/json"}
    import requests as _r
    resp = _r.post(
        f"https://datanalytics-int.worldbank.org/__api__/v1/content/900cfa7d-9dde-443a-b75f-f4b5cad7bfb6/deploy",
        headers=AUTH2, json={"bundle_id": bundle_id}, verify=False,
    )
    print(resp.status_code, resp.text[:300])
    sys.exit(0)

AUTH = {"Authorization": f"Key {API_KEY}"}
ROOT = Path(__file__).parent

# Step 0 — detach git so bundle upload is allowed
print("Detaching git repository connection...")
resp = requests.delete(
    f"{SERVER}__api__/v1/content/{CONTENT_GUID}/repository",
    headers=AUTH,
    verify=False,
)
if resp.ok or resp.status_code == 404:
    print("  Git connection removed (or was already gone)")
else:
    print(f"  Warning: could not detach git ({resp.status_code}): {resp.text[:200]}")

# Files and folders to bundle
INCLUDE = [
    "app.py",
    "requirements.txt",
    "manifest.json",
    "ai",
    "hub",
    "components",
    "data",
    "reports",
    "utils",
]

EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_DIRS     = {"__pycache__", ".venv", ".git", ".devcontainer"}

def collect_files():
    files = []
    for item in INCLUDE:
        p = ROOT / item
        if not p.exists():
            continue
        if p.is_file():
            files.append(p)
        else:
            for f in p.rglob("*"):
                if f.is_file() and f.suffix not in EXCLUDE_SUFFIXES:
                    if not any(part in EXCLUDE_DIRS for part in f.parts):
                        files.append(f)
    return files

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

print("Collecting files...")
files = collect_files()
print(f"  {len(files)} files found")

# Build / update manifest
manifest_path = ROOT / "manifest.json"
if manifest_path.exists():
    with open(manifest_path) as f:
        manifest = json.load(f)
else:
    manifest = {"version": 1, "locale": "en_US", "platform": "3.11", "metadata": {"appmode": "streamlit", "entrypoint": "app.py"}, "packages": {}, "files": {}}

manifest["files"] = {}
for fp in files:
    rel = str(fp.relative_to(ROOT)).replace("\\", "/")
    manifest["files"][rel] = {"checksum": sha256(fp)}

with open(manifest_path, "w") as f:
    json.dump(manifest, f, indent=2)
print("  manifest.json updated")

# Create tar.gz bundle
tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
tmp.close()
bundle_path = tmp.name

with tarfile.open(bundle_path, "w:gz") as tar:
    for fp in files:
        arcname = str(fp.relative_to(ROOT)).replace("\\", "/")
        tar.add(fp, arcname=arcname)

size_kb = os.path.getsize(bundle_path) // 1024
print(f"Bundle created: {size_kb} KB")

# Upload bundle
print("Uploading bundle to Posit Connect...")
with open(bundle_path, "rb") as f:
    resp = requests.post(
        f"{SERVER}__api__/v1/content/{CONTENT_GUID}/bundles",
        headers={"Authorization": f"Key {API_KEY}"},
        files={"archive": ("bundle.tar.gz", f, "application/gzip")},
        verify=False,
    )

if not resp.ok:
    print(f"Upload failed: {resp.status_code}")
    print(resp.text[:500])
    os.unlink(bundle_path)
    sys.exit(1)

bundle_id = resp.json()["id"]
print(f"Bundle uploaded (id={bundle_id})")

# Trigger deployment
print("Triggering deployment...")
resp = requests.post(
    f"{SERVER}__api__/v1/content/{CONTENT_GUID}/deploy",
    headers={**AUTH, "Content-Type": "application/json"},
    json={"bundle_id": bundle_id},
    verify=False,
)

os.unlink(bundle_path)

if resp.ok:
    task_id = resp.json().get("task_id", "?")
    print(f"\nDeployment started! Task: {task_id}")
    print(f"Watch progress: {SERVER}connect/#/apps/{CONTENT_GUID}")
else:
    print(f"Deploy trigger failed: {resp.status_code}")
    print(resp.text[:500])
    sys.exit(1)
