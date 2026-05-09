from __future__ import annotations

import json
import logging
import math
import os
import asyncio
import base64
import re
import requests
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from telegram import Update
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
)

from google_sheet_sync import GoogleSheetSync, build_evening_kpi_text

try:
    import gspread
except ImportError:
    gspread = None


TARGET_CLIPS = 20
TARGET_VIEWS = 30000
ONE_CLIP_BIG = 10000
ONE_CLIP_MID = 5000
LOW_CLIP_MIN = 1000
LOW_CLIP_MAX = 1500
WEEKLY_TARGET = 5  # clip/week

SAFE_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
DEFAULT_REMINDER_TIMES = "09:00,13:00,20:30"
DEFAULT_ENABLE_PAID_PHASE2 = "false"
APPROVED_SCRIPT_STATUS_KEYWORDS = {
    "đã duyệt",
    "da duyet",
    "duyệt",
    "duyet",
    "approved",
    "ok",
    "đã đăng",
    "da dang",
    "posted",
    "done",
}


@dataclass
class MonthStats:
    month_key: str
    clip_count: int
    total_views: int
    clips_ge_10k: int
    clips_ge_5k: int
    clips_1k_to_1p5k: int


def _load_env(workspace_root: Path) -> None:
    env_file = workspace_root / ".env"
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Always honor workspace .env values; setdefault can keep stale empty vars.
        os.environ[key.strip()] = value.strip()


def _setup_gservice_credentials(workspace_root: Path) -> None:
    """Decode Base64 JSON từ ENV (GSERVICE_ACCOUNT_JSON_B64) nếu cần deploy cloud"""
    creds_dir = workspace_root / "credentials"
    creds_dir.mkdir(exist_ok=True)
    
    creds_file = creds_dir / "google-service-account.json"
    
    # Nếu file đã tồn tại, skip
    if creds_file.exists():
        return
    
    # Thử decode từ Base64 ENV var (dùng cho cloud deployment)
    b64_json = (
        os.getenv("GSERVICE_ACCOUNT_JSON_B64", "").strip()
        or os.getenv("GOOGLE_CREDENTIALS_B64", "").strip()
    )
    if b64_json:
        try:
            json_str = base64.b64decode(b64_json).decode("utf-8")
            creds_file.write_text(json_str, encoding="utf-8")
            logging.info("[Setup] Decoded GSERVICE_ACCOUNT_JSON_B64 → credentials/google-service-account.json")
            return
        except Exception as e:
            logging.warning(f"[Setup] Không decode được GSERVICE_ACCOUNT_JSON_B64: {e}")
    
    # Thử direct JSON từ ENV var (backup)
    json_direct = (
        os.getenv("GSERVICE_ACCOUNT_JSON", "").strip()
        or os.getenv("GOOGLE_CREDENTIALS", "").strip()
    )
    if json_direct:
        try:
            creds_file.write_text(json_direct, encoding="utf-8")
            logging.info("[Setup] Lưu GSERVICE_ACCOUNT_JSON → credentials/google-service-account.json")
            return
        except Exception as e:
            logging.warning(f"[Setup] Không lưu GSERVICE_ACCOUNT_JSON: {e}")


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in SAFE_TRUE_VALUES


def _paid_phase2_enabled() -> bool:
    """Paid TikTok API mode is opt-in only; keep free-first by default."""
    raw = os.getenv("ENABLE_PAID_PHASE2", DEFAULT_ENABLE_PAID_PHASE2)
    return _to_bool(raw)


def _state_file(workspace_root: Path) -> Path:
    return workspace_root / "outputs" / "loki-kpi-bot" / "state.json"


def _empty_state() -> Dict[str, Any]:
    return {
        "authorized_chat_id": None,
        "next_clip_id": 1,
        "records": {},
        "pending_2h_checks": {},  # clip_id -> {check_time, posted_at, title}
    }


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        state = _empty_state()
        _save_state(path, state)
        return state

    data = json.loads(path.read_text(encoding="utf-8"))
    if "records" not in data:
        data["records"] = {}
    if "next_clip_id" not in data:
        data["next_clip_id"] = 1
    if "authorized_chat_id" not in data:
        data["authorized_chat_id"] = None
    if "pending_2h_checks" not in data:
        data["pending_2h_checks"] = {}
    return data


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _month_key(day: Optional[date] = None) -> str:
    d = day or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def _parse_date(date_text: str) -> date:
    return datetime.strptime(date_text, "%Y-%m-%d").date()


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    current_month = date(year, month, 1)
    return (next_month - current_month).days


def _stats_for_month(state: Dict[str, Any], month_key: str) -> MonthStats:
    rows = state.get("records", {}).get(month_key, [])
    clip_count = len(rows)
    total_views = sum(int(r.get("views", 0)) for r in rows)
    clips_ge_10k = sum(1 for r in rows if int(r.get("views", 0)) >= ONE_CLIP_BIG)
    clips_ge_5k = sum(1 for r in rows if ONE_CLIP_MID <= int(r.get("views", 0)) < ONE_CLIP_BIG)
    clips_1k_to_1p5k = sum(
        1 for r in rows if LOW_CLIP_MIN <= int(r.get("views", 0)) <= LOW_CLIP_MAX
    )
    return MonthStats(
        month_key=month_key,
        clip_count=clip_count,
        total_views=total_views,
        clips_ge_10k=clips_ge_10k,
        clips_ge_5k=clips_ge_5k,
        clips_1k_to_1p5k=clips_1k_to_1p5k,
    )


# ---------------------------------------------------------------------------
# Google Sheet helpers
# ---------------------------------------------------------------------------

def _sheet_open():
    """Open the configured Google Spreadsheet. Returns None if unavailable."""
    if gspread is None:
        return None
    url = os.getenv("GSHEET_URL", "").strip()
    if not url:
        return None
    try:
        sid = url.split("/d/", 1)[1].split("/", 1)[0] if "/d/" in url else url

        # 1) Prefer credential file if present.
        cred = os.getenv("GSERVICE_ACCOUNT_FILE", "").strip()
        if cred:
            cred_path = Path(cred)
            if not cred_path.is_absolute():
                cred_path = Path(__file__).resolve().parents[1] / cred_path
            if cred_path.exists():
                gc = gspread.service_account(filename=str(cred_path))
                return gc.open_by_key(sid)
            logging.warning(f"[Sheet] Credential file not found: {cred_path}")

        # 2) Fallback to base64 JSON in env (cloud friendly).
        b64_json = (
            os.getenv("GSERVICE_ACCOUNT_JSON_B64", "").strip()
            or os.getenv("GOOGLE_CREDENTIALS_B64", "").strip()
        )
        if b64_json:
            try:
                info = json.loads(base64.b64decode(b64_json).decode("utf-8"))
                gc = gspread.service_account_from_dict(info)
                return gc.open_by_key(sid)
            except Exception as inner_exc:
                logging.warning(f"[Sheet] Invalid GSERVICE_ACCOUNT_JSON_B64: {inner_exc}")

        # 3) Fallback to raw JSON in env.
        raw_json = (
            os.getenv("GSERVICE_ACCOUNT_JSON", "").strip()
            or os.getenv("GOOGLE_CREDENTIALS", "").strip()
        )
        if raw_json:
            try:
                info = json.loads(raw_json)
                gc = gspread.service_account_from_dict(info)
                return gc.open_by_key(sid)
            except Exception as inner_exc:
                logging.warning(f"[Sheet] Invalid GSERVICE_ACCOUNT_JSON: {inner_exc}")

        logging.warning("[Sheet] No usable Google credential source found")
        return None
    except Exception as exc:
        logging.warning(f"[Sheet] Cannot open spreadsheet: {exc}")
        return None


def _log_sheet_health() -> None:
    """Emit one startup log to verify Sheets connectivity in production."""
    sh = _sheet_open()
    if sh is None:
        logging.warning("[Sheet] Startup check failed: cannot open spreadsheet")
        return

    try:
        titles = [ws.title for ws in sh.worksheets()]
        logging.info(f"[Sheet] Startup check OK: spreadsheet='{sh.title}', worksheets={len(titles)}")
        if _ws_like(sh, "LỊCH ĐĂNG") is None:
            logging.warning("[Sheet] Startup check: worksheet 'LỊCH ĐĂNG' not found")
        else:
            logging.info("[Sheet] Startup check: worksheet 'LỊCH ĐĂNG' found")
    except Exception as exc:
        logging.warning(f"[Sheet] Startup check error: {exc}")


def _ws_like(sh, preferred: str):
    """Find worksheet by title with casefold fallback."""
    try:
        return sh.worksheet(preferred)
    except Exception:
        pass
    wanted = preferred.strip().casefold()
    for ws in sh.worksheets():
        t = ws.title.strip().casefold()
        if t == wanted or t.startswith(wanted):
            return ws
    return None


def _find_col(header: List[str], *names: str) -> Optional[int]:
    norm = [(v or "").strip().casefold() for v in header]
    for name in names:
        key = name.strip().casefold()
        if key in norm:
            return norm.index(key)
    return None


def _parse_clip_number_text(raw: str) -> Optional[int]:
    s = (raw or "").strip()
    if not s:
        return None
    s = s.replace("#", "").strip()
    try:
        return int(s)
    except ValueError:
        return None


def _is_script_status_approved(raw: str) -> bool:
    status = (raw or "").strip().casefold()
    if not status:
        return False
    return any(key in status for key in APPROVED_SCRIPT_STATUS_KEYWORDS)


def _find_script_header_row(values: List[List[str]]) -> Optional[int]:
    for i, row in enumerate(values, start=1):
        norm = [(cell or "").strip().casefold() for cell in row]
        has_video = "video #" in norm
        has_status = "trạng thái" in norm or "trang thai" in norm
        if has_video and has_status:
            return i
    return None


def _get_approved_script_entry(sh, clip_id: int) -> tuple[bool, str, Optional[str]]:
    """Return approved SCRIPT title for clip_id.

    Output: (ok, message, title)
    """
    try:
        ws = _ws_like(sh, "SCRIPT")
        if ws is None:
            return False, "Không tìm thấy tab SCRIPT", None

        values = ws.get_all_values()
        header_row_idx = _find_script_header_row(values)
        if header_row_idx is None:
            return False, "Không tìm thấy header trong tab SCRIPT", None

        header = values[header_row_idx - 1]
        c_video = _find_col(header, "Video #")
        c_title = _find_col(header, "Tiêu đề", "Tiêu Đề")
        c_status = _find_col(header, "Trạng Thái", "Trạng thái", "Trang Thai")
        if c_video is None or c_title is None or c_status is None:
            return False, "Tab SCRIPT thiếu cột Video # / Tiêu đề / Trạng Thái", None

        for row in values[header_row_idx:]:
            video_val = row[c_video] if c_video < len(row) else ""
            video_num = _parse_clip_number_text(video_val)
            if video_num != clip_id:
                continue

            title = (row[c_title] if c_title < len(row) else "").strip()
            status = (row[c_status] if c_status < len(row) else "").strip()
            if not _is_script_status_approved(status):
                return (
                    False,
                    f"Clip #{clip_id} trong SCRIPT chưa duyệt (Trạng Thái: '{status or 'trống'}')",
                    None,
                )
            if not title:
                return False, f"Clip #{clip_id} trong SCRIPT chưa có Tiêu đề", None
            return True, "OK", title

        return False, f"Không thấy clip #{clip_id} trong tab SCRIPT", None
    except Exception as exc:
        return False, f"Lỗi đọc tab SCRIPT: {exc}", None


def _approved_script_clip_ids(sh) -> set[int]:
    try:
        ws = _ws_like(sh, "SCRIPT")
        if ws is None:
            return set()
        values = ws.get_all_values()
        header_row_idx = _find_script_header_row(values)
        if header_row_idx is None:
            return set()
        header = values[header_row_idx - 1]
        c_video = _find_col(header, "Video #")
        c_status = _find_col(header, "Trạng Thái", "Trạng thái", "Trang Thai")
        if c_video is None or c_status is None:
            return set()

        out: set[int] = set()
        for row in values[header_row_idx:]:
            video_val = row[c_video] if c_video < len(row) else ""
            status = row[c_status] if c_status < len(row) else ""
            video_num = _parse_clip_number_text(video_val)
            if video_num is None:
                continue
            if _is_script_status_approved(status):
                out.add(video_num)
        return out
    except Exception:
        return set()


def _find_schedule_header_row(values: List[List[str]]) -> Optional[int]:
    """Locate the header row for LỊCH ĐĂNG even when the sheet uses '#' instead of 'CLIP #'"""
    for i, row in enumerate(values, start=1):
        norm = [(cell or "").strip().casefold() for cell in row]
        has_date = "ngày đăng" in norm
        has_clip_marker = "clip #" in norm or "#" in norm
        if has_date and has_clip_marker:
            return i
    return None


def _sheet_write_clip(
    clip_id: int,
    clip_day: date,
    posted_at: datetime,
    title: str = "",
    link: str = "",
) -> bool:
    """Write a new clip row (or update existing) in LỊCH ĐĂNG sheet."""
    sh = _sheet_open()
    if sh is None:
        return False
    try:
        ws = _ws_like(sh, "LỊCH ĐĂNG")
        if ws is None:
            logging.warning("[Sheet] LỊCH ĐĂNG not found")
            return False

        values = ws.get_all_values()
        # Find header row
        header_row_idx = _find_schedule_header_row(values)
        if header_row_idx is None:
            return False

        header = values[header_row_idx - 1]
        c_num = _find_col(header, "CLIP #", "#")
        c_title = _find_col(header, "TIÊU ĐỀ", "CHỦ ĐỀ / TIÊU ĐỀ")
        c_date = _find_col(header, "NGÀY ĐĂNG")
        c_posted = _find_col(header, "Giờ đăng thực tế")
        c_check2h = _find_col(header, "Giờ check +2h")
        c_link_tiktok = _find_col(header, "LINK TikTok VN", "LINK TikTok")
        c_agent = _find_col(header, "Agent note")

        # Find existing row for this clip number
        target_row = None
        for r_idx in range(header_row_idx + 1, len(values) + 1):
            row = values[r_idx - 1] if r_idx - 1 < len(values) else []
            num_val = row[c_num] if c_num is not None and c_num < len(row) else ""
            if (num_val or "").strip() == str(clip_id):
                target_row = r_idx
                break

        if target_row is None:
            # Find first empty slot
            for r_idx in range(header_row_idx + 1, header_row_idx + 30):
                row = values[r_idx - 1] if r_idx - 1 < len(values) else []
                num_val = row[c_num] if c_num is not None and c_num < len(row) else ""
                if not (num_val or "").strip():
                    target_row = r_idx
                    break

        if target_row is None:
            return False

        check_time = datetime(
            posted_at.year, posted_at.month, posted_at.day,
            posted_at.hour, posted_at.minute
        )
        check_2h = check_time + timedelta(hours=2)

        updates = []
        if c_num is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_num + 1), "values": [[str(clip_id)]]})
        if c_title is not None and title:
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_title + 1), "values": [[title]]})
        if c_date is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_date + 1), "values": [[clip_day.strftime("%d/%m/%Y")]]})
        if c_posted is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_posted + 1), "values": [[posted_at.strftime("%H:%M")]]})
        if c_check2h is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_check2h + 1), "values": [[check_2h.strftime("%H:%M")]]})
        if c_link_tiktok is not None and link:
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_link_tiktok + 1), "values": [[link]]})
        if c_agent is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_agent + 1), "values": [["⏳ Chờ check 2h"]]})

        if updates:
            ws.batch_update(updates, value_input_option="RAW")

        _sheet_refresh_dashboard_loki(sh)
        _sheet_apply_kpi_month_formulas(sh, clip_day)
        return True
    except Exception as exc:
        logging.warning(f"[Sheet] _sheet_write_clip error: {exc}")
        return False


