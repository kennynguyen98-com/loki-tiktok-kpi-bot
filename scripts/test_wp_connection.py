"""
Quick test: verify WordPress REST API connection for GIP.
Run: python scripts/test_wp_connection.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]

# Load .env manually (no dependency needed)
env_file = root / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))

wp_user = os.environ.get("WP_USER", "")
wp_pass = os.environ.get("WP_APP_PASS", "")
api_url = cfg.get("wordpress_api_url", "").rstrip("/")

if not wp_user or not wp_pass or not api_url:
    print("ERROR: Missing WP_USER, WP_APP_PASS or wordpress_api_url in config")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Installing requests...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

token = base64.b64encode(f"{wp_user}:{wp_pass}".encode()).decode()
headers = {"Authorization": f"Basic {token}"}

print(f"Testing connection to: {api_url}")
resp = requests.get(f"{api_url}/users/me", headers=headers, timeout=15)

if resp.status_code == 200:
    data = resp.json()
    print(f"SUCCESS - Connected as: {data.get('name')} (id={data.get('id')})")
    print(f"Roles: {data.get('roles')}")
    print("\nWordPress connection is ready.")
else:
    print(f"FAILED - Status {resp.status_code}: {resp.text[:300]}")
    sys.exit(1)
