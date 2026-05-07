# Kenny Philippines TikTok Workflow

Mục tiêu của workspace này là tạo nhanh bộ asset TikTok dạng slideshow ảnh để thu lead tư vấn du học Philippines.

## Workflow tạo nội dung

1. Nhập chủ đề hoặc keyword
2. Chọn loại bài: thông tin, cảm xúc, CTA
3. Tạo script 5-8 slide (slide 1 là hook, slide cuối là CTA)
4. Tạo caption + hashtag
5. Chuẩn bị script reply comment để kéo lead về Zalo
6. Xuất file trong outputs để đăng tay lên TikTok

## Chạy nhanh pipeline TikTok

```bash
python scripts/run_tiktok_leadgen.py --keyword "du hoc philippines" --days 14 --posts-per-day 1 --image-dir "assets/tiktok-images" --outputs-dir "outputs"
```

Nếu cần 2 bài/ngày:

```bash
python scripts/run_tiktok_leadgen.py --keyword "du hoc philippines" --days 14 --posts-per-day 2 --image-dir "assets/tiktok-images" --outputs-dir "outputs"
```

## Dữ liệu bạn cần cập nhật trước khi chạy

- Ảnh nguồn: đặt vào thư mục `assets/tiktok-images/`
- Dữ liệu trường: điền vào file `context/data/schools.csv`

## Output sau mỗi lần chạy

- `outputs/[run-id]/tiktok-plan.json`
- `outputs/[run-id]/posting-calendar.csv`
- `outputs/[run-id]/captions.txt`
- `outputs/[run-id]/lead-inbox-playbook.md`
- `outputs/[run-id]/lead-tracker-template.csv`
- `outputs/[run-id]/README-quick-start.txt`

## Quy tắc an toàn

- Không cam kết visa, đậu visa, việc làm, thu nhập
- Không bịa số liệu học phí
- Học phí chỉ nêu dạng "từ X" hoặc "khoảng X"
- Kenny review rồi mới đăng tay lên TikTok

## Telegram bot nhac KPI Loki Tran

Bot nay chi theo doi duy nhat KPI cho kenh TikTok Loki Tran.

### KPI mac dinh

- So clip: 20
- Tong view: 30,000
- It nhat 1 clip >= 10,000 view
- It nhat 1 clip >= 5,000 view
- 18 clip con lai tap trung nhom 1,000-1,500 view

### Cai dat nhanh

1. Tao bot bang `@BotFather`, lay token.
2. Copy `.env.example` thanh `.env`, dien `TELEGRAM_BOT_TOKEN`.
3. Cai package:

```bash
pip install "python-telegram-bot[job-queue]==21.6"
```

4. Chay bot:

```bash
python scripts/telegram_kpi_bot.py
```

5. Trong Telegram, vao chat voi bot va go:

```text
/start
/help
```

### Lenh su dung nhanh

- `/status`: xem tien do KPI
- `/add_clip <views> [yyyy-mm-dd]`: them 1 clip
- `/set_views <clip_id> <views>`: cap nhat view clip
- `/remove_clip <clip_id>`: xoa clip
- `/list_clips`: xem danh sach clip trong thang
- `/nudge_now`: nhac KPI ngay lap tuc
- `/sync_sheet_now`: dong bo Google Sheet ngay

### Du lieu bot luu o dau

- `outputs/loki-kpi-bot/state.json`

### Google Sheet sync + bao cao 17h

Bot da ho tro:

- 12:00: sync du lieu len tab `Phan tich kenh hang tuan`
- 17:00: gui bao cao KPI ngay qua Telegram

Can cai them package:

```bash
pip install gspread google-auth requests
```

Can cau hinh trong `.env`:

- `GSHEET_URL` hoac `GSHEET_ID`
- `GSERVICE_ACCOUNT_FILE` (duong dan file service-account.json)
- `GSHEET_TAB_WEEKLY` (mac dinh: `Phan tich kenh hang tuan`)
- `GSHEET_TAB_INFO` (mac dinh: `Thong tin`)
- `TG_SHEET_SYNC_HOUR=12`, `TG_SHEET_SYNC_MINUTE=00`
- `TG_EVENING_REPORT_HOUR=17`, `TG_EVENING_REPORT_MINUTE=00`

Luu y quyen:

- Share Google Sheet cho service account email voi quyen `Editor`.
- TikTok/Facebook de auto-lay so can them API hoac ben thu 3 (hien tai bot ghi chu yeu YouTube neu co `YOUTUBE_API_KEY`, va ghi snapshot note cho cac nen tang con lai).