def _sheet_update_stats(
    clip_id: int,
    views: int,
    likes: int,
    comments: int,
    shares: int,
    followers: int,
) -> bool:
    """Update 2h performance stats for a clip row in LỊCH ĐĂNG."""
    sh = _sheet_open()
    if sh is None:
        return False
    try:
        ws = _ws_like(sh, "LỊCH ĐĂNG")
        if ws is None:
            return False

        values = ws.get_all_values()
        header_row_idx = _find_schedule_header_row(values)
        if header_row_idx is None:
            return False

        header = values[header_row_idx - 1]
        c_num = _find_col(header, "CLIP #", "#")
        c_view2h = _find_col(header, "View 2h")
        c_like2h = _find_col(header, "Like 2h")
        c_cmt2h = _find_col(header, "Comment 2h")
        c_share2h = _find_col(header, "Share 2h")
        c_flw2h = _find_col(header, "Follower tăng 2h")
        c_checked = _find_col(header, "Đã check 2h?")
        c_agent = _find_col(header, "Agent note")

        target_row = None
        for r_idx in range(header_row_idx + 1, len(values) + 1):
            row = values[r_idx - 1] if r_idx - 1 < len(values) else []
            num_val = row[c_num] if c_num is not None and c_num < len(row) else ""
            if (num_val or "").strip() == str(clip_id):
                target_row = r_idx
                break

        if target_row is None:
            return False

        updates = []
        for col, val in [
            (c_view2h, views), (c_like2h, likes),
            (c_cmt2h, comments), (c_share2h, shares),
            (c_flw2h, followers),
        ]:
            if col is not None:
                updates.append({"range": gspread.utils.rowcol_to_a1(target_row, col + 1), "values": [[val]]})
        if c_checked is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_checked + 1), "values": [["✅ Đã check"]]})
        if c_agent is not None:
            eng = round((likes + comments + shares) / max(views, 1) * 100, 1)
            note = f"Eng rate: {eng}%"
            if views >= 10000:
                note += " 🔥 Viral"
            elif views >= 5000:
                note += " ✅ Tốt"
            elif views < 1000:
                note += " ⚠️ Thấp"
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_agent + 1), "values": [[note]]})

        if updates:
            ws.batch_update(updates, value_input_option="RAW")

        # Keep DASHBOARD KÊNH in sync with LỊCH ĐĂNG after every stats update.
        _sheet_refresh_dashboard_loki(sh)
        _sheet_apply_kpi_month_formulas(sh)
        return True
    except Exception as exc:
        logging.warning(f"[Sheet] _sheet_update_stats error: {exc}")
        return False


def _sheet_set_views_only(clip_id: int, views: int) -> bool:
    """Update only View 2h in LỊCH ĐĂNG then refresh Dashboard/KPI."""
    sh = _sheet_open()
    if sh is None:
        return False
    try:
        ws = _ws_like(sh, "LỊCH ĐĂNG")
        if ws is None:
            return False

        values = ws.get_all_values()
        header_row_idx = _find_schedule_header_row(values)
        if header_row_idx is None:
            return False

        header = values[header_row_idx - 1]
        c_num = _find_col(header, "CLIP #", "#")
        c_view2h = _find_col(header, "View 2h")
        if c_num is None or c_view2h is None:
            return False

        target_row = None
        for r_idx in range(header_row_idx + 1, len(values) + 1):
            row = values[r_idx - 1] if r_idx - 1 < len(values) else []
            num_val = row[c_num] if c_num < len(row) else ""
            if (num_val or "").strip() == str(clip_id):
                target_row = r_idx
                break

        if target_row is None:
            return False

        ws.update(
            range_name=gspread.utils.rowcol_to_a1(target_row, c_view2h + 1),
            values=[[views]],
            value_input_option="RAW",
        )
        _sheet_refresh_dashboard_loki(sh)
        _sheet_apply_kpi_month_formulas(sh)
        return True
    except Exception as exc:
        logging.warning(f"[Sheet] _sheet_set_views_only error: {exc}")
        return False


def _parse_sheet_date(text: str) -> Optional[date]:
    s = (text or "").strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m"):
        try:
            d = datetime.strptime(s, fmt).date()
            # dd/mm without year: assume current year
            if fmt == "%d/%m":
                d = d.replace(year=date.today().year)
            return d
        except ValueError:
            continue
    return None


def _to_int_safe(raw: str) -> int:
    s = str(raw or "").replace(",", "").strip()
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _month_window(target_day: date) -> tuple[date, date]:
    start = date(target_day.year, target_day.month, 1)
    if target_day.month == 12:
        end = date(target_day.year + 1, 1, 1)
    else:
        end = date(target_day.year, target_day.month + 1, 1)
    return start, end


def _sheet_apply_kpi_month_formulas(sh, target_day: Optional[date] = None) -> bool:
    """Fill KPI monthly formulas for current month based on LỊCH ĐĂNG."""
    try:
        ws = _ws_like(sh, "KPI")
        if ws is None:
            return False

        d = target_day or date.today()
        # Table layout is fixed: Tháng 4..12 are columns D..L.
        month_col = d.month
        if month_col < 4 or month_col > 12:
            return False

        start, end = _month_window(d)
        date_start = f"DATE({start.year};{start.month};{start.day})"
        date_end = f"DATE({end.year};{end.month};{end.day})"

        # Parse NGÀY ĐĂNG as dd/mm/yyyy text (legacy rows) and fallback to DATEVALUE.
        date_expr = (
            "IFERROR("
            "DATE("
            "VALUE(RIGHT('LỊCH ĐĂNG'!$H:$H;4));"
            "VALUE(MID('LỊCH ĐĂNG'!$H:$H;4;2));"
            "VALUE(LEFT('LỊCH ĐĂNG'!$H:$H;2))"
            ");"
            "IFERROR(DATEVALUE('LỊCH ĐĂNG'!$H:$H);0)"
            ")"
        )

        # KPI table rows in sheet: 16..19
        # Row 16: Số video đã đăng
        # Row 17: Video đạt >=10K view
        # Row 18: Video đạt >=5K view
        # Row 19: Tổng view
        formulas = [
            f"=SUMPRODUCT(('LỊCH ĐĂNG'!$B:$B<>\"\")*({date_expr}>={date_start})*({date_expr}<{date_end}))",
            f"=SUMPRODUCT(('LỊCH ĐĂNG'!$B:$B<>\"\")*({date_expr}>={date_start})*({date_expr}<{date_end})*(IFERROR(VALUE('LỊCH ĐĂNG'!$K:$K);0)>=10000))",
            f"=SUMPRODUCT(('LỊCH ĐĂNG'!$B:$B<>\"\")*({date_expr}>={date_start})*({date_expr}<{date_end})*(IFERROR(VALUE('LỊCH ĐĂNG'!$K:$K);0)>=5000))",
            f"=SUMPRODUCT(('LỊCH ĐĂNG'!$B:$B<>\"\")*({date_expr}>={date_start})*({date_expr}<{date_end})*IFERROR(VALUE('LỊCH ĐĂNG'!$K:$K);0))",
        ]

        a1 = gspread.utils.rowcol_to_a1(16, month_col)
        ws.update(range_name=f"{a1}:{gspread.utils.rowcol_to_a1(19, month_col)}", values=[[f] for f in formulas], value_input_option="USER_ENTERED")
        return True
    except Exception as exc:
        logging.warning(f"[Sheet] _sheet_apply_kpi_month_formulas error: {exc}")
        return False


def _week_window_monday_sunday(ref: date) -> tuple[date, date]:
    start = ref - timedelta(days=ref.weekday())
    end = start + timedelta(days=6)
    return start, end


def _collect_schedule_rows(sh) -> tuple[list[list[str]], list[str], int] | tuple[None, None, None]:
    ws = _ws_like(sh, "LỊCH ĐĂNG")
    if ws is None:
        return None, None, None
    values = ws.get_all_values()
    header_row_idx = _find_schedule_header_row(values)
    if header_row_idx is None:
        return None, None, None
    header = values[header_row_idx - 1]
    rows = values[header_row_idx:]
    return rows, header, header_row_idx


def _sheet_fill_weekly_report(sh, week_start: date, week_end: date) -> bool:
    """Fill BÁO CÁO TUẦN from LỊCH ĐĂNG data."""
    try:
        ws = _ws_like(sh, "BÁO CÁO TUẦN")
        if ws is None:
            return False

        rows, header, _ = _collect_schedule_rows(sh)
        if rows is None or header is None:
            return False

        c_num = _find_col(header, "CLIP #", "#")
        c_title = _find_col(header, "CHỦ ĐỀ / TIÊU ĐỀ", "TIÊU ĐỀ")
        c_link = _find_col(header, "LINK TikTok VN")
        c_date = _find_col(header, "NGÀY ĐĂNG")
        c_views = _find_col(header, "View 2h")
        c_likes = _find_col(header, "Like 2h")
        c_comments = _find_col(header, "Comment 2h")
        c_shares = _find_col(header, "Share 2h")

        if None in (c_num, c_date, c_views):
            return False

        week_rows = []
        for row in rows:
            num_val = row[c_num].strip() if c_num < len(row) else ""
            if not num_val:
                continue
            d = _parse_sheet_date(row[c_date] if c_date < len(row) else "")
            if d is None or d < week_start or d > week_end:
                continue
            week_rows.append(row)

        approved_script_ids = _approved_script_clip_ids(sh)

        clips = len(week_rows)
        total_views = sum(_to_int_safe(row[c_views] if c_views < len(row) else "") for row in week_rows)
        ge10k = sum(1 for row in week_rows if _to_int_safe(row[c_views] if c_views < len(row) else "") >= 10000)
        ge5k = sum(1 for row in week_rows if _to_int_safe(row[c_views] if c_views < len(row) else "") >= 5000)
        approved_posted_scripts = 0
        for row in week_rows:
            num_val = row[c_num] if c_num < len(row) else ""
            n = _parse_clip_number_text(num_val)
            if n is not None and n in approved_script_ids:
                approved_posted_scripts += 1

        # Header info row
        week_no = week_start.isocalendar().week
        updates = [
            {"range": "C4", "values": [[str(week_no)]]},
            {"range": "E4", "values": [[week_start.strftime("%d/%m/%Y")]]},
            {"range": "G4", "values": [[week_end.strftime("%d/%m/%Y")]]},
        ]

        # KPI section rows 8..12
        # Keep these rows formula-driven so editing E4/G4 (from/to date) updates KPI instantly.
        date_expr = (
            "IFERROR(IF(ISNUMBER('LỊCH ĐĂNG'!$H:$H);'LỊCH ĐĂNG'!$H:$H;"
            "DATE(VALUE(RIGHT('LỊCH ĐĂNG'!$H:$H;4));VALUE(MID('LỊCH ĐĂNG'!$H:$H;4;2));VALUE(LEFT('LỊCH ĐĂNG'!$H:$H;2))));0)"
        )
        updates.extend([
            {
                "range": "D8",
                "values": [[f"=SUMPRODUCT(('LỊCH ĐĂNG'!$B:$B<>\"\")*({date_expr}>=$E$4)*({date_expr}<=$G$4))"]],
            },
            {
                "range": "D9",
                "values": [[f"=SUMPRODUCT((IFERROR(VALUE('LỊCH ĐĂNG'!$K:$K);0))*({date_expr}>=$E$4)*({date_expr}<=$G$4))"]],
            },
            {
                "range": "D10",
                "values": [[f"=SUMPRODUCT(('LỊCH ĐĂNG'!$B:$B<>\"\")*({date_expr}>=$E$4)*({date_expr}<=$G$4)*(IFERROR(VALUE('LỊCH ĐĂNG'!$K:$K);0)>=10000))"]],
            },
            {
                "range": "D11",
                "values": [[f"=SUMPRODUCT(('LỊCH ĐĂNG'!$B:$B<>\"\")*({date_expr}>=$E$4)*({date_expr}<=$G$4)*(IFERROR(VALUE('LỊCH ĐĂNG'!$K:$K);0)>=5000))"]],
            },
            {"range": "D12", "values": [[approved_posted_scripts]]},
        ])
        for r in [8, 9, 10, 11, 12]:
            updates.append({"range": f"E{r}", "values": [[f"=IF(D{r}>=C{r};\"✅\";\"⚠️\")"]]})
            updates.append({"range": f"F{r}", "values": [[f"=D{r}-C{r}"]]})

        # Top 3 rows 16..18
        top = sorted(week_rows, key=lambda row: _to_int_safe(row[c_views] if c_views < len(row) else ""), reverse=True)[:3]
        for idx in range(3):
            r = 16 + idx
            if idx < len(top):
                row = top[idx]
                title = row[c_title] if (c_title is not None and c_title < len(row)) else ""
                views = _to_int_safe(row[c_views] if c_views < len(row) else "")
                likes = _to_int_safe(row[c_likes] if c_likes is not None and c_likes < len(row) else "")
                cmt = _to_int_safe(row[c_comments] if c_comments is not None and c_comments < len(row) else "")
                link = row[c_link] if (c_link is not None and c_link < len(row)) else ""
                updates.extend([
                    {"range": f"B{r}", "values": [[title]]},
                    {"range": f"C{r}", "values": [["TikTok VN"]]},
                    {"range": f"D{r}", "values": [[views]]},
                    {"range": f"E{r}", "values": [[likes]]},
                    {"range": f"F{r}", "values": [[cmt]]},
                    {"range": f"G{r}", "values": [[link]]},
                ])
            else:
                updates.extend([
                    {"range": f"B{r}:G{r}", "values": [["", "", "", "", "", ""]]},
                ])

        # Section C quick diagnosis row 22
        if approved_posted_scripts <= 0:
            updates.extend([
                {"range": "B22", "values": [["Chỉ tổng hợp số liệu thực tế"]]},
                {"range": "C22", "values": [["Chưa có SCRIPT duyệt"]]},
                {"range": "D22:G22", "values": [["", "", "", ""]]},
            ])
        else:
            if clips < 5:
                issue, cause, action = "Thiếu số clip", "Tần suất đăng chưa đủ", "Ưu tiên 1-2 clip/ngày trong 3 ngày tới"
            elif total_views < 7500:
                issue, cause, action = "View thấp", "Hook/thumbnail chưa đủ mạnh", "A/B test hook 2s đầu + caption CTA rõ hơn"
            else:
                issue, cause, action = "On track", "KPI tuần đạt", "Giữ nhịp đều và tập trung clip có khả năng >=5K"
            updates.extend([
                {"range": "B22", "values": [[issue]]},
                {"range": "C22", "values": [[cause]]},
                {"range": "D22:G22", "values": [[action, "", "", ""]]},
            ])

        ws.batch_update(updates, value_input_option="USER_ENTERED")
        return True
    except Exception as exc:
        logging.warning(f"[Sheet] _sheet_fill_weekly_report error: {exc}")
        return False


