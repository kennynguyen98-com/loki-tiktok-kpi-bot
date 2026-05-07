from __future__ import annotations

import json
import logging
import math
import os
import asyncio
import base64
from dataclasses import dataclass
from datetime import date, datetime
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


def _sheet_write_clip(
    clip_id: int,
    clip_day: date,
    posted_at: datetime,
    title: str = "",
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
        header_row_idx = None
        for i, row in enumerate(values, start=1):
            upper = " ".join((c or "").strip().upper() for c in row)
            if "CLIP" in upper and "NGÀY" in upper:
                header_row_idx = i
                break
        if header_row_idx is None:
            return False

        header = values[header_row_idx - 1]
        c_num = _find_col(header, "CLIP #", "#")
        c_title = _find_col(header, "TIÊU ĐỀ", "CHỦ ĐỀ / TIÊU ĐỀ")
        c_date = _find_col(header, "NGÀY ĐĂNG")
        c_posted = _find_col(header, "Giờ đăng thực tế")
        c_check2h = _find_col(header, "Giờ check +2h")
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
        from datetime import timedelta
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
        if c_agent is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(target_row, c_agent + 1), "values": [["⏳ Chờ check 2h"]]})

        if updates:
            ws.batch_update(updates, value_input_option="RAW")
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
        header_row_idx = None
        for i, row in enumerate(values, start=1):
            upper = " ".join((c or "").strip().upper() for c in row)
            if "CLIP" in upper and "NGÀY" in upper:
                header_row_idx = i
                break
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
        return True
    except Exception as exc:
        logging.warning(f"[Sheet] _sheet_update_stats error: {exc}")
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
        "/add_clip <views> [yyyy-mm-dd] - Thêm 1 clip\n"
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
        "/content_ideas - Gợi ý chủ đề video AI\n\n"
        "📊 STATS 2H:\n"
        "/log_stats <id> <view> <like> <cmt> <share> <flw> - Nhập số liệu 2h sau đăng"
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
        f"Nhập số liệu để bot cập nhật sheet và tính KPI:\n"
        f"/log_stats {clip_id} <view> <like> <comment> <share> <follower_tang>\n\n"
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

    # Usage: /add_clip <views> [yyyy-mm-dd] [title...]
    if not context.args:
        await update.message.reply_text(
            "Dùng: /add_clip <views> [yyyy-mm-dd] [tên clip]\n"
            "Ví dụ: /add_clip 0 2026-05-07 Học AI\n"
            "(views = 0 lúc mới đăng, điền số thật sau 2h khi bot nhắc)"
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
    state["next_clip_id"] = clip_id + 1
    posted_at = datetime.now()

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

    # Write to Google Sheet
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


async def cmd_log_stats(update: Update, context: CallbackContext) -> None:
    """Nhận số liệu sau 2h: /log_stats <clip_id> <view> <like> <cmt> <share> <flw>"""
    if update.effective_chat is None or context.bot_data.get("state_path") is None:
        return

    state_path: Path = context.bot_data["state_path"]
    state = _load_state(state_path)
    if not _is_authorized(state, update.effective_chat.id):
        await update.message.reply_text(_reject_text())
        return

    usage = "Dùng: /log_stats <clip_id> <view> <like> <comment> <share> <follower_tăng>\nVí dụ: /log_stats 1 1200 85 12 3 5"
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
    await update.message.reply_text(f"Đã cập nhật clip #{clip_id} -> {views:,} view.\n\n{_progress_text(state)}")


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

    text = _progress_text(state)
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

    text = build_evening_kpi_text(_progress_text(state))
    await context.bot.send_message(chat_id=int(chat_id), text=text)


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


def build_application(token: str, workspace_root: Path) -> Application:
    app = Application.builder().token(token).build()
    app.bot_data["state_path"] = _state_file(workspace_root)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("add_clip", cmd_add_clip))
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

    _register_jobs(app)
    return app


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
