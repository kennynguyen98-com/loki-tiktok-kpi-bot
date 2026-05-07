from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import textwrap
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

SAFE_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class TiktokPost:
    day: int
    publish_date: str
    post_index: int
    audience: str
    style: str
    lead_magnet: str
    hook: str
    slide_texts: List[str]
    caption: str
    cta: str
    dm_keyword: str
    hashtags: List[str]
    image_source: str
    image_output: str


def slugify(text: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean.strip("-") or "untitled"


def _pick_images(image_dir: Path) -> List[Path]:
    if not image_dir.exists():
        return []
    files = [p for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in SAFE_IMAGE_EXTS]
    files.sort()
    return files


def _build_audience_pool() -> List[str]:
    return [
        "hoc sinh cap 3 o Viet Nam",
        "sinh vien dai hoc o Viet Nam",
        "nguoi di lam can tang IELTS nhanh",
        "nguoi Viet dang o Han Quoc muon sang Phil hoc",
        "nguoi Viet dang lam viec tai Philippines",
    ]


def _build_style_pool() -> List[str]:
    return [
        "chuyen gia nghiem tuc",
        "chia se thuc chien",
        "boc phot sai lam",
    ]


def _build_lead_magnets() -> List[str]:
    return [
        "checklist du hoc Philippines 2026",
        "bang chi phi thuc te theo tung truong",
        "lo trinh tang diem IELTS 3 thang tai Cebu",
        "bo tai lieu xin hoc bong va giam phi",
        "mau ke hoach hoc + sinh hoat 12 tuan",
        "bang so sanh Phil vs Viet Nam ve toc do len band",
    ]


def _build_hooks(topic: str) -> List[str]:
    return [
        f"3 sai lam khien ban ton gap doi chi phi khi {topic}",
        f"Neu toi hoc lai tu dau, toi se lam gi khi {topic}?",
        f"Du hoc Philippines co that su re hon? Day la so lieu that",
        f"Nguoi Viet o nuoc ngoai co nen bay sang Phil hoc IELTS khong?",
        f"1 lo trinh 90 ngay de tang diem IELTS nhanh hon o Viet Nam",
    ]


def _slide_pack(audience: str, magnet: str, style: str) -> List[str]:
    return [
        f"Tuong tac cao voi tep: {audience}",
        "Van de: hoc lau nhung diem len cham",
        "Giai phap: hoc tap trung 8-12 tuan tai Philippines",
        f"Qua tang inbox: {magnet}",
        "Comment tu khoa de nhan tai lieu + tu van 1:1",
    ]


def _caption(topic: str, audience: str, magnet: str, cta: str) -> str:
    return (
        f"{topic} cho {audience}: khong can noi ly thuyet dai dong. "
        f"Toi gom san bo tai lieu {magnet}. "
        f"{cta}"
    )


def _hashtags() -> List[str]:
    return [
        "#duhocphilippines",
        "#hocielts",
        "#ielts",
        "#duhoc",
        "#cebu",
        "#studyabroad",
        "#kennyphilippines",
    ]


def _save_calendar(posts: List[TiktokPost], out_file: Path) -> None:
    with out_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "publish_date",
            "day",
            "post_index",
            "audience",
            "style",
            "lead_magnet",
            "hook",
            "dm_keyword",
            "caption",
            "hashtags",
            "image_source",
            "image_output",
        ])
        for p in posts:
            writer.writerow([
                p.publish_date,
                p.day,
                p.post_index,
                p.audience,
                p.style,
                p.lead_magnet,
                p.hook,
                p.dm_keyword,
                p.caption,
                " ".join(p.hashtags),
                p.image_source,
                p.image_output,
            ])


def _save_captions(posts: List[TiktokPost], out_file: Path) -> None:
    lines: List[str] = []
    for p in posts:
        lines.append(f"=== Day {p.day} | Post {p.post_index} ===")
        lines.append(f"Hook: {p.hook}")
        lines.append(f"Caption: {p.caption}")
        lines.append(f"CTA: {p.cta}")
        lines.append(f"Tu khoa inbox: {p.dm_keyword}")
        lines.append(f"Hashtags: {' '.join(p.hashtags)}")
        lines.append("Slides:")
        for idx, s in enumerate(p.slide_texts, start=1):
            lines.append(f"  {idx}. {s}")
        lines.append("")

    out_file.write_text("\n".join(lines), encoding="utf-8")


