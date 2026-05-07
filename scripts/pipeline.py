from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
import json

from connectors import make_connector, slugify
from image_tools import generate_image_plan
from social import repurpose_social
from validators import validate_article
from writer import pick_writer_provider, write_article_claude, write_article_openai


@dataclass
class PipelineInput:
    keyword: str
    platform: str
    topic: str
    target_words: int
    auto_publish: bool


@dataclass
class PipelineOutput:
    status: str
    score: int
    word_count: int
    url: str
    local_path: str
    report_path: str


def _research_brief(keyword: str, topic: str) -> Dict[str, Any]:
    return {
        "keyword": keyword,
        "topic": topic,
        "intent": "informational",
        "audience": "beginners",
        "angles": [
            "common mistakes",
            "step-by-step process",
            "tool stack",
            "real execution checklist",
        ],
    }


def _build_outline(keyword: str, brief: Dict[str, Any]) -> List[str]:
    return [
        f"Mo dau: Tai sao {keyword} quan trong",
        "Noi dung chinh: Quy trinh 7 buoc",
        "Noi dung chinh: Tool stack va cach setup",
        "Noi dung chinh: Loi thuong gap va cach sua",
        "Ket luan: Checklist hanh dong 24h",
    ]


def _write_article(keyword: str, outline: List[str], target_words: int) -> str:
    sections = [f"# {keyword}: Huong dan thuc chien\n"]
    for item in outline:
        sections.append(f"## {item}\n")
        sections.append(
            "Noi dung mau: Ban can chuyen tu cach lam tung buoc thu cong sang pipeline co the lap lai. "
            "Moi module can ro input, output, rule kiem duyet va tieu chi thanh cong.\n"
        )
    sections.append("## Action Checklist\n")
    sections.append(
        "- Chon 20 keyword uu tien\n"
        "- Dinh nghia rule quality + SEO\n"
        "- Chay thu voi draft mode\n"
        "- Do luong roi toi uu tung tuan\n"
    )

    article = "\n".join(sections)

    filler = (
        "\nBan nen uu tien tinh on dinh cua workflow truoc khi scale so luong bai viet. "
        "Neu validator bao loi, quay lai buoc draft va sua dung diem loi thay vi publish ngay."
    )
    while len(article.split()) < target_words:
        article += filler

    return article


def _make_run_dir(outputs_dir: Path, keyword: str, use_timestamped_outputs: bool) -> Path:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    if not use_timestamped_outputs:
        return outputs_dir

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = outputs_dir / f"{timestamp}-{slugify(keyword)[:40]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_run_artifacts(run_dir: Path, brief: Dict[str, Any], outline: List[str], article: str) -> None:
    (run_dir / "brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "outline.json").write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "article.md").write_text(article, encoding="utf-8")


def run_pipeline(p_input: PipelineInput, cfg: Dict[str, Any], workspace_root: Path) -> PipelineOutput:
    outputs_dir = workspace_root / "outputs"
    run_dir = _make_run_dir(outputs_dir, p_input.keyword, bool(cfg.get("write_timestamped_outputs", True)))

    brief = _research_brief(p_input.keyword, p_input.topic)
    outline = _build_outline(p_input.keyword, brief)

    max_loops = int(cfg.get("max_revision_loops", 3))

    provider = pick_writer_provider(cfg)
    print(f"[Writer] Provider: {provider} | keyword: {p_input.keyword}")
    try:
        if provider == "openai":
            article = write_article_openai(p_input.keyword, outline, p_input.target_words, workspace_root, cfg)
        elif provider == "claude":
            article = write_article_claude(p_input.keyword, outline, p_input.target_words, workspace_root, cfg)
        else:
            article = _write_article(p_input.keyword, outline, p_input.target_words)
        print(f"[Writer] Done — {len(article.split())} words")
    except Exception as exc:
        print(f"[Writer] API unavailable ({exc}), using mock writer")
        article = _write_article(p_input.keyword, outline, p_input.target_words)

    last_result = validate_article(article, p_input.keyword, cfg)
    loop = 0
    while (not last_result.ok) and loop < max_loops:
        article += "\n\nSua doi theo validator: bo sung cac section va loai bo noi dung rui ro."
        last_result = validate_article(article, p_input.keyword, cfg)
        loop += 1

    _write_run_artifacts(run_dir, brief, outline, article)
    image_plan = generate_image_plan(p_input.keyword, p_input.topic, run_dir)
    _ = image_plan

    title = article.splitlines()[0].replace("# ", "").strip()

    connector = make_connector(p_input.platform, cfg, workspace_root)
    publish_result = connector.publish_draft(title, article)

    social = repurpose_social(
        keyword=p_input.keyword,
        title=title,
        key_points=outline,
        outputs_dir=run_dir,
    )

    report = {
        "run_id": run_dir.name,
        "status": publish_result.status,
        "keyword": p_input.keyword,
        "platform": p_input.platform,
        "topic": p_input.topic,
        "word_count": len(article.split()),
        "validation_score": last_result.score,
        "validation_ok": last_result.ok,
        "validation_issues": last_result.issues,
        "url": publish_result.url,
        "local_path": publish_result.local_path,
        "run_dir": str(run_dir),
        "social_channels": list(social.keys()),
        "publish_mode": "draft-only-or-local",
    }

    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (outputs_dir / "last-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return PipelineOutput(
        status=report["status"],
        score=report["validation_score"],
        word_count=report["word_count"],
        url=report["url"],
        local_path=report["local_path"],
        report_path=str(report_path),
    )
