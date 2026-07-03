from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from extract_computaform import parse_past_run_row, write_outputs


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs" / "hollywoodbets_greyville_2026_07_04"
JSON_PATH = OUTPUT_DIR / "hollywoodbets_greyville_2026_07_04_extraction.json"


MANUAL_PAST_ROWS = {
    ("5", "2"): [
        "23May26 SCT G 613 MjpmF 160k 0 1200S A D D Louw 57.5-2.5 10-16 25-1 10-1 9 2.9 7 5.7 1 0.10 *Lenoxx 60 69.08 23.4 1 -0.8 .05880 45 0/12 Green-hung-fluent",
    ],
    ("6", "6"): [
        "g 14Jun26 TUR G 878 MjpmF 125k 0 1000S AH C Murray 60 1-10 7-2 6-1 5 3.3 4 2.4 1 1.00 *Rivera 57.5 57.58 22.1 1 +0.2 .05740 64 0/2 Broke thr-drew away",
    ],
    ("6", "11"): [
        "13Jan26 KEN G 295 MjpmF 160k 0 1000S A C Zackey 60 2-13 25-2 18-10 13 5.2 10 3.7 1 0.50 *Hero's Journey 60 60.65 23.1 1 -1.6 .06115 29 1/9 Lost 2L-green-flew",
        "31Jan26 KEN G 320 JstkC 600k 0 1100S A C Zackey 60 4-13 2-1 15-10 2 2.4 2 1.1 2 0.40 Red Spice 59 66.62 25.0 9 -0.2 .06030 56 1/12 Stubborn-resp-noise",
    ],
}


def runner_lookup(data: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(runner["race_number"], runner["horse_number"]): runner for runner in data["runners"]}


def repair_meeting(data: dict[str, Any]) -> None:
    data["meeting"].update(
        {
            "racecourse": "HOLLYWOODBETS GREYVILLE",
            "surface": "TURF/POLY",
            "track": "HOLLYWOODBETS GREYVILLE TURF/POLY",
            "track_direction": "clockwise round turn",
            "track_notes": (
                "Turf. 2800m triangular-shaped circuit. All races clockwise round turn with 430m run-in. "
                "Poly. 2000m track inside the turf circuit. All races clockwise round turn with 440m run-in. "
                "False rail: 2m."
            ),
            "draw_bias_notes": (
                "DRAW: Turf: Low-number draws preferable, especially 1400m and 1600m. "
                "Poly: Low-number draws slightly preferred 1400m and 1600m."
            ),
        }
    )


def repair_runners(data: dict[str, Any]) -> None:
    lookup = runner_lookup(data)
    for key, row_texts in MANUAL_PAST_ROWS.items():
        runner = lookup[key]
        runner["past_runs"] = [
            parse_past_run_row(row_text, runner["race_number"], runner["horse_number"], runner["horse_name"])
            for row_text in row_texts
        ]

    bisou = lookup[("6", "6")]
    if bisou["past_runs"]:
        bisou["past_runs"][0]["shoes_headgear"] = "AH"
        bisou["past_runs"][0]["jockey"] = "C Murray"

    st_harry = lookup[("6", "11")]
    st_harry["days_since_last_race"] = "154"
    st_harry["days_since_last_win"] = "172"
    st_harry["headgear_change"] = "No TT last"
    st_harry["breeding"].update(
        {
            "sire": "Harry Angel (IRE)",
            "dam": "Divine Day (AUS)",
            "damsire": "Domesday (AUS)",
            "foaled": "19 Oct 2023",
            "gelded": "",
            "bred_by": "Worldwide Bloodstock",
        }
    )
    st_harry["owner"] = (
        "Messrs G A R Sturlese, H J Da Silva, B W Hamilton, S Perumal, "
        "S Poriazis & Dr R Rotham"
    )
    st_harry["career_record"].update(
        {
            "life": "2-1-1-0-0",
            "surface": "TURF: 2-1-1-0-0",
            "current_year": "2-1-1-0-0",
            "previous_year": "0-0-0-0-0",
            "jockey_horse": "0-0-0-0-0",
            "jockey_trainer": "10-0-1-2-1",
            "poly": "",
            "normal": "",
            "good": "2-1-1-0-0",
            "wet": "0-0-0-0-0",
            "course": "0-0-0-0-0",
            "distance": "0-0-0-0-0",
            "course_and_distance": "0-0-0-0-0",
            "rain": "",
            "class": "ClasB: 0-0-0-0-0",
            "win_place_range": "10-1100m",
            "headgear": "2-1-1-0-0",
            "rest_record": "Rest+1: 1-1-0-0-0",
            "distance_category": "0-0-0-0-0",
        }
    )


