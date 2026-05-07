from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pipeline import PipelineInput, run_pipeline


SAFE_TRUE_VALUES = {"1", "true", "yes", "y", "on"}


def _load_env(workspace_root: Path) -> None:
    env_file = workspace_root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def to_bool(value: str) -> bool:
    return str(value).strip().lower() in SAFE_TRUE_VALUES


def _warn_publish_request(cli_value: str, cfg: dict) -> None:
    requested = None
    if cli_value:
        requested = to_bool(cli_value)
    elif "auto_publish" in cfg:
        requested = bool(cfg.get("auto_publish", False))

    if requested:
        print("[Safety] Live publish requests are ignored. Pipeline runs in local-only or draft-only mode.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run content agent pipeline")
    parser.add_argument("--keyword", required=True, help="Main keyword to process")
    parser.add_argument("--platform", default="", help="wordpress|shopify|generic")
    parser.add_argument("--topic", default="", help="Topic bucket")
    parser.add_argument("--target-words", type=int, default=0, help="Target article length")
    parser.add_argument("--auto-publish", default="", help="Deprecated. Ignored for safety.")
    args = parser.parse_args()

    workspace_root = Path(__file__).resolve().parents[1]
    _load_env(workspace_root)
    cfg = json.loads((workspace_root / "config.json").read_text(encoding="utf-8"))

    _warn_publish_request(args.auto_publish, cfg)
    if bool(cfg.get("allow_live_publish", False)):
        print("[Safety] config allow_live_publish=true is ignored. Live publish is disabled by code.")
    cfg["auto_publish"] = False
    cfg["allow_live_publish"] = False

    p_input = PipelineInput(
        keyword=args.keyword,
        platform=args.platform or cfg.get("default_platform", "generic"),
        topic=args.topic or cfg.get("default_topic", "general"),
        target_words=args.target_words or int(cfg.get("default_target_words", 1400)),
        auto_publish=False,
    )

    output = run_pipeline(p_input, cfg, workspace_root)

    print("=== PIPELINE RESULT ===")
    print(f"status: {output.status}")
    print(f"score: {output.score}")
    print(f"word_count: {output.word_count}")
    print(f"url: {output.url}")
    print(f"local_path: {output.local_path}")
    print(f"report: {output.report_path}")


if __name__ == "__main__":
    main()
