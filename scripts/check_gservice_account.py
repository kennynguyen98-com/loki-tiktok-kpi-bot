from __future__ import annotations

import json
import os
from pathlib import Path


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    load_env(root / ".env")

    key_file = os.getenv("GSERVICE_ACCOUNT_FILE", "").strip()
    if not key_file:
        print("GSERVICE_ACCOUNT_FILE is empty in .env")
        return 1

    path = (root / key_file).resolve() if not Path(key_file).is_absolute() else Path(key_file)
    if not path.exists():
        print(f"Service account key file not found: {path}")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    required = ["type", "project_id", "client_email", "private_key"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        print("Invalid key file. Missing:", ", ".join(missing))
        return 1

    print("Service account key loaded OK")
    print("project_id:", data.get("project_id"))
    print("client_email:", data.get("client_email"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
