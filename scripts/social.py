from __future__ import annotations

from pathlib import Path
from typing import Dict, List
import json


def repurpose_social(keyword: str, title: str, key_points: List[str], outputs_dir: Path) -> Dict[str, str]:
    snippets = {
        "facebook": f"{title}\n\n3 diem chinh ve {keyword}:\n- " + "\n- ".join(key_points[:3]),
        "linkedin": f"Case note: {title}\n\nApplied workflow for {keyword}:\n1) Research\n2) Draft\n3) Validate\n4) Publish",
        "tiktok": f"Hook: 3 sai lam khi lam {keyword}\nBody: " + " | ".join(key_points[:3]) + "\nCTA: Comment 'template' de nhan checklist",
        "youtube_shorts": f"Short script: Neu ban dang lam {keyword}, day la 3 buoc toi uu nhat de ra bai nhanh ma van dung chuan.",
    }
    outputs_dir.mkdir(parents=True, exist_ok=True)
    (outputs_dir / "social-snippets.json").write_text(
        json.dumps(snippets, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return snippets
