# Skill Pipeline TikTok — Điều Phối

## Mục tiêu

Biến 1 chủ đề thành 1 bộ asset TikTok đầy đủ.

Skill này điều phối các skill khác:

- 00-gip-core.skill.md
- 10-research.skill.md
- 20-outline.skill.md
- 30-writing.skill.md
- 40-seo-validator.skill.md
- 50-wordpress-publish.skill.md
- 60-lead-inbox.skill.md

## Đầu vào

- chu_de (ví dụ: "chi phí học tiếng Anh tại Philippines")
- loai_bai: thông-tin | cam-xuc | cta (mặc định: tự xác định theo research)

## Trình tự bắt buộc

1. Nghiên cứu: xác định loại bài, angle, hook
2. Cấu trúc slide: tạo khung theo loại bài
3. Viết: text slide + caption + hashtag
4. Viết script reply comment
5. Kiểm tra: rà hook, CTA, claim
6. Sửa nếu fail (tối đa 3 vòng)
7. Xuất output + report

## Tiêu chí chất lượng

- Slide 1 có hook
- Slide cuối có CTA
- Không có cụm từ cấm
- Số slide trong khoảng 5–10
- Có ít nhất 1 con số/dữ liệu cụ thể

## Gói đầu ra

- `slide-script.md` — text từng slide
- `caption.txt` — caption + hashtag
- `reply-script.md` — script reply comment
- `report.json` — loại bài, số slide, hook, CTA được dùng
