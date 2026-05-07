from __future__ import annotations

import base64
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


SAFE_TRUE_VALUES = {"1", "true", "yes", "y", "on"}


@dataclass
class PublishResult:
    status: str
    url: str
    local_path: str
    remote_id: Optional[int] = None


def slugify(text: str) -> str:
    prepared = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFKD", prepared).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9\s-]", "", normalized).strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    return slug[:80] or "untitled"


def _is_truthy(value: Any) -> bool:
    return str(value).strip().lower() in SAFE_TRUE_VALUES


def _save_local(title: str, content: str, workspace_root: Path, cfg: Dict[str, Any]) -> PublishResult:
    posts_dir = workspace_root / "posts"
    posts_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(title)
    local_file = posts_dir / f"{slug}.md"
    local_file.write_text(content, encoding="utf-8")
    base = cfg.get("site_base_url", "https://gipfulfillment.com").rstrip("/")
    return PublishResult(status="saved-local", url=f"{base}/{slug}", local_path=str(local_file))


class BaseConnector:
    def __init__(self, cfg: Dict[str, Any], workspace_root: Path):
        self.cfg = cfg
        self.workspace_root = workspace_root

    def publish_draft(self, title: str, content: str) -> PublishResult:
        return _save_local(title, content, self.workspace_root, self.cfg)


class WordPressConnector(BaseConnector):
    """
    Publishes to WordPress via REST API in draft-only mode.

    Safety requirements:
    - live publish is permanently disabled in code
    - local save always happens first
    - WordPress drafts require both config + env confirmation
    - dedicated non-admin WordPress user is required by default
    """

    def _draft_api_enabled(self) -> bool:
        if not _is_truthy(self.cfg.get("allow_wordpress_drafts", False)):
            print("[Safety] WordPress draft API disabled in config — saved locally only.")
            return False
        if not _is_truthy(os.environ.get("WP_ALLOW_DRAFTS", "0")):
            print("[Safety] WP_ALLOW_DRAFTS != 1 — saved locally only.")
            return False
        return True

    def _user_allowed(self, wp_user: str) -> bool:
        if not _is_truthy(self.cfg.get("require_non_admin_wp_user", True)):
            return True

        blocked = {
            str(name).strip().lower()
            for name in self.cfg.get("disallowed_wp_usernames", ["admin", "administrator"])
            if str(name).strip()
        }
        if wp_user.strip().lower() in blocked:
            print(f"[Safety] WordPress user '{wp_user}' is blocked for agent use — saved locally only.")
            return False
        return True

    def publish_draft(self, title: str, content: str) -> PublishResult:
        local = _save_local(title, content, self.workspace_root, self.cfg)

        if _is_truthy(self.cfg.get("allow_live_publish", False)) or _is_truthy(self.cfg.get("auto_publish", False)):
            print("[Safety] Live publish is disabled by code and will be ignored.")

        if not self._draft_api_enabled():
            return local

        wp_user = os.environ.get("WP_USER", "").strip()
        wp_pass = os.environ.get("WP_APP_PASS", "").strip()
        api_url = self.cfg.get("wordpress_api_url", "").rstrip("/")

        if not wp_user or not wp_pass or not api_url:
            print("[WordPress] Missing WP_USER / WP_APP_PASS / wordpress_api_url — saved locally only.")
            return local

        if not self._user_allowed(wp_user):
            return local

        if not HAS_REQUESTS:
            print("[WordPress] 'requests' library not installed. Run: pip install requests")
            return local

        token = base64.b64encode(f"{wp_user}:{wp_pass}".encode()).decode()
        headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "title": title,
            "content": content,
            "status": "draft",
        }

        try:
            resp = _requests.post(f"{api_url}/posts", json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            remote_id = data.get("id")
            remote_url = data.get("link", local.url)
            print(f"[WordPress] Draft created — id={remote_id}, status=draft, url={remote_url}")
            return PublishResult(
                status="wordpress-draft",
                url=remote_url,
                local_path=local.local_path,
                remote_id=remote_id,
            )
        except Exception as exc:
            print(f"[WordPress] API error: {exc} — saved locally only.")
            return local


class ShopifyConnector(BaseConnector):
    pass


class GenericConnector(BaseConnector):
    pass


def make_connector(platform: str, cfg: Dict[str, Any], workspace_root: Path) -> BaseConnector:
    p = (platform or "generic").lower()
    if p == "wordpress":
        return WordPressConnector(cfg, workspace_root)
    if p == "shopify":
        return ShopifyConnector(cfg, workspace_root)
    return GenericConnector(cfg, workspace_root)
