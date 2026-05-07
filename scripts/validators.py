from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any
import re
import unicodedata


@dataclass
class ValidationResult:
    ok: bool
    score: int
    issues: List[str]


def word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def _normalize(text: str) -> str:
    # Normalize Vietnamese accents and whitespace so rule checks are stable.
    prepared = text.replace("đ", "d").replace("Đ", "D")
    no_accent = unicodedata.normalize("NFKD", prepared).encode("ascii", "ignore").decode("ascii")
    no_accent = no_accent.lower()
    no_accent = re.sub(r"[^a-z0-9\s]", " ", no_accent)
    return re.sub(r"\s+", " ", no_accent).strip()


def validate_article(article: str, keyword: str, cfg: Dict[str, Any]) -> ValidationResult:
    issues: List[str] = []
    score = 100

    rules = cfg.get("validation", {})
    min_words = int(rules.get("min_words", 1000))
    max_words = int(rules.get("max_words", 3000))
    banned_phrases = [p.lower() for p in rules.get("banned_phrases", [])]
    required_sections = rules.get("required_sections", [])

    wc = word_count(article)
    if wc < min_words:
        issues.append(f"Too short: {wc} < {min_words}")
        score -= 20
    if wc > max_words:
        issues.append(f"Too long: {wc} > {max_words}")
        score -= 10

    normalized_article = _normalize(article)

    if rules.get("require_keyword_in_title", True):
        first_line = article.splitlines()[0] if article.splitlines() else ""
        if _normalize(keyword) not in _normalize(first_line):
            issues.append("Keyword not found in title")
            score -= 15

    lowered = article.lower()
    for phrase in banned_phrases:
        if phrase in lowered:
            issues.append(f"Banned phrase detected: {phrase}")
            score -= 20

    section_aliases = {
        "mo dau": ["mo dau", "gioi thieu", "tai sao", "tong quan"],
        "noi dung chinh": ["noi dung chinh", "quy trinh", "huong dan", "tool stack", "loi thuong gap"],
        "ket luan": ["ket luan", "tong ket", "chot lai", "ket lai"],
    }

    for section in required_sections:
        key = _normalize(section)
        aliases = section_aliases.get(key, [key])
        if not any(alias in normalized_article for alias in aliases):
            issues.append(f"Missing section: {section}")
            score -= 10

    if score < 0:
        score = 0

    return ValidationResult(ok=len(issues) == 0, score=score, issues=issues)
