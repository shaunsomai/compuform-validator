from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd
import pdfplumber


PDF_NAME = "HOLLYWOODBETS GREYVILLE@2026.05.13.pdf"
DEFAULT_PDF = Path(__file__).resolve().parent / "pdfs" / PDF_NAME
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"


RUNNER_TEMPLATE: dict[str, Any] = {
    "race_number": "",
    "horse_number": "",
    "draw": "",
    "horse_name": "",
    "last_3_runs": "",
    "runs_wins_places": "",
    "first3_percentage": "",
    "earnings": "",
    "average_earnings": "",
    "age_colour_sex": "",
    "weight": "",
    "allowance": "",
    "trainer": "",
    "trainer_win_percentage": "",
    "jockey": "",
    "jockey_win_percentage": "",
    "hmerit_rating": "",
    "cmerit_rating": "",
    "form_comment": "",
    "forecast_price": "",
    "computaform_rating": "",
    "speed_rating": "",
    "best_weighted_rating": "",
    "top_first3_distance_category": "",
    "top_first3_track": "",
    "best_vs_average": "",
    "shoes": "",
    "race_rating": "",
    "equipment": "",
    "headgear_change": "",
    "days_since_last_race": "",
    "days_since_last_win": "",
    "breeding": {
        "sire": "",
        "dam": "",
        "damsire": "",
        "foaled": "",
        "gelded": "",
        "bred_by": "",
    },
    "owner": "",
    "career_record": {
        "life": "",
        "surface": "",
        "current_year": "",
        "previous_year": "",
        "jockey_horse": "",
        "jockey_trainer": "",
        "poly": "",
        "normal": "",
        "good": "",
        "wet": "",
        "course": "",
        "distance": "",
        "course_and_distance": "",
        "rain": "",
        "class": "",
        "win_place_range": "",
    },
    "past_runs": [],
    "collateral_formlines": [],
}


def empty_schema(source_pdf: str) -> dict[str, Any]:
    return {
        "meeting": {
            "racecourse": "",
            "date": "",
            "day": "",
            "surface": "",
            "track": "",
            "number_of_races": "",
            "first_race_time": "",
            "track_direction": "",
            "track_notes": "",
            "draw_bias_notes": "",
            "source_pdf": source_pdf,
        },
        "races": [],
        "runners": [],
        "ratings": {
            "computaform_ratings_by_race": [],
            "speed_ratings_by_race": [],
            "best_weighted_by_race": [],
            "best_on_ratings": [],
        },
        "betting": {
            "today_best_bet": "",
            "today_top_value": "",
            "best_swinger": "",
            "best_exacta": "",
            "best_trifecta": "",
            "best_quartet": "",
            "bipot": [],
            "place_accumulator": [],
            "pick6": [],
            "jackpot1": [],
            "jackpot2": [],
        },
        "validation": {
            "pages_processed": "",
            "races_found": "",
            "runners_found_by_race": [],
            "missing_fields": [],
            "unclear_fields": [],
            "possible_ocr_errors": [],
            "warnings": [],
        },
    }


def clean_text(value: Any, keep_newlines: bool = False) -> str:
    if value is None:
        return ""
    text = str(value)
    # The PDF maps decimal points/halves to U+FFFD in many numeric fields.
    text = text.replace("\ufffd", ".")
    text = text.replace("\u00a0", " ")
    text = text.replace("\r", "\n")
    if not keep_newlines:
        text = re.sub(r"\s+", " ", text)
    else:
        text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_table(table: list[list[Any]]) -> list[list[str]]:
    return [[clean_text(cell, keep_newlines=True) for cell in row] for row in table]