def _brief_generate(topic: str) -> Dict[str, str]:
    t = topic.strip()
    if not t:
        t = "Fulfillment Philippines"
    title = f"{t[:38]}".strip()
    thumb = f"{t[:18].upper()} | THỰC CHIẾN"
    caption = (
        f"{t}: seller mới thường vướng chỗ nào? Mình gom case thật + cách xử lý nhanh trong 60s. "
        f"Comment 'CHECKLIST' để mình gửi form vận hành mẫu."
    )
    desc = (
        f"Góc nhìn thực chiến về {t}.\n"
        f"#fulfillment #tiktokshop #shopee #philippines #sellerviet #gip"
    )
    return {
        "title": title,
        "thumbnail": thumb,
        "caption": caption,
        "desc": desc,
        "note": "Generated by /brief",
    }


def _sheet_add_brief(topic: str) -> tuple[bool, str]:
    sh = _sheet_open()
    if sh is None:
        return False, "Sheet chưa kết nối"
    try:
        ws = _ws_like(sh, "CONTENT BRIEF")
        if ws is None:
            return False, "Không thấy sheet CONTENT BRIEF"

        vals = ws.get_all_values()
        header_row = None
        for i, row in enumerate(vals, start=1):
            norm = " ".join((x or "").strip().casefold() for x in row)
            if "chủ đề" in norm and "trạng thái" in norm:
                header_row = i
                break
        if header_row is None:
            return False, "Không tìm thấy header CONTENT BRIEF"

        header = vals[header_row - 1]
        c_num = _find_col(header, "#")
        c_title = _find_col(header, "CHỦ ĐỀ / TIÊU ĐỀ")
        c_thumb = _find_col(header, "THUMBNAIL TITLE")
        c_caption = _find_col(header, "CAPTION")
        c_desc = _find_col(header, "MÔ TẢ + HASHTAG")
        c_note = _find_col(header, "GHI CHÚ")
        c_status = _find_col(header, "TRẠNG THÁI")

        next_row = None
        next_id = 1
        for r in range(header_row + 1, header_row + 40):
            row = vals[r - 1] if r - 1 < len(vals) else []
            num = row[c_num].strip() if c_num is not None and c_num < len(row) else ""
            if num:
                next_id = max(next_id, _to_int_safe(num) + 1)
            elif next_row is None:
                next_row = r
        if next_row is None:
            next_row = header_row + 1

        b = _brief_generate(topic)
        updates = []
        if c_num is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(next_row, c_num + 1), "values": [[next_id]]})
        if c_title is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(next_row, c_title + 1), "values": [[b["title"]]]})
        if c_thumb is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(next_row, c_thumb + 1), "values": [[b["thumbnail"]]]})
        if c_caption is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(next_row, c_caption + 1), "values": [[b["caption"]]]})
        if c_desc is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(next_row, c_desc + 1), "values": [[b["desc"]]]})
        if c_note is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(next_row, c_note + 1), "values": [[b["note"]]]})
        if c_status is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(next_row, c_status + 1), "values": [["🟡 Chờ duyệt"]]})

        if updates:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
        return True, f"Đã thêm brief vào row {next_row}"
    except Exception as exc:
        return False, f"Lỗi ghi CONTENT BRIEF: {exc}"


def _sheet_refresh_dashboard_loki(sh) -> bool:
    """Update DASHBOARD KÊNH / TikTok Loki Trần block from LỊCH ĐĂNG data."""
    try:
        ws_schedule = _ws_like(sh, "LỊCH ĐĂNG")
        ws_dash = _ws_like(sh, "DASHBOARD KÊNH")
        if ws_schedule is None or ws_dash is None:
            return False

        values = ws_schedule.get_all_values()
        header_row_idx = _find_schedule_header_row(values)
        if header_row_idx is None:
            return False

        header = values[header_row_idx - 1]
        c_num = _find_col(header, "CLIP #", "#")
        c_date = _find_col(header, "NGÀY ĐĂNG")
        c_view2h = _find_col(header, "View 2h")
        c_like2h = _find_col(header, "Like 2h")
        c_cmt2h = _find_col(header, "Comment 2h")
        c_flw2h = _find_col(header, "Follower tăng 2h")

        if None in (c_num, c_date, c_view2h, c_like2h, c_cmt2h, c_flw2h):
            return False

        today = date.today()
        week_start = today - __import__("datetime").timedelta(days=today.weekday())
        month_start = date(today.year, today.month, 1)
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        quarter_start = date(today.year, quarter_month, 1)
        year_start = date(today.year, 1, 1)

        buckets = {
            "today": {"views": 0, "likes": 0, "comments": 0, "followers": 0, "clips": 0},
            "week": {"views": 0, "likes": 0, "comments": 0, "followers": 0, "clips": 0},
            "month": {"views": 0, "likes": 0, "comments": 0, "followers": 0, "clips": 0},
            "quarter": {"views": 0, "likes": 0, "comments": 0, "followers": 0, "clips": 0},
            "year": {"views": 0, "likes": 0, "comments": 0, "followers": 0, "clips": 0},
        }

        for r_idx in range(header_row_idx + 1, len(values) + 1):
            row = values[r_idx - 1] if r_idx - 1 < len(values) else []
            num_val = row[c_num] if c_num < len(row) else ""
            if not (num_val or "").strip():
                continue

            d = _parse_sheet_date(row[c_date] if c_date < len(row) else "")
            if d is None:
                continue

            def _to_int(idx: int) -> int:
                if idx >= len(row):
                    return 0
                raw = str(row[idx]).replace(",", "").strip()
                if not raw:
                    return 0
                try:
                    return int(float(raw))
                except ValueError:
                    return 0

            v = _to_int(c_view2h)
            l = _to_int(c_like2h)
            c = _to_int(c_cmt2h)
            f = _to_int(c_flw2h)

            if d == today:
                b = buckets["today"]
                b["views"] += v
                b["likes"] += l
                b["comments"] += c
                b["followers"] += f
                b["clips"] += 1
            if d >= week_start:
                b = buckets["week"]
                b["views"] += v
                b["likes"] += l
                b["comments"] += c
                b["followers"] += f
                b["clips"] += 1
            if d >= month_start:
                b = buckets["month"]
                b["views"] += v
                b["likes"] += l
                b["comments"] += c
                b["followers"] += f
                b["clips"] += 1
            if d >= quarter_start:
                b = buckets["quarter"]
                b["views"] += v
                b["likes"] += l
                b["comments"] += c
                b["followers"] += f
                b["clips"] += 1
            if d >= year_start:
                b = buckets["year"]
                b["views"] += v
                b["likes"] += l
                b["comments"] += c
                b["followers"] += f
                b["clips"] += 1

        dash_vals = ws_dash.get_all_values()
        loki_title_row = None
        for i, row in enumerate(dash_vals, start=1):
            text = " ".join((x or "").strip() for x in row).casefold()
            if "tiktok loki" in text:
                loki_title_row = i
                break
        if loki_title_row is None:
            return False

        metric_rows = {}
        for i in range(loki_title_row + 1, min(loki_title_row + 25, len(dash_vals) + 1)):
            row = dash_vals[i - 1]
            metric = (row[1] if len(row) > 1 else "").strip().casefold()
            if metric:
                metric_rows[metric] = i

        periods = ["today", "week", "month", "quarter", "year"]

        def series(metric_key: str) -> List[int]:
            return [buckets[p][metric_key] for p in periods]

        def series_avg() -> List[float]:
            out = []
            for p in periods:
                clips = buckets[p]["clips"]
                out.append(round(buckets[p]["views"] / clips, 1) if clips else 0)
            return out

        updates = []
        row_views = metric_rows.get("views")
        row_likes = metric_rows.get("likes")
        row_comments = metric_rows.get("comments")
        row_followers = metric_rows.get("followers")
        row_clips = metric_rows.get("clip đăng") or metric_rows.get("clip dang")
        row_avg = metric_rows.get("views/clip tb")
        row_kpi_target = metric_rows.get("kpi target tháng") or metric_rows.get("kpi target thang")
        row_gap_views = metric_rows.get("gap views (tháng)") or metric_rows.get("gap views (thang)")
        row_gap_clips = metric_rows.get("gap clips (tháng)") or metric_rows.get("gap clips (thang)")
        row_pct_views = metric_rows.get("% views to 30k")
        row_pct_clips = metric_rows.get("% clips to 20")
        row_kpi_week = (
            metric_rows.get("kpi tuần")
            or metric_rows.get("kpi tuan")
            or metric_rows.get("kpi tuần (clip)")
            or metric_rows.get("kpi tuan (clip)")
        )
        row_gap_week = (
            metric_rows.get("gap kpi tuần")
            or metric_rows.get("gap kpi tuan")
            or metric_rows.get("gap kpi tuần (clip)")
            or metric_rows.get("gap kpi tuan (clip)")
        )
        row_last_updated = metric_rows.get("cập nhật lúc") or metric_rows.get("cap nhat luc")

        for row_idx, vals in [
            (row_views, series("views")),
            (row_likes, series("likes")),
            (row_comments, series("comments")),
            (row_followers, series("followers")),
            (row_clips, series("clips")),
            (row_avg, series_avg()),
        ]:
            if row_idx is None:
                continue
            # C:G = HÔM NAY..NĂM NAY
            updates.append({"range": f"C{row_idx}:G{row_idx}", "values": [vals]})

        # KPI rows use column E = THÁNG NÀY
        if row_kpi_target:
            updates.append({"range": f"E{row_kpi_target}", "values": [["20 clip / 30.000 view"]]})

        # Gap views (tháng) = max(0, 30000 - current month views)
        if row_gap_views:
            month_views = buckets["month"]["views"]
            gap_views = max(0, 30000 - month_views)
            updates.append({"range": f"E{row_gap_views}", "values": [[gap_views]]})

        # Gap clips (tháng) = max(0, 20 - current month clips)
        if row_gap_clips:
            month_clips = buckets["month"]["clips"]
            gap_clips = max(0, 20 - month_clips)
            updates.append({"range": f"E{row_gap_clips}", "values": [[gap_clips]]})

        # % views to 30k
        if row_pct_views:
            month_views = buckets["month"]["views"]
            pct_views = round((month_views / 30000 * 100), 1) if month_views > 0 else 0
            updates.append({"range": f"E{row_pct_views}", "values": [[f"{pct_views}%"]]})

        # % clips to 20
        if row_pct_clips:
            month_clips = buckets["month"]["clips"]
            pct_clips = round((month_clips / 20 * 100), 1) if month_clips > 0 else 0
            updates.append({"range": f"E{row_pct_clips}", "values": [[f"{pct_clips}%"]]})

        # KPI tuần đặt ở cột D = TUẦN NÀY
        if row_kpi_week:
            updates.append({"range": f"D{row_kpi_week}", "values": [[f"{WEEKLY_TARGET} clip/tuần"]]})

        if row_gap_week:
            week_clips = buckets["week"]["clips"]
            gap_week = max(0, WEEKLY_TARGET - week_clips)
            updates.append({"range": f"D{row_gap_week}", "values": [[gap_week]]})

        # Last updated marker
        if row_last_updated:
            updates.append({"range": f"E{row_last_updated}", "values": [[datetime.now().strftime("%d/%m %H:%M")]]})

        if updates:
            ws_dash.batch_update(updates, value_input_option="RAW")
        return True
    except Exception as exc:
        logging.warning(f"[Sheet] _sheet_refresh_dashboard_loki error: {exc}")
        return False


def _week_clips_count(state: Dict[str, Any]) -> int:
    """Count clips posted this calendar week (Mon-Sun)."""
    today = date.today()
    week_start = today - __import__('datetime').timedelta(days=today.weekday())
    count = 0
    for rows in state.get("records", {}).values():
        for r in rows:
            try:
                d = date.fromisoformat(r.get("date", ""))
                if d >= week_start:
                    count += 1
            except ValueError:
                pass
    return count


def _kpi_warning_text(state: Dict[str, Any]) -> str:
    """Return KPI warning text for this week's progress."""
    today = date.today()
    week_clips = _week_clips_count(state)
    days_left_week = 6 - today.weekday()  # days until Sunday incl. today
    missing = max(0, WEEKLY_TARGET - week_clips)

    if missing == 0:
        return f"✅ Tuần này đã đạt {week_clips}/{WEEKLY_TARGET} clip. Giữ đà!"

    urgency = "🔴" if days_left_week <= 1 else ("🟠" if days_left_week <= 3 else "🟡")
    lines = [
        f"{urgency} KPI TUẦN NÀY",
        f"Đã đăng: {week_clips}/{WEEKLY_TARGET} clip",
        f"Còn thiếu: {missing} clip",
        f"Còn {days_left_week} ngày trong tuần",
    ]
    if days_left_week > 0:
        lines.append(f"Cần đăng ít nhất {math.ceil(missing / days_left_week)} clip/ngày để kịp")
    else:
        lines.append("⚠️ Hôm nay là Chủ nhật - tuần này không đạt mục tiêu!")
    return "\n".join(lines)


def _today_report_text(state: Dict[str, Any], target_day: Optional[date] = None) -> str:
    """Daily recap used in scheduled Telegram reports."""
    d = target_day or date.today()
    today_clips = 0
    today_views = 0

    for month_rows in state.get("records", {}).values():
        for row in month_rows:
            try:
                posted_day = date.fromisoformat(str(row.get("date", "")))
            except ValueError:
                continue
            if posted_day != d:
                continue
            today_clips += 1
            today_views += int(row.get("views", 0) or 0)

    now_stats = _stats_for_month(state, _month_key(d))
    clip_gap = max(0, TARGET_CLIPS - now_stats.clip_count)
    view_gap = max(0, TARGET_VIEWS - now_stats.total_views)

    lines = [
        f"BÁO CÁO NGÀY {d.strftime('%d/%m/%Y')}",
        f"- Hôm nay đăng: {today_clips} clip",
        f"- View từ clip đăng hôm nay: {today_views:,}",
        f"- KPI tháng còn thiếu: {clip_gap} clip | {view_gap:,} view",
    ]
    if today_clips == 0:
        lines.append("- CẢNH BÁO: Hôm nay chưa có clip mới.")
    if today_clips > 0 and today_views < 1000:
        lines.append("- CẢNH BÁO: View clip hôm nay đang thấp (<1.000).")
    if clip_gap == 0 and view_gap == 0:
        lines.append("- Trạng thái: Đã đạt KPI tháng.")
    return "\n".join(lines)