def _save_dm_playbook(out_file: Path) -> None:
    content = """# TikTok DM Playbook (Inbox -> Zalo/Facebook)

## Muc tieu
- Chot thong tin lien he nhanh
- Loc lead nong / am
- Dat lich tu van trong 24h

## Script 1: Auto-reply khi vao inbox
Chao ban, minh da nhan tin roi nhe.
Ban comment tu khoa nao de minh gui dung bo tai lieu?
(1) Checklist du hoc
(2) Bang chi phi
(3) Lo trinh IELTS 3 thang

## Script 2: Xin thong tin toi thieu
De minh tu van sat nhat, ban gui giup minh 4 thong tin:
- Muc tieu diem IELTS
- Thoi gian co the di hoc
- Ngan sach du kien
- Ban dang o Viet Nam hay dang o nuoc ngoai

## Script 3: Keo qua Zalo/Facebook
Minh gui bang chi phi va lo trinh chi tiet qua Zalo/Facebook cho de theo doi nhe.
Ban de lai Zalo hoac link Facebook, minh gui trong 5 phut.

## Script 4: Follow-up 24h
Hom qua minh da gui tai lieu. Ban da xem den phan chi phi chua?
Neu can, minh len luon 1 lo trinh ca nhan hoa 15 phut free cho ban.

## Rule van hanh
- Khong tranh luan dai trong comment
- Dua lead vao inbox cang som cang tot
- Moi inbox phai co 1 hanh dong tiep theo ro rang
"""
    out_file.write_text(content, encoding="utf-8")


def _save_lead_tracker_template(out_file: Path) -> None:
    rows = [
        [
            "created_at",
            "source_video",
            "tiktok_handle",
            "full_name",
            "segment",
            "ielts_target",
            "timeline",
            "budget",
            "current_location",
            "contact_channel",
            "contact_value",
            "status",
            "next_action",
            "next_action_date",
            "owner",
            "notes",
        ],
        [
            "2026-04-21 10:00",
            "day1-post1",
            "@abc",
            "Nguyen Van A",
            "nguoi di lam",
            "6.5",
            "3 thang",
            "70 trieu",
            "Viet Nam",
            "zalo",
            "09xxxxxx",
            "new",
            "gui bang chi phi",
            "2026-04-22",
            "Kenny",
            "quan tam Cebu",
        ],
    ]

    with out_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def _try_render_images(posts: List[TiktokPost], media_dir: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception:
        print("[Image] Pillow chua co. Bo qua buoc chen text len anh. Cai bang: pip install pillow")
        return

    media_dir.mkdir(parents=True, exist_ok=True)
    for p in posts:
        src = Path(p.image_source)
        if not src.exists():
            continue

        try:
            img = Image.open(src).convert("RGB")
        except Exception:
            continue

        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 54)
            sub_font = ImageFont.truetype("arial.ttf", 34)
        except Exception:
            font = ImageFont.load_default()
            sub_font = ImageFont.load_default()

        overlay_title = textwrap.fill(p.hook, width=24)
        overlay_cta = textwrap.fill(f"Inbox: {p.dm_keyword}", width=26)

        pad = 40
        draw.rectangle([(0, 0), (img.width, int(img.height * 0.24))], fill=(0, 0, 0, 150))
        draw.rectangle([(0, int(img.height * 0.82)), (img.width, img.height)], fill=(0, 0, 0, 150))

        draw.text((pad, 30), overlay_title, fill=(255, 255, 255), font=font)
        draw.text((pad, int(img.height * 0.86)), overlay_cta, fill=(255, 255, 255), font=sub_font)

        out = Path(p.image_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, format="PNG", optimize=True)


def build_posts(
    keyword: str,
    start_date: datetime,
    days: int,
    posts_per_day: int,
    image_paths: List[Path],
    run_media_dir: Path,
) -> List[TiktokPost]:
    audiences = _build_audience_pool()
    styles = _build_style_pool()
    magnets = _build_lead_magnets()
    hooks = _build_hooks(keyword)

    posts: List[TiktokPost] = []
    image_idx = 0

    for day in range(1, days + 1):
        publish_date = (start_date + timedelta(days=day - 1)).strftime("%Y-%m-%d")
        for post_idx in range(1, posts_per_day + 1):
            seed = (day * 100) + post_idx
            rnd = random.Random(seed)
            audience = audiences[(day + post_idx) % len(audiences)]
            style = styles[(day + post_idx) % len(styles)]
            magnet = magnets[(day + post_idx) % len(magnets)]
            hook = hooks[rnd.randrange(len(hooks))]

            dm_keyword = f"PHIL{day:02d}{post_idx:02d}"
            cta = f"Comment '{dm_keyword}' de nhan qua + tu van giam phi"
            slides = _slide_pack(audience, magnet, style)
            caption = _caption(keyword, audience, magnet, cta)

            if image_paths:
                src = image_paths[image_idx % len(image_paths)]
                image_idx += 1
            else:
                src = Path("")

            output_name = f"day{day:02d}-post{post_idx:02d}.png"
            output_path = run_media_dir / output_name

            posts.append(
                TiktokPost(
                    day=day,
                    publish_date=publish_date,
                    post_index=post_idx,
                    audience=audience,
                    style=style,
                    lead_magnet=magnet,
                    hook=hook,
                    slide_texts=slides,
                    caption=caption,
                    cta=cta,
                    dm_keyword=dm_keyword,
                    hashtags=_hashtags(),
                    image_source=str(src),
                    image_output=str(output_path),
                )
            )

    return posts


