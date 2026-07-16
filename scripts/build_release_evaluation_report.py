from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "release_eval"

from scripts.evaluate_chat_grounding import summarize_grounding_reports


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def report_source_name(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)) if path.is_absolute() else str(path)


def build_release_report(report_paths: list[Path]) -> dict:
    created_at = datetime.now().astimezone()
    release_reports = []
    source_reports = []

    for path in report_paths:
        payload = load_report(path)
        reports = payload.get("reports") or []
        source_reports.append(
            {
                "path": report_source_name(path),
                "run_id": payload.get("run_id", path.stem),
                "report_count": int(payload.get("report_count") or len(reports)),
            }
        )
        for report in reports:
            item = dict(report)
            item["source_report"] = report_source_name(path)
            release_reports.append(item)

    return {
        "run_id": created_at.strftime("%Y-%m-%d_%H-%M-%S"),
        "created_at": created_at.isoformat(timespec="seconds"),
        "script": "scripts/build_release_evaluation_report.py",
        "source_reports": source_reports,
        "report_count": len(release_reports),
        "summary": summarize_grounding_reports(release_reports),
        "reports": release_reports,
    }


def save_release_report(payload: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{payload['run_id']}.json"
    counter = 1
    while output_path.exists():
        output_path = output_dir / f"{payload['run_id']}-{counter}.json"
        counter += 1
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a release evaluation report from grounding reports.")
    parser.add_argument("reports", nargs="+", type=Path, help="Grounding report JSON files to include.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_release_report(args.reports)
    output_path = save_release_report(payload, args.output_dir)
    print(f"Saved release evaluation report: {output_path}")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
