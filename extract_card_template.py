from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from extract_computaform import (
    clean_text,
    clean_track_notes,
    extract,
    output_folder_for_pdf,
    slug_from_pdf_name,
    write_outputs,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ROOT / "outputs"
DEFAULT_MANIFEST = ROOT / "site_manifest.json"
TEMPLATE_VERSION = "2026-07-12"


@dataclass
class ExtractionContext:
    pdf_path: Path
    output_dir: Path
    applied_handlers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


AnomalyHandler = Callable[[dict[str, Any], ExtractionContext], int]


def add_unique_warning(data: dict[str, Any], warning: str) -> None:
    validation = data.setdefault("validation", {})
    warnings = validation.setdefault("warnings", [])
    if warning not in warnings:
        warnings.append(warning)


def repair_track_note_noise(data: dict[str, Any], context: ExtractionContext) -> int:
    """Remove map/ad fragments if they leak into track or draw-bias notes."""
    meeting = data.get("meeting", {})
    changed = 0
    for key in ("track_notes", "draw_bias_notes"):
        original = clean_text(meeting.get(key, ""))
        if not original:
            continue
        cleaned = clean_track_notes(original)
        if cleaned and cleaned != original:
            meeting[key] = cleaned
            changed += 1
    if changed:
        context.notes.append(f"Cleaned track/draw note noise in {changed} field(s).")
    return changed


def repair_past_run_headgear_jockey_leaks(data: dict[str, Any], context: ExtractionContext) -> int:
    """Move OCR-shifted shoes/headgear and MR tokens out of jockey names."""
    pattern = re.compile(r"^(?P<headgear>[AB]{1,2}[A-Z]?t?|H)\s+(?P<mr>\d+|--|X)\s+(?P<jockey>.+)$")
    changed = 0
    for runner in data.get("runners", []):
        for row in runner.get("past_runs", []):
            jockey = clean_text(row.get("jockey", ""))
            match = pattern.match(jockey)
            if not match:
                continue
            if not row.get("shoes_headgear"):
                row["shoes_headgear"] = match.group("headgear")
            if not row.get("official_merit_rating"):
                row["official_merit_rating"] = match.group("mr")
            row["jockey"] = match.group("jockey")
            changed += 1
    if changed:
        context.notes.append(f"Repaired {changed} past-run jockey/headgear token leak(s).")
    return changed


def audit_quality_counts(data: dict[str, Any], context: ExtractionContext) -> int:
    """Add reusable quality counts that make anomalies visible after each run."""
    runners = data.get("runners", [])
    past_rows = [row for runner in runners for row in runner.get("past_runs", [])]
    collateral_rows = [row for runner in runners for row in runner.get("collateral_formlines", [])]

    headgear_leaks = [
        row
        for row in past_rows
        if re.match(r"^(?:[AB]{1,2}[A-Z]?t?|H)\s+(?:\d+|--|X)\s+", clean_text(row.get("jockey", "")))
    ]
    past_missing_core = [
        row
        for row in past_rows
        if not row.get("date")
        or not row.get("course")
        or not row.get("weight_allowance")
        or not row.get("draw_runners")
    ]
    nonzero_life_no_past = [
        runner
        for runner in runners
        if runner.get("career_record", {}).get("life")
        and not clean_text(runner["career_record"]["life"]).startswith("0-")
        and not runner.get("past_runs")
    ]

    quality = {
        "template_version": TEMPLATE_VERSION,
        "runners": str(len(runners)),
        "past_performance_rows": str(len(past_rows)),
        "collateral_formline_rows": str(len(collateral_rows)),
        "missing_draw": str(sum(1 for runner in runners if not runner.get("draw"))),
        "missing_computaform_rating": str(sum(1 for runner in runners if not runner.get("computaform_rating"))),
        "missing_speed_rating": str(sum(1 for runner in runners if not runner.get("speed_rating"))),
        "past_rows_missing_core_fields": str(len(past_missing_core)),
        "past_run_headgear_jockey_leaks": str(len(headgear_leaks)),
        "runners_with_nonzero_life_record_but_no_past_runs": str(len(nonzero_life_no_past)),
    }
    data.setdefault("validation", {})["template_quality_checks"] = quality

    issue_messages = {
        "missing_draw": "Template quality check: one or more runners are missing draw values.",
        "missing_computaform_rating": "Template quality check: one or more runners are missing Computaform ratings.",
        "missing_speed_rating": "Template quality check: one or more runners are missing speed ratings.",
        "past_rows_missing_core_fields": "Template quality check: one or more past-performance rows are missing core fields.",
        "past_run_headgear_jockey_leaks": "Template quality check: possible shoes/headgear tokens remain in jockey fields.",
        "runners_with_nonzero_life_record_but_no_past_runs": (
            "Template quality check: one or more runners have non-zero life records but no parsed past runs."
        ),
    }
    for key, message in issue_messages.items():
        if int(quality[key]):
            add_unique_warning(data, message)

    return 0


# Add future anomaly handlers here, in the order they should run.
ANOMALY_HANDLERS: list[tuple[str, AnomalyHandler]] = [
    ("repair_track_note_noise", repair_track_note_noise),
    ("repair_past_run_headgear_jockey_leaks", repair_past_run_headgear_jockey_leaks),
    ("audit_quality_counts", audit_quality_counts),
]


def apply_anomaly_template(data: dict[str, Any], context: ExtractionContext) -> bool:
    changed = False
    for name, handler in ANOMALY_HANDLERS:
        count = handler(data, context)
        if count:
            changed = True
            context.applied_handlers.append(name)
    validation = data.setdefault("validation", {})
    validation["anomaly_template"] = {
        "version": TEMPLATE_VERSION,
        "applied_handlers": context.applied_handlers,
        "notes": context.notes,
    }
    return changed


def manifest_sort_key(source: dict[str, Any]) -> tuple[datetime, str]:
    label = source.get("label", "")
    match = re.search(r"(\d{1,2} [A-Za-z]+ \d{4})$", label)
    if match:
        try:
            return datetime.strptime(match.group(1), "%d %B %Y"), source.get("id", "")
        except ValueError:
            pass
    return datetime.max, source.get("id", "")


def build_manifest(
    output_dir: Path,
    manifest_path: Path,
    include_ids: set[str] | None = None,
    include_all_outputs: bool = False,
) -> list[dict[str, str]]:
    include_ids = include_ids or set()
    existing_sources: dict[str, dict[str, str]] = {}
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing_sources = {item["id"]: item for item in existing.get("sources", []) if item.get("id")}

    sources: list[dict[str, str]] = []
    for json_path in sorted(output_dir.glob("*/*_extraction.json")):
        source_id = json_path.parent.name
        if not include_all_outputs and source_id not in existing_sources and source_id not in include_ids:
            continue
        data = json.loads(json_path.read_text(encoding="utf-8"))
        meeting = data.get("meeting", {})
        source_pdf = clean_text(meeting.get("source_pdf", ""))
        if not source_pdf:
            continue
        existing_item = existing_sources.get(source_id, {})
        venue = clean_text(meeting.get("track") or meeting.get("racecourse", ""))
        date = clean_text(meeting.get("date", ""))
        generated_label = f"{venue} - {date}".strip(" -") or source_id
        sources.append(
            {
                "id": source_id,
                "label": existing_item.get("label") or generated_label,
                "pdf": source_pdf,
                "json_path": json_path.relative_to(ROOT).as_posix(),
            }
        )

    sources.sort(key=manifest_sort_key)
    manifest_path.write_text(json.dumps({"sources": sources}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sources


def run_one_pdf(pdf_path: Path, output_dir: Path) -> dict[str, Any]:
    data, _paths = extract(pdf_path, output_dir)
    context = ExtractionContext(pdf_path=pdf_path, output_dir=output_dir)
    apply_anomaly_template(data, context)
    write_outputs(data, output_folder_for_pdf(output_dir, pdf_path))
    return {
        "source_pdf": data["meeting"]["source_pdf"],
        "output_folder": str(output_folder_for_pdf(output_dir, pdf_path)),
        "pages_processed": data["validation"].get("pages_processed", ""),
        "races_found": data["validation"].get("races_found", ""),
        "runners": len(data.get("runners", [])),
        "template_quality_checks": data["validation"].get("template_quality_checks", {}),
        "warnings": data["validation"].get("warnings", []),
        "applied_handlers": context.applied_handlers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reusable Computaform extraction template with anomaly handlers and quality checks."
    )
    parser.add_argument("--pdf", type=Path, nargs="+", required=True, help="One or more Computaform PDFs to extract.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--update-manifest", action="store_true", help="Rebuild site_manifest.json after extraction.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--include-all-outputs",
        action="store_true",
        help="When rebuilding the manifest, include every output JSON folder, even if it was not already listed.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results = []
    extracted_ids: set[str] = set()
    for pdf_path in args.pdf:
        resolved_pdf = pdf_path.resolve()
        if not resolved_pdf.exists():
            print(f"Missing PDF: {resolved_pdf}", file=sys.stderr)
            return 1
        results.append(run_one_pdf(resolved_pdf, args.output_dir.resolve()))
        extracted_ids.add(slug_from_pdf_name(resolved_pdf))

    manifest_count = None
    if args.update_manifest:
        manifest_count = len(
            build_manifest(
                args.output_dir.resolve(),
                args.manifest.resolve(),
                include_ids=extracted_ids,
                include_all_outputs=args.include_all_outputs,
            )
        )

    print(json.dumps({"results": results, "manifest_sources": manifest_count}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
