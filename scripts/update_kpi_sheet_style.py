from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import gspread
from dotenv import load_dotenv

# Classic theme (closer to original look)
HEADER_BG = {"red": 0.09019, "green": 0.10196, "blue": 0.23529}  # #171A3C
SUBTITLE_BG = {"red": 0.06274, "green": 0.23529, "blue": 0.41960}  # #103C6B
SECTION_BG = {"red": 0.09019, "green": 0.33333, "blue": 0.15294}  # #175527
ODD_BG = {"red": 0.90980, "green": 0.92941, "blue": 0.96078}  # #E8EDF5
EVEN_BG = {"red": 1.0, "green": 1.0, "blue": 1.0}  # #FFFFFF
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
YELLOW = {"red": 1.0, "green": 0.84313, "blue": 0.0}  # #FFD700


def hex_to_rgb_01(hex_color: str) -> Dict[str, float]:
    hex_color = hex_color.lstrip("#")
    return {
        "red": int(hex_color[0:2], 16) / 255.0,
        "green": int(hex_color[2:4], 16) / 255.0,
        "blue": int(hex_color[4:6], 16) / 255.0,
    }


def extract_sheet_id(sheet_url: str) -> str:
    if "/d/" in sheet_url:
        return sheet_url.split("/d/", 1)[1].split("/", 1)[0]
    return sheet_url


def worksheet_by_title(spreadsheet, title: str):
    wanted = title.strip().casefold()
    for ws in spreadsheet.worksheets():
        if ws.title.strip().casefold() == wanted:
            return ws
    return None


def find_col_index(header_row: List[str], target: str) -> Optional[int]:
    t = target.strip().casefold()
    for i, v in enumerate(header_row):
        if (v or "").strip().casefold() == t:
            return i
    return None


def add_list_validation_request(
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    values: List[str],
) -> Dict:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": start_col,
                "endColumnIndex": end_col,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in values],
                },
                "strict": True,
                "showCustomUi": True,
            },
        }
    }


def add_conditional_color_request(
    sheet_id: int,
    start_row: int,
    end_row: int,
    col: int,
    text_equals: str,
    bg_hex: str,
    text_hex: str = "#FFFFFF",
) -> Dict:
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": col,
                        "endColumnIndex": col + 1,
                    }
                ],
                "booleanRule": {
                    "condition": {
                        "type": "TEXT_EQ",
                        "values": [{"userEnteredValue": text_equals}],
                    },
                    "format": {
                        "backgroundColor": hex_to_rgb_01(bg_hex),
                        "textFormat": {"foregroundColor": hex_to_rgb_01(text_hex), "bold": True},
                    },
                },
            },
            "index": 0,
        }
    }


def clear_conditional_rules_request(sheet_id: int, count: int) -> List[Dict]:
    # Remove from top repeatedly.
    return [
        {"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}}
        for _ in range(count)
    ]


def set_row_height_request(sheet_id: int, start_index: int, end_index: int, pixel_size: int) -> Dict:
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": start_index,
                "endIndex": end_index,
            },
            "properties": {"pixelSize": pixel_size},
            "fields": "pixelSize",
        }
    }


def add_alternating_bg_rules(sheet_id: int, start_row: int, end_row: int, start_col: int, end_col: int) -> List[Dict]:
    odd = {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": start_col,
                        "endColumnIndex": end_col,
                    }
                ],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": "=ISODD(ROW())"}],
                    },
                    "format": {"backgroundColor": ODD_BG},
                },
            },
            "index": 0,
        }
    }
    even = {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": start_col,
                        "endColumnIndex": end_col,
                    }
                ],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": "=ISEVEN(ROW())"}],
                    },
                    "format": {"backgroundColor": EVEN_BG},
                },
            },
            "index": 0,
        }
    }
    return [odd, even]


def repeat_cell_request(
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    user_format: Dict,
    fields: str,
) -> Dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": end_row,
                "startColumnIndex": start_col,
                "endColumnIndex": end_col,
            },
            "cell": {"userEnteredFormat": user_format},
            "fields": fields,
        }
    }


def get_header_rows(values: List[List[str]]) -> List[int]:
    rows = []
    for idx, row in enumerate(values, start=1):
        joined = " ".join((c or "").strip().upper() for c in row)
        if "#" in row and ("CHỦ ĐỀ" in joined or "LOẠI CONTENT" in joined or "TRẠNG THÁI" in joined):
            rows.append(idx)
    return rows