def slug_from_pdf_name(pdf_path: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", pdf_path.stem).strip("_").lower()
    return slug or "extracted_pdf"


def output_folder_for_pdf(base_output_dir: Path, pdf_path: Path) -> Path:
    return base_output_dir / slug_from_pdf_name(pdf_path)


def meeting_parts_from_pdf_name(pdf_path: Path) -> tuple[str, str]:
    match = re.match(r"(?P<course>.+?)@(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})$", pdf_path.stem)
    if not match:
        return "", ""
    course = clean_text(match.group("course").replace("_", " "))
    months = {
        "01": "January",
        "02": "February",
        "03": "March",
        "04": "April",
        "05": "May",
        "06": "June",
        "07": "July",
        "08": "August",
        "09": "September",
        "10": "October",
        "11": "November",
        "12": "December",
    }
    month_name = months.get(match.group("month"), "")
    if not month_name:
        return course, ""
    day_number = str(int(match.group("day")))
    return course, f"{day_number} {month_name} {match.group('year')}"


def apply_meeting_fallbacks(data: dict[str, Any], pdf_path: Path) -> None:
    filename_course, filename_date = meeting_parts_from_pdf_name(pdf_path)
    if not data["meeting"]["racecourse"]:
        data["meeting"]["racecourse"] = data["meeting"].get("track") or filename_course
    if not data["meeting"]["date"]:
        data["meeting"]["date"] = filename_date
    if not data["meeting"]["number_of_races"] and data["races"]:
        data["meeting"]["number_of_races"] = str(len(data["races"]))
    if not data["meeting"]["first_race_time"] and data["races"]:
        data["meeting"]["first_race_time"] = data["races"][0].get("race_time", "")


def first_match(pattern: str, text: str, group: int | str = 1, flags: int = re.I | re.S) -> str:
    match = re.search(pattern, text, flags)
    if not match:
        return ""
    return clean_text(match.group(group))


def odds_to_slash(value: str) -> str:
    value = clean_text(value)
    if re.fullmatch(r"\d+(?:-\d+)+", value):
        return value.replace("-", "/")
    return value


def split_allowance(weight: str) -> tuple[str, str]:
    weight = clean_text(weight)
    if "-" not in weight:
        return weight, ""
    base, allowance = weight.split("-", 1)
    return weight, allowance


def load_pdf(pdf_path: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            raw_text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            raw_tables = page.extract_tables() or []
            pages.append(
                {
                    "page_number": index,
                    "width": page.width,
                    "height": page.height,
                    "text_raw": raw_text,
                    "text": clean_text(raw_text, keep_newlines=True),
                    "tables": [clean_table(table) for table in raw_tables if table],
                }
            )
    return pages


def extract_meeting(data: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    first_pages = "\n".join(page["text"] for page in pages[:8])
    match = re.search(
        r"^([A-Z][A-Z0-9 @'’&.\-]+?)\s+(\d{1,2}\s+[A-Z]+\s+\d{4})\s+-\s+(\d+)\s+RACES\s+-\s+RACE\s+1\s+@\s+(\d{1,2}:\d{2})",
        first_pages,
        re.I | re.M,
    )
    if match:
        printed_course = clean_text(match.group(1))
        data["meeting"]["racecourse"] = printed_course.replace(" POLY", "")
        data["meeting"]["date"] = clean_text(match.group(2)).title()
        data["meeting"]["number_of_races"] = match.group(3)
        data["meeting"]["first_race_time"] = match.group(4)

    day = first_match(r"\b(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)\b", first_pages)
    if day:
        data["meeting"]["day"] = day.title()

    track_notes = first_match(r"((?:Poly|Turf)\..*?DRAW:.*?)(?:\n[A-Z ]{3,}|ALPHABETICAL INDEX|$)", first_pages, 1, flags=re.I | re.S)
    if track_notes:
        data["meeting"]["track_notes"] = track_notes
        direction = first_match(r"All races ([^.]+?round turn)", track_notes)
        if not direction:
            direction = first_match(r"beyond 1000m ([^.]+?round turn)", track_notes)
        data["meeting"]["track_direction"] = direction or ""
        draw = first_match(r"(DRAW:.*)$", track_notes, 1, flags=re.I | re.S)
        data["meeting"]["draw_bias_notes"] = draw

    summary_surface = ""
    summary_track = ""
    for page in pages:
        if "No L3 Name" not in page["text"] or not page["tables"]:
            continue
        table0 = page["tables"][0]
        if table0 and table0[0]:
            summary_surface = clean_text(table0[0][0])
            summary_track = clean_text(table0[1][1]) if len(table0) > 1 and len(table0[1]) > 1 else ""
            break
    if summary_surface:
        data["meeting"]["surface"] = summary_surface
    if summary_track:
        data["meeting"]["track"] = summary_track


def detect_summary_pages(pages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary_pages: dict[str, dict[str, Any]] = {}
    for page in pages:
        text = page["text"]
        if "No L3 Name" not in text or "CFORM RATINGS" not in text:
            continue
        race_number = ""
        if page["tables"] and len(page["tables"][0]) >= 3 and len(page["tables"][0][2]) >= 3:
            candidate = clean_text(page["tables"][0][2][2])
            if candidate.isdigit():
                race_number = candidate
        if not race_number:
            race_number = first_match(r"Computaform Class:\s*[A-Z]\s+(\d+)", text)
        if not race_number:
            continue
        summary_pages[race_number] = page
    return dict(sorted(summary_pages.items(), key=lambda item: int(item[0])))


def parse_race_header(page: dict[str, Any]) -> dict[str, str]:
    text = page["text"]
    table0 = page["tables"][0] if page["tables"] else []
    race: dict[str, str] = {
        "race_number": "",
        "race_time": "",
        "race_name": "",
        "race_type": "",
        "race_class": "",
        "distance": "",
        "distance_category": "",
        "turn": "",
        "surface": "",
        "tab_bet_types": "",
        "stake": "",
        "prize_breakdown": "",
        "rcis": "",
        "race_ref": "",
        "average_merit_rating": "",
        "average_first3_percentage": "",
        "class_average_time": "",
        "class_average_per_metre": "",
        "course_record": "",
        "wfa": "",
    }
    if table0 and len(table0) >= 3:
        top = table0[0]
        middle = table0[1]
        lower = table0[2]
        race["surface"] = clean_text(top[0]) if len(top) > 0 else ""
        race["turn"] = clean_text(top[1]) if len(top) > 1 else ""
        race["race_time"] = clean_text(top[2]) if len(top) > 2 else first_match(r"\b(\d{1,2}:\d{2})\b", text)
        race["tab_bet_types"] = clean_text(top[3]) if len(top) > 3 else ""
        race["distance"] = clean_text(top[4]) if len(top) > 4 else first_match(r"\b(\d{3,4}m)\b", text)
        race["race_number"] = clean_text(lower[2]) if len(lower) > 2 else ""

        title_block = clean_text(middle[3], keep_newlines=True) if len(middle) > 3 else ""
        title_lines = [line.strip() for line in title_block.splitlines() if line.strip()]
        race_name_lines = []
        for line in title_lines:
            if line.startswith("(") or line.startswith("Gross Stake:"):
                break
            race_name_lines.append(line)
        race["race_name"] = clean_text(" ".join(race_name_lines))
        if len(title_lines) > len(race_name_lines) and title_lines[len(race_name_lines)].startswith("("):
            race["race_type"] = title_lines[len(race_name_lines)].strip("()")
        race["stake"] = first_match(r"Gross Stake:\s*(R[\d,]+)", title_block)
        gross_index = next((i for i, line in enumerate(title_lines) if line.startswith("Gross Stake:")), -1)
        if gross_index >= 0:
            race["prize_breakdown"] = clean_text(" ".join(title_lines[gross_index:]))
            race["rcis"] = first_match(r"\b(?:RCIS|4RIS):\s*(R[\d,]+)", race["prize_breakdown"])

        left_block = clean_text(lower[0], keep_newlines=True) if len(lower) > 0 else ""
        right_block = clean_text(lower[4], keep_newlines=True) if len(lower) > 4 else ""
        race["race_ref"] = first_match(r"Race Ref:\s*(\d+)", left_block)
        race["race_class"] = first_match(r"Computaform Class:\s*([A-Z])", left_block)
        race["average_merit_rating"] = first_match(r"Average Merit Rating:\s*([-\d]+)", left_block)
        race["average_first3_percentage"] = first_match(r"Average % in FIRST 3 all Runners:\s*([\d.]+%)", left_block)
        race["wfa"] = first_match(r"WFA:\s*([^\n]+)", left_block)
        race["distance_category"] = first_match(r"DC:\s*([^\n]+)", right_block)
        race["class_average_time"] = first_match(r"Class Average:\s*([\d.]+s)", right_block)
        race["class_average_per_metre"] = first_match(r"Class Ave Per Metre:\s*([.\d-]+)", right_block)
        record = first_match(r"Crse Record:\s*([^\n]+(?:\n[^\n]+)?)", right_block, 1, flags=re.I)
        race["course_record"] = clean_text(record)

    if not race["race_number"]:
        race["race_number"] = first_match(r"Computaform Class:\s*[A-Z]\s+(\d+)", text)
    if not race["race_time"]:
        race["race_time"] = first_match(r"\b(\d{1,2}:\d{2})\b", text)
    if not race["distance"]:
        race["distance"] = first_match(r"\b(\d{3,4}m)\b", text)
    if not race["stake"]:
        race["stake"] = first_match(r"Gross Stake:\s*(R[\d,]+)", text)
    if not race["race_ref"]:
        race["race_ref"] = first_match(r"Race Ref:\s*(\d+)", text)
    if not race["distance_category"]:
        race["distance_category"] = first_match(r"DC:\s*([^\n]+)", text)
    if not race["wfa"]:
        race["wfa"] = first_match(r"WFA:\s*([^\n]+)", text)
    return race


def parse_runner_line(line: str, race_number: str) -> dict[str, Any] | None:
    line = clean_text(line)
    pattern = re.compile(
        r"^(?P<num>\d{1,2})\s+"
        r"(?:(?P<last3>[0-9][0-9A-Z]*)\s+)?"
        r"(?:(?P<equipment>[pq])\s+)?"
        r"(?P<name>.+?)\s+"
        r"(?P<runs>\d+-\d+-\d+-\d+)\s*"
        r"(?P<f3>\d+%)\s+"
        r"(?P<earnings>R[\d,]+)\s+"
        r"(?P<ave>R[\d,]+)\s+"
        r"(?P<acs>\d+[A-Za-z]+)\s+"
        r"(?P<wgt>\d+(?:\.\d)?(?:-\d+\.\d)?)\s+"
        r"(?P<draw>\d+)\s+"
        r"(?P<rest>.+)$"
    )
    match = pattern.match(line)
    if not match:
        return None

    rest = match.group("rest")
    percents = list(re.finditer(r"(?:\d+\.\d+|--)%", rest))
    if len(percents) < 2:
        return None
    trainer = clean_text(rest[: percents[0].start()])
    trainer_win = percents[0].group(0)
    jockey = clean_text(rest[percents[0].end() : percents[1].start()])
    jockey_win = percents[1].group(0)
    tail = clean_text(rest[percents[1].end() :])

    tail_match = re.match(
        r"(?P<hmr>--|\d+)\s+(?P<cmr>--|\d+)\s+(?P<comment>.+?)\s+(?P<price>\d+(?:-\d+)+)$",
        tail,
    )
    if tail_match:
        hmr = tail_match.group("hmr")
        cmr = tail_match.group("cmr")
        comment = clean_text(tail_match.group("comment"))
        forecast = odds_to_slash(tail_match.group("price"))
    else:
        hmr = ""
        cmr = ""
        comment = tail
        forecast = ""

    runner = deepcopy(RUNNER_TEMPLATE)
    printed_weight, allowance = split_allowance(match.group("wgt"))
    runner.update(
        {
            "race_number": race_number,
            "horse_number": match.group("num"),
            "draw": match.group("draw"),
            "horse_name": clean_text(match.group("name")),
            "last_3_runs": clean_text(match.group("last3") or ""),
            "runs_wins_places": match.group("runs"),
            "first3_percentage": match.group("f3"),
            "earnings": match.group("earnings"),
            "average_earnings": match.group("ave"),
            "age_colour_sex": match.group("acs"),
            "weight": printed_weight,
            "allowance": allowance,
            "trainer": trainer,
            "trainer_win_percentage": trainer_win,
            "jockey": jockey,
            "jockey_win_percentage": jockey_win,
            "hmerit_rating": hmr,
            "cmerit_rating": cmr,
            "form_comment": comment,
            "forecast_price": forecast,
            "equipment": clean_text(match.group("equipment") or ""),
        }
    )
    return runner


def extract_runner_lines(text: str) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    in_runners = False
    runner_lines: list[str] = []
    for line in lines:
        if "No L3 Name" in line:
            in_runners = True
            continue
        if in_runners and line.strip() == "MROF":
            break
        if in_runners and re.match(r"^\d{1,2}\s+", line.strip()):
            runner_lines.append(line.strip())
    return runner_lines


def parse_rating_entry(entry: str, race_number: str, rating_type: str) -> dict[str, str] | None:
    entry = clean_text(entry)
    entry = re.sub(r"^(?:MROF|TSAF)\s+", "", entry, flags=re.I)
    if not entry or entry.upper() == "N/A":
        return None
    match = re.match(r"^(?P<num>\d{1,2})\s+(?P<name>.+?)\s+(?P<value>[+-]?\d+(?:\.\d+)?)$", entry)
    if not match:
        return {
            "race_number": race_number,
            "rating_type": rating_type,
            "horse_number": "unclear",
            "horse_name": "unclear",
            "value": entry,
        }
    return {
        "race_number": race_number,
        "rating_type": rating_type,
        "horse_number": match.group("num"),
        "horse_name": clean_text(match.group("name")),
        "value": match.group("value"),
    }


def parse_ratings(page: dict[str, Any], race_number: str) -> dict[str, list[dict[str, str]]]:
    ratings = {
        "computaform": [],
        "speed": [],
        "top_first3_distance": [],
        "top_first3_track": [],
        "best_vs_average": [],
        "best_weighted": [],
    }
    rating_table = None
    for table in page["tables"]:
        if table and any("CFORM RATINGS" in (cell or "") for cell in table[0]):
            rating_table = table
            break
    if not rating_table:
        return ratings

    header_aliases = {
        "computaform": "CFORM RATINGS",
        "speed": "CF SPEED RATINGS",
        "top_first3_distance": "TOP % 1ST 3 THIS DC",
        "top_first3_track": "TOP % 1ST 3 AT TRACK",
        "best_vs_average": "BEST VS AVE THIS DC",
        "best_weighted": "BEST WEIGHTED",
    }
    column_map: dict[str, int] = {}
    for col, cell in enumerate(rating_table[0]):
        upper_cell = clean_text(cell, keep_newlines=True).upper()
        for key, label in header_aliases.items():
            if label in upper_cell and key not in column_map:
                column_map[key] = col
                embedded_lines = [
                    clean_text(line)
                    for line in clean_text(cell, keep_newlines=True).splitlines()
                    if clean_text(line)
                ]
                for line in embedded_lines:
                    if label in line.upper() or line.upper() in {"MROF", "TSAF"}:
                        continue
                    parsed = parse_rating_entry(line, race_number, key)
                    if parsed:
                        ratings[key].append(parsed)

    for row in rating_table[1:]:
        if row and row[0].startswith("TIPSTER"):
            break
        for key, col in column_map.items():
            if col >= len(row):
                continue
            parsed = parse_rating_entry(row[col], race_number, key)
            if parsed:
                ratings[key].append(parsed)
    return ratings


def parse_tipster(page: dict[str, Any]) -> list[str]:
    for table in page["tables"]:
        for row in table:
            first = clean_text(row[0]) if row else ""
            if first.startswith("TIPSTER") or first.startswith("IPSTER"):
                _, _, selections = first.partition(":")
                return [clean_text(item) for item in selections.split(";") if clean_text(item)]
    match = re.search(r"(?:TIPSTER|IPSTER)\s+[^:]+:\s*(.+)", page["text"], re.I)
    if match:
        return [clean_text(item) for item in match.group(1).split(";") if clean_text(item)]
    return []


def parse_aux_race_table(page: dict[str, Any]) -> tuple[dict[str, str], list[str], str, str]:
    betting_legs = {
        "bipot": "",
        "pa": "",
        "pick6": "",
        "jackpot1": "",
        "jackpot2": "",
        "rolling_double": "",
        "pick3": "",
    }
    draw_stats: list[str] = []
    same_trainer = ""
    preview = ""
    one_col_table = None
    for table in page["tables"]:
        if table and len(table[0]) == 1 and any(
            ("WINS/RUNS FROM DRAW" in row[0] or "INS/RUNS FROM DRAW" in row[0] or "Same Trainer:" in row[0])
            for row in table
            if row
        ):
            one_col_table = table
            break
    if one_col_table:
        leg_line = clean_text(one_col_table[0][0])
        same_trainer = first_match(r"Same Trainer:\s*(.+?)(?:\.|$)", leg_line)
        segment_patterns = {
            "bipot": r"(?:Not Included in Bipot|Bipot (?:Leg \d+|Cls \d{1,2}:\d{2}))",
            "pa": r"P/A (?:Leg \d+|Cls \d{1,2}:\d{2})",
            "pick6": r"Pick 6 (?:Leg \d+|Cls \d{1,2}:\d{2})",
            "jackpot1": r"(?:Jkpt 1|Jackpot 1) (?:Leg \d+|Cls \d{1,2}:\d{2})",
            "jackpot2": r"(?:Jkpt 2|Jackpot 2) (?:Leg \d+|Cls \d{1,2}:\d{2})",
            "rolling_double": r"Double \d+ Leg \d+",
            "pick3": r"Pick 3 \d+ Leg \d+",
        }
        for key, pattern in segment_patterns.items():
            value = first_match(pattern, leg_line, 0)
            if value:
                betting_legs[key] = value

        draw_line = clean_text(one_col_table[1][0]) if len(one_col_table) > 1 else ""
        draw_match = re.search(r"(?:WINS/RUNS|INS/RUNS) FROM DRAW:\s*(.+)$", draw_line, re.I | re.S)
        if draw_match:
            draw_stats = [clean_text(item) for item in draw_match.group(1).split(";") if clean_text(item)]

        preview_line = clean_text(one_col_table[2][0], keep_newlines=True) if len(one_col_table) > 2 else ""
        preview = first_match(r"(?:PREVIEW|REVIEW):\s*(.+)$", preview_line, 1, flags=re.I | re.S)
    return betting_legs, draw_stats, same_trainer, preview


def apply_rating_values_to_runners(runners: list[dict[str, Any]], ratings: dict[str, list[dict[str, str]]]) -> None:
    index = {(runner["race_number"], runner["horse_number"]): runner for runner in runners}
    field_map = {
        "computaform": "computaform_rating",
        "speed": "speed_rating",
        "top_first3_distance": "top_first3_distance_category",
        "top_first3_track": "top_first3_track",
        "best_vs_average": "best_vs_average",
        "best_weighted": "best_weighted_rating",
    }
    for rating_type, field_name in field_map.items():
        for item in ratings.get(rating_type, []):
            runner = index.get((item["race_number"], item["horse_number"]))
            if runner:
                runner[field_name] = item["value"]


def is_profile_card_table(table: list[list[str]]) -> bool:
    if len(table) < 3 or not table[0]:
        return False
    expected = ["Shoes", "CF Rate", "Headgear", "Jockey", "KGs", "MR", "Draw"]
    return table[0][: len(expected)] == expected


def profile_card_values(table: list[list[str]]) -> dict[str, str]:
    values = {"draw": "", "cf_rate": "", "sp_rate": "", "headgear": "", "shoes": "", "race_rating": ""}
    if len(table) > 1:
        row = table[1]
        values["shoes"] = clean_text(row[0]) if len(row) > 0 else ""
        values["cf_rate"] = clean_text(row[1]) if len(row) > 1 else ""
        values["headgear"] = clean_text(row[2]) if len(row) > 2 else ""
        values["draw"] = clean_text(row[6]) if len(row) > 6 else ""
    if len(table) > 3 and len(table[3]) > 1 and clean_text(table[3][1]):
        values["sp_rate"] = clean_text(table[3][1])
    else:
        for row in table:
            for cell in row:
                match = re.search(r"Sp Rate\s*\n\s*([A-Z]|\d+)", cell or "", re.I)
                if match:
                    values["sp_rate"] = clean_text(match.group(1))
                    break
            if values["sp_rate"]:
                break
    if len(table) > 3 and len(table[3]) > 5 and clean_text(table[3][5]):
        values["race_rating"] = clean_text(table[3][5])
    else:
        for row in table:
            for cell in row:
                match = re.search(r"RR\s*\n\s*([A-Z]|\d+)", cell or "", re.I)
                if match:
                    values["race_rating"] = clean_text(match.group(1))
                    break
            if values["race_rating"]:
                break
    return values


def page_numbers_for_race(
    pages: list[dict[str, Any]],
    summary_pages: dict[str, dict[str, Any]],
    race_number: str,
    meeting_name: str = "",
) -> set[int]:
    ordered = [(int(num), page["page_number"]) for num, page in summary_pages.items()]
    current_start = summary_pages[race_number]["page_number"]
    next_starts = [page_no for num, page_no in ordered if num > int(race_number)]
    end_page = min(next_starts) - 1 if next_starts else len(pages)
    if race_number == max(summary_pages, key=int):
        included: list[int] = []
        previous_included = False
        meeting_upper = meeting_name.upper()
        for page in pages:
            if not (current_start <= page["page_number"] <= end_page):
                continue
            upper_text = page["text"].upper()
            first_line = next((line.strip().upper() for line in page["text"].splitlines() if line.strip()), "")
            include = page["page_number"] == current_start
            if f"RACE {race_number} " in upper_text and (not meeting_upper or meeting_upper in upper_text):
                include = True
            if previous_included and first_line.startswith("HORSE DATE CRSE"):
                include = True
            if include:
                included.append(page["page_number"])
                previous_included = True
            elif previous_included:
                break
        if included:
            end_page = max(included)
    return set(range(current_start, end_page + 1))


def enrich_runners_with_profile_cards(
    data: dict[str, Any],
    pages: list[dict[str, Any]],
    summary_pages: dict[str, dict[str, Any]],
) -> None:
    runners_by_race_draw: dict[tuple[str, str], dict[str, Any]] = {
        (runner["race_number"], runner["draw"]): runner for runner in data["runners"]
    }
    ordered = [(int(num), page["page_number"]) for num, page in summary_pages.items()]
    last_race = max(summary_pages, key=int) if summary_pages else ""
    for race_number, summary_page in summary_pages.items():
        start_page = summary_page["page_number"]
        next_starts = [page_no for num, page_no in ordered if num > int(race_number)]
        end_page = min(next_starts) - 1 if next_starts else len(pages)
        race_page_numbers = page_numbers_for_race(pages, summary_pages, race_number, data["meeting"].get("racecourse", ""))
        for page in pages:
            if page["page_number"] not in race_page_numbers:
                continue
            for table in page["tables"]:
                if not is_profile_card_table(table):
                    continue
                values = profile_card_values(table)
                runner = runners_by_race_draw.get((race_number, values["draw"]))
                if not runner:
                    continue
                if values["cf_rate"]:
                    runner["computaform_rating"] = values["cf_rate"]
                if values["sp_rate"]:
                    runner["speed_rating"] = values["sp_rate"]
                if values["race_rating"]:
                    runner["race_rating"] = values["race_rating"]
                if values["headgear"]:
                    runner["headgear_change"] = values["headgear"]
                if values["shoes"]:
                    runner["shoes"] = values["shoes"]


def extract_races_and_runners(data: dict[str, Any], summary_pages: dict[str, dict[str, Any]]) -> None:
    all_runners: list[dict[str, Any]] = []
    ratings_by_type = {
        "computaform": [],
        "speed": [],
        "best_weighted": [],
    }
    for race_number, page in summary_pages.items():
        race_header = parse_race_header(page)
        betting_legs, draw_stats, same_trainer, preview = parse_aux_race_table(page)
        ratings = parse_ratings(page, race_number)
        runners = []
        for line in extract_runner_lines(page["text"]):
            parsed = parse_runner_line(line, race_number)
            if parsed:
                runners.append(parsed)

        apply_rating_values_to_runners(runners, ratings)

        race = {
            **race_header,
            "betting_legs": betting_legs,
            "same_trainer_notes": same_trainer,
            "preview": preview,
            "tipster_selections": parse_tipster(page),
            "draw_stats": draw_stats,
            "runners": [runner["horse_number"] for runner in runners],
        }
        data["races"].append(race)
        all_runners.extend(runners)

        ratings_by_type["computaform"].append({"race_number": race_number, "ratings": ratings["computaform"]})
        ratings_by_type["speed"].append({"race_number": race_number, "ratings": ratings["speed"]})
        ratings_by_type["best_weighted"].append({"race_number": race_number, "ratings": ratings["best_weighted"]})

    data["runners"] = all_runners
    data["ratings"]["computaform_ratings_by_race"] = ratings_by_type["computaform"]
    data["ratings"]["speed_ratings_by_race"] = ratings_by_type["speed"]
    data["ratings"]["best_weighted_by_race"] = ratings_by_type["best_weighted"]


def page_text_range_for_race(
    pages: list[dict[str, Any]],
    summary_pages: dict[str, dict[str, Any]],
    race_number: str,
    meeting_name: str = "",
) -> str:
    race_page_numbers = page_numbers_for_race(pages, summary_pages, race_number, meeting_name)
    selected = [page["text"] for page in pages if page["page_number"] in race_page_numbers]
    return "\n".join(selected)


def parse_profile_blocks(text: str) -> list[tuple[re.Match[str], str]]:
    header_re = re.compile(
        r"(?m)^(?P<num>\d{1,2})\s+(?P<name>[A-Z0-9’' .\-&]+?)\s+LIFE:\s+"
        r"(?P<life>\d+-\d+-\d+-\d+-\d+)\s+(?P<surface_label>POLY|TURF):\s+(?P<surface_record>\d+-\d+-\d+-\d+-\d+)\s+"
        r"CRSE:\s+(?P<course>\d+-\d+-\d+-\d+-\d+)"
    )
    matches = list(header_re.finditer(text))
    blocks = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((match, text[start:end]))
    return blocks


def parse_breeding(block: str) -> dict[str, str]:
    breeding = {
        "sire": "",
        "dam": "",
        "damsire": "",
        "foaled": first_match(r"Foaled:\s*([0-9]{1,2}\s+[A-Za-z]{3}\s+\d{4})", block),
        "gelded": first_match(r"Gelded:\s*([0-9]{1,2}\s+[A-Za-z]{3}\s+\d{4})", block),
        "bred_by": first_match(r"BRED BY:\s*(.+?)(?:\s+OWNER/S:|$)", block, 1, flags=re.I | re.S),
    }
    for line in block.splitlines()[1:8]:
        line = clean_text(line)
        if " - " not in line or " by " not in line:
            continue
        if re.search(r"(?:\b[A-Za-z]\s+){6,}", line):
            breeding["sire"] = breeding["dam"] = breeding["damsire"] = "unclear"
            return breeding
        match = re.match(r"^\d+\s+[A-Za-z]+\s+[a-z]\s+(?P<sire>.+?)\s+-\s+(?P<dam>.+?)\s+by\s+(?P<damsire>.+)$", line)
        if match:
            breeding["sire"] = clean_text(match.group("sire"))
            breeding["dam"] = clean_text(match.group("dam"))
            breeding["damsire"] = clean_text(match.group("damsire"))
            return breeding
    if "Foaled:" in block and not any(breeding[key] for key in ("sire", "dam", "damsire")):
        breeding["sire"] = breeding["dam"] = breeding["damsire"] = "unclear"
    return breeding


def blank_past_run(race_number: str, horse_number: str, horse_name: str, raw_text: str) -> dict[str, str]:
    return {
        "race_number": race_number,
        "horse_number": horse_number,
        "horse_name": horse_name,
        "date_marker": "",
        "date": "",
        "course": "",
        "going": "",
        "ref": "",
        "race_class_stake": "",
        "race_class": "",
        "stake": "",
        "average_merit_rating": "",
        "distance": "",
        "distance_metres": "",
        "straight_or_turn": "",
        "shoes_headgear": "",
        "official_merit_rating": "",
        "jockey": "",
        "weight_allowance": "",
        "draw_runners": "",
        "opening_betting": "",
        "starting_price": "",
        "position_800m": "",
        "lengths_800m": "",
        "position_400m": "",
        "lengths_400m": "",
        "finish_position": "",
        "finish_length": "",
        "winner_or_second": "",
        "winner_weight": "",
        "winner_time": "",
        "final_400": "",
        "finish_rank": "",
        "horse_adjusted_vs_average": "",
        "adjusted_time_per_metre": "",
        "speed_rating": "",
        "next_start_winners": "",
        "comment": "",
        "raw_text": raw_text,
    }


def is_weight_token(token: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?(?:[-+]\d+\.\d+)?", token))


def is_distance_token(token: str) -> bool:
    match = re.fullmatch(r"(\d{3,4})([A-Za-z])?", token)
    if not match:
        return False
    metres = int(match.group(1))
    return 600 <= metres <= 4000


def is_time_token(token: str) -> bool:
    return token == "NTT" or bool(re.fullmatch(r"\d{2,3}\.\d{2}", token))


def is_shoes_headgear_token(token: str) -> bool:
    return bool(re.fullmatch(r"[AB]{1,2}t?|[ABH]", token))


def split_embedded_winner_weight(token: str) -> tuple[str, str] | None:
    match = re.match(r"^(?P<name>.*?[A-Za-z][A-Za-z/.'â€™-]*?)(?P<weight>\d+(?:\.\d+)?)$", token)
    if not match:
        return None
    return clean_text(match.group("name")), match.group("weight")


def split_next_start_and_comment(tokens: list[str]) -> tuple[str, str]:
    if not tokens:
        return "", ""
    first = tokens[0]
    match = re.match(r"^(\d+/\d+)(?:\.+)?(.*)$", first)
    if not match:
        return first, clean_text(" ".join(tokens[1:]))
    next_start = match.group(1)
    comment_start = clean_text(match.group(2).replace(".", " "))
    comment = clean_text(" ".join([comment_start, *tokens[1:]]))
    return next_start, comment


def assign_past_run_winner_tail(row: dict[str, str], tokens: list[str], winner_weight_idx: int) -> None:
    row["winner_weight"] = tokens[winner_weight_idx]
    row["winner_time"] = tokens[winner_weight_idx + 1] if winner_weight_idx + 1 < len(tokens) else ""
    row["final_400"] = tokens[winner_weight_idx + 2] if winner_weight_idx + 2 < len(tokens) else ""
    row["finish_rank"] = tokens[winner_weight_idx + 3] if winner_weight_idx + 3 < len(tokens) else ""
    row["horse_adjusted_vs_average"] = tokens[winner_weight_idx + 4] if winner_weight_idx + 4 < len(tokens) else ""
    row["adjusted_time_per_metre"] = tokens[winner_weight_idx + 5] if winner_weight_idx + 5 < len(tokens) else ""
    speed_idx = winner_weight_idx + 6
    next_start_idx = winner_weight_idx + 7
    if speed_idx < len(tokens):
        if re.match(r"^\d+/\d+", tokens[speed_idx]):
            next_start_idx = speed_idx
        else:
            row["speed_rating"] = tokens[speed_idx]
    next_start, comment = split_next_start_and_comment(tokens[next_start_idx:])
    row["next_start_winners"] = next_start
    row["comment"] = comment


def parse_past_run_row(line: str, race_number: str, horse_number: str, horse_name: str) -> dict[str, str]:
    line = clean_text(line)
    row = blank_past_run(race_number, horse_number, horse_name, line)

    marker_match = re.match(r"^(?P<marker>[a-z])\s+(?P<rest>\d{2}[A-Za-z]{3}\d{2}\b.*)$", line)
    if marker_match:
        row["date_marker"] = marker_match.group("marker")
        line = marker_match.group("rest")

    tokens = line.split()
    if len(tokens) < 4:
        return row

    row["date"] = tokens[0]
    row["course"] = tokens[1] if len(tokens) > 1 else ""
    row["going"] = tokens[2] if len(tokens) > 2 else ""
    row["ref"] = tokens[3] if len(tokens) > 3 else ""
    row["race_class"] = tokens[4] if len(tokens) > 4 else ""
    row["stake"] = tokens[5] if len(tokens) > 5 else ""
    row["race_class_stake"] = clean_text(" ".join([row["race_class"], row["stake"]]))
    idx = 6
    if idx < len(tokens) and not is_distance_token(tokens[idx]):
        row["average_merit_rating"] = tokens[idx]
        idx += 1
    if idx < len(tokens):
        row["distance"] = tokens[idx]
        idx += 1
    distance_match = re.match(r"^(\d+)([A-Za-z])?$", row["distance"])
    if distance_match:
        row["distance_metres"] = distance_match.group(1)
        row["straight_or_turn"] = distance_match.group(2) or ""

    weight_idx = -1
    for i in range(idx, len(tokens) - 1):
        if is_weight_token(tokens[i]) and re.fullmatch(r"\d+-\d+", tokens[i + 1]):
            weight_idx = i
            break
    if weight_idx == -1:
        return row

    pre_weight = tokens[idx:weight_idx]
    if pre_weight and is_shoes_headgear_token(pre_weight[0]) and (
        len(pre_weight) >= 3 or (len(pre_weight) >= 2 and re.fullmatch(r"\d+|--|X", pre_weight[1]))
    ):
        row["shoes_headgear"] = pre_weight.pop(0)
    if pre_weight and re.fullmatch(r"\d+|--|X", pre_weight[0]):
        row["official_merit_rating"] = pre_weight.pop(0)
    row["jockey"] = clean_text(" ".join(pre_weight))
    row["weight_allowance"] = tokens[weight_idx]
    row["draw_runners"] = tokens[weight_idx + 1]
    row["opening_betting"] = odds_to_slash(tokens[weight_idx + 2]) if weight_idx + 2 < len(tokens) else ""
    row["starting_price"] = odds_to_slash(tokens[weight_idx + 3]) if weight_idx + 3 < len(tokens) else ""

    pos_idx = weight_idx + 4
    positional_fields = [
        "position_800m",
        "lengths_800m",
        "position_400m",
        "lengths_400m",
        "finish_position",
        "finish_length",
    ]
    for offset, field in enumerate(positional_fields):
        if pos_idx + offset < len(tokens):
            row[field] = tokens[pos_idx + offset]

    winner_start = pos_idx + len(positional_fields)
    winner_weight_idx = -1
    for i in range(winner_start, len(tokens) - 1):
        if is_weight_token(tokens[i]) and is_time_token(tokens[i + 1]):
            winner_weight_idx = i
            break
    if winner_weight_idx == -1:
        for i in range(winner_start, len(tokens) - 1):
            embedded = split_embedded_winner_weight(tokens[i])
            if embedded and is_time_token(tokens[i + 1]):
                winner_name, winner_weight = embedded
                row["winner_or_second"] = clean_text(" ".join([*tokens[winner_start:i], winner_name]))
                tokens[i] = winner_weight
                assign_past_run_winner_tail(row, tokens, i)
                return row
        if winner_start < len(tokens):
            row["winner_or_second"] = clean_text(" ".join(tokens[winner_start:]))
        return row

    row["winner_or_second"] = clean_text(" ".join(tokens[winner_start:winner_weight_idx]))
    assign_past_run_winner_tail(row, tokens, winner_weight_idx)
    return row


def extract_past_runs(block: str, race_number: str, horse_number: str, horse_name: str) -> list[dict[str, str]]:
    runs: list[dict[str, str]] = []
    in_table = False
    current: dict[str, str] | None = None
    for raw_line in block.splitlines():
        line = clean_text(raw_line)
        if not line:
            continue
        if line.startswith("Date Crs G Ref"):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("COLLATERAL FORMLINES") or line.startswith("END COLLATERAL"):
            break
        date_like = re.match(r"^(?:[a-z]\s+)?\d{2}[A-Za-z]{3}\d{2}\b", line)
        if date_like:
            if current:
                runs.append(current)
            current = parse_past_run_row(line, race_number, horse_number, horse_name)
        elif current and not re.match(r"^\d{1,2}\s+[A-Z0-9’' .\-&]+?\s+LIFE:", line):
            current["raw_text"] = clean_text(current["raw_text"] + " " + line)
    if current:
        runs.append(current)
    return runs


def enrich_runners_with_profiles(
    data: dict[str, Any],
    pages: list[dict[str, Any]],
    summary_pages: dict[str, dict[str, Any]],
) -> None:
    runner_index = {
        (runner["race_number"], runner["horse_number"], runner["horse_name"].upper()): runner
        for runner in data["runners"]
    }
    for race_number in summary_pages:
        text = page_text_range_for_race(pages, summary_pages, race_number, data["meeting"].get("racecourse", ""))
        for header, block in parse_profile_blocks(text):
            name = clean_text(header.group("name"))
            key = (race_number, header.group("num"), name.upper())
            runner = runner_index.get(key)
            if not runner:
                continue
            runner["career_record"]["life"] = header.group("life")
            surface_label = clean_text(header.group("surface_label")).upper()
            surface_record = header.group("surface_record")
            runner["career_record"]["surface"] = f"{surface_label}: {surface_record}"
            if surface_label == "POLY":
                runner["career_record"]["poly"] = surface_record
            runner["career_record"]["course"] = header.group("course")
            runner["career_record"]["distance"] = first_match(r"Dist:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["course_and_distance"] = first_match(r"C&D:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["current_year"] = first_match(r"2026:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["previous_year"] = first_match(r"2025:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["normal"] = first_match(r"Norm:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["good"] = first_match(r"Good:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["wet"] = first_match(r"Wet:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["rain"] = first_match(r"Rain:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["class"] = first_match(r"Class:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["jockey_trainer"] = first_match(r"J\+T:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["jockey_horse"] = first_match(r"J\+H:\s*(\d+-\d+-\d+-\d+-\d+)", block)
            runner["career_record"]["win_place_range"] = first_match(r"WPR:\s*([^\s]+)", block)
            profile_best_vs_average = first_match(r"Best vs Ave:\s*([+-]?\d+(?:\.\d+)?)", block)
            if profile_best_vs_average:
                runner["best_vs_average"] = profile_best_vs_average

            runner["days_since_last_race"] = first_match(r"(\d+)\s+Days Since Last Race", block)
            runner["days_since_last_win"] = first_match(r"(\d+)\s+Days Since Last Win", block)
            breeding = parse_breeding(block)
            runner["breeding"].update({key: value for key, value in breeding.items() if value})
            owner = first_match(r"OWNER/S:\s*(.+?)(?:\n\d+\nDate Crs|Date Crs|$)", block, 1, flags=re.I | re.S)
            if owner:
                runner["owner"] = owner
            runner["past_runs"] = extract_past_runs(block, race_number, runner["horse_number"], runner["horse_name"])


def parse_collateral_lines(
    data: dict[str, Any],
    pages: list[dict[str, Any]],
    summary_pages: dict[str, dict[str, Any]],
) -> None:
    runners_by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for runner in data["runners"]:
        runners_by_race[runner["race_number"]].append(runner)

    runner_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for runner in data["runners"]:
        runner_lookup[(runner["race_number"], runner["horse_name"].upper())] = runner

    for race_number, runners in runners_by_race.items():
        names = sorted([runner["horse_name"].upper() for runner in runners], key=len, reverse=True)
        if race_number not in summary_pages:
            continue
        text = page_text_range_for_race(pages, summary_pages, race_number, data["meeting"].get("racecourse", ""))
        start = text.upper().find("COLLATERAL FORMLINES")
        if start == -1:
            continue
        section = text[start:]
        pending_wrapped_name = ""
        for line in section.splitlines():
            clean_line = clean_text(line)
            if not clean_line or clean_line.startswith("Horse Date") or clean_line.startswith("COLLATERAL"):
                continue
            if clean_line.startswith("END COLLATERAL") or clean_line.startswith("© COPYRIGHT"):
                break
            if clean_line.endswith("-") and not re.search(r"\d{2}[A-Za-z]{3}\d{2}", clean_line):
                pending_wrapped_name = clean_line.rstrip("-")
                continue
            if pending_wrapped_name:
                clean_line = clean_text(pending_wrapped_name + clean_line)
                pending_wrapped_name = ""

            matched_name = ""
            for name in names:
                if clean_line.upper().startswith(name + " "):
                    matched_name = name
                    break
                folded = name.replace(" ", "")
                if clean_line.upper().startswith(folded):
                    matched_name = name
                    break
            if not matched_name:
                continue
            tokens = clean_line.split()
            row = {
                "race_number": race_number,
                "horse_name": matched_name,
                "date": "",
                "course": "",
                "raw_text": clean_line,
            }
            for i, token in enumerate(tokens):
                if re.match(r"^\d{2}[A-Za-z]{3}\d{2}$", token):
                    row["date"] = token
                    row["course"] = tokens[i + 1] if i + 1 < len(tokens) else ""
                    break
            runner = runner_lookup.get((race_number, matched_name))
            if runner:
                runner["collateral_formlines"].append(row)


def extract_betting_page(data: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    page = next((page for page in pages if "COMPUTAFORM BEST BETS & PERMS" in page["text"]), None)
    if not page:
        data["validation"]["warnings"].append("Best bets/permutations page not found.")
        return
    tables = page["tables"]
    if not tables:
        data["validation"]["warnings"].append("Best bets/permutations page had no extractable tables.")
        return

    perm_table = tables[0]
    current_bet = ""
    bet_key_map = {
        "BIPOT": "bipot",
        "P/A": "place_accumulator",
        "PICK 6": "pick6",
        "JACKPOT 1": "jackpot1",
        "JACKPOT 2": "jackpot2",
    }
    race_start_map = {
        "bipot": 1,
        "place_accumulator": 2,
        "pick6": 3,
        "jackpot1": 4,
        "jackpot2": 5,
    }
    for row in perm_table:
        cells = [clean_text(cell) for cell in row if clean_text(cell)]
        if not cells:
            continue
        heading = cells[0].upper()
        matched_heading = next((key for key in bet_key_map if heading.startswith(key)), "")
        if matched_heading:
            current_bet = bet_key_map[matched_heading]
            continue
        if current_bet and cells[0].upper().startswith("LEG"):
            leg_no = int(first_match(r"LEG\s+(\d+)", cells[0]) or "0")
            data["betting"][current_bet].append(
                {
                    "leg": str(leg_no),
                    "race_number": str(race_start_map[current_bet] + leg_no - 1) if leg_no else "",
                    "selections": cells[1:],
                }
            )

    if len(tables) > 1:
        best_table = tables[1]
        label_map = {
            "TODAY’S BEST BET": "today_best_bet",
            "TODAY'S BEST BET": "today_best_bet",
            "TODAY’S TOP VALUE": "today_top_value",
            "TODAY'S TOP VALUE": "today_top_value",
            "BEST SWINGER": "best_swinger",
            "BEST EXACTA": "best_exacta",
            "BEST TRIFECTA": "best_trifecta",
            "BEST QUARTET": "best_quartet",
        }
        rows = [clean_text(row[0]) for row in best_table if row and clean_text(row[0])]
        for idx, row in enumerate(rows[:-1]):
            key = label_map.get(row.upper())
            if key:
                data["betting"][key] = rows[idx + 1]


def extract_best_on_ratings(data: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    page = next((page for page in pages if "TODAY’S BEST ON RATINGS" in page["text"] or "TODAY'S BEST ON RATINGS" in page["text"]), None)
    if not page:
        return
    text = page["text"]
    section = first_match(r"TODAY.S BEST ON RATINGS(.+?)(?:NOTE:|GENERAL COPYRIGHT NOTICE)", text, 1, flags=re.I | re.S)
    rows = []
    seen = set()
    for race_no, horse_no, horse_name in re.findall(
        r"RACE\s+(\d+):\s+(\d+)\s+([A-Z0-9’' .\-]+?)(?=\s*(?:\.?\s*RACE\s+\d+:|\n\d+\s+-|$))",
        section,
        flags=re.I | re.S,
    ):
        cleaned_name = clean_text(horse_name).rstrip(" .")
        key = (race_no, horse_no, cleaned_name.upper())
        if key in seen:
            continue
        seen.add(key)
        rows.append({"race_number": race_no, "horse_number": horse_no, "horse_name": cleaned_name})
    data["ratings"]["best_on_ratings"] = rows


def validate(data: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    validation = data["validation"]
    validation["pages_processed"] = str(len(pages))
    validation["races_found"] = str(len(data["races"]))
    by_race = defaultdict(list)
    for runner in data["runners"]:
        by_race[runner["race_number"]].append(runner)
    validation["runners_found_by_race"] = [
        {"race_number": race_number, "runner_count": str(len(by_race[race_number]))}
        for race_number in sorted(by_race, key=lambda x: int(x))
    ]

    expected_races = data["meeting"].get("number_of_races")
    if expected_races and expected_races != validation["races_found"]:
        validation["warnings"].append(f"Meeting advertises {expected_races} races but parser found {validation['races_found']}.")

    for runner in data["runners"]:
        if not runner["race_number"]:
            validation["missing_fields"].append({"entity": "runner", "horse_name": runner["horse_name"], "field": "race_number"})
        for field in ("horse_number", "horse_name", "draw", "jockey", "trainer"):
            if not runner.get(field):
                validation["missing_fields"].append(
                    {
                        "entity": "runner",
                        "race_number": runner["race_number"],
                        "horse_number": runner["horse_number"],
                        "field": field,
                    }
                )
        if any(value == "unclear" for value in runner["breeding"].values()):
            validation["unclear_fields"].append(
                {
                    "entity": "runner",
                    "race_number": runner["race_number"],
                    "horse_number": runner["horse_number"],
                    "horse_name": runner["horse_name"],
                    "field": "breeding",
                    "reason": "Breeding line extracted with disrupted character order/spacing.",
                }
            )

    for race in data["races"]:
        for field in ("race_number", "race_time", "race_name", "distance", "stake", "race_ref"):
            if not race.get(field):
                validation["missing_fields"].append({"entity": "race", "race_number": race["race_number"], "field": field})

    for bucket_name in ("computaform_ratings_by_race", "speed_ratings_by_race", "best_weighted_by_race"):
        for bucket in data["ratings"][bucket_name]:
            race_number = bucket["race_number"]
            runner_numbers = {runner["horse_number"] for runner in by_race[race_number]}
            for item in bucket["ratings"]:
                if item["horse_number"] not in runner_numbers and item["horse_number"] != "unclear":
                    validation["warnings"].append(
                        f"{bucket_name} item {item['horse_number']} {item['horse_name']} does not match Race {race_number} runners."
                    )

    replacement_count = sum(page.get("text_raw", "").count("\ufffd") for page in pages)
    if replacement_count:
        validation["possible_ocr_errors"].append(
            f"The PDF emitted U+FFFD {replacement_count} times; extractor converted those glyphs to decimal points in cleaned text."
        )

    past_rows = [past_run for runner in data["runners"] for past_run in runner["past_runs"]]
    past_missing_core = [
        past_run
        for past_run in past_rows
        if not past_run.get("date") or not past_run.get("course") or not past_run.get("weight_allowance") or not past_run.get("draw_runners")
    ]
    collateral_rows = [row for runner in data["runners"] for row in runner["collateral_formlines"]]
    collateral_missing_date = [row for row in collateral_rows if not row.get("date")]
    validation["quality_checks"] = {
        "past_performance_rows": str(len(past_rows)),
        "past_performance_rows_missing_core_fields": str(len(past_missing_core)),
        "collateral_formline_rows": str(len(collateral_rows)),
        "collateral_formline_rows_missing_date": str(len(collateral_missing_date)),
    }
    if past_missing_core:
        validation["warnings"].append(
            f"Past-performance parse quality: {len(past_missing_core)} of {len(past_rows)} rows are missing date, course, weight, or draw/runners."
        )
    if collateral_missing_date:
        validation["warnings"].append(
            f"Collateral formlines parse quality: {len(collateral_missing_date)} of {len(collateral_rows)} rows are missing a parsed date."
        )


def rows_for_spreadsheets(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    race_rows = []
    for race in data["races"]:
        race_rows.append(
            {
                "race_number": race["race_number"],
                "race_time": race["race_time"],
                "race_name": race["race_name"],
                "race_type": race["race_type"],
                "race_class": race["race_class"],
                "distance": race["distance"],
                "distance_category": race.get("distance_category", ""),
                "turn": race.get("turn", ""),
                "surface": race["surface"],
                "tab_bet_types": race.get("tab_bet_types", ""),
                "stake": race["stake"],
                "prize_breakdown": race.get("prize_breakdown", ""),
                "rcis": race.get("rcis", ""),
                "race_ref": race["race_ref"],
                "average_merit_rating": race["average_merit_rating"],
                "average_first3_percentage": race["average_first3_percentage"],
                "class_average_time": race["class_average_time"],
                "class_average_per_metre": race.get("class_average_per_metre", ""),
                "course_record": race["course_record"],
                "wfa": race.get("wfa", ""),
                "same_trainer_notes": race["same_trainer_notes"],
                "tipster_selections": "; ".join(race["tipster_selections"]),
                "preview": race["preview"],
            }
        )

    runner_rows = []
    for runner in data["runners"]:
        row = {
            key: value
            for key, value in runner.items()
            if key not in {"breeding", "career_record", "past_runs", "collateral_formlines"}
        }
        row.update({f"breeding_{key}": value for key, value in runner["breeding"].items()})
        row.update({f"career_{key}": value for key, value in runner["career_record"].items()})
        row["past_run_count"] = len(runner["past_runs"])
        row["collateral_formline_count"] = len(runner["collateral_formlines"])
        runner_rows.append(row)

    rating_rows = []
    rating_sources = {
        "computaform_ratings_by_race": "computaform",
        "speed_ratings_by_race": "speed",
        "best_weighted_by_race": "best_weighted",
    }
    for bucket_name, source in rating_sources.items():
        for bucket in data["ratings"][bucket_name]:
            for item in bucket["ratings"]:
                rating_rows.append({**item, "source": source})

    betting_rows = []
    for bet_key in ("bipot", "place_accumulator", "pick6", "jackpot1", "jackpot2"):
        for leg in data["betting"][bet_key]:
            betting_rows.append(
                {
                    "bet": bet_key,
                    "leg": leg["leg"],
                    "race_number": leg["race_number"],
                    "selections": " ".join(leg["selections"]),
                }
            )

    past_rows = []
    collateral_rows = []
    for runner in data["runners"]:
        past_rows.extend(runner["past_runs"])
        collateral_rows.extend(runner["collateral_formlines"])

    meeting_rows = [data["meeting"]]
    return {
        "meeting": meeting_rows,
        "races": race_rows,
        "runners": runner_rows,
        "ratings": rating_rows,
        "betting_perms": betting_rows,
        "past_performance": past_rows,
        "collateral_formlines": collateral_rows,
    }


def markdown_table(rows: list[dict[str, Any]], max_rows: int = 30) -> str:
    if not rows:
        return "_No rows extracted._"
    rows = rows[:max_rows]
    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        values = [clean_text(row.get(header, "")).replace("|", "\\|") for header in headers]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def write_outputs(data: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = slug_from_pdf_name(Path(data["meeting"]["source_pdf"]))
    paths = {
        "json": output_dir / f"{stem}_extraction.json",
        "xlsx": output_dir / f"{stem}_tables.xlsx",
        "markdown": output_dir / f"{stem}_report.md",
    }

    paths["json"].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    tables = rows_for_spreadsheets(data)
    with pd.ExcelWriter(paths["xlsx"], engine="openpyxl") as writer:
        for sheet_name, rows in tables.items():
            pd.DataFrame(rows).to_excel(writer, index=False, sheet_name=sheet_name[:31])
            csv_path = output_dir / f"{stem}_{sheet_name}.csv"
            paths[f"csv_{sheet_name}"] = csv_path
            with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer_csv = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["empty"])
                writer_csv.writeheader()
                writer_csv.writerows(rows or [{"empty": ""}])

    risk_rows: list[dict[str, Any]] = []
    for item in data["validation"]["unclear_fields"]:
        risk_rows.append(item if isinstance(item, dict) else {"type": "unclear", "detail": item})
    for item in data["validation"]["possible_ocr_errors"]:
        risk_rows.append({"type": "possible_ocr_error", "detail": item})
    for item in data["validation"]["warnings"]:
        risk_rows.append({"type": "warning", "detail": item})

    markdown_sections = [
        "# Computaform Extraction Report",
        "",
        "## 1. Extraction Summary",
        f"- Source PDF: `{data['meeting']['source_pdf']}`",
        f"- Pages processed: {data['validation']['pages_processed']}",
        f"- Races found: {data['validation']['races_found']}",
        f"- Runners found: {sum(int(item['runner_count']) for item in data['validation']['runners_found_by_race'])}",
        "",
        "## JSON",
        "```json",
        json.dumps(data, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 2. Meeting-Level Data",
        markdown_table(tables["meeting"]),
        "",
        "## 3. Race-Level Data",
        markdown_table(tables["races"]),
        "",
        "## 4. Runner-Level Data",
        markdown_table(tables["runners"]),
        "",
        "## 5. Ratings Tables",
        markdown_table(tables["ratings"]),
        "",
        "## 6. Tipster and Betting-Permutation Data",
        markdown_table(tables["betting_perms"]),
        "",
        "## 7. Past-Performance Data",
        markdown_table(tables["past_performance"]),
        "",
        "## 8. Validation Checks",
        "```json",
        json.dumps(data["validation"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 9. Unclear or Risky Fields",
        markdown_table(risk_rows),
        "",
        "## 10. Spreadsheet-Ready Tables",
        "\n".join(f"- `{path.name}`" for key, path in sorted(paths.items()) if key.startswith("csv_") or key == "xlsx"),
        "",
    ]
    paths["markdown"].write_text("\n".join(markdown_sections), encoding="utf-8")
    return paths


def extract(pdf_path: Path = DEFAULT_PDF, output_dir: Path = DEFAULT_OUTPUT_DIR) -> tuple[dict[str, Any], dict[str, Path]]:
    pages = load_pdf(pdf_path)
    data = empty_schema(pdf_path.name)
    extract_meeting(data, pages)
    summary_pages = detect_summary_pages(pages)
    extract_races_and_runners(data, summary_pages)
    apply_meeting_fallbacks(data, pdf_path)
    enrich_runners_with_profile_cards(data, pages, summary_pages)
    enrich_runners_with_profiles(data, pages, summary_pages)
    parse_collateral_lines(data, pages, summary_pages)
    extract_betting_page(data, pages)
    extract_best_on_ratings(data, pages)
    validate(data, pages)
    paths = write_outputs(data, output_folder_for_pdf(output_dir, pdf_path))
    return data, paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract structured data from a Computaform-style PDF.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Parent output directory. A PDF-named subfolder is created inside it.")
    args = parser.parse_args()

    data, paths = extract(args.pdf, args.output_dir)
    print(json.dumps(
        {
            "source_pdf": data["meeting"]["source_pdf"],
            "pages_processed": data["validation"]["pages_processed"],
            "races_found": data["validation"]["races_found"],
            "runners_found_by_race": data["validation"]["runners_found_by_race"],
            "outputs": {key: str(path) for key, path in paths.items()},
            "warnings": data["validation"]["warnings"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
