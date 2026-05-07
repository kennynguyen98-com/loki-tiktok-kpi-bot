from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List
import json


def generate_image_plan(keyword: str, topic: str, outputs_dir: Path) -> List[Dict[str, str]]:
    prompts = [
        {
            "slot": "cover",
            "prompt": f"Editorial cover image about {keyword} in {topic} style, clean composition, modern colors",
        },
        {
            "slot": "section-1",
            "prompt": f"Supporting infographic style visual for key advice about {keyword}",
        },
        {
            "slot": "section-2",
            "prompt": f"Realistic travel scene illustrating practical tips for {keyword}",
        },
    ]

    outputs_dir.mkdir(parents=True, exist_ok=True)
    manifest = outputs_dir / "image-plan.json"
    manifest.write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")
    return prompts