def _latest_clip_datetime(state: Dict[str, Any]) -> Optional[datetime]:
    """Get latest clip timestamp from local state records."""
    latest: Optional[datetime] = None
    for rows in state.get("records", {}).values():
        for row in rows:
            dt_val: Optional[datetime] = None
            created_raw = str(row.get("created_at", "") or "").strip()
            if created_raw:
                try:
                    dt_val = datetime.fromisoformat(created_raw)
                except ValueError:
                    dt_val = None
            if dt_val is None:
                date_raw = str(row.get("date", "") or "").strip()
                if date_raw:
                    try:
                        dt_val = datetime.combine(date.fromisoformat(date_raw), datetime.min.time())
                    except ValueError:
                        dt_val = None
            if dt_val is not None and (latest is None or dt_val > latest):
                latest = dt_val
    return latest


# ---------------------------------------------------------------------------

def _progress_text(state: Dict[str, Any], target_day: Optional[date] = None) -> str:
    now = target_day or date.today()
    mk = _month_key(now)
    stats = _stats_for_month(state, mk)

    days_total = _days_in_month(now.year, now.month)
    days_elapsed = now.day
    days_left = max(0, days_total - days_elapsed)

    clip_gap = max(0, TARGET_CLIPS - stats.clip_count)
    view_gap = max(0, TARGET_VIEWS - stats.total_views)

    expected_clip_now = math.ceil(TARGET_CLIPS * (days_elapsed / days_total))
    expected_view_now = math.ceil(TARGET_VIEWS * (days_elapsed / days_total))
    clip_pace_gap = max(0, expected_clip_now - stats.clip_count)
    view_pace_gap = max(0, expected_view_now - stats.total_views)

    needed_clip_per_day = math.ceil(clip_gap / max(1, days_left)) if clip_gap else 0
    needed_view_per_day = math.ceil(view_gap / max(1, days_left)) if view_gap else 0

    status = [
        f"KPI Loki Trần | Tháng {mk}",
        f"- Clip: {stats.clip_count}/{TARGET_CLIPS} (thiếu {clip_gap})",
        f"- Tổng view: {stats.total_views:,}/{TARGET_VIEWS:,} (thiếu {view_gap:,})",
        f"- Clip >=10K: {stats.clips_ge_10k} (mục tiêu: >=1)",
        f"- Clip >=5K: {stats.clips_ge_5k} (mục tiêu: >=1)",
        f"- Clip 1K-1.5K: {stats.clips_1k_to_1p5k}/18",
        f"- Tiến độ theo ngày: clip thiếu {clip_pace_gap}, view thiếu {view_pace_gap:,}",
        f"- Để kịp KPI: mỗi ngày cần trung bình {needed_clip_per_day} clip và {needed_view_per_day:,} view",
    ]

    if clip_gap == 0 and view_gap == 0:
        status.append("- Trạng thái: Đã đạt KPI tháng. Xuất sắc.")
    elif days_left == 0:
        status.append("- Trạng thái: Hết tháng rồi, chưa đạt KPI. Cần tổng kết và reset mục tiêu tháng mới.")
    else:
        status.append(f"- Trạng thái: Còn {days_left} ngày để chốt KPI.")

    return "\n".join(status)


def _nudge_text(state: Dict[str, Any], today: Optional[date] = None) -> str:
    d = today or date.today()
    mk = _month_key(d)
    stats = _stats_for_month(state, mk)

    days_total = _days_in_month(d.year, d.month)
    days_elapsed = d.day
    days_left = max(0, days_total - days_elapsed)

    clip_gap = max(0, TARGET_CLIPS - stats.clip_count)
    view_gap = max(0, TARGET_VIEWS - stats.total_views)

    if clip_gap == 0 and view_gap == 0:
        return "KPI đã về đích. Hôm nay tập trung giữ nhịp và nâng chất lượng clip."

    expected_clip_now = math.ceil(TARGET_CLIPS * (days_elapsed / days_total))
    expected_view_now = math.ceil(TARGET_VIEWS * (days_elapsed / days_total))
    behind_clip = max(0, expected_clip_now - stats.clip_count)
    behind_view = max(0, expected_view_now - stats.total_views)

    if days_left <= 5 or behind_clip >= 3 or behind_view >= 6000:
        tone = "CẢNH BÁO ĐỎ"
        action = (
            "Kế hoạch ngày hôm nay: đăng tối thiểu 2 clip, ưu tiên 1 clip hook mạnh để target >=5K view. "
            "Sau khi đăng xong, dùng /add_clip để cập nhật view gốc và tôi tiếp tục nhắc."
        )
    elif behind_clip >= 1 or behind_view >= 2000:
        tone = "Cần tăng tốc"
        action = (
            "Hôm nay không được bỏ qua: đăng ít nhất 1 clip mới trước 21:00. "
            "Tập trung chủ đề để ra inbox và comment, rồi cập nhật bằng /add_clip."
        )
    else:
        tone = "Đúng tiến độ"
        action = "Tiến độ tạm ổn. Vẫn cần đăng 1 clip hôm nay để giữ nhịp KPI."

    return (
        f"[{tone}] Loki Trần KPI\n"
        f"Còn thiếu {clip_gap} clip và {view_gap:,} view, còn {days_left} ngày.\n"
        f"{action}"
    )


def _is_authorized(state: Dict[str, Any], chat_id: int) -> bool:
    auth_chat = state.get("authorized_chat_id")
    return auth_chat is None or int(auth_chat) == int(chat_id)


def _reject_text() -> str:
    return "Bot này chỉ phục vụ 1 chat được ủy quyền. Dùng /start trong chat chính để gán quyền."