def _ensure_run_dir(outputs_root: Path, keyword: str) -> Path:
    outputs_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-tiktok-{slugify(keyword)[:36]}"
    run_dir = outputs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _copy_raw_images(image_paths: List[Path], target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for p in image_paths:
        dst = target_dir / p.name
        if not dst.exists():
            try:
                shutil.copy2(p, dst)
            except Exception:
                continue


def main() -> None:
    parser = argparse.ArgumentParser(description="Build TikTok text+image leadgen package for Philippines study-abroad")
    parser.add_argument("--keyword", required=True, help="Main topic, ex: du hoc Philippines")
    parser.add_argument("--days", type=int, default=14, help="Number of days to generate")
    parser.add_argument("--posts-per-day", type=int, default=1, help="Posts per day (1 or 2 recommended)")
    parser.add_argument("--image-dir", default="", help="Folder containing source images")
    parser.add_argument("--start-date", default="", help="YYYY-MM-DD, default today")
    parser.add_argument("--outputs-dir", default="outputs", help="Output root folder")
    args = parser.parse_args()

    if args.days < 1:
        raise ValueError("--days must be >= 1")
    if args.posts_per_day < 1:
        raise ValueError("--posts-per-day must be >= 1")

    outputs_root = Path(args.outputs_dir)
    run_dir = _ensure_run_dir(outputs_root, args.keyword)
    media_dir = run_dir / "media"
    raw_dir = run_dir / "raw-images"

    start_date = datetime.now()
    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d")

    image_paths: List[Path] = []
    if args.image_dir:
        image_paths = _pick_images(Path(args.image_dir))
        _copy_raw_images(image_paths, raw_dir)

    posts = build_posts(
        keyword=args.keyword,
        start_date=start_date,
        days=args.days,
        posts_per_day=args.posts_per_day,
        image_paths=image_paths,
        run_media_dir=media_dir,
    )

    _try_render_images(posts, media_dir)

    payload = {
        "run_id": run_dir.name,
        "keyword": args.keyword,
        "days": args.days,
        "posts_per_day": args.posts_per_day,
        "total_posts": len(posts),
        "generated_at": datetime.now().isoformat(),
        "posts": [asdict(p) for p in posts],
        "notes": {
            "music": "Dung nhac trend nhe, uu tien beat nhanh 95-125 BPM, volume 18-25% duoi voice/text.",
            "publish": "TikTok auto-publish qua API co gioi han, workflow nay tao bo asset de dang nhanh ban-tu-dong.",
        },
    }

    (run_dir / "tiktok-plan.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _save_calendar(posts, run_dir / "posting-calendar.csv")
    _save_captions(posts, run_dir / "captions.txt")
    _save_dm_playbook(run_dir / "lead-inbox-playbook.md")
    _save_lead_tracker_template(run_dir / "lead-tracker-template.csv")

    quick_start = textwrap.dedent(
        f"""
        TikTok leadgen package da tao xong.

        Run dir: {run_dir}
        Tong so post: {len(posts)}

        Thu tu thuc thi moi ngay:
        1) Mo posting-calendar.csv -> lay post cua ngay hom nay.
        2) Dung media/dayXX-postYY.png (neu co) hoac raw-images de edit nhanh tren CapCut.
        3) Copy caption tu captions.txt.
        4) Dang TikTok + ghim comment CTA voi dm_keyword.
        5) Tra loi inbox theo lead-inbox-playbook.md.
        6) Cap nhat lead-tracker-template.csv.
        """
    ).strip()
    (run_dir / "README-quick-start.txt").write_text(quick_start, encoding="utf-8")

    print("=== TIKTOK LEADGEN PACKAGE ===")
    print(f"run_dir: {run_dir}")
    print(f"total_posts: {len(posts)}")
    print("files:")
    print(f"- {run_dir / 'tiktok-plan.json'}")
    print(f"- {run_dir / 'posting-calendar.csv'}")
    print(f"- {run_dir / 'captions.txt'}")
    print(f"- {run_dir / 'lead-inbox-playbook.md'}")
    print(f"- {run_dir / 'lead-tracker-template.csv'}")
    print(f"- {run_dir / 'README-quick-start.txt'}")


if __name__ == "__main__":
    main()
