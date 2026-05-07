# ⚡ QUICK START: Deploy Bot lên Render trong 15 phút

## Bước 1: Init Git & Push lên GitHub (5 phút)

```powershell
cd "d:\Kenny\HỌC AI AGENT\Tiktok Loki Trần"

# Init git
git init

# Add all files
git add -A

# Commit
git commit -m "TikTok KPI bot: KPI tracking + 5 TikTok assistant commands + cloud support"

# Chỉ làm lần đầu: set default branch name
git config --global init.defaultBranch main

# Verify GitHub SSH key (optional, nếu chưa setup)
# ssh -T git@github.com
```

**Bước tiếp:** Tạo repo trên GitHub
1. Vào https://github.com/new
2. Đặt tên: `loki-tiktok-kpi-bot`
3. Chọn **Private**
4. Click **Create repository**

Sau đó, add remote và push:
```powershell
git remote add origin https://github.com/YOUR_USERNAME/loki-tiktok-kpi-bot.git
git branch -M main
git push -u origin main
```

Xác nhận: Vào GitHub → repo → Thấy toàn bộ files

---

## Bước 2: Deploy lên Render (10 phút)

### 2.1 Chuẩn bị credentials
Cần convert `credentials/google-service-account.json` thành Base64 để upload lên Render:

```powershell
# Read JSON file  
$json = Get-Content "credentials/google-service-account.json" -Raw

# Convert to Base64
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
$base64 = [System.Convert]::ToBase64String($bytes)

# Copy to clipboard (hoặc print để copy)
$base64 | Set-Clipboard
Write-Host "✅ Base64 string copied to clipboard!"
```

### 2.2 Vào Render & deploy
1. Vào https://render.com → Sign up/login
2. Click **New +** → **Web Service**
3. Chọn **GitHub** → Authorize → Chọn repo `loki-tiktok-kpi-bot`
4. **Configure:**
   - Name: `loki-kpi-bot`
   - Environment: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python scripts/telegram_kpi_bot.py`
   - Instance Type: **Free** ✅

5. **Environment Variables** - Add các trường này:
   ```
   TELEGRAM_BOT_TOKEN=8610109419:AAHB7vaRNIm2c4Q-zRLE2aEsJPL4ccWvqRA
   GSHEET_URL=https://docs.google.com/spreadsheets/d/1WeyxlFL2WKNUQb8N-puGGHp55ZW1H55h_dfAdFl1JE8/edit?gid=1298895767#gid=1298895767
   GSERVICE_ACCOUNT_JSON_B64=<PASTE BASE64 STRING HERE>
   TG_REMINDER_TIMES=09:00,13:00,20:30
   TG_MORNING_STATUS_HOUR=8
   TG_MORNING_STATUS_MINUTE=30
   TG_SHEET_SYNC_HOUR=12
   TG_SHEET_SYNC_MINUTE=0
   TG_EVENING_REPORT_HOUR=17
   TG_EVENING_REPORT_MINUTE=0
   ```

6. Click **Create Web Service** → Chờ 2-3 phút

### 2.3 Verify deploy
- Xem **Logs** tab → Chờ dòng `Application started`
- Mở Telegram → Gửi `/help` → Bot trả lời ✅

---

## 📋 Checklist

- [ ] GitHub repo created & pushed
- [ ] Base64 credentials encoded
- [ ] Render service created
- [ ] Environment variables set (including GSERVICE_ACCOUNT_JSON_B64)
- [ ] Bot deployed & logs show "Application started"
- [ ] Test Telegram: `/help` → OK
- [ ] Test Telegram: `/status` → OK
- [ ] Test Telegram: `/daily_plan` → OK

---

## 🎉 Kết quả

✅ Bot chạy 24/7 trên cloud  
✅ Không cần máy công ty  
✅ Tương tác qua Telegram từ bất kỳ đâu  
✅ Auto-sync Google Sheet lúc 12:00  
✅ Miễn phí (Render $0, Google Sheets $0, Telegram $0)  

---

## 🐛 Troubleshoot nhanh

### Bot không phản hồi
- Render Logs → Có error? → Fix & re-push code
- Verify TELEGRAM_BOT_TOKEN hợp lệ
- Restart: Render dashboard → Manual Deploy

### Google Sheet sync fail
- Check GSERVICE_ACCOUNT_JSON_B64 decode đúng
- Verify service account có quyền access sheet
- Test local: `/sync_sheet_now` → Có error gì?

### Tắt local bot
```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*telegram_kpi_bot*' } | Remove-CimInstance -Force
```

---

**Bất kỳ issue gì, xem chi tiết tại DEPLOY_RENDER.md** 📖