async def cmd_start(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state_path: Path = context.bot_data["state_path"]
    state = _load_state(state_path)
    chat_id = update.effective_chat.id

    if state.get("authorized_chat_id") is None:
        state["authorized_chat_id"] = chat_id
        _save_state(state_path, state)
        await update.message.reply_text(
            "Đã gán chat này là chat quản lý KPI Loki Trần.\n"
            "Dùng /help để xem lệnh."
        )
        return

    if not _is_authorized(state, chat_id):
        await update.message.reply_text(_reject_text())
        return

    await update.message.reply_text(
        "Bot KPI Loki Trần đang hoạt động.\n"
        "Mình sẽ nhắc đến khi đạt KPI tháng. Dùng /status để xem tiến độ hiện tại."
    )


async def cmd_help(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    help_text = (
        "📊 LỆNH KPI:\n"
        "/status - Xem tiến độ KPI tháng hiện tại\n"
        "/add_clip <views> [yyyy-mm-dd] - Thêm 1 clip theo SCRIPT đã duyệt\n"
        "/log_clip <id> <view> <like> <cmt> [yyyy-mm-dd] [hh:mm] - Nhập nhanh clip đã đăng\n"
        "/set_views <clip_id> <views> - Sửa views của clip\n"
        "/remove_clip <clip_id> - Xóa clip khỏi thống kê\n"
        "/list_clips - Liệt kê clip trong tháng\n"
        "/nudge_now - Gửi 1 lần nhắc gấp ngay\n"
        "/sync_sheet_now - Đồng bộ Google Sheet ngay\n"
        "/reset_month [yyyy-mm] - Xóa dữ liệu tháng (cẩn thận)\n\n"
        "🎬 TRỢ LÝ TIKTOK:\n"
        "/daily_plan - Kế hoạch content hôm nay\n"
        "/weekly_review - Tóm tắt tuần vừa rồi\n"
        "/platform_snapshot - Trạng thái real-time\n"
        "/lead_report - Báo cáo lead từ comment\n"
        "/content_ideas - Gợi ý chủ đề video AI\n"
        "/brief <chủ đề> - Generate + ghi CONTENT BRIEF\n\n"
        "🆓 FREE MODE (không mất phí):\n"
        "/free_status - Trạng thái mode miễn phí\n"
        "/free_refresh - Refresh KPI + Dashboard ngay\n\n"
        "⚙️ PHASE 2:\n"
        "/phase2_status - Kiểm tra trạng thái API TikTok\n"
        "/phase2_scan_now - Chạy detect+refresh ngay\n\n"
        "📊 STATS 2H:\n"
        "/log_stats <id> <view> <like> <cmt> <share> <flw> - Nhập số liệu 2h sau đăng\n"
        "/report_now - Gửi báo cáo tuần ngay"
    )
    await update.message.reply_text(help_text)


async def cmd_status(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    await update.message.reply_text(_progress_text(state))


async def _remind_2h_check(context: CallbackContext) -> None:
    """Job fired 2 hours after /add_clip to ask for performance stats."""
    data = context.job.data
    chat_id = data["chat_id"]
    clip_id = data["clip_id"]
    title = data.get("title", f"clip #{clip_id}")
    text = (
        f"⏰ ĐÃ 2 TIẾNG SAU KHI ĐĂNG\n"
        f"Clip #{clip_id} — {title}\n\n"
        f"Nhập số liệu để bot cập nhật sheet và tính KPI (không nhập dấu < >):\n"
        f"Ví dụ: /log_stats {clip_id} 1200 85 12 3 5"
    )
    await context.bot.send_message(chat_id=int(chat_id), text=text)


async def cmd_add_clip(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state_path: Path = context.bot_data["state_path"]
    state = _load_state(state_path)
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    # Usage: /add_clip <views> [yyyy-mm-dd]
    if not context.args:
        await update.message.reply_text(
            "Dùng: /add_clip <views> [yyyy-mm-dd]\n"
            "Ví dụ: /add_clip 0 2026-05-07\n"
            "(Tiêu đề lấy từ tab SCRIPT đã duyệt theo đúng số clip.)"
        )
        return

    try:
        views = int(context.args[0])
        if views < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Views phải là số nguyên >= 0")
        return

    clip_day = date.today()
    arg_offset = 1
    if len(context.args) >= 2:
        try:
            clip_day = _parse_date(context.args[1])
            arg_offset = 2
        except ValueError:
            arg_offset = 1  # not a date, treat as title

    title = " ".join(context.args[arg_offset:]).strip() if len(context.args) > arg_offset else ""

    mk = _month_key(clip_day)
    state.setdefault("records", {})
    state["records"].setdefault(mk, [])

    clip_id = int(state.get("next_clip_id", 1))

    sh = _sheet_open()
    if sh is None:
        await update.message.reply_text("Không kết nối được Google Sheet. Chưa thể xác minh SCRIPT đã duyệt.")
        return

    ok_script, script_msg, script_title = _get_approved_script_entry(sh, clip_id)
    if not ok_script or not script_title:
        await update.message.reply_text(
            "❌ Không thể thêm clip mới theo rule chuẩn.\n"
            f"- Clip dự kiến: #{clip_id}\n"
            f"- Lý do: {script_msg}\n"
            "Hãy duyệt đúng dòng trong tab SCRIPT trước khi /add_clip."
        )
        return

    state["next_clip_id"] = clip_id + 1
    posted_at = datetime.now()

    title = script_title
    state["records"][mk].append(
        {
            "id": clip_id,
            "date": clip_day.isoformat(),
            "views": views,
            "title": title,
            "created_at": posted_at.isoformat(timespec="seconds"),
        }
    )

    # Store pending 2h check
    from datetime import timedelta
    check_time = posted_at + timedelta(hours=2)
    state["pending_2h_checks"][str(clip_id)] = {
        "check_time": check_time.isoformat(timespec="seconds"),
        "posted_at": posted_at.isoformat(timespec="seconds"),
        "title": title or f"clip #{clip_id}",
    }
    _save_state(state_path, state)

    # Write to Google Sheet (SCRIPT -> LỊCH ĐĂNG)
    sheet_ok = _sheet_write_clip(clip_id, clip_day, posted_at, title)
    sheet_note = " (Sheet: đã kết nối, đã ghi vào sheet)" if sheet_ok else " (Sheet: chưa kết nối)"

    # Schedule 2h reminder job
    chat_id = update.effective_chat.id
    context.application.job_queue.run_once(
        _remind_2h_check,
        when=timedelta(hours=2),
        data={"chat_id": chat_id, "clip_id": clip_id, "title": title or f"clip #{clip_id}"},
        name=f"2h_check_{clip_id}",
    )

    week_clips = _week_clips_count(state)
    missing_week = max(0, WEEKLY_TARGET - week_clips)

    msg_lines = [
        f"✅ Đã thêm clip #{clip_id} ({clip_day.isoformat()}){sheet_note}",
        f"⏰ Mình sẽ nhắc bạn nhập số liệu sau 2 tiếng.",
        "",
        _progress_text(state, clip_day),
    ]
    if missing_week > 0:
        msg_lines.append(f"\n📌 Tuần này còn thiếu {missing_week} clip để đạt {WEEKLY_TARGET}/tuần.")
    else:
        msg_lines.append(f"\n🎉 Tuần này đã đủ {WEEKLY_TARGET} clip!")

    await update.message.reply_text("\n".join(msg_lines))


async def cmd_log_clip(update: Update, context: CallbackContext) -> None:
    """Quick one-line update: date + views + likes + comments for a posted clip."""
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state_path: Path = context.bot_data["state_path"]
    state = _load_state(state_path)
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    usage = "Dùng: /log_clip <clip_id> <views> <likes> <comments> [yyyy-mm-dd] [hh:mm]"
    if len(context.args) < 4:
        await update.message.reply_text(usage)
        return

    try:
        clip_id = int(context.args[0])
        views = int(context.args[1])
        likes = int(context.args[2])
        comments = int(context.args[3])
        if min(clip_id, views, likes, comments) < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(usage)
        return

    clip_day = date.today()
    if len(context.args) >= 5:
        try:
            clip_day = _parse_date(context.args[4])
        except ValueError:
            await update.message.reply_text(usage)
            return

    post_time = datetime.now().time().replace(second=0, microsecond=0)
    if len(context.args) >= 6:
        try:
            hh, mm = context.args[5].split(":", 1)
            post_time = datetime.strptime(f"{int(hh):02d}:{int(mm):02d}", "%H:%M").time()
        except Exception:
            await update.message.reply_text(usage)
            return

    posted_at = datetime.combine(clip_day, post_time)

    sh = _sheet_open()
    if sh is None:
        await update.message.reply_text("Không kết nối được Google Sheet. Chưa thể kiểm tra SCRIPT đã duyệt.")
        return

    ok_script, script_msg, script_title = _get_approved_script_entry(sh, clip_id)
    if not ok_script or not script_title:
        await update.message.reply_text(
            "❌ Không thể ghi clip theo rule chuẩn.\n"
            f"- Clip #{clip_id}\n"
            f"- Lý do: {script_msg}\n"
            "Chỉ nhận clip có nội dung đã duyệt trong tab SCRIPT."
        )
        return

    mk = _month_key(clip_day)
    state.setdefault("records", {})
    state["records"].setdefault(mk, [])

    found = False
    for rows in state.get("records", {}).values():
        for row in rows:
            if int(row.get("id", -1)) == clip_id:
                row["date"] = clip_day.isoformat()
                row["views"] = views
                row["title"] = script_title
                row["likes_2h"] = likes
                row["comments_2h"] = comments
                row["shares_2h"] = int(row.get("shares_2h", 0) or 0)
                row["followers_2h"] = int(row.get("followers_2h", 0) or 0)
                row["updated_at"] = datetime.now().isoformat(timespec="seconds")
                found = True
                break
        if found:
            break

    if not found:
        state["records"][mk].append(
            {
                "id": clip_id,
                "date": clip_day.isoformat(),
                "views": views,
                "title": script_title,
                "created_at": posted_at.isoformat(timespec="seconds"),
                "likes_2h": likes,
                "comments_2h": comments,
                "shares_2h": 0,
                "followers_2h": 0,
            }
        )

    state["next_clip_id"] = max(int(state.get("next_clip_id", 1)), clip_id + 1)
    state["pending_2h_checks"].pop(str(clip_id), None)
    _save_state(state_path, state)

    sheet_write_ok = _sheet_write_clip(clip_id, clip_day, posted_at, script_title)
    sheet_stats_ok = _sheet_update_stats(clip_id, views, likes, comments, 0, 0)
    sheet_ok = sheet_write_ok and sheet_stats_ok

    status_text = "✅ Đã cập nhật LỊCH ĐĂNG + KPI + Dashboard" if sheet_ok else "⚠️ Đã lưu local, sheet cập nhật chưa trọn vẹn"
    await update.message.reply_text(
        "\n".join(
            [
                f"✅ Đã nhập nhanh clip #{clip_id}",
                f"- Ngày đăng: {clip_day.isoformat()} {post_time.strftime('%H:%M')}",
                f"- View: {views:,} | Like: {likes} | Comment: {comments}",
                f"- Tiêu đề: {script_title}",
                status_text,
                "",
                _progress_text(state, clip_day),
            ]
        )
    )


async def cmd_log_stats(update: Update, context: CallbackContext) -> None:
    """Nhận số liệu sau 2h: /log_stats <clip_id> <view> <like> <cmt> <share> <flw>"""
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state_path: Path = context.bot_data["state_path"]
    state = _load_state(state_path)
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    usage = "Dùng (không nhập dấu < >): /log_stats 1 1200 85 12 3 5"
    if not context.args or len(context.args) < 6:
        await update.message.reply_text(usage)
        return

    try:
        clip_id = int(context.args[0])
        views = int(context.args[1])
        likes = int(context.args[2])
        comments = int(context.args[3])
        shares = int(context.args[4])
        followers = int(context.args[5])
    except (ValueError, IndexError):
        await update.message.reply_text(usage)
        return

    # Update views in local state
    updated_local = False
    for rows in state.get("records", {}).values():
        for r in rows:
            if int(r.get("id", -1)) == clip_id:
                r["views"] = views
                r["likes_2h"] = likes
                r["comments_2h"] = comments
                r["shares_2h"] = shares
                r["followers_2h"] = followers
                r["stats_updated_at"] = datetime.now().isoformat(timespec="seconds")
                updated_local = True
                break
        if updated_local:
            break

    # Mark pending check as done
    state["pending_2h_checks"].pop(str(clip_id), None)
    _save_state(state_path, state)

    # Update Google Sheet
    sheet_ok = _sheet_update_stats(clip_id, views, likes, comments, shares, followers)

    # Calculate engagement
    eng = round((likes + comments + shares) / max(views, 1) * 100, 1)
    perf = "🔥 Viral!" if views >= 10000 else ("✅ Tốt" if views >= 5000 else ("🟡 Trung bình" if views >= 1000 else "⚠️ Thấp"))

    sheet_note = "\n✅ Sheet: đã kết nối, đã cập nhật Google Sheet." if sheet_ok else "\n(Sheet chưa kết nối - dữ liệu lưu local)"

    report = [
        f"📊 STATS 2H — CLIP #{clip_id}",
        f"👁 View: {views:,}   {perf}",
        f"❤️ Like: {likes}   💬 Comment: {comments}",
        f"↗️ Share: {shares}   👥 Follower tăng: {followers}",
        f"📈 Engagement rate: {eng}%",
        "",
        _progress_text(state),
        sheet_note,
    ]
    await update.message.reply_text("\n".join(report))


async def cmd_set_views(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state_path: Path = context.bot_data["state_path"]
    state = _load_state(state_path)
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    if len(context.args) < 2:
        await update.message.reply_text("Dùng: /set_views <clip_id> <views>")
        return

    try:
        clip_id = int(context.args[0])
        views = int(context.args[1])
        if views < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("clip_id và views phải là số nguyên hợp lệ")
        return

    found = False
    for _, rows in state.get("records", {}).items():
        for row in rows:
            if int(row.get("id", -1)) == clip_id:
                row["views"] = views
                row["updated_at"] = datetime.now().isoformat(timespec="seconds")
                found = True
                break
        if found:
            break

    if not found:
        await update.message.reply_text(f"Không tìm thấy clip #{clip_id}")
        return

    _save_state(state_path, state)
    sheet_ok = _sheet_set_views_only(clip_id, views)
    await update.message.reply_text(
        f"Đã cập nhật clip #{clip_id} -> {views:,} view."
        f"\n{'✅ Đã sync Dashboard + KPI ngay.' if sheet_ok else '⚠️ Chưa sync được sheet ngay.'}"
        f"\n\n{_progress_text(state)}"
    )


async def cmd_remove_clip(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state_path: Path = context.bot_data["state_path"]
    state = _load_state(state_path)
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    if not context.args:
        await update.message.reply_text("Dùng: /remove_clip <clip_id>")
        return

    try:
        clip_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("clip_id phải là số")
        return

    removed = False
    for mk in list(state.get("records", {}).keys()):
        rows = state["records"][mk]
        before = len(rows)
        rows = [r for r in rows if int(r.get("id", -1)) != clip_id]
        if len(rows) != before:
            removed = True
            state["records"][mk] = rows
            break

    if not removed:
        await update.message.reply_text(f"Không tìm thấy clip #{clip_id}")
        return

    _save_state(state_path, state)
    await update.message.reply_text(f"Đã xóa clip #{clip_id}.\n\n{_progress_text(state)}")


async def cmd_list_clips(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    mk = _month_key()
    rows = state.get("records", {}).get(mk, [])
    if not rows:
        await update.message.reply_text(f"Tháng {mk} chưa có clip nào. Dùng /add_clip để thêm.")
        return

    rows_sorted = sorted(rows, key=lambda r: int(r.get("id", 0)))
    lines = [f"Danh sách clip tháng {mk}:"]
    for r in rows_sorted:
        lines.append(f"- #{r['id']} | {r['date']} | {int(r['views']):,} view")
    await update.message.reply_text("\n".join(lines))


async def cmd_nudge_now(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    await update.message.reply_text(_nudge_text(state))


async def cmd_sync_sheet_now(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    sync = GoogleSheetSync()
    result = sync.sync_midday(state)
    if result.ok:
        await update.message.reply_text(f"{result.message}. Đã ghi {result.rows_written} dòng.")
    else:
        await update.message.reply_text(result.message)


async def cmd_reset_month(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state_path: Path = context.bot_data["state_path"]
    state = _load_state(state_path)
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    mk = _month_key()
    if context.args:
        mk = context.args[0].strip()

    if mk not in state.get("records", {}):
        await update.message.reply_text(f"Không có dữ liệu cho tháng {mk}")
        return

    state["records"][mk] = []
    _save_state(state_path, state)
    await update.message.reply_text(f"Đã reset dữ liệu tháng {mk}.")


async def cmd_daily_plan(update: Update, context: CallbackContext) -> None:
    """Gợi ý kế hoạch content hôm nay dựa trên tiến độ KPI"""
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    mk = _month_key()
    stats = _stats_for_month(state, mk)
    days_total = _days_in_month(date.today().year, date.today().month)
    days_elapsed = date.today().day
    days_left = max(0, days_total - days_elapsed)

    clip_gap = max(0, TARGET_CLIPS - stats.clip_count)
    view_gap = max(0, TARGET_VIEWS - stats.total_views)

    plan = [
        f"📅 KẾ HOẠCH HÔM NAY ({date.today().isoformat()})",
        f"Tính đến nay: {stats.clip_count}/{TARGET_CLIPS} clip, {stats.total_views:,}/{TARGET_VIEWS:,} view",
        f"Còn {days_left} ngày, thiếu {clip_gap} clip và {view_gap:,} view\n",
    ]

    if days_left <= 3:
        plan.append("🔴 CHẾ ĐỘ KHẨN CẤP - Chỉ còn 3 ngày cuối tháng!")
        plan.append("Hôm nay BẮT BUỘC: đăng ít nhất 2 clip.")
        plan.append("- 1 clip hook mạnh (aim 10K+ view)")
        plan.append("- 1 clip trending/follow-up (aim 5K+ view)")
    elif clip_gap >= 3:
        plan.append("🟡 TẢI TRỌNG NẶNG - Đang out của plan")
        plan.append("Hôm nay BẮT BUỘC: 2 clip gồi nội dung mạnh nhất:")
        plan.append("- Clip 1 (12h-14h): Video giáo dục/trend (CTR cao)")
        plan.append("- Clip 2 (18h-20h): Video cá nhân/behind-the-scenes (engagement)")
    elif clip_gap >= 1:
        plan.append("🟠 CẢNH BÁO - Nên tăng tốc nhẹ")
        plan.append("Hôm nay: 1-2 clip chuẩn bị trước:")
        plan.append("- Ưu tiên chủ đề trending hoặc follow-up viral")
    else:
        plan.append("🟢 ĐÚNG TIẾN ĐỘ")
        plan.append("Hôm nay: 1 clip để giữ nhịp, không cần quá tải")

    plan.append("\n💡 Quy trình đăng:")
    plan.append("1. Quay video (tối thiểu 3 clip draft)")
    plan.append("2. Add hook strong trong 2 giây đầu")
    plan.append("3. Caption rõ CTA (like, follow, comment)")
    plan.append("4. Post lúc peak time (12h hoặc 20h)")
    plan.append("5. Dùng /add_clip <views> để update sau 12h")

    await update.message.reply_text("\n".join(plan))


async def cmd_weekly_review(update: Update, context: CallbackContext) -> None:
    """Tóm tắt tuần vừa rồi: performance & learnings"""
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    today = date.today()
    from datetime import timedelta
    week_ago = today - timedelta(days=7)
    
    # Lấy tất cả records từ state
    all_records = []
    for mk in state.get("records", {}):
        all_records.extend(state["records"][mk])
    
    # Lọc records trong 7 ngày vừa rồi
    week_records = []
    for r in all_records:
        try:
            r_date = datetime.fromisoformat(r.get('date', '')).date()
            if r_date >= week_ago:
                week_records.append(r)
        except (ValueError, TypeError):
            continue

    if not week_records:
        await update.message.reply_text("Tuần vừa rồi chưa có clip nào trong thống kê.")
        return

    total_clips = len(week_records)
    total_views = sum(int(r.get("views", 0)) for r in week_records)
    avg_views = total_views // total_clips if total_clips > 0 else 0
    top_clip = max(week_records, key=lambda r: int(r.get("views", 0))) if week_records else None

    review = [
        f"📊 WEEKLY REVIEW ({week_ago.isoformat()} → {today.isoformat()})",
        f"Tổng: {total_clips} clip, {total_views:,} view, avg {avg_views:,} view/clip\n",
    ]

    if top_clip:
        review.append(f"🔝 Top performer: #{top_clip['id']} ({top_clip['date']}) - {int(top_clip['views']):,} view")

    top_3 = sorted(week_records, key=lambda r: int(r.get("views", 0)), reverse=True)[:3]
    if top_3:
        review.append("\nTop 3 clip:")
        for i, clip in enumerate(top_3, 1):
            review.append(f"  {i}. #{clip['id']} - {int(clip['views']):,} view ({clip['date']})")

    low_performers = [r for r in week_records if int(r.get("views", 0)) < 1000]
    if low_performers:
        review.append(f"\n⚠️ Underperforming (<1K): {len(low_performers)} clip")

    review.append("\n📈 Learnings:")
    review.append("- Nếu top clip là trend/story: lặp lại format & timing")
    review.append("- Nếu underperforming: review hook, thumbnail, caption")
    review.append("- Tăng tần suất post nếu avg < 3K view")

    await update.message.reply_text("\n".join(review))


async def cmd_platform_snapshot(update: Update, context: CallbackContext) -> None:
    """Real-time status across TikTok, YouTube, Facebook"""
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    mk = _month_key()
    stats = _stats_for_month(state, mk)

    snapshot = [
        f"🎯 PLATFORM SNAPSHOT - {date.today().isoformat()}",
        "",
        "📱 TIKTOK (@loki_tran)",
        f"- Followers: 1,578 (from profile)",
        f"- Likes: 8,927 (all-time)",
        f"- Clip tháng: {stats.clip_count}",
        f"- View tháng: {stats.total_views:,}",
        f"- Top clip: {stats.clips_ge_10k} × 10K+, {stats.clips_ge_5k} × 5K+\n",
        
        "🎬 YOUTUBE (@GIP.FULFILLMENT)",
        "- Subscribers: 239",
        "- Status: 29 videos (mostly internal)\n",
        
        "📘 FACEBOOK (GIPVietNam)",
        "- Followers: 3,200",
        "- Status: Active\n",
        
        "🌐 WEBSITE (gipfulfillment.com)",
        "- Traffic: Monitor via Google Search Console",
        "- Status: WordPress + Rank Math SEO\n",
        
        "📊 OVERALL METRICS:",
        f"- Primary channel: TikTok (1.5K followers, growth target)",
        f"- Content focus: B2B fulfillment education for sellers",
        f"- CTA priority: Inbox → Zalo → Website contact form"
    ]

    await update.message.reply_text("\n".join(snapshot))


async def cmd_lead_report(update: Update, context: CallbackContext) -> None:
    """Báo cáo lead từ comment & engagement"""
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    report = [
        f"📞 LEAD REPORT - {date.today().isoformat()}",
        "",
        "🚀 CTA HIERARCHY:",
        "1. Inbox Fanpage → 1st priority (fastest conversion)",
        "2. Zalo (0372345694) → 2nd priority (direct message)",
        "3. Form tại gipfulfillment.com/lien-he → 3rd priority\n",
        
        "📋 HOW TO TRACK LEADS TODAY:",
        "- Check Inbox hàng ngày (Fanpage GIP Việt Nam)",
        "- Log new inquiries → /add_lead command (coming soon)",
        "- Update conversion status in Google Sheet",
        "- Follow-up via Zalo trong 2h\n",
        
        "🎯 LEAD QUALITY METRICS:",
        "- Inquiry source: Fanpage, Zalo, Website form",
        "- Seller type: New seller, scaling seller, brand owner",
        "- Service interest: Fulfillment, COD, Logistics, Telesale\n",
        
        "📊 CURRENT MONTH:",
        "- Tracks clips & views (not leads yet)",
        "- Leads will integrate when Google Sheet columns updated",
        "- Target: 50+ qualified inquiries/month\n",
        
        "💡 ACTION:",
        "- Setup: Add Zalo 0372345694 to quick contacts",
        "- Daily: Check Fanpage inbox & log in Sheet",
        "- Follow-up: Send quotation within 24h"
    ]

    await update.message.reply_text("\n".join(report))


async def cmd_content_ideas(update: Update, context: CallbackContext) -> None:
    """AI gợi ý chủ đề video dựa trên trending & history"""
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    mk = _month_key()
    stats = _stats_for_month(state, mk)

    ideas = [
        f"💡 CONTENT IDEAS - {date.today().isoformat()}",
        "",
        "🔥 TRENDING TOPICS (B2B Fulfillment Focus):",
        "1. 'Bán hàng sang Philippines - Gặp vấn đề gì?' (FAQ format)",
        "2. 'COD Philippines hoạt động thế nào?' (Explainer)",
        "3. 'Chi phí fulfillment thực tế khi gửi sang Philippines' (Numbers)",
        "4. 'Seller Việt đầu tiên kiếm 10M/tháng ở SG' (Story/case)",
        "5. 'Shopee vs TikTok Shop Philippines - nên chọn cái nào?' (Comparison)\n",
        
        "🎬 CONTENT PILLARS (Rotate hàng tuần):",
        "- Education (30%): How-to, explainers, tutorials",
        "- Stories (40%): Seller success, challenges, behind-the-scenes",
        "- Tips (20%): Quick wins, hacks, mistakes to avoid",
        "- CTA (10%): Direct call-to-action for leads\n",
        
        "📈 HOOK FORMULAS (High CTR):",
        "- 'Hóa ra [unexpected truth]' → retention hook",
        "- '[Number] seller Việt không biết cái này' → curiosity",
        "- 'Mình test [solution] và có kết quả...' → credibility",
        "- '[Pain point] - Giải pháp trong 60 giây' → quick value\n",
        
        "🎯 POST SCHEDULE (Mỗi tuần):",
        "- Mon 12h: Trending/education (high hook)",
        "- Wed 18h: Story/journey (high engagement)",
        "- Fri 20h: Quick tips (quick watch)",
        "- Sat 12h: CTA/lead gen (call to action)\n",
        
        "✨ THIS WEEK'S TOP 3 IDEAS:",
        "1. 'COD là gì + Ví dụ thực tế' (60s educational short)",
        "2. 'Mình kiểm tra 10 seller Việt bán lên Philippines' (story format)",
        "3. 'Sai lầm #1 khi fulfillment lần đầu' (mistake series)\n",
        
        "📌 NEXT STEPS:",
        "- Pick 1 idea today → Shoot by tonight",
        "- Add hook + caption tomorrow morning",
        "- Post at 12h or 20h → Update /add_clip after 6h"
    ]

    await update.message.reply_text("\n".join(ideas))


async def cmd_brief(update: Update, context: CallbackContext) -> None:
    """Generate and store a CONTENT BRIEF row from a topic."""
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    topic = " ".join(context.args).strip()
    if not topic:
        await update.message.reply_text("Dùng: /brief <chủ đề>\nVí dụ: /brief COD Philippines cho seller mới")
        return

    ok, msg = _sheet_add_brief(topic)
    if not ok:
        await update.message.reply_text(f"❌ {msg}")
        return

    b = _brief_generate(topic)
    await update.message.reply_text(
        "✅ Đã generate brief vào CONTENT BRIEF\n"
        f"- Chủ đề: {b['title']}\n"
        f"- Thumbnail: {b['thumbnail']}\n"
        f"- Caption: {b['caption']}\n"
        f"- Mô tả+hashtag: {b['desc']}\n"
        f"{msg}"
    )


def _phase2_status_text() -> str:
    logging.info("[DEBUG] _phase2_status_text() started")
    try:
        logging.info("[DEBUG] Creating TikTokMetricsProvider...")
        provider = TikTokMetricsProvider()
        logging.info(f"[DEBUG] Provider created: has rapidapi_key={bool(provider.rapidapi_key)}")
    except Exception as e:
        logging.error(f"[ERROR] Failed to create TikTokMetricsProvider: {e}", exc_info=True)
        return f"❌ Lỗi: {e}"
    
    rapid = "✅" if provider.rapidapi_key else "❌"
    paid_enabled = _paid_phase2_enabled()
    paid_ready = paid_enabled and provider.available()
    refresh_hours = max(1, int(os.getenv("TG_TIKTOK_REFRESH_HOURS", "12")))
    logging.info(f"[DEBUG] Status: paid_enabled={paid_enabled}, paid_ready={paid_ready}")
    return (
        "🧩 PHASE 2 STATUS\n"
        f"- Free-first mode: {'✅ Bật' if not paid_enabled else '⚠️ Tắt'}\n"
        f"- ENABLE_PAID_PHASE2: {'✅' if paid_enabled else '❌'}\n"
        f"- RAPIDAPI_KEY: {rapid}\n"
        f"- RAPIDAPI_HOST: {provider.host}\n"
        f"- Provider sẵn sàng: {'✅ Có' if paid_ready else '❌ Chưa'}\n"
        "- Detect clip mới: mỗi 6h\n"
        f"- Refresh stats: mỗi {refresh_hours}h\n"
        "- Test tay: /phase2_scan_now\n"
        "- Miễn phí: /free_refresh"
    )


def _run_free_refresh() -> tuple[bool, str]:
    """Refresh sheet-derived KPI/dashboard without any paid API provider."""
    sh = _sheet_open()
    if sh is None:
        return False, "Sheet chưa kết nối"

    dash_ok = _sheet_refresh_dashboard_loki(sh)
    kpi_ok = _sheet_apply_kpi_month_formulas(sh)

    if dash_ok and kpi_ok:
        return True, "Đã refresh Dashboard + KPI từ dữ liệu LỊCH ĐĂNG"
    if dash_ok:
        return True, "Đã refresh Dashboard (KPI chưa refresh được)"
    if kpi_ok:
        return True, "Đã refresh KPI (Dashboard chưa refresh được)"
    return False, "Không refresh được Dashboard/KPI"


async def cmd_free_status(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return
    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    paid_enabled = _paid_phase2_enabled()
    await update.message.reply_text(
        "🆓 FREE MODE STATUS\n"
        f"- Chế độ miễn phí: {'✅ Đang bật' if not paid_enabled else '⚠️ Đang tắt'}\n"
        "- Không dùng RapidAPI/TikAPI để tính KPI\n"
        "- Luồng dữ liệu chuẩn: SCRIPT(đã duyệt) -> LỊCH ĐĂNG -> KPI\n"
        "- Nguồn KPI: /add_clip, /log_clip, /log_stats + LỊCH ĐĂNG\n"
        "- Lệnh nên dùng: /free_refresh, /status, /weekly_review"
    )


async def cmd_free_refresh(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return
    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    ok, msg = _run_free_refresh()
    icon = "✅" if ok else "⚠️"
    await update.message.reply_text(f"{icon} {msg}")


async def cmd_phase2_status(update: Update, context: CallbackContext) -> None:
    logging.info(f"[DEBUG] cmd_phase2_status called from chat_id={update.effective_chat.id if update.effective_chat else 'None'}")
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        logging.warning("[DEBUG] Missing effective_chat or state_path")
        return
    logging.info("[DEBUG] Loading state...")
    state = _load_state(context.bot_data["state_path"])
    logging.info(f"[DEBUG] State loaded, checking authorization for chat_id={update.effective_chat.id}")
    if not _is_authorized(state, update.effective_chat.id):
        logging.warning(f"[DEBUG] Chat {update.effective_chat.id} not authorized")
        await update.message.reply_text(_reject_text())
        return
    logging.info("[DEBUG] Authorized, generating status text...")
    try:
        status_text = _phase2_status_text()
        logging.info(f"[DEBUG] Status text generated ({len(status_text)} chars)")
        await update.message.reply_text(status_text)
        logging.info("[DEBUG] Status reply sent successfully")
    except Exception as e:
        logging.error(f"[ERROR] Failed in cmd_phase2_status: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Lỗi: {e}")


async def cmd_phase2_scan_now(update: Update, context: CallbackContext) -> None:
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return
    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    provider = TikTokMetricsProvider()
    if not _paid_phase2_enabled() or not provider.available():
        ok, msg = _run_free_refresh()
        await update.message.reply_text(
            (
                f"{'✅' if ok else '⚠️'} Đang chạy FREE MODE: {msg}\n"
                "Để bật phase 2 trả phí: set ENABLE_PAID_PHASE2=true và thêm RAPIDAPI_KEY/TIKAPI_KEY.\n"
                "Dùng /phase2_status để kiểm tra lại."
            )
        )
        return

    await detect_new_clip_job(context)
    await refresh_tiktok_stats_job(context)
    await update.message.reply_text("✅ Đã chạy scan Phase 2 thủ công (detect + refresh).")


# ---------------------------------------------------------------------------
# Auto-job helpers: channel info, schedule link index, stats update
# ---------------------------------------------------------------------------

def _read_tiktok_loki_handle(sh) -> Optional[str]:
    """Read the active TikTok VN handle (@loki_tran) from the Thông tin tab."""
    try:
        ws = _ws_like(sh, "Thông tin")
        if ws is None:
            logging.warning("[Sheet] Thông tin tab not found")
            return None
        values = ws.get_all_values()
        # Find header row (has NỀN TẢNG and LINK)
        header_row_idx = None
        for i, row in enumerate(values, start=1):
            norm = [(c or "").strip().casefold() for c in row]
            if "nền tảng" in norm and "link" in norm:
                header_row_idx = i
                break
        if header_row_idx is None:
            # Fall back: scan all rows for tiktok.com/@
            for row in values:
                for cell in row:
                    m = re.search(r"tiktok\.com/@([^/?\s]+)", (cell or ""))
                    if m:
                        return m.group(1)
            return None
        header = values[header_row_idx - 1]
        c_platform = _find_col(header, "NỀN TẢNG", "Nền tảng")
        c_link = _find_col(header, "LINK", "Link")
        c_status = _find_col(header, "TRẠNG THÁI", "Trạng thái")
        for row in values[header_row_idx:]:
            platform = (row[c_platform] if c_platform is not None and c_platform < len(row) else "").strip()
            link = (row[c_link] if c_link is not None and c_link < len(row) else "").strip()
            status = (row[c_status] if c_status is not None and c_status < len(row) else "").strip()
            if ("tiktok" in platform.lower() and "vn" in platform.lower()) or "tiktok vn" in platform.lower():
                if "hoạt động" in status.lower() or "hoat dong" in status.lower():
                    m = re.search(r"tiktok\.com/@([^/?\s]+)", link)
                    if m:
                        return m.group(1)
        return None
    except Exception as exc:
        logging.warning(f"[Sheet] _read_tiktok_loki_handle error: {exc}")
        return None


def _sheet_read_schedule_links(sh) -> Dict[str, int]:
    """Return {tiktok_vn_link: clip_id} for rows that already have a TikTok link."""
    result: Dict[str, int] = {}
    try:
        ws = _ws_like(sh, "LỊCH ĐĂNG")
        if ws is None:
            return result
        values = ws.get_all_values()
        header_row_idx = _find_schedule_header_row(values)
        if header_row_idx is None:
            return result
        header = values[header_row_idx - 1]
        c_num = _find_col(header, "CLIP #", "#")
        c_link = _find_col(header, "LINK TikTok VN", "LINK TikTok")
        if c_num is None or c_link is None:
            return result
        for row in values[header_row_idx:]:
            num = (row[c_num] if c_num < len(row) else "").strip()
            link = (row[c_link] if c_link < len(row) else "").strip()
            if num and link and "tiktok.com" in link:
                try:
                    result[link] = int(num)
                except ValueError:
                    pass
        return result
    except Exception as exc:
        logging.warning(f"[Sheet] _sheet_read_schedule_links error: {exc}")
        return result


def _next_available_schedule_id(sh) -> Optional[int]:
    """Return the lowest clip # in LỊCH ĐĂNG that has no NGÀY ĐĂNG yet."""
    try:
        ws = _ws_like(sh, "LỊCH ĐĂNG")
        if ws is None:
            return None
        values = ws.get_all_values()
        header_row_idx = _find_schedule_header_row(values)
        if header_row_idx is None:
            return None
        header = values[header_row_idx - 1]
        c_num = _find_col(header, "CLIP #", "#")
        c_date = _find_col(header, "NGÀY ĐĂNG")
        if c_num is None:
            return None
        for row in values[header_row_idx:]:
            num = (row[c_num] if c_num < len(row) else "").strip()
            date_val = (row[c_date] if c_date is not None and c_date < len(row) else "").strip() if c_date else ""
            if num and not date_val:
                try:
                    return int(num)
                except ValueError:
                    pass
        return None
    except Exception as exc:
        logging.warning(f"[Sheet] _next_available_schedule_id error: {exc}")
        return None


def _sheet_auto_update_stats(
    sh,
    clip_id: int,
    views: int,
    likes: int,
    comments: int,
    shares: int,
) -> bool:
    """Auto-refresh stats from API — updates numbers + agent note, does NOT mark Đã check 2h?."""
    try:
        ws = _ws_like(sh, "LỊCH ĐĂNG")
        if ws is None:
            return False
        values = ws.get_all_values()
        header_row_idx = _find_schedule_header_row(values)
        if header_row_idx is None:
            return False
        header = values[header_row_idx - 1]
        c_num = _find_col(header, "CLIP #", "#")
        c_view2h = _find_col(header, "View 2h")
        c_like2h = _find_col(header, "Like 2h")
        c_cmt2h = _find_col(header, "Comment 2h")
        c_share2h = _find_col(header, "Share 2h")
        c_agent = _find_col(header, "Agent note")

        target_row = None
        for r_idx in range(header_row_idx + 1, len(values) + 1):
            row = values[r_idx - 1] if r_idx - 1 < len(values) else []
            if (row[c_num] if c_num is not None and c_num < len(row) else "").strip() == str(clip_id):
                target_row = r_idx
                break
        if target_row is None:
            return False

        updates = []
        for col, val in [(c_view2h, views), (c_like2h, likes), (c_cmt2h, comments), (c_share2h, shares)]:
            if col is not None:
                updates.append({"range": gspread.utils.rowcol_to_a1(target_row, col + 1), "values": [[val]]})
        if c_agent is not None:
            eng = round((likes + comments + shares) / max(views, 1) * 100, 1)
            note = f"🤖 Auto {datetime.now().strftime('%d/%m %H:%M')} | Eng {eng}%"
            if views >= 10000:
                note += " 🔥"
            elif views >= 5000:
                note += " ✅"
            elif views > 0 and views < 1000:
                note += " ⚠️"
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_agent + 1), "values": [[note]]})
        if updates:
            ws.batch_update(updates, value_input_option="RAW")
        return True
    except Exception as exc:
        logging.warning(f"[Sheet] _sheet_auto_update_stats error: {exc}")
        return False


class TikTokMetricsProvider:
    """RapidAPI TikTok adapter.

    Set env var RAPIDAPI_KEY to activate.
    Optionally set RAPIDAPI_TIKTOK_HOST to override the default API host.

    Default host: tiktok-data-api.p.rapidapi.com
    Endpoints used:
      GET /user/posts?username=<handle>&count=20  → recent videos
      GET /video/info?video_id=<id>               → single video stats
    """

    _DEFAULT_HOST = "tiktok-data-api.p.rapidapi.com"

    def __init__(self) -> None:
        self.rapidapi_key: str = os.getenv("RAPIDAPI_KEY", "").strip()
        self.host: str = os.getenv("RAPIDAPI_TIKTOK_HOST", self._DEFAULT_HOST).strip()

    def available(self) -> bool:
        return bool(self.rapidapi_key)

    def _is_tiktok_api23(self) -> bool:
        return "tiktok-api23" in (self.host or "")

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: Dict[str, str]) -> Optional[Any]:
        """GET request to RapidAPI. Returns parsed JSON or None on error."""
        url = f"https://{self.host}{endpoint}"
        headers = {
            "X-RapidAPI-Key": self.rapidapi_key,
            "X-RapidAPI-Host": self.host,
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            logging.warning(f"[RapidAPI] {resp.status_code} from {endpoint}: {resp.text[:300]}")
            return None
        except requests.RequestException as exc:
            logging.warning(f"[RapidAPI] Request failed ({endpoint}): {exc}")
            return None

    # ------------------------------------------------------------------
    # Response normalizer — handles multiple common RapidAPI formats
    # ------------------------------------------------------------------

    def _normalize_video(self, raw: Any, handle: str = "") -> Optional[Dict[str, Any]]:
        """Normalize a raw video object from various RapidAPI response shapes."""
        if not isinstance(raw, dict):
            return None

        # Video ID
        vid_id = str(raw.get("id") or raw.get("video_id") or raw.get("aweme_id") or "").strip()
        if not vid_id:
            return None

        # Title / description
        title = str(
            raw.get("desc") or raw.get("description") or raw.get("title") or raw.get("video_description") or ""
        ).strip()

        # Creation timestamp
        create_ts_raw = (
            raw.get("createTime") or raw.get("create_time") or raw.get("created_at")
            or (raw.get("video") or {}).get("create_time") or 0
        )
        try:
            create_ts = int(create_ts_raw)
        except (TypeError, ValueError):
            create_ts = 0

        # Stats block — try several common keys
        stats = (
            raw.get("stats") or raw.get("statistics") or raw.get("statistics_v2")
            or raw.get("stats_v2") or {}
        )
        def _int(d: dict, *keys: str) -> int:
            for k in keys:
                v = d.get(k)
                if v is not None:
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        pass
            return 0

        views = _int(stats, "playCount", "play_count", "viewCount", "view_count")
        likes = _int(stats, "diggCount", "like_count", "likeCount", "heart_count")
        comments = _int(stats, "commentCount", "comment_count")
        shares = _int(stats, "shareCount", "share_count")

        handle_clean = handle.lstrip("@") if handle else "loki_tran"
        link = f"https://www.tiktok.com/@{handle_clean}/video/{vid_id}"
        posted_at = datetime.fromtimestamp(create_ts) if create_ts > 0 else None

        return {
            "video_id": vid_id,
            "title": title,
            "link": link,
            "posted_at": posted_at,
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_recent_posts(self, handle: str, hours_back: int = 6) -> List[Dict[str, Any]]:
        """Fetch recent videos for *handle*. Returns normalized dicts posted within *hours_back* hours."""
        if not self.available():
            return []
        handle_clean = handle.lstrip("@")
        if self._is_tiktok_api23():
            info = self._get("/api/user/info", {"uniqueId": handle_clean})
            if info is None:
                return []
            sec_uid = (
                ((info.get("userInfo") or {}).get("user") or {}).get("secUid")
                or info.get("secUid")
                or ""
            )
            if not sec_uid:
                logging.warning(f"[RapidAPI] Missing secUid for @{handle_clean}")
                return []
            data = self._get("/api/user/posts", {"secUid": sec_uid, "count": "20", "cursor": "0"})
        else:
            data = self._get("/user/posts", {"username": handle_clean, "count": "20"})
        if data is None:
            return []
        # Unwrap common response envelopes
        items: List[Any] = (
            (data.get("data") or {}).get("itemList")
            or
            (data.get("data") or {}).get("videos")
            or (data.get("data") or {}).get("items")
            or data.get("videos")
            or data.get("items")
            or data.get("aweme_list")
            or []
        )
        cutoff = datetime.now() - timedelta(hours=hours_back)
        results: List[Dict[str, Any]] = []
        for raw in items:
            norm = self._normalize_video(raw, handle_clean)
            if norm and norm["posted_at"] and norm["posted_at"] >= cutoff:
                results.append(norm)
        logging.info(f"[RapidAPI] detect_recent_posts @{handle_clean}: {len(items)} total, {len(results)} in last {hours_back}h")
        return results

    def fetch_post_metrics(self, post_url: str) -> Optional[Dict[str, int]]:
        """Fetch current stats for a TikTok video URL. Returns {views, likes, comments, shares}."""
        if not self.available():
            return None
        # Extract video_id from URL: .../video/1234567890
        m = re.search(r"/video/(\d+)", post_url)
        if not m:
            logging.warning(f"[RapidAPI] Cannot parse video_id from URL: {post_url}")
            return None
        video_id = m.group(1)
        # Extract handle from URL for proper link reconstruction
        hm = re.search(r"tiktok\.com/@([^/?\s]+)", post_url)
        handle = hm.group(1) if hm else "loki_tran"

        if self._is_tiktok_api23():
            data = self._get("/api/post/detail", {"videoId": video_id})
        else:
            data = self._get("/video/info", {"video_id": video_id})
        if data is None:
            return None
        # Unwrap common response envelopes
        raw: Any = (
            ((data.get("itemInfo") or {}).get("itemStruct"))
            or
            data.get("data")
            or data.get("video")
            or data.get("aweme_detail")
            or data
        )
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        norm = self._normalize_video(raw, handle)
        if norm:
            return {
                "views": norm["views"],
                "likes": norm["likes"],
                "comments": norm["comments"],
                "shares": norm["shares"],
            }
        return None


async def kpi_formula_refresh_job(context: CallbackContext) -> None:
    """Refresh KPI monthly formulas from LỊCH ĐĂNG."""
    sh = _sheet_open()
    if sh is None:
        return
    _sheet_apply_kpi_month_formulas(sh)


def _last_completed_week() -> tuple[date, date]:
    today = date.today()
    current_week_start = today - timedelta(days=today.weekday())
    last_week_end = current_week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)
    return last_week_start, last_week_end


async def saturday_weekly_report_job(context: CallbackContext) -> None:
    """Generate BÁO CÁO TUẦN and push summary to Telegram at Saturday 08:00."""
    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)
    chat_id = state.get("authorized_chat_id")
    if chat_id is None:
        return

    sh = _sheet_open()
    if sh is None:
        await context.bot.send_message(chat_id=int(chat_id), text="⚠️ Không mở được sheet để tổng hợp BÁO CÁO TUẦN.")
        return

    ws, we = _last_completed_week()
    ok = _sheet_fill_weekly_report(sh, ws, we)
    if not ok:
        await context.bot.send_message(chat_id=int(chat_id), text="⚠️ Không ghi được BÁO CÁO TUẦN từ LỊCH ĐĂNG.")
        return

    await context.bot.send_message(
        chat_id=int(chat_id),
        text=(
            f"📮 BÁO CÁO TUẦN đã cập nhật\n"
            f"- Tuần: {ws.strftime('%d/%m/%Y')} → {we.strftime('%d/%m/%Y')}\n"
            f"- Sheet: BÁO CÁO TUẦN\n"
            f"- Nguồn: LỊCH ĐĂNG"
        ),
    )


async def weekly_report_sync_only_job(context: CallbackContext) -> None:
    """Daily sync that updates only tab BÁO CÁO TUẦN."""
    sh = _sheet_open()
    if sh is None:
        logging.warning("[WeeklyReport] Cannot open Google Sheet")
        return

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = today
    ok = _sheet_fill_weekly_report(sh, week_start, week_end)
    if not ok:
        logging.warning("[WeeklyReport] Sync failed")
        return

    # Optional Telegram note to authorized chat
    try:
        app = context.application
        state_path: Path = app.bot_data["state_path"]
        state = _load_state(state_path)
        chat_id = state.get("authorized_chat_id")
        if chat_id is not None:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    "Đã cập nhật BÁO CÁO TUẦN (chỉ tab này).\n"
                    f"Tuần: {week_start.strftime('%d/%m/%Y')} -> {week_end.strftime('%d/%m/%Y')}"
                ),
            )
    except Exception as exc:
        logging.warning(f"[WeeklyReport] Notify failed: {exc}")


def _auto_job_notify_enabled() -> bool:
    raw = os.getenv("AUTO_JOB_NOTIFY_TELEGRAM", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


async def _notify_auto_job(context: CallbackContext, title: str, details: List[str]) -> None:
    """Send auto-job summary to authorized chat if enabled."""
    if not _auto_job_notify_enabled():
        return
    try:
        app = context.application
        state_path: Path = app.bot_data["state_path"]
        state = _load_state(state_path)
        chat_id = state.get("authorized_chat_id")
        if chat_id is None:
            return
        text = f"🤖 {title}\n" + "\n".join(f"- {x}" for x in details)
        await context.bot.send_message(chat_id=int(chat_id), text=text)
    except Exception as exc:
        logging.warning(f"[AutoJob] notify failed: {exc}")


async def detect_new_clip_job(context: CallbackContext) -> None:
    """Every 6h: crawl @loki_tran, detect new videos, auto-write to LỊCH ĐĂNG.

    Activates automatically when RAPIDAPI_KEY is set.
    No manual commands needed from Kenny.
    """
    logging.info("[TikTok] detect_new_clip_job started")
    provider = TikTokMetricsProvider()
    if not provider.available():
        logging.info("[TikTok] detect_new_clip_job: RAPIDAPI_KEY not set — skip")
        await _notify_auto_job(
            context,
            "Detect clip mới (6h)",
            ["Trạng thái: skip", "Lý do: chưa có RAPIDAPI_KEY"],
        )
        return

    sh = _sheet_open()
    if sh is None:
        logging.warning("[TikTok] detect_new_clip_job: cannot open Google Sheet")
        await _notify_auto_job(
            context,
            "Detect clip mới (6h)",
            ["Trạng thái: lỗi", "Lý do: không mở được Google Sheet"],
        )
        return

    # Read channel handle from Thông tin tab
    handle = _read_tiktok_loki_handle(sh)
    if not handle:
        logging.warning("[TikTok] detect_new_clip_job: no active TikTok VN channel in Thông tin tab")
        await _notify_auto_job(
            context,
            "Detect clip mới (6h)",
            ["Trạng thái: lỗi", "Lý do: không tìm thấy TikTok VN active trong tab Thông tin"],
        )
        return

    # Index of links already recorded in LỊCH ĐĂNG
    existing_links = _sheet_read_schedule_links(sh)

    # Fetch videos posted in last 6h
    recent_videos = provider.detect_recent_posts(handle, hours_back=6)
    if not recent_videos:
        logging.info(f"[TikTok] detect_new_clip_job: no new videos in last 6h for @{handle}")
        await _notify_auto_job(
            context,
            "Detect clip mới (6h)",
            [f"Kênh: @{handle}", "Video mới trong 6h: 0", "Đã ghi vào LỊCH ĐĂNG: 0"],
        )
        return

    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)

    new_clips: List[str] = []
    duplicate_count = 0
    failed_count = 0
    slot_exhausted = False
    # Sort oldest-first so clip IDs are assigned in chronological order
    for video in sorted(recent_videos, key=lambda v: v["posted_at"] or datetime.min):
        if video["link"] in existing_links:
            logging.info(f"[TikTok] Already in sheet, skip: {video['link']}")
            duplicate_count += 1
            continue

        # Find next pre-numbered empty row in LỊCH ĐĂNG
        clip_id = _next_available_schedule_id(sh)
        if clip_id is None:
            logging.warning("[TikTok] detect_new_clip_job: no empty slot in LỊCH ĐĂNG, stop")
            slot_exhausted = True
            break

        posted_at = video["posted_at"] or datetime.now()
        ok = _sheet_write_clip(
            clip_id=clip_id,
            clip_day=posted_at.date(),
            posted_at=posted_at,
            title=video["title"],
            link=video["link"],
        )
        if not ok:
            logging.warning(f"[TikTok] detect_new_clip_job: failed to write clip #{clip_id}")
            failed_count += 1
            continue

        # Update state
        state.setdefault("clips", {})[str(clip_id)] = {
            "id": clip_id,
            "title": video["title"],
            "date": posted_at.strftime("%Y-%m-%d"),
            "views": video["views"],
            "likes": video["likes"],
            "comments": video["comments"],
            "shares": video["shares"],
            "link": video["link"],
        }
        if clip_id >= state.get("next_id", 1):
            state["next_id"] = clip_id + 1
        _save_state(state_path, state)

        new_clips.append(f"#{clip_id} — {video['title'][:40] or video['link']}")
        logging.info(f"[TikTok] Auto-detected new clip #{clip_id}: {video['title'][:60]}")

    # Notify Kenny on Telegram
    if new_clips:
        chat_id = state.get("authorized_chat_id")
        if chat_id:
            lines = "\n".join(f"• {c}" for c in new_clips)
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"🆕 Phát hiện {len(new_clips)} clip mới từ @{handle}!\n"
                    f"Đã ghi vào LỊCH ĐĂNG tự động:\n{lines}"
                ),
            )

    summary_lines = [
        f"Kênh: @{handle}",
        f"Video API trả về (6h): {len(recent_videos)}",
        f"Đã có sẵn trong sheet: {duplicate_count}",
        f"Ghi mới thành công: {len(new_clips)}",
        f"Ghi lỗi: {failed_count}",
    ]
    if slot_exhausted:
        summary_lines.append("Cảnh báo: hết slot trống trong LỊCH ĐĂNG")
    await _notify_auto_job(context, "Detect clip mới (6h)", summary_lines)


async def refresh_tiktok_stats_job(context: CallbackContext) -> None:
    """Every 12h: fetch current stats for all clips with TikTok links in LỊCH ĐĂNG.

    Activates automatically when RAPIDAPI_KEY is set.
    Updates views/likes/comments/shares + refreshes Dashboard & KPI.
    """
    logging.info("[TikTok] refresh_tiktok_stats_job started")
    provider = TikTokMetricsProvider()
    if not provider.available():
        logging.info("[TikTok] refresh_tiktok_stats_job: RAPIDAPI_KEY not set — skip")
        await _notify_auto_job(
            context,
            "Refresh stats (12h)",
            ["Trạng thái: skip", "Lý do: chưa có RAPIDAPI_KEY"],
        )
        return

    sh = _sheet_open()
    if sh is None:
        logging.warning("[TikTok] refresh_tiktok_stats_job: cannot open Google Sheet")
        await _notify_auto_job(
            context,
            "Refresh stats (12h)",
            ["Trạng thái: lỗi", "Lý do: không mở được Google Sheet"],
        )
        return

    link_to_id = _sheet_read_schedule_links(sh)
    if not link_to_id:
        logging.info("[TikTok] refresh_tiktok_stats_job: no TikTok links found in LỊCH ĐĂNG")
        await _notify_auto_job(
            context,
            "Refresh stats (12h)",
            ["Link TikTok trong LỊCH ĐĂNG: 0", "Clip update thành công: 0"],
        )
        return

    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)

    updated = 0
    no_data = 0
    write_failed = 0
    for link, clip_id in link_to_id.items():
        metrics = provider.fetch_post_metrics(link)
        if metrics is None:
            logging.warning(f"[TikTok] refresh_stats: no data for clip #{clip_id} ({link})")
            no_data += 1
            continue
        ok = _sheet_auto_update_stats(
            sh, clip_id,
            metrics["views"], metrics["likes"],
            metrics["comments"], metrics["shares"],
        )
        if ok:
            updated += 1
            # Update state too
            clip_state = state.get("clips", {}).get(str(clip_id), {})
            clip_state.update({"views": metrics["views"], "likes": metrics["likes"],
                               "comments": metrics["comments"], "shares": metrics["shares"]})
            state.setdefault("clips", {})[str(clip_id)] = clip_state
        else:
            write_failed += 1

    if updated:
        _save_state(state_path, state)
        _sheet_refresh_dashboard_loki(sh)
        _sheet_apply_kpi_month_formulas(sh)
        logging.info(f"[TikTok] refresh_tiktok_stats_job: updated {updated}/{len(link_to_id)} clips")

    await _notify_auto_job(
        context,
        "Refresh stats (12h)",
        [
            f"Link TikTok cần update: {len(link_to_id)}",
            f"Update thành công: {updated}",
            f"Không lấy được data API: {no_data}",
            f"Ghi sheet lỗi: {write_failed}",
        ],
    )


async def cmd_report_now(update: Update, context: CallbackContext) -> None:
    """Fill BÁO CÁO TUẦN cho tuần đang chạy (T2 → hôm nay)."""
    if update.effective_chat is None:
        return
    state = _load_state(context.bot_data["state_path"])
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end = today

    sh = _sheet_open()
    if sh is None:
        await update.message.reply_text("⚠️ Không mở được sheet.")
        return

    ok = _sheet_fill_weekly_report(sh, week_start, week_end)
    if ok:
        await update.message.reply_text(
            f"✅ BÁO CÁO TUẦN đã cập nhật\n"
            f"Tuần: {week_start.strftime('%d/%m/%Y')} → {week_end.strftime('%d/%m/%Y')} (đến hôm nay)\n"
            f"Mở sheet để xem kết quả."
        )
    else:
        await update.message.reply_text("⚠️ Ghi BÁO CÁO TUẦN thất bại.")


async def morning_kpi_warning_job(context: CallbackContext) -> None:
    """Gửi cảnh báo KPI mỗi sáng 8:30 — tiến độ tuần."""
    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)
    chat_id = state.get("authorized_chat_id")
    if chat_id is None:
        return
    text = f"☀️ BÁO CÁO SÁNG\n\n{_kpi_warning_text(state)}\n\n{_progress_text(state)}"
    await context.bot.send_message(chat_id=int(chat_id), text=text)


async def friday_kpi_warning_job(context: CallbackContext) -> None:
    """Gửi cảnh báo mỗi Thứ 6 17:00 nếu tuần chưa đủ clip."""
    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)
    chat_id = state.get("authorized_chat_id")
    if chat_id is None:
        return

    # Only send if not yet meeting weekly target
    week_clips = _week_clips_count(state)
    if week_clips >= WEEKLY_TARGET:
        return

    missing = WEEKLY_TARGET - week_clips
    text = (
        f"🔔 NHẮC CUỐI TUẦN — THỨ 6 17H\n"
        f"Tuần này: {week_clips}/{WEEKLY_TARGET} clip\n"
        f"Còn thiếu {missing} clip — còn 2 ngày (T7 & CN) để bắt kịp!\n\n"
        f"{_progress_text(state)}"
    )
    await context.bot.send_message(chat_id=int(chat_id), text=text)


async def reminder_job(context: CallbackContext) -> None:
    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)
    chat_id = state.get("authorized_chat_id")
    if chat_id is None:
        return

    text = _nudge_text(state)
    await context.bot.send_message(chat_id=int(chat_id), text=text)


