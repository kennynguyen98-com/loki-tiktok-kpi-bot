# 🚀 Deploy Telegram KPI Bot lên Render.com (24/7 Free)

## Overview
Bot sẽ chạy **24/7 trên server Render** (miễn phí). Không cần máy công ty bật. Chỉ cần Telegram để điều khiển.

---

## BƯỚC 1: Chuẩn bị GitHub (5 phút)

### 1.1 Tạo GitHub repository
- Vào https://github.com/new
- **Repository name:** `loki-tiktok-kpi-bot` (hay tên khác)
- **Visibility:** Private (để bảo vệ credentials)
- **Initialize:** No (sẽ push từ local)
- Click **Create repository**

### 1.2 Setup Git local
```powershell
cd "d:\Kenny\HỌC AI AGENT\Tiktok Loki Trần"

# Init git (nếu chưa có)
git init

# Add remote
git remote add origin https://github.com/YOUR_USERNAME/loki-tiktok-kpi-bot.git

# Verify .gitignore (đã có, bảo vệ .env & credentials)
git add .

# Commit & push
git commit -m "Initial commit: TikTok KPI bot with 5 assistant commands"
git branch -M main
git push -u origin main
```

**Xác nhận:** Vào https://github.com/YOUR_USERNAME/loki-tiktok-kpi-bot → Thấy files có trên GitHub

---

## BƯỚC 2: Tạo Render Service (10 phút)

### 2.1 Signup Render
- Vào https://render.com (hoặc login nếu đã có account)
- Click **New +** → **Web Service**

### 2.2 Connect GitHub
- Chọn **GitHub** → Authorize
- Tìm repository `loki-tiktok-kpi-bot`
- Click **Connect**

### 2.3 Configure Service
| Trường | Giá trị |
|-------|--------|
| **Name** | loki-kpi-bot (tên service) |
| **Environment** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python scripts/telegram_kpi_bot.py` |
| **Instance Type** | Free tier (không tốn tiền) |

### 2.4 Set Environment Variables
Scroll down → **Environment** → Add tất cả variables từ `.env`:

```
TELEGRAM_BOT_TOKEN=8610109419:AAHB7vaRNIm2c4Q-zRLE2aEsJPL4ccWvqRA
GSHEET_URL=https://docs.google.com/spreadsheets/d/1WeyxlFL2WKNUQb8N-puGGHp55ZW1H55h_dfAdFl1JE8/edit?gid=1298895767#gid=1298895767
GSERVICE_ACCOUNT_FILE=credentials/google-service-account.json
TG_REMINDER_TIMES=09:00,13:00,20:30
TG_MORNING_STATUS_HOUR=8
TG_MORNING_STATUS_MINUTE=30
TG_SHEET_SYNC_HOUR=12
TG_SHEET_SYNC_MINUTE=0
TG_EVENING_REPORT_HOUR=17
TG_EVENING_REPORT_MINUTE=0
```

### 2.5 Xử lý Google Service Account JSON
**Vấn đề:** File `credentials/google-service-account.json` cần được upload, nhưng .gitignore bảo vệ nó.

**Giải pháp:** Chuyển nội dung JSON thành ENV variable

#### Option A: Encode JSON to Base64 (Recommended)
```powershell
# Read JSON file
$json = Get-Content "credentials/google-service-account.json" -Raw

# Convert to Base64
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
$base64 = [System.Convert]::ToBase64String($bytes)

# Print to copy
Write-Output $base64
```

**Thêm variable mới vào Render:**
```
GSERVICE_ACCOUNT_JSON_B64=<paste base64 string here>
```

#### Option B: Paste JSON directly (simpler nhưng ít secure)
Thêm variable:
```
GSERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
```

### 2.6 Cập nhật bot code để đọc từ ENV
Nếu dùng Base64, cập nhật `scripts/telegram_kpi_bot.py`:

```python
# Thêm ở đầu file:
import base64

# Trong hàm main(), trước khi load .env:
def _setup_gservice_credentials():
    """Decode Base64 JSON từ ENV nếu có"""
    workspace_root = Path(__file__).resolve().parents[1]
    creds_dir = workspace_root / "credentials"
    creds_dir.mkdir(exist_ok=True)
    
    creds_file = creds_dir / "google-service-account.json"
    if creds_file.exists():
        return  # Already exists
    
    b64_json = os.getenv("GSERVICE_ACCOUNT_JSON_B64", "").strip()
    if b64_json:
        try:
            json_str = base64.b64decode(b64_json).decode("utf-8")
            creds_file.write_text(json_str, encoding="utf-8")
        except Exception as e:
            print(f"[WARN] Không decode được GSERVICE_ACCOUNT_JSON_B64: {e}")

