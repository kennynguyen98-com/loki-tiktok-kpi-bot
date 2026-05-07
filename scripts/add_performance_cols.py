"""
Thêm các cột performance vào sheet LỊCH ĐĂNG:
Giờ đăng thực tế | Giờ check +2h | View 2h | Like 2h | Comment 2h |
Share 2h | Follower tăng 2h | Đã check 2h? | Agent note | Cảnh báo KPI
"""
from __future__ import annotations

import os
from typing import List, Optional

import gspread
from dotenv import load_dotenv


NEW_COLS = [
    "Giờ đăng thực tế",
    "Giờ check +2h",
    "View 2h",
    "Like 2h",
    "Comment 2h",
    "Share 2h",
    "Follower tăng 2h",
    "Đã check 2h?",
    "Agent note",
    "Cảnh báo KPI",
]


def extract_sheet_id(url: str) -> str:
    if "/d/" in url:
        return url.split("/d/", 1)[1].split("/", 1)[0]
    return url


def worksheet_like(spreadsheet, preferred: str):
    try:
        return spreadsheet.worksheet(preferred)
    except Exception:
        pass
    wanted = preferred.strip().casefold()
    for ws in spreadsheet.worksheets():
        if ws.title.strip().casefold() == wanted:
            return ws
        if ws.title.strip().casefold().startswith(wanted):
            return ws
    raise RuntimeError(f"Worksheet not found: {preferred}")


def find_header_row(values: List[List[str]]) -> Optional[int]:
    for i, row in enumerate(values, start=1):
        upper = [(c or "").strip().upper() for c in row]
        if "#" in upper and "TIÊU ĐỀ" in " ".join(upper) and "NGÀY ĐĂNG" in " ".join(upper):
            return i
    return None


def main() -> None:
    load_dotenv()
    gc = gspread.service_account(filename=os.getenv("GSERVICE_ACCOUNT_FILE"))
    sh = gc.open_by_key(extract_sheet_id(os.getenv("GSHEET_URL", "")))

    # Work on primary LỊCH ĐĂNG (not the (1) duplicate)
    target_sheets = [ws for ws in sh.worksheets()
                     if "lịch đăng" == ws.title.strip().casefold()
                     or (ws.title.strip().casefold().startswith("lịch") and "(1)" not in ws.title)]
    # Prefer exact match
    exact = [ws for ws in target_sheets if ws.title.strip().casefold() == "lịch đăng"]
    ws = exact[0] if exact else (target_sheets[0] if target_sheets else None)
    if ws is None:
        print("ERROR: Could not find LỊCH ĐĂNG")
        return

    values = ws.get_all_values()
    header_row_idx = find_header_row(values)
    if header_row_idx is None:
        print(f"ERROR: Could not find header row in {ws.title}")
        print("First 8 rows:", [r[:10] for r in values[:8]])
        return

    header = values[header_row_idx - 1]

    # Check which columns already exist
    existing = [(c or "").strip() for c in header]
    to_add = [col for col in NEW_COLS if col not in existing]

    if not to_add:
        print(f"All performance columns already exist in '{ws.title}'")
        return

    # Find last used column in header row
    last_col = len(header)
    while last_col > 0 and (header[last_col - 1] or "").strip() == "":
        last_col -= 1

    # Append new headers after last used column
    updates = []
    for i, col_name in enumerate(to_add):
        col_letter = gspread.utils.rowcol_to_a1(header_row_idx, last_col + 1 + i).rstrip("0123456789")
        cell_addr = f"{col_letter}{header_row_idx}"
        updates.append({"range": cell_addr, "values": [[col_name]]})

    ws.batch_update(updates, value_input_option="RAW")

    # Style the new header cells (dark navy bg, white bold text)
    sid = ws.id
    start_col_idx = last_col   # 0-based
    end_col_idx = last_col + len(to_add)

    HEADER_BG = {"red": 0.09019, "green": 0.10196, "blue": 0.23529}
    WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}

    sh.batch_update({"requests": [{
        "repeatCell": {
            "range": {
                "sheetId": sid,
                "startRowIndex": header_row_idx - 1,
                "endRowIndex": header_row_idx,
                "startColumnIndex": start_col_idx,
                "endColumnIndex": end_col_idx,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": HEADER_BG,
                    "textFormat": {"foregroundColor": WHITE, "bold": True, "fontFamily": "Arial", "fontSize": 10},
                    "wrapStrategy": "WRAP",
                    "verticalAlignment": "MIDDLE",
                }
            },
            "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat,userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment",
        }
    }]})

    # Add dropdown for "Đã check 2h?" column
    check_col_name = "Đã check 2h?"
    if check_col_name in to_add:
        check_col_offset = to_add.index(check_col_name)
        check_col_sheet_idx = last_col + check_col_offset  # 0-based

        # Apply dropdown 20 rows down from header
        sh.batch_update({"requests": [{
            "setDataValidation": {
                "range": {
                    "sheetId": sid,
                    "startRowIndex": header_row_idx,
                    "endRowIndex": header_row_idx + 20,
                    "startColumnIndex": check_col_sheet_idx,
                    "endColumnIndex": check_col_sheet_idx + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "✅ Đã check"},
                            {"userEnteredValue": "⏳ Chờ 2h"},
                            {"userEnteredValue": "❌ Bỏ qua"},
                        ],
                    },
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        }]})

    print(f"Added {len(to_add)} columns to '{ws.title}' starting at column {last_col + 1}:")
    for c in to_add:
        print(f"  + {c}")


if __name__ == "__main__":
    main()