def get_section_rows(values: List[List[str]]) -> List[int]:
    rows = []
    for idx, row in enumerate(values, start=1):
        joined = " ".join((c or "").strip().upper() for c in row)
        if "TUẦN" in joined or "SECTION" in joined or "THÁNG" in joined:
            rows.append(idx)
    return rows


def last_used_row_col(values: List[List[str]]) -> Tuple[int, int]:
    last_row = 1
    last_col = 1
    for r_idx, row in enumerate(values, start=1):
        row_has_data = False
        for c_idx, cell in enumerate(row, start=1):
            if (cell or "").strip() != "":
                row_has_data = True
                last_col = max(last_col, c_idx)
        if row_has_data:
            last_row = r_idx
    return last_row, last_col


def find_table_blocks(values: List[List[str]], max_col: int) -> List[Tuple[int, int, int, int]]:
    header_rows = get_header_rows(values)
    section_rows = set(get_section_rows(values))
    blocks: List[Tuple[int, int, int, int]] = []
    total_rows = len(values)

    for i, header_row in enumerate(header_rows):
        next_header = header_rows[i + 1] if i + 1 < len(header_rows) else total_rows + 1
        end_row = min(next_header - 1, total_rows)
        for r in range(header_row + 1, min(next_header, total_rows + 1)):
            row_vals = values[r - 1] if r - 1 < len(values) else []
            if r in section_rows or all((v or "").strip() == "" for v in row_vals[:max_col]):
                end_row = r - 1
                break
        if end_row > header_row:
            blocks.append((header_row, end_row, 1, max_col))
    return blocks


def style_sheet(spreadsheet, ws, report: List[str]) -> None:
    values = ws.get_all_values()
    last_row, last_col = last_used_row_col(values)
    row_count = max(last_row, 1)
    col_count = max(last_col, 1)

    requests: List[Dict] = []

    # Clear old conditional rules then rebuild classic row styles.
    meta_sheet = next(s for s in spreadsheet.fetch_sheet_metadata()["sheets"] if s["properties"]["sheetId"] == ws.id)
    existing_rules = len(meta_sheet.get("conditionalFormats", []))
    requests.extend(clear_conditional_rules_request(ws.id, existing_rules))

    # Apply default font to used range.
    requests.append(
        repeat_cell_request(
            ws.id,
            0,
            row_count,
            0,
            col_count,
            {
                "textFormat": {"fontFamily": "Arial", "fontSize": 10},
                "horizontalAlignment": "LEFT",
                "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP",
            },
            "userEnteredFormat.textFormat.fontFamily,userEnteredFormat.textFormat.fontSize,userEnteredFormat.horizontalAlignment,userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy",
        )
    )

    # Comfortable spacing.
    requests.append(set_row_height_request(ws.id, 0, row_count, 30))

    # Fill base background.
    requests.append(
        repeat_cell_request(
            ws.id,
            0,
            row_count,
            0,
            col_count,
            {"backgroundColor": EVEN_BG},
            "userEnteredFormat.backgroundColor",
        )
    )

    # Title row styling.
    requests.append(
        repeat_cell_request(
            ws.id,
            0,
            1,
            0,
            col_count,
            {
                "backgroundColor": HEADER_BG,
                "textFormat": {"foregroundColor": YELLOW, "bold": True, "fontFamily": "Arial", "fontSize": 16},
            },
            "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat",
        )
    )
    requests.append(set_row_height_request(ws.id, 0, 1, 38))

    # Subtitle row under title.
    if row_count >= 2 and any((v or "").strip() for v in values[1][:col_count]):
        requests.append(
            repeat_cell_request(
                ws.id,
                1,
                2,
                0,
                col_count,
                {
                    "backgroundColor": SUBTITLE_BG,
                    "textFormat": {"foregroundColor": YELLOW, "bold": True, "fontFamily": "Arial", "fontSize": 10},
                },
                "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat",
            )
        )
        requests.append(set_row_height_request(ws.id, 1, 2, 26))

    # Section rows.
    for r in get_section_rows(values):
        requests.append(
            repeat_cell_request(
                ws.id,
                r - 1,
                r,
                0,
                col_count,
                {
                    "backgroundColor": SECTION_BG,
                    "textFormat": {"foregroundColor": WHITE, "bold": True, "fontFamily": "Arial", "fontSize": 12},
                },
                "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat",
            )
        )
        requests.append(set_row_height_request(ws.id, r - 1, r, 28))

    # Table header rows.
    for r in get_header_rows(values):
        requests.append(
            repeat_cell_request(
                ws.id,
                r - 1,
                r,
                0,
                col_count,
                {
                    "backgroundColor": HEADER_BG,
                    "textFormat": {"foregroundColor": WHITE, "bold": True, "fontFamily": "Arial", "fontSize": 12},
                },
                "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat",
            )
        )
        requests.append(set_row_height_request(ws.id, r - 1, r, 30))

    # Classic alternating rows in table blocks.
    table_blocks = find_table_blocks(values, col_count)
    for start_r, end_r, start_c, end_c in table_blocks:
        if end_r > start_r:
            requests.extend(add_alternating_bg_rules(ws.id, start_r, end_r, start_c - 1, end_c))

    try:
        spreadsheet.batch_update({"requests": requests})
    except Exception as exc:
        # Some sheets may contain protected ranges that reject border edits.
        # Retry without updateBorders so typography/colors/spacing still apply.
        if "protected" in str(exc).lower():
            safe_requests = [r for r in requests if "updateBorders" not in r]
            spreadsheet.batch_update({"requests": safe_requests})
            report.append(f"Styled sheet (no-border fallback): {ws.title}")
            return
        raise
    report.append(f"Styled sheet: {ws.title}")


