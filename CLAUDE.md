# CLAUDE.md -- Du An TikTok Loki Tran x GIP Fulfillment

## Day La Gi

Workspace nay quan ly toan bo noi dung TikTok cho kenh **Loki Tran** (@loki_tran) -- kenh ca nhan phuc vu chien luoc truyen thong cua GIP Fulfillment.

Khong phai kenh giai tri. Day la kenh B2B nham toi seller Viet muon ban hang sang thi truong Dong Nam A, dac biet Philippines.

---

## Ve GIP Fulfillment

GIP Fulfillment la don vi van hanh TMDT tai **Philippines**, phuc vu seller Viet va brand Viet muon ban hang sang thi truong nay.

**Nang luc cot loi:**
- Luu kho, dong goi, xu ly don hang tai Philippines
- Telesale xac nhan don va doi ngu CSR ban dia
- Giao hang noi dia Philippines
- COD va doi soat minh bach
- Ho tro van hanh Shopee / TikTok Shop Philippines

**Thong tin lien he:**
- Website: gipfulfillment.com
- Facebook: facebook.com/GIPVietNam (3.2K followers)
- Zalo: 0372345694
- Email: sales@ecomsea.co
- Dia chi VN: 275 Nguyen Trai, Thanh Xuan, Ha Noi
- Dia chi Philippines: Valenzuela, NCR Third District, Philippines

**CTA uu tien (theo thu tu):**
1. Inbox Fanpage GIP
2. Zalo
3. Form lien he tai gipfulfillment.com/lien-he

---

## Kenh TikTok Loki Tran

**Handle:** @loki_tran
**Bio hien tai:** Kenh chia se goc nhin thuc te ve TMDT Dong Nam A
**Chi so hien tai (thang 5/2026):**
- Followers: 1,578
- Likes tong: 8,927

**Dinh vi kenh:**
- Nguoi chia se goc nhin thuc chien, khong phai agency hay chuyen gia sao rong
- Tone: chuyen gia thuc te, noi thang pain point, khong dung giong hoa my
- Noi dung xoay quanh van hanh TMDT Dong Nam A, dac biet Philippines

**Doi tuong muc tieu:**
- Seller Viet muon mo rong sang Philippines
- Nguoi moi bat dau ban hang online, chua biet logistics/fulfillment
- Brand Viet quan tam go-global

---

## Kenh TikTok GIP Philippines (kenh phu)

**Handle:** @gipecomphilippines
**Bio:** E-commerce Services in Philippines -- Fulfillment | COD | Logistics | Warehousing
**Chi so hien tai:** 12 followers, 75 likes -- kenh moi, chua co noi dung

---

## KPI Thang Cua Kenh Loki Tran

| Hang muc | Muc tieu |
|---|---|
| Tong so clip | 20 |
| Tong view | 30,000 |
| 1 clip dat | >= 10,000 view |
| 1 clip dat | >= 5,000 view |
| 18 clip con lai | 1,000-1,500 view/clip |

**Thu nhap theo KPI:**
- Luong co ban: 10,000,000 d
- 5 clip + 5K view: luong co ban
- 10 clip + 10K view: +3,000,000 d
- 15 clip + 25K view: +4,000,000 d
- 20 clip + 30K view: +5,000,000 d

**Thuong them:**
- 1 clip dat 10K view: +1,000,000 d
- 1 clip dat 50K view: +5,000,000 d
- 1 clip dat 100K view: +10,000,000 d

---

## Bot Nhac KPI

File: scripts/telegram_kpi_bot.py

Bot Telegram chay doc lap, nhac nho tu dong theo lich va tinh toan KPI realtime.

Cai dat: pip install python-telegram-bot[job-queue]==21.6
Chay: python scripts/telegram_kpi_bot.py

Can thiet lap trong .env:
- TELEGRAM_BOT_TOKEN=<token tu @BotFather>
- TG_REMINDER_TIMES=09:00,13:00,20:30
- TG_MORNING_STATUS_HOUR=08
- TG_MORNING_STATUS_MINUTE=30

Lenh bot:
- /start -- kich hoat chat
- /status -- xem tien do KPI
- /add_clip <views> [yyyy-mm-dd] -- them clip
- /set_views <id> <views> -- sua view
- /list_clips -- danh sach clip thang
- /nudge_now -- nhac ngay

Du lieu luu tai: outputs/loki-kpi-bot/state.json

---

## Cac Kenh Khac Cua GIP

| Kenh | Link | Chi so |
|---|---|---|
| YouTube | youtube.com/@GIP.FULFILLMENT | 239 subscribers, 29 videos |
| Facebook | facebook.com/GIPVietNam | 3,200 followers |
| Website | gipfulfillment.com | WordPress + Rank Math |

YouTube hien chu yeu la video noi bo (recap, YEP, TVC, MV sinh nhat) -- chua co series content B2B cho seller.

---

## Nguyen Tac Noi Dung TikTok

**Lam:**
- Hook manh trong 2 giay dau
- Noi thang pain point cua seller
- Kem so lieu thuc te hoac vi du cu the
- CTA ro: inbox, Zalo, hoac comment keyword
- Moi video phai tra loi duoc: nguoi xem dang gap van de gi?

**Khong lam:**
- Khong cam ket doanh thu, loi nhuan
- Khong bia so lieu thi truong
- Khong giong agency/sao rong
- Khong quang cao lo lieu kieu GIP tot nhat
- Khong de cap muc thue co dinh neu khong co can cu

---

## Chu De Uu Tien Hien Tai

1. Ban hang sang Philippines -- bat dau tu dau
2. COD Philippines -- hoat dong the nao, rui ro gi
3. Shopee / TikTok Shop Philippines -- setup va van hanh
4. Chi phi thuc te khi di thi truong Philippines
5. Fulfillment la gi, khi nao can thue
6. Sai lam pho bien cua seller Viet khi go-global

---

## Vai Tro Cua Claude Trong Workspace Nay

- KPI Tracker: doc state.json, tinh toan tien do, de xuat dieu chinh
- Content Strategist: de xuat chu de video, hook, angle phu hop audience
- Copywriter: viet script TikTok, caption, reply comment keo lead
- Analyst: phan tich video nao keo reach, noi dung nao co kha nang inbox

Claude khong tu dang bai, khong tu publish. Moi noi dung deu qua review truoc.