async def morning_status_job(context: CallbackContext) -> None:
    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)
    chat_id = state.get("authorized_chat_id")
    if chat_id is None:
        return

    text = f"{_today_report_text(state)}\n\n{_progress_text(state)}"
    await context.bot.send_message(chat_id=int(chat_id), text=text)


async def midday_sync_job(context: CallbackContext) -> None:
    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)
    chat_id = state.get("authorized_chat_id")
    if chat_id is None:
        return

    sync = GoogleSheetSync()
    result = sync.sync_midday(state)
    if result.ok:
        await context.bot.send_message(
            chat_id=int(chat_id),
            text=f"{result.message}. Đã ghi {result.rows_written} dòng.",
        )
    else:
        await context.bot.send_message(chat_id=int(chat_id), text=result.message)


async def evening_kpi_job(context: CallbackContext) -> None:
    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)
    chat_id = state.get("authorized_chat_id")
    if chat_id is None:
        return

    recap = f"{_today_report_text(state)}\n\n{_kpi_warning_text(state)}\n\n{_progress_text(state)}"
    text = build_evening_kpi_text(recap)
    await context.bot.send_message(chat_id=int(chat_id), text=text)


async def no_clip_24h_alert_job(context: CallbackContext) -> None:
    """Alert if there is no new clip for at least 24 hours."""
    app = context.application
    state_path: Path = app.bot_data["state_path"]
    state = _load_state(state_path)
    chat_id = state.get("authorized_chat_id")
    if chat_id is None:
        return

    latest = _latest_clip_datetime(state)
    if latest is None:
        return

    now_dt = datetime.now()
    hours_since = (now_dt - latest).total_seconds() / 3600
    if hours_since < 24:
        return

    cooldown_hours = max(1, int(os.getenv("TG_NO_CLIP_ALERT_COOLDOWN_HOURS", "12")))
    last_alert_raw = str(state.get("last_no_clip_alert_at", "") or "").strip()
    if last_alert_raw:
        try:
            last_alert = datetime.fromisoformat(last_alert_raw)
            if (now_dt - last_alert).total_seconds() < cooldown_hours * 3600:
                return
        except ValueError:
            pass

    text = (
        "CANH BAO 24H KHONG CO CLIP MOI\n"
        f"- Clip gan nhat: {latest.strftime('%d/%m/%Y %H:%M')}\n"
        f"- Da qua: {int(hours_since)} gio\n"
        "- De xuat: dang toi thieu 1 clip trong hom nay de giu nhip KPI.\n\n"
        f"{_progress_text(state)}"
    )
    await context.bot.send_message(chat_id=int(chat_id), text=text)
    state["last_no_clip_alert_at"] = now_dt.isoformat(timespec="seconds")
    _save_state(state_path, state)