def apply_task_1(spreadsheet, report: List[str]) -> None:
    ws = worksheet_by_title(spreadsheet, "LỊCH ĐĂNG")
    if ws is None:
        report.append("Task 1 skipped: Missing sheet LỊCH ĐĂNG")
        return

    values = ws.get_all_values()
    header_row_idx = None
    loai_col = None
    index_col = None
    target_label = None

    for r_idx, row in enumerate(values, start=1):
        upper = [(c or "").strip().upper() for c in row]
        row_set = set(upper)
        if "LOẠI CONTENT" in row_set or "CHỦ ĐỀ" in row_set:
            header_row_idx = r_idx
            loai_col = find_col_index(row, "LOẠI CONTENT")
            target_label = "LOẠI CONTENT"
            if loai_col is None:
                loai_col = find_col_index(row, "CHỦ ĐỀ")
                target_label = "CHỦ ĐỀ"

            index_col = find_col_index(row, "#")
            if index_col is None:
                index_col = find_col_index(row, "CLIP #")
            if index_col is None:
                index_col = find_col_index(row, "CLIP#")

            if loai_col is not None and index_col is not None:
                break

    if header_row_idx is None or loai_col is None or index_col is None:
        report.append("Task 1 skipped: Could not find LOẠI CONTENT/CHỦ ĐỀ and #/CLIP # headers in LỊCH ĐĂNG")
        return

    target_rows: List[int] = []
    for r_idx, row in enumerate(values, start=1):
        if r_idx <= header_row_idx:
            continue
        cell = row[index_col] if index_col < len(row) else ""
        txt = (cell or "").strip()
        if txt.isdigit():
            num = int(txt)
            if 1 <= num <= 20:
                target_rows.append(r_idx)

    if not target_rows:
        report.append("Task 1 skipped: No clip rows found in LỊCH ĐĂNG")
        return

    first_row = min(target_rows)
    first = first_row - 1
    last = first_row - 1 + 20

    requests: List[Dict] = []
    options = ["Bubble Wrap", "Brand / Ecom", "Real-time", "Cuộc sống"]
    requests.append(add_list_validation_request(ws.id, first, last, loai_col, loai_col + 1, options))

    # Approximate colored chips by conditional formatting on selected value.
    requests.append(add_conditional_color_request(ws.id, first, last, loai_col, "Bubble Wrap", "#0F3460", "#FFFFFF"))
    requests.append(add_conditional_color_request(ws.id, first, last, loai_col, "Brand / Ecom", "#2E7D32", "#FFFFFF"))
    requests.append(add_conditional_color_request(ws.id, first, last, loai_col, "Real-time", "#E65100", "#FFFFFF"))
    requests.append(add_conditional_color_request(ws.id, first, last, loai_col, "Cuộc sống", "#C62828", "#FFFFFF"))

    spreadsheet.batch_update({"requests": requests})
    report.append(
        f"Task 1 done: LỊCH ĐĂNG ({target_label}) {chr(65 + loai_col)}{first_row}:{chr(65 + loai_col)}{first_row + 19}"
    )


