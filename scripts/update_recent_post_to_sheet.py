from __future__ import annotations

import os
from datetime import date
from typing import List, Optional

import gspread
from dotenv import load_dotenv


def extract_sheet_id(sheet_url: str) -> str:
    if "/d/" in sheet_url:
        return sheet_url.split("/d/", 1)[1].split("/", 1)[0]
    return sheet_url


def find_col(header_row: List[str], names: List[str]) -> Optional[int]:
    normalized = [(v or "").strip().casefold() for v in header_row]
    for name in names:
        key = name.strip().casefold()
        if key in normalized:
            return normalized.index(key)
    return None


def worksheet_like(spreadsheet, preferred: str):
    # Exact first
    try:
        return spreadsheet.worksheet(preferred)
    except Exception:
        pass

    wanted = preferred.strip().casefold()
    candidates = []
    for ws in spreadsheet.worksheets():
        title = ws.title.strip()
        fold = title.casefold()
        if fold == wanted:
            return ws
        if fold.startswith(wanted):
            candidates.append(ws)
    if candidates:
        return candidates[0]
    raise RuntimeError(f"Worksheet not found: {preferred}")


def main() -> None:
    load_dotenv()

    sheet_url = os.getenv("GSHEET_URL", "").strip()
    service_account_file = os.getenv("GSERVICE_ACCOUNT_FILE", "").strip()
    if not sheet_url or not service_account_file:
        raise RuntimeError("Missing GSHEET_URL or GSERVICE_ACCOUNT_FILE")

    gc = gspread.service_account(filename=service_account_file)
    sh = gc.open_by_key(extract_sheet_id(sheet_url))

    # 1) Update CONTENT BRIEF row for clip 1 / Học AI
    brief = worksheet_like(sh, "CONTENT BRIEF")
    brief_values = brief.get_all_values()

    header_idx = None
    for i, row in enumerate(brief_values, start=1):
        row_join = " ".join((c or "") for c in row).casefold()
        if "chủ đề" in row_join and "trạng thái" in row_join:
            header_idx = i
            break

    if header_idx is None:
        raise RuntimeError("Cannot find header row in CONTENT BRIEF")

    header = brief_values[header_idx - 1]
    col_num = find_col(header, ["#"])
    col_title = find_col(header, ["CHỦ ĐỀ / TIÊU ĐỀ VIDEO", "CHỦ ĐỀ / TIÊU ĐỀ"])
    col_thumb = find_col(header, ["THUMBNAIL TITLE\n(chữ to trên ảnh bìa)", "THUMBNAIL TITLE"])
    col_caption = find_col(header, ["CAPTION\n(hook dòng đầu)", "CAPTION"])
    col_desc = find_col(header, ["MÔ TẢ\n(description đầy đủ)", "MÔ TẢ"])
    col_hash = find_col(header, ["HASHTAG"])
    col_status = find_col(header, ["TRẠNG THÁI"])

    if None in (col_num, col_title, col_thumb, col_caption, col_desc, col_hash, col_status):
        raise RuntimeError("Missing expected columns in CONTENT BRIEF")

    target_row = None
    for r in range(header_idx + 1, min(header_idx + 40, len(brief_values) + 1)):
        row = brief_values[r - 1] if r - 1 < len(brief_values) else []
        val_num = row[col_num] if col_num < len(row) else ""
        val_title = row[col_title] if col_title < len(row) else ""
        if (val_num or "").strip() == "1" or (val_title or "").strip().casefold() == "học ai":
            target_row = r
            break

    if target_row is None:
        target_row = header_idx + 1

    caption_text = "Muốn đi nhanh thì đi một mình, muốn đi xa thì đi cùng nhau 🤝"
    desc_text = (
        "Nhóm anh em khởi nghiệp của mình vừa có buổi training về AI Agent do anh cả trong nhóm "
        "trực tiếp chia sẻ. Từ cách setup workflow đến cách áp dụng vào thực tế content mỗi ngày."
    )
    hashtag_text = "#lokitran #gip #xuhuong #ai #aiagent"

    # Update only target row cells, no formula/data structure changes.
    updates = [
        {"range": gspread.utils.rowcol_to_a1(target_row, col_title + 1), "values": [["Học AI"]]},
        {"range": gspread.utils.rowcol_to_a1(target_row, col_thumb + 1), "values": [["Học AI Agent"]]},
        {"range": gspread.utils.rowcol_to_a1(target_row, col_caption + 1), "values": [[caption_text]]},
        {"range": gspread.utils.rowcol_to_a1(target_row, col_desc + 1), "values": [[desc_text]]},
        {"range": gspread.utils.rowcol_to_a1(target_row, col_hash + 1), "values": [[hashtag_text]]},
        {"range": gspread.utils.rowcol_to_a1(target_row, col_status + 1), "values": [["Đã đăng"]]},
    ]
    brief.batch_update(updates)

    # 2) Mark posting date in LỊCH ĐĂNG for clip #1.
    lich = worksheet_like(sh, "LỊCH ĐĂNG")
    lich_values = lich.get_all_values()

    lich_header_idx = None
    for i, row in enumerate(lich_values, start=1):
        joined = " ".join((c or "") for c in row).casefold()
        if "clip #" in joined and "ngày đăng" in joined:
            lich_header_idx = i
            break

    if lich_header_idx is not None:
        h = lich_values[lich_header_idx - 1]
        c_clip = find_col(h, ["CLIP #", "#"])
        c_date = find_col(h, ["NGÀY ĐĂNG"])
        c_title = find_col(h, ["TIÊU ĐỀ", "CHỦ ĐỀ / TIÊU ĐỀ"])

        if c_clip is not None and c_date is not None:
            row_clip_1 = None
            for r in range(lich_header_idx + 1, min(lich_header_idx + 50, len(lich_values) + 1)):
                row = lich_values[r - 1] if r - 1 < len(lich_values) else []
                clip_val = row[c_clip] if c_clip < len(row) else ""
                if (clip_val or "").strip() == "1":
                    row_clip_1 = r
                    break

            if row_clip_1 is not None:
                today = date.today().strftime("%d/%m/%Y")
                lich_updates = [
                    {"range": gspread.utils.rowcol_to_a1(row_clip_1, c_date + 1), "values": [[today]]},
                ]
                if c_title is not None:
                    lich_updates.append(
                        {"range": gspread.utils.rowcol_to_a1(row_clip_1, c_title + 1), "values": [["Học AI"]]}
                    )
                lich.batch_update(lich_updates)

    print(f"Updated CONTENT BRIEF row {target_row} and synced LỊCH ĐĂNG for clip #1.")


if __name__ == "__main__":
    main()
