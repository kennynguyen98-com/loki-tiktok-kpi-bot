from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import gspread
except ImportError:
    gspread = None

try:
    import requests
except ImportError:
    requests = None

WEEKLY_HEADERS = [
    "date",
    "platform",
    "account",
    "metric_type",
    "post_url",
    "content_type",
    "posted_at",
    "views_12h",
    "views_24h",
    "likes",
    "comments",
    "shares",
    "saves",
    "followers",
    "profile_views",
    "leads",
    "notes",
    "external_id",
    "updated_at",
]


@dataclass
class SheetSyncResult:
    ok: bool
    message: str
    rows_written: int = 0


class GoogleSheetSync:
    def __init__(self) -> None:
        self.sheet_url = os.getenv("GSHEET_URL", "").strip()
        self.sheet_id = os.getenv("GSHEET_ID", "").strip()
        self.service_account_file = os.getenv("GSERVICE_ACCOUNT_FILE", "").strip()
        self.weekly_tab = os.getenv("GSHEET_TAB_WEEKLY", "Phân tích kênh hàng tuần").strip()
        self.info_tab = os.getenv("GSHEET_TAB_INFO", "Thông tin").strip()
        self.youtube_api_key = os.getenv("YOUTUBE_API_KEY", "").strip()

    def enabled(self) -> bool:
        return bool((self.sheet_url or self.sheet_id) and self.service_account_file)

    def _extract_sheet_id(self) -> str:
        if self.sheet_id:
            return self.sheet_id

        if "/d/" in self.sheet_url:
            return self.sheet_url.split("/d/", 1)[1].split("/", 1)[0]
        return self.sheet_url

    def _open_sheet(self):
        if gspread is None:
            raise RuntimeError("Missing gspread. Run: pip install gspread")
        if not self.enabled():
            raise RuntimeError("Missing GSHEET_URL/GSHEET_ID or GSERVICE_ACCOUNT_FILE")

        gc = gspread.service_account(filename=self.service_account_file)
        return gc.open_by_key(self._extract_sheet_id())

    def _ensure_headers(self, ws) -> None:
        existing = ws.row_values(1)
        if not existing:
            ws.update("A1", [WEEKLY_HEADERS])
            return

        merged = list(existing)
        for h in WEEKLY_HEADERS:
            if h not in merged:
                merged.append(h)

        if merged != existing:
            ws.update("A1", [merged])

    def _get_worksheet(self, sh, wanted_title: str):
        # Prefer exact title first.
        try:
            return sh.worksheet(wanted_title)
        except Exception:
            pass

        # Fallback to case-insensitive match to avoid minor naming mismatches.
        wanted_fold = wanted_title.strip().casefold()
        for ws in sh.worksheets():
            if ws.title.strip().casefold() == wanted_fold:
                return ws

        raise RuntimeError(wanted_title)

    def _find_info_accounts(self, info_ws) -> List[Dict[str, str]]:
        rows = info_ws.get_all_values()
        accounts: List[Dict[str, str]] = []
        for row in rows:
            if not row:
                continue

            # Sheet của bạn đang để tên kênh ở cột C, link ở cột D.
            name = row[2].strip() if len(row) >= 3 else ""
            url = row[3].strip() if len(row) >= 4 else ""
            if not url.startswith("http"):
                continue
            accounts.append({"name": name or "unknown", "url": url})
        return accounts

    def _youtube_channel_stats(self, url: str) -> Dict[str, Any]:
        if requests is None:
            return {"notes": "Missing requests package"}
        if not self.youtube_api_key:
            return {"notes": "Need YOUTUBE_API_KEY"}

        channel_id = ""
        if "/channel/" in url:
            channel_id = url.split("/channel/", 1)[1].split("/", 1)[0]

        if not channel_id:
            return {"notes": "YouTube URL chưa phải dạng /channel/<id>; cần API mapping handle"}

        endpoint = "https://www.googleapis.com/youtube/v3/channels"
        params = {
            "part": "statistics",
            "id": channel_id,
            "key": self.youtube_api_key,
        }
        resp = requests.get(endpoint, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return {"notes": "Không lấy được channel stats"}

        stats = items[0].get("statistics", {})
        return {
            "followers": int(stats.get("subscriberCount", 0)),
            "views_24h": int(stats.get("viewCount", 0)),
            "notes": "YouTube subscriber/view tổng",
        }

    def _platform_snapshot(self, account: Dict[str, str]) -> Dict[str, Any]:
        name = account.get("name", "")
        url = account.get("url", "")
        lower = f"{name} {url}".lower()

        if "youtube" in lower:
            return self._youtube_channel_stats(url)
        if "tiktok" in lower:
            return {"notes": "TikTok cần API bên thứ 3 hoặc token TikTok Business"}
        if "facebook" in lower:
            return {"notes": "Facebook cần Graph API token"}
        return {"notes": "Nền tảng chưa có adapter"}

    def sync_midday(self, state: Dict[str, Any]) -> SheetSyncResult:
        try:
            sh = self._open_sheet()
            weekly_ws = self._get_worksheet(sh, self.weekly_tab)
            info_ws = self._get_worksheet(sh, self.info_tab)
            self._ensure_headers(weekly_ws)

            rows_to_append: List[List[Any]] = []
            today = date.today().isoformat()
            now = datetime.now().isoformat(timespec="seconds")

            accounts = self._find_info_accounts(info_ws)
            for account in accounts:
                snap = self._platform_snapshot(account)
                rows_to_append.append([
                    today,
                    account.get("url", ""),
                    account.get("name", ""),
                    "snapshot",
                    "",
                    "",
                    "",
                    "",
                    snap.get("views_24h", ""),
                    "",
                    "",
                    "",
                    "",
                    snap.get("followers", ""),
                    "",
                    "",
                    snap.get("notes", ""),
                    f"snapshot:{today}:{account.get('name', '')}",
                    now,
                ])

            current_month = today[:7]
            for row in state.get("records", {}).get(current_month, []):
                clip_id = int(row.get("id", 0))
                rows_to_append.append([
                    row.get("date", today),
                    "tiktok",
                    "loki_tran",
                    "clip",
                    "",
                    "",
                    row.get("date", today),
                    row.get("views", ""),
                    row.get("views", ""),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "bot record",
                    f"clip:{clip_id}",
                    now,
                ])

            if rows_to_append:
                weekly_ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")

            return SheetSyncResult(True, "Đã sync Google Sheet lúc 12h", rows_written=len(rows_to_append))
        except Exception as exc:
            return SheetSyncResult(False, f"Sync sheet lỗi: {exc}", rows_written=0)


def build_evening_kpi_text(progress_text: str) -> str:
    return "Báo cáo KPI 17:00\n\n" + progress_text