def apply_task_3(spreadsheet, report: List[str]) -> None:
    # CONTENT BRIEF - TRẠNG THÁI
    brief_ws = worksheet_by_title(spreadsheet, "CONTENT BRIEF")
    if brief_ws is not None:
        values = brief_ws.get_all_values()
        header_row_idx = None
        status_col = None
        for r_idx, row in enumerate(values, start=1):
            col_idx = find_col_index(row, "TRẠNG THÁI")
            if col_idx is not None:
                header_row_idx = r_idx
                status_col = col_idx
                break

        if header_row_idx is not None and status_col is not None:
            # Apply down 20 rows after header.
            start = header_row_idx
            end = header_row_idx + 20
            options = ["Chưa làm", "Đang làm", "Đã quay", "Đã đăng"]
            brief_ws.spreadsheet.batch_update(
                {
                    "requests": [
                        add_list_validation_request(
                            brief_ws.id,
                            start,
                            end,
                            status_col,
                            status_col + 1,
                            options,
                        )
                    ]
                }
            )
            report.append(f"Task 3 done: CONTENT BRIEF TRẠNG THÁI rows {start+1}-{end}")
        else:
            report.append("Task 3 partial: CONTENT BRIEF missing TRẠNG THÁI header")
    else:
        report.append("Task 3 partial: Missing CONTENT BRIEF sheet")

    # BÁO CÁO TUẦN - ĐẠT?
    weekly_ws = worksheet_by_title(spreadsheet, "BÁO CÁO TUẦN")
    if weekly_ws is not None:
        values = weekly_ws.get_all_values()
        header_row_idx = None
        dat_col = None
        for r_idx, row in enumerate(values, start=1):
            col_idx = find_col_index(row, "ĐẠT?")
            if col_idx is not None:
                header_row_idx = r_idx
                dat_col = col_idx
                break

        if header_row_idx is not None and dat_col is not None:
            start = header_row_idx
            end = min(weekly_ws.row_count, header_row_idx + 25)
            options = ["✅ Đạt", "❌ Chưa đạt", "⏳ Đang đo"]
            weekly_ws.spreadsheet.batch_update(
                {
                    "requests": [
                        add_list_validation_request(
                            weekly_ws.id,
                            start,
                            end,
                            dat_col,
                            dat_col + 1,
                            options,
                        )
                    ]
                }
            )
            report.append(f"Task 3 done: BÁO CÁO TUẦN ĐẠT? rows {start+1}-{end}")
        else:
            report.append("Task 3 partial: BÁO CÁO TUẦN missing ĐẠT? header")
    else:
        report.append("Task 3 partial: Missing BÁO CÁO TUẦN sheet")


def apply_task_2(spreadsheet, report: List[str]) -> None:
    metadata = spreadsheet.fetch_sheet_metadata()
    full_protected_ids = set()
    for s in metadata.get("sheets", []):
        sid = s.get("properties", {}).get("sheetId")
        for pr in s.get("protectedRanges", []):
            rg = pr.get("range", {})
            if rg.get("sheetId") == sid and all(
                k not in rg
                for k in ("startRowIndex", "endRowIndex", "startColumnIndex", "endColumnIndex")
            ):
                full_protected_ids.add(sid)

    # Apply consistent dashboard style across all worksheets in the file.
    for ws in spreadsheet.worksheets():
        if ws.id in full_protected_ids:
            report.append(f"Task 2 skipped (protected): {ws.title}")
            continue
        style_sheet(spreadsheet, ws, report)


def main() -> None:
    load_dotenv()

    sheet_url = os.getenv("GSHEET_URL", "").strip()
    service_account_file = os.getenv("GSERVICE_ACCOUNT_FILE", "").strip()

    if not sheet_url:
        raise RuntimeError("Missing GSHEET_URL in .env")
    if not service_account_file:
        raise RuntimeError("Missing GSERVICE_ACCOUNT_FILE in .env")

    gc = gspread.service_account(filename=service_account_file)
    spreadsheet = gc.open_by_key(extract_sheet_id(sheet_url))

    report: List[str] = []
    report.append(f"Spreadsheet: {spreadsheet.title}")

    apply_task_2(spreadsheet, report)
    apply_task_1(spreadsheet, report)
    apply_task_3(spreadsheet, report)

    print("\n".join(report))


if __name__ == "__main__":
    main()