def _parse_reminder_times() -> List[tuple[int, int]]:
    raw = os.getenv("TG_REMINDER_TIMES", DEFAULT_REMINDER_TIMES)
    result: List[tuple[int, int]] = []
    for piece in raw.split(","):
        item = piece.strip()
        if not item:
            continue
        try:
            hh, mm = item.split(":", 1)
            hhi = int(hh)
            mmi = int(mm)
            if not (0 <= hhi <= 23 and 0 <= mmi <= 59):
                continue
            result.append((hhi, mmi))
        except Exception:
            continue

    if not result:
        result = [(9, 0), (13, 0), (20, 30)]
    return result


def _register_jobs(app: Application) -> None:
    jq = app.job_queue
    if jq is None:
        return

    times = _parse_reminder_times()
    for hour, minute in times:
        jq.run_daily(reminder_job, time=datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time())

    morning_hour = int(os.getenv("TG_MORNING_STATUS_HOUR", "8"))
    morning_minute = int(os.getenv("TG_MORNING_STATUS_MINUTE", "30"))
    jq.run_daily(
        morning_status_job,
        time=datetime.strptime(f"{morning_hour:02d}:{morning_minute:02d}", "%H:%M").time(),
    )

    sync_hour = int(os.getenv("TG_SHEET_SYNC_HOUR", "12"))
    sync_minute = int(os.getenv("TG_SHEET_SYNC_MINUTE", "0"))
    jq.run_daily(
        midday_sync_job,
        time=datetime.strptime(f"{sync_hour:02d}:{sync_minute:02d}", "%H:%M").time(),
    )

    evening_hour = int(os.getenv("TG_EVENING_REPORT_HOUR", "17"))
    evening_minute = int(os.getenv("TG_EVENING_REPORT_MINUTE", "0"))
    jq.run_daily(
        evening_kpi_job,
        time=datetime.strptime(f"{evening_hour:02d}:{evening_minute:02d}", "%H:%M").time(),
    )

    # Morning KPI warning 8:30 every day
    jq.run_daily(
        morning_kpi_warning_job,
        time=datetime.strptime("08:30", "%H:%M").time(),
    )

    # Friday 17:00 weekly warning
    import datetime as _dt
    jq.run_daily(
        friday_kpi_warning_job,
        time=_dt.time(17, 0),
        days=(4,),  # 4 = Friday (APScheduler: 0=Mon)
    )

    # Daily KPI formula refresh (from LỊCH ĐĂNG -> KPI monthly table)
    jq.run_daily(
        kpi_formula_refresh_job,
        time=datetime.strptime("00:10", "%H:%M").time(),
    )

    # Saturday 08:00: build weekly report and send Telegram summary
    jq.run_daily(
        saturday_weekly_report_job,
        time=datetime.strptime("08:00", "%H:%M").time(),
        days=(5,),  # Saturday
    )

    # Daily: sync only BÁO CÁO TUẦN (no updates to other tabs)
    weekly_sync_hour = int(os.getenv("TG_WEEKLY_SYNC_HOUR", "18"))
    weekly_sync_minute = int(os.getenv("TG_WEEKLY_SYNC_MINUTE", "10"))
    jq.run_daily(
        weekly_report_sync_only_job,
        time=datetime.strptime(f"{weekly_sync_hour:02d}:{weekly_sync_minute:02d}", "%H:%M").time(),
    )

    # Every 6h: detect new clips (provider placeholder)
    jq.run_repeating(
        detect_new_clip_job,
        interval=timedelta(hours=6),
        first=timedelta(seconds=30),
    )

    # Refresh stats interval can be tuned by env (default 12h = 2 times/day)
    refresh_hours = max(1, int(os.getenv("TG_TIKTOK_REFRESH_HOURS", "12")))
    jq.run_repeating(
        refresh_tiktok_stats_job,
        interval=timedelta(hours=refresh_hours),
        first=timedelta(seconds=90),
    )

    # Every N hours: alert if no new clip in 24h (anti-spam via cooldown)
    no_clip_check_hours = max(1, int(os.getenv("TG_NO_CLIP_ALERT_CHECK_HOURS", "2")))
    jq.run_repeating(
        no_clip_24h_alert_job,
        interval=timedelta(hours=no_clip_check_hours),
        first=timedelta(minutes=15),
    )


