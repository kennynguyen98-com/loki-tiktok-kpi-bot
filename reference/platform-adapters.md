# Platform Adapter — TikTok Slideshow

## Output của workflow

Workflow không auto-publish lên TikTok.
Tất cả asset được tạo local, Kenny đăng tay.

## Cấu trúc output mỗi bài

```
outputs/
  [YYYY-MM-DD-chu-de]/
    slide-script.md        ← text từng slide
    caption.txt            ← caption + hashtag
    reply-script.md        ← script reply comment
    report.json            ← meta: loại bài, số slide, hook, CTA
```

## File ảnh

- Đặt vào: `assets/tiktok-images/[YYYY-MM-DD-chu-de]/`
- Tên file: `slide-01.png`, `slide-02.png`...
- Tỉ lệ: 9:16
- Tạo bằng: Canva hoặc CapCut (dùng template cố định)

## Nhạc trend

- Chọn thủ công trong TikTok trước khi đăng
- Ưu tiên: instrumental / lofi / upbeat không có lời
- Không nên dùng nhạc có lời mạnh — lấn át text slide