def update_validation(data: dict[str, Any]) -> None:
    validation = data.setdefault("validation", {})
    past_rows = [row for runner in data["runners"] for row in runner.get("past_runs", [])]
    missing_core = [
        row
        for row in past_rows
        if not row.get("date") or not row.get("course") or not row.get("weight_allowance") or not row.get("draw_runners")
    ]
    empty_life_past = [
        {
            "race_number": runner["race_number"],
            "horse_number": runner["horse_number"],
            "horse_name": runner["horse_name"],
        }
        for runner in data["runners"]
        if runner.get("career_record", {}).get("life") and not runner.get("past_runs")
    ]
    validation["quality_checks"]["past_performance_rows"] = str(len(past_rows))
    validation["quality_checks"]["past_performance_rows_missing_core_fields"] = str(len(missing_core))
    validation["quality_checks"]["runners_with_life_record_but_no_past_runs"] = str(len(empty_life_past))
    validation["quality_checks"]["manual_coordinate_repaired_past_rows"] = str(sum(len(rows) for rows in MANUAL_PAST_ROWS.values()))
    validation["quality_counts"] = {
        "career_life": str(sum(1 for runner in data["runners"] if runner.get("career_record", {}).get("life"))),
        "career_surface": str(sum(1 for runner in data["runners"] if runner.get("career_record", {}).get("surface"))),
        "career_course": str(sum(1 for runner in data["runners"] if runner.get("career_record", {}).get("course"))),
        "career_distance": str(sum(1 for runner in data["runners"] if runner.get("career_record", {}).get("distance"))),
        "career_course_and_distance": str(
            sum(1 for runner in data["runners"] if runner.get("career_record", {}).get("course_and_distance"))
        ),
        "days_since_last_race": str(sum(1 for runner in data["runners"] if runner.get("days_since_last_race"))),
        "days_since_last_win": str(sum(1 for runner in data["runners"] if runner.get("days_since_last_win"))),
        "structured_past_run_rows": str(len(past_rows)),
        "placeholder_past_run_rows": "0",
    }
    validation["coordinate_repair_notes"] = [
        "TUDOR ROSE, BISOU BISOU, and ST HARRY past-performance rows were repaired from fixed PDF word coordinates because the clean text layer split the date/count tokens across visual lines.",
        "Meeting layer corrected to HOLLYWOODBETS GREYVILLE TURF/POLY rather than the first-race POLYTRACK header.",
    ]


def normalize_nested_runner_keys(data: dict[str, Any]) -> None:
    runner_keys: set[str] = set()
    career_keys: set[str] = set()
    breeding_keys: set[str] = set()
    for runner in data["runners"]:
        runner_keys.update(runner.keys())
        career_keys.update(runner.get("career_record", {}).keys())
        breeding_keys.update(runner.get("breeding", {}).keys())
    for runner in data["runners"]:
        for key in runner_keys:
            if key not in {"career_record", "breeding", "past_runs", "collateral_formlines"}:
                runner.setdefault(key, "")
        career = runner.setdefault("career_record", {})
        breeding = runner.setdefault("breeding", {})
        for key in career_keys:
            career.setdefault(key, "")
        for key in breeding_keys:
            breeding.setdefault(key, "")


def main() -> int:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    repair_meeting(data)
    repair_runners(data)
    update_validation(data)
    normalize_nested_runner_keys(data)
    paths = write_outputs(data, OUTPUT_DIR)
    print(paths["json"])
    print(data["validation"]["quality_checks"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
