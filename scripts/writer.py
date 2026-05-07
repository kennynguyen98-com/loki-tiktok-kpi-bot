from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Any, List


def _load_context(workspace_root: Path) -> str:
    """Load brand context files to inject into the prompt."""
    ctx_dir = workspace_root / "context"
    parts = []
    for fname in ["profile.md", "strategy.md", "voice.md"]:
        f = ctx_dir / fname
        if f.exists():
            parts.append(f"### {fname}\n{f.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _load_skills(workspace_root: Path) -> str:
    skills_dir = workspace_root / ".claude" / "skills"
    if not skills_dir.exists():
        return ""

    ordered = [
        "content-creation.skill.md",
        "00-gip-core.skill.md",
        "10-research.skill.md",
        "20-outline.skill.md",
        "30-writing.skill.md",
        "40-seo-validator.skill.md",
        "50-wordpress-publish.skill.md",
    ]

    parts: List[str] = []
    for fname in ordered:
        f = skills_dir / fname
        if f.exists():
            parts.append(f"### {fname}\n{f.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _build_prompt(keyword: str, outline: List[str], target_words: int, context_text: str, skill_text: str) -> str:
    sections = "\n".join(f"- {s}" for s in outline)
    return f"""Bạn là chuyên gia content marketing cho lĩnh vực eCommerce và logistics Đông Nam Á.

## Brand context
{context_text}

## Skill stack (must follow)
{skill_text}

## Nhiệm vụ
Viết một bài blog hoàn chỉnh bằng tiếng Việt về từ khóa: **{keyword}**

## Yêu cầu cấu trúc
{sections}

## Yêu cầu chất lượng
- Độ dài: khoảng {target_words} từ
- Dùng heading H2/H3 rõ ràng
- Đoạn văn ngắn 2-4 dòng
- Có số liệu thực tế khi có thể
- Kết bài có CTA nhẹ về dịch vụ GIP Fulfillment
- KHÔNG hứa hẹn lợi nhuận hay thành công 100%
- Tiêu đề bài phải chứa từ khóa chính

Trả về bài viết đầy đủ, bắt đầu bằng dòng: # [Tiêu đề bài viết]
"""


def write_article_claude(
    keyword: str,
    outline: List[str],
    target_words: int,
    workspace_root: Path,
    cfg: Dict[str, Any],
) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    try:
        import anthropic
    except ImportError:
        import subprocess
        print("Installing anthropic SDK...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic", "-q"])
        import anthropic

    context_text = _load_context(workspace_root)
    skill_text = _load_skills(workspace_root)
    prompt = _build_prompt(keyword, outline, target_words, context_text, skill_text)

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


def write_article_openai(
    keyword: str,
    outline: List[str],
    target_words: int,
    workspace_root: Path,
    cfg: Dict[str, Any],
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set in .env")

    try:
        from openai import OpenAI
    except ImportError:
        import subprocess
        print("Installing openai SDK...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "-q"])
        from openai import OpenAI

    context_text = _load_context(workspace_root)
    skill_text = _load_skills(workspace_root)
    prompt = _build_prompt(keyword, outline, target_words, context_text, skill_text)

    client = OpenAI(api_key=api_key)
    model = cfg.get("openai_model", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        temperature=0.4,
        messages=[
            {
                "role": "system",
                "content": "You are a Vietnamese B2B ecommerce content writer for logistics and fulfillment topics.",
            },
            {"role": "user", "content": prompt},
        ],
    )

    return resp.choices[0].message.content or ""


def pick_writer_provider(cfg: Dict[str, Any]) -> str:
    forced = str(cfg.get("writer_provider", "auto")).strip().lower()
    if forced in {"openai", "claude"}:
        return forced
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "claude"
    return "mock"