def build_application(token: str, workspace_root: Path) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["state_path"] = _state_file(workspace_root)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("add_clip", cmd_add_clip))
    app.add_handler(CommandHandler("log_clip", cmd_log_clip))
    app.add_handler(CommandHandler("set_views", cmd_set_views))
    app.add_handler(CommandHandler("remove_clip", cmd_remove_clip))
    app.add_handler(CommandHandler("list_clips", cmd_list_clips))
    app.add_handler(CommandHandler("nudge_now", cmd_nudge_now))
    app.add_handler(CommandHandler("sync_sheet_now", cmd_sync_sheet_now))
    app.add_handler(CommandHandler("reset_month", cmd_reset_month))
    
    # TikTok assistant commands
    app.add_handler(CommandHandler("daily_plan", cmd_daily_plan))
    app.add_handler(CommandHandler("weekly_review", cmd_weekly_review))
    app.add_handler(CommandHandler("platform_snapshot", cmd_platform_snapshot))
    app.add_handler(CommandHandler("lead_report", cmd_lead_report))
    app.add_handler(CommandHandler("content_ideas", cmd_content_ideas))
    app.add_handler(CommandHandler("log_stats", cmd_log_stats))
    app.add_handler(CommandHandler("brief", cmd_brief))
    app.add_handler(CommandHandler("free_status", cmd_free_status))
    app.add_handler(CommandHandler("free_refresh", cmd_free_refresh))
    app.add_handler(CommandHandler("phase2_status", cmd_phase2_status))
    app.add_handler(CommandHandler("report_now", cmd_report_now))
    app.add_handler(CommandHandler("phase2_scan_now", cmd_phase2_scan_now))

    _register_jobs(app)
    return app


def _sync_state_from_sheet(state_path: Path) -> None:
    """On startup, rebuild state.json records from LỊCH ĐĂNG sheet.

    This ensures Railway ephemeral filesystem losses don't zero out KPI data.
    Only fills records that are missing locally — does not overwrite existing.
    """
    sh = _sheet_open()
    if sh is None:
        logging.warning("[Sync] Cannot open sheet for startup state sync")
        return

    try:
        rows, header, header_row_idx = _collect_schedule_rows(sh)
        if rows is None or header is None:
            logging.warning("[Sync] LỊCH ĐĂNG header not found")
            return

        c_num = _find_col(header, "CLIP #", "#")
        c_title = _find_col(header, "CHỦ ĐỀ / TIÊU ĐỀ", "TIÊU ĐỀ")
        c_date = _find_col(header, "NGÀY ĐĂNG")
        c_view2h = _find_col(header, "View 2h")
        c_posted = _find_col(header, "Giờ đăng thực tế")

        if c_num is None or c_date is None:
            return

        state = _load_state(state_path)
        max_id = int(state.get("next_clip_id", 1)) - 1
        added = 0

        for row in rows:
            num_val = (row[c_num] if c_num < len(row) else "").strip()
            if not num_val:
                continue
            try:
                clip_id = int(num_val)
            except ValueError:
                continue

            d = _parse_sheet_date(row[c_date] if c_date < len(row) else "")
            if d is None:
                continue

            mk = _month_key(d)
            state.setdefault("records", {})
            state["records"].setdefault(mk, [])

            # Skip if already recorded locally
            existing_ids = {int(r.get("id", -1)) for r in state["records"][mk]}
            if clip_id in existing_ids:
                continue

            views = _to_int_safe(row[c_view2h] if c_view2h is not None and c_view2h < len(row) else "")
            title = (row[c_title] if c_title is not None and c_title < len(row) else "").strip()

            state["records"][mk].append({
                "id": clip_id,
                "date": d.isoformat(),
                "views": views,
                "title": title,
                "created_at": d.isoformat(),
                "source": "sheet_sync",
            })
            max_id = max(max_id, clip_id)
            added += 1

        state["next_clip_id"] = max_id + 1
        _save_state(state_path, state)
        logging.info(f"[Sync] Startup sheet sync: {added} clip(s) loaded into state")
    except Exception as exc:
        logging.warning(f"[Sync] Startup sheet sync error: {exc}")


def main() -> None:
    workspace_root = Path(__file__).resolve().parents[1]
    _load_env(workspace_root)
    _setup_gservice_credentials(workspace_root)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")

    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    _log_sheet_health()

    # Sync state from sheet on startup to survive Railway ephemeral restarts
    state_path = _state_file(workspace_root)
    _sync_state_from_sheet(state_path)

    app = build_application(token=token, workspace_root=workspace_root)

    # Python 3.14+ no longer creates a default loop implicitly in main thread.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    print("[Bot] Loki KPI bot is running. Press Ctrl+C to stop.")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