# Gọi trước main():
def main() -> None:
    _setup_gservice_credentials()
    workspace_root = Path(__file__).resolve().parents[1]
    # ... rest of main
```

### 2.7 Click "Create Web Service"
- Render sẽ build + deploy (2-3 phút)
- Chờ đến "Your service is live on ..."

---

## BƯỚC 3: Deploy (Automatic)

Render sẽ:
1. ✅ Clone repo từ GitHub
2. ✅ Install dependencies từ `requirements.txt`
3. ✅ Chạy `python scripts/telegram_kpi_bot.py`
4. ✅ Bot đăng nhập Telegram & ready

**Xác nhận:** Mở Telegram → Gửi `/status` → Bot phản hồi (có thể delay 30s lần đầu)

---

## BƯỚC 4: Persist Data (State File)

**Vấn đề:** Render container bị reset, state.json mất

**Giải pháp tạm thời:** Dùng Google Sheet làm backup (đã làm)
- `/sync_sheet_now` lúc 12:00 (automatic)
- Dữ liệu lưu vào Google Sheet → An toàn

**Giải pháp dài hạn (Optional):**
- Upgrade lên Render **Paid** ($7/tháng) → Thêm persistent disk
- Hoặc chuyển sang **Railway.app** (cũng free tier, nhưng disk persist lâu hơn)

---

## BƯỚC 5: Update Code (Continuous Deployment)

Khi update bot code:

```powershell
# 1. Edit file
# 2. Test locally (tuỳ chọn)
# 3. Commit & push
git add scripts/telegram_kpi_bot.py
git commit -m "Add new features"
git push origin main
```

Render sẽ **tự động redeploy** trong 2-3 phút (auto-sync từ GitHub)

---

## BƯỚC 6: Monitor & Debug

### Logs
- Vào Render dashboard → Chọn service `loki-kpi-bot`
- Tab **Logs** → Xem real-time output

### Manual Trigger
- Chat Telegram: `/nudge_now` → Bot gửi reminder ngay
- Chat Telegram: `/sync_sheet_now` → Bot sync Google Sheet ngay

### Restart Service
- Render dashboard → Click service → **Manual Deploy**

---

## 🎯 Checklist Deploy

- [ ] GitHub repo created & code pushed
- [ ] Render account created
- [ ] Service connected to GitHub
- [ ] ENV variables added (including GSERVICE_ACCOUNT_JSON_B64 hoặc JSON file upload)
- [ ] Service deployed (Live ✅)
- [ ] Test `/help` on Telegram (phản hồi OK)
- [ ] Test `/status` on Telegram (phản hồi OK)
- [ ] Test `/daily_plan` on Telegram (phản hồi OK)
- [ ] Scheduled jobs hoạt động (12:00 sync, 08:30 status, etc.)

---

## 🐛 Troubleshooting

### Bot không phản hồi Telegram
1. Check Render logs → Có error gì không?
2. Verify TELEGRAM_BOT_TOKEN hợp lệ (copy từ @BotFather)
3. Restart service: Render dashboard → Manual Deploy

### Google Sheet sync fail
1. Check Render logs → Có auth error không?
2. Verify credentials file được decode đúng
3. Check Google Sheet share access (service account có quyền không?)

### Local bot still running?
- Lệnh để kill bot local:
```powershell
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*telegram_kpi_bot*' } | Remove-CimInstance -Force
```

---

## 📊 Cost
- Render free tier: **$0/month** (chạy 750 giờ = 31 ngày liên tục) ✅
- Google Sheets API: **Miễn phí** ✅
- Telegram Bot API: **Miễn phí** ✅

**Total: $0** 🎉

---

## 🎬 Kế tiếp

1. Deploy xong ✅ → Bot chạy 24/7
2. Test tất cả commands → Verify working
3. Setup cron job để fetch TikTok metrics (optional, nếu cần real-time stats)

Xong! Bot sẽ:
- ✅ Chạy 24/7 trên cloud (không cần máy công ty)
- ✅ Nhắc KPI theo schedule (09:00, 13:00, 20:30)
- ✅ Sync Google Sheet lúc 12:00 tự động
- ✅ Gửi report hàng tối (17:00)
- ✅ Sẽ tương tác via Telegram từ bất kỳ đâu

**Cùng triển khai?** 🚀
