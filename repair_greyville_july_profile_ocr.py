from __future__ import annotations

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from extract_computaform import write_outputs


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs" / "hollywoodbets_greyville_2026_07_04_1"
JSON_PATH = OUTPUT_DIR / "hollywoodbets_greyville_2026_07_04_1_extraction.json"
WORDBOX_PATH = ROOT / "tmp_ocr" / "g0704_all_wordboxes.json"


RACE_PAGE_RANGES = {
    "1": range(12, 17),
    "2": range(17, 21),
    "3": range(22, 26),
    "4": range(27, 31),
    "5": range(32, 35),
    "6": range(36, 40),
    "7": range(42, 48),
    "8": range(50, 54),
    "9": range(54, 59),
    "10": range(61, 66),
    "11": range(68, 73),
    "12": range(73, 78),
}

SUMMARY_PROFILE_PAGES = {"12", "17", "22", "27", "32", "36", "42", "50", "54", "61", "68", "73"}

COURSE_SURFACE = {
    "GRP": "poly",
    "GRY": "turf",
    "SCI": "turf",
    "SCT": "turf",
    "TFI": "turf",
    "TUR": "turf",
    "VAA": "turf",
    "KEN": "turf",
    "DBV": "turf",
    "FRV": "poly",
    "FVP": "poly",
}

GREYVILLE_CODES = {"GRP", "GRY"}

CAREER_DEFAULTS = {
    "life": "",
    "current_year": "",
    "previous_year": "",
    "jockey_horse": "",
    "jockey_trainer": "",
    "poly": "",
    "course": "",
    "distance": "",
    "course_and_distance": "",
    "rain": "",
    "class": "",
    "surface": "",
    "normal": "",
    "good": "",
    "wet": "",
    "win_place_range": "",
    "turf": "",
    "headgear": "",
    "rest_record": "",
    "distance_category": "",
}


def norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("’", "'")).strip()


def norm_name(value: str) -> str:
    value = re.sub(r"\((?:AUS|ARG|BRZ|FR|GB|GER|IRE|USA|ZIM)\)", "", value or "", flags=re.I)
    value = re.sub(r"[^A-Za-z0-9]+", "", value).upper()
    return value


def parse_summary_counts(value: str) -> list[int] | None:
    cleaned = re.sub(r"\s*\d{1,3}%\s*$", "", value or "")
    numbers = re.findall(r"\d+", cleaned)
    if len(numbers) < 4:
        return None
    return [int(item) for item in numbers[:4]]


def parse_five_part_record(value: str) -> list[int] | None:
    value = value.split(":", 1)[-1].strip() if ":" in value else value
    if not re.fullmatch(r"\d+(?:-\d+){4}", value or ""):
        return None
    return [int(item) for item in value.split("-")]


def plausible_name(card_name: str, known_name: str) -> bool:
    left = norm_name(card_name)
    right = norm_name(known_name)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.72


def words_for_page(page: dict[str, Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for line in page["lines"]:
        for word in line.get("words", []):
            words.append(word)
    return words


def text_in_range(words: list[dict[str, Any]], y: float, xmin: float, xmax: float, tolerance: float = 7.0) -> str:
    selected = [
        word
        for word in words
        if abs(float(word["y"]) - y) <= tolerance and xmin <= float(word["x"]) < xmax
    ]
    selected.sort(key=lambda word: float(word["x"]))
    return norm_text(" ".join(word["text"] for word in selected))


def simple_cell_split(value: str) -> tuple[str, str]:
    parts = value.split(maxsplit=1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def normalize_record_value(value: str, max_starts: int | None = None) -> str:
    value = norm_text(value)
    recordish = value.replace("O", "0").replace("o", "0")
    recordish = re.sub(r"\s*\.\s*", "-", recordish)
    recordish = re.sub(r"\s+", "", recordish)
    if not re.fullmatch(r"\d+(?:-\d+)+", recordish):
        return value
    parts = recordish.split("-")
    if len(parts) == 5:
        return recordish

    def partitions(token: str) -> list[list[str]]:
        if token == "":
            return [[]]
        results: list[list[str]] = []
        max_piece = min(3, len(token))
        for size in range(1, max_piece + 1):
            piece = token[:size]
            for rest in partitions(token[size:]):
                results.append([piece] + rest)
        if len(token) <= 3:
            results.append([token])
        # Stable de-duplication.
        unique: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for result in results:
            key = tuple(result)
            if key not in seen:
                seen.add(key)
                unique.append(result)
        return unique

    candidate_parts: list[list[str]] = [[]]
    for part in parts:
        next_candidates: list[list[str]] = []
        for prefix in candidate_parts:
            for split in partitions(part):
                if len(prefix) + len(split) <= 5:
                    next_candidates.append(prefix + split)
        candidate_parts = next_candidates
    expanded: list[tuple[list[str], int]] = []
    for candidate in candidate_parts:
        if len(candidate) <= 5:
            append_count = 5 - len(candidate)
            expanded.append((candidate + ["0"] * append_count, append_count))

    valid: list[tuple[list[str], int]] = []
    for candidate, append_count in expanded:
        if len(candidate) != 5:
            continue
        nums = [int(item) for item in candidate]
        if nums[0] >= sum(nums[1:]):
            valid.append(([str(num) for num in nums], append_count))

    if max_starts is not None:
        constrained = [item for item in valid if int(item[0][0]) <= max_starts]
        if constrained:
            valid = constrained

    if not valid:
        return value
    # Prefer a split that explains the printed digits without padding missing zeroes.
    # Then prefer a plausible larger start count, rather than merged "94" style starts.
    valid.sort(key=lambda item: (item[1], -int(item[0][0]), sum(int(part) for part in item[0][1:])))
    return "-".join(valid[0][0])


def split_position(value: str) -> tuple[str, str]:
    first, rest = simple_cell_split(value)
    return first, rest


def fix_date_token(value: str) -> str:
    if not value:
        return ""
    value = value.replace("l", "1").replace("I", "1")
    value = re.sub(r"^([0-3]?\d)0ct", r"\1Oct", value, flags=re.I)
    value = re.sub(r"^([0-3]?\d)0c", r"\1Oc", value, flags=re.I)
    return value


def parse_date_cell(value: str) -> dict[str, str]:
    parts = value.split()
    if not parts:
        return {"date_marker": "", "date": "", "course": "", "going": "", "raw_course": ""}
    raw_date = parts[0]
    marker = ""
    if len(raw_date) > 1 and raw_date[0].islower():
        marker = raw_date[0]
        raw_date = raw_date[1:]
    date = fix_date_token(raw_date)
    course = parts[1] if len(parts) > 1 else ""
    raw_course = course
    going = parts[2] if len(parts) > 2 else ""
    if course.endswith("n") and len(course) > 3:
        course = course[:-1]
    course = course.upper().replace("0", "O")
    going = going.upper()
    return {"date_marker": marker, "date": date, "course": course, "going": going, "raw_course": raw_course}


def parse_dist_sh(value: str) -> tuple[str, str, str]:
    match = re.match(r"(?P<dist>\d{3,4})(?P<turn>[A-Za-z])?\s*(?P<rest>.*)$", value or "")
    if not match:
        return "", "", value
    turn = (match.group("turn") or "").upper()
    rest = norm_text(match.group("rest") or "")
    if turn not in {"S", "L", "R"}:
        rest = norm_text((turn + " " + rest).strip())
        turn = ""
    return match.group("dist"), turn, rest


def parse_mr_jockey(value: str) -> tuple[str, str]:
    match = re.match(r"(?P<mr>\d{1,3})\s+(?P<jockey>.*)$", value or "")
    if not match:
        return "", value
    return match.group("mr"), norm_text(match.group("jockey"))


def parse_winner_cell(value: str) -> tuple[str, str, str]:
    parts = value.split()
    if not parts:
        return "", "", ""
    finish_length = parts[0]
    winner_weight = ""
    winner = " ".join(parts[1:])
    if parts[1:] and re.match(r"^\d{2,3}(?:\.\d)?$", parts[-1]):
        winner_weight = parts[-1]
        winner = " ".join(parts[1:-1])
    return finish_length, norm_text(winner), winner_weight


def parse_time_cell(value: str) -> tuple[str, str]:
    parts = value.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def parse_hav_adj(value: str) -> tuple[str, str]:
    parts = value.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def parse_wr_comment(value: str) -> tuple[str, str]:
    value = norm_text(value)
    if not value:
        return "", ""
    match = re.match(r"(?P<wr>\d+\s*/\s*\d+\.{0,8})\s*(?P<comment>.*)$", value)
    if not match:
        return "", value
    return norm_text(match.group("wr").replace(" ", "")), norm_text(match.group("comment"))


def nearest_numeric(lines: list[dict[str, Any]], label_y: float, xmin: float, xmax: float, max_delta: float = 75) -> str:
    candidates: list[tuple[float, str]] = []
    for line in lines:
        y = float(line["y"])
        x = float(line["x"])
        text = line["text"]
        if label_y < y <= label_y + max_delta and xmin <= x <= xmax and re.search(r"\d", text):
            candidates.append((y, norm_text(text)))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def parse_top_fields(card_lines: list[dict[str, Any]], runner: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    career = runner.setdefault("career_record", {})
    breeding = runner.setdefault("breeding", {})

    for line in card_lines:
        text = norm_text(line["text"])
        x = float(line["x"])
        y = float(line["y"])

        if x < 260 and re.match(r"^\d{1,2}|I\s+", text):
            details["profile_card_name"] = text

        if "Days Since Last Race" in text:
            days = re.search(r"(\d+)\s+Days Since Last Race\s*-\s*(?:(\d+)\s+Days Since Last Win|No Win)", text)
            if days:
                runner["days_since_last_race"] = days.group(1)
                runner["days_since_last_win"] = days.group(2) or "No Win"
            bred = re.search(r"BRED BY[:' ]+\s*(.*?)(?:OWNER'?S|OWNENS|OWNERS|$)", text, re.I)
            if bred:
                breeding["bred_by"] = norm_text(bred.group(1))
            owner = re.search(r"(?:OWNER'?S|OWNENS|OWNERS)[:' ]+\s*(.*)$", text, re.I)
            if owner:
                runner["owner"] = norm_text(owner.group(1))

        if "Foaled:" in text:
            foaled = re.search(r"Foaled:\s*([^G]+?)(?:\s+Gelded:|$)", text, re.I)
            gelded = re.search(r"Gelded:\s*(.*)$", text, re.I)
            if foaled:
                breeding["foaled"] = norm_text(foaled.group(1))
            if gelded:
                breeding["gelded"] = norm_text(gelded.group(1))

        if 140 <= x <= 180 and y > min(float(ln["y"]) for ln in card_lines) and " by " in text and "Foaled:" not in text:
            # Best-effort parse of the pedigree line. Wrapped lines remain in raw top text.
            match = re.match(
                r"(?P<age>\d+\s*[A-Za-z]+)\s+(?P<sire>.*?)\s+[•*-]\s+(?P<dam>.*?)\s+by\s+(?P<damsire>.*)$",
                text,
                flags=re.I,
            )
            if match:
                runner["age_colour_sex"] = norm_text(match.group("age")).replace(" ", "")
                breeding["sire"] = norm_text(match.group("sire"))
                breeding["dam"] = norm_text(match.group("dam"))
                breeding["damsire"] = norm_text(match.group("damsire"))

        if "WPR:" in text:
            career["win_place_range"] = norm_text(text.split("WPR:", 1)[1])
            text = norm_text(text.split("WPR:", 1)[0])

        label_match = re.match(r"(?P<label>[A-Za-z0-9+& ]+):\s*(?P<value>.*)$", text)
        if label_match:
            label = norm_text(label_match.group("label"))
            raw_value = norm_text(label_match.group("value"))
            summary_counts = parse_summary_counts(runner.get("runs_wins_places", ""))
            life_value = career.get("life", "")
            life_counts = parse_five_part_record(life_value)
            max_starts = None
            if label != "LIFE" and summary_counts and life_counts and life_counts[:4] == summary_counts:
                max_starts = life_counts[0]
            value = normalize_record_value(raw_value, max_starts=max_starts)
            if label == "LIFE":
                career["life"] = value
            elif label == "Earn":
                details["profile_earnings"] = value
            elif label in {"2026", "2025"}:
                career["current_year" if label == "2026" else "previous_year"] = value
            elif label == "J+H":
                career["jockey_horse"] = value
            elif label == "J+T":
                career["jockey_trainer"] = value
            elif label in {"POLY", "TURF"}:
                career["surface"] = f"{label}: {value}"
                if label == "POLY":
                    career["poly"] = value
                else:
                    career["turf"] = value
            elif label in {"Norm", "Good"}:
                career[label.lower()] = value
            elif label in {"Rain", "Wet"}:
                career["rain" if label == "Rain" else "wet"] = value
            elif label == "Hdgr":
                career["headgear"] = value
            elif label.startswith("Rest"):
                career["rest_record"] = f"{label}: {value}"
            elif label == "WPR":
                career["win_place_range"] = value
            elif label == "CRSE":
                career["course"] = value
            elif label == "Dist":
                career["distance"] = value
            elif label == "C&D":
                career["course_and_distance"] = value
            elif label == "Dcat":
                career["distance_category"] = value
            elif label.startswith("Clas"):
                career["class"] = f"{label}: {value}"
            elif label == "Best vs Ave":
                runner["best_vs_average"] = re.sub(r"([+-]\d)\s+(\d{2})$", r"\1.\2", value)

    for line in card_lines:
        text = norm_text(line["text"])
        if "F Rate" in text:
            cf_rate = nearest_numeric(card_lines, float(line["y"]), 1000, 1045)
            if cf_rate:
                runner["computaform_rating"] = cf_rate
        if "Sp Rate" in text or "sp Rate" in text:
            sp_rate = nearest_numeric(card_lines, float(line["y"]), 1000, 1045)
            if sp_rate:
                runner["speed_rating"] = sp_rate
        if re.search(r"No\s+h[dt][l/]?gr\s*last", text, re.I):
            runner["headgear_change"] = text

    for line in card_lines:
        x = float(line["x"])
        text = norm_text(line["text"])
        if 1340 <= x <= 1375 and re.search(r"\d", text) and "KG" not in text:
            nums = re.findall(r"\d+(?:\.\d+)?", text)
            if nums:
                runner["weight"] = nums[0]
            if len(nums) >= 2 and not runner.get("draw"):
                runner["draw"] = nums[-1]

    return details


def parse_past_rows(
    race_number: str,
    runner: dict[str, Any],
    page: dict[str, Any],
    page_words: list[dict[str, Any]],
    start_y: float,
    end_y: float,
    header_y: float,
) -> list[dict[str, str]]:
    date_lines: list[dict[str, Any]] = []
    for line in page["lines"]:
        text = norm_text(line["text"])
        x = float(line["x"])
        y = float(line["y"])
        if not (header_y + 8 <= y < end_y):
            continue
        if not (45 <= x <= 105):
            continue
        if re.match(r"^[a-zI0-9O]{0,3}[0-3IilO]?\d[A-Za-z0-9]{3,5}\s+[A-Za-z0-9]{2,4}", text):
            date_lines.append(line)
    date_lines.sort(key=lambda line: float(line["y"]))

    rows: list[dict[str, str]] = []
    columns = {
        "date": (45, 190),
        "ref_race": (190, 310),
        "average_merit_rating": (310, 345),
        "dist_sh": (345, 420),
        "mr_jockey": (420, 555),
        "weight_allowance": (555, 615),
        "draw_runners": (615, 665),
        "opening_betting": (665, 710),
        "starting_price": (710, 755),
        "position_800": (755, 815),
        "position_400": (815, 870),
        "finish_position": (870, 895),
        "winner": (895, 1085),
        "time": (1085, 1175),
        "finish_rank": (1175, 1200),
        "hav_adj": (1200, 1285),
        "speed_rating": (1285, 1320),
        "wr_comment": (1320, 1500),
    }
    for line in date_lines:
        y = float(line["y"])
        cells = {name: text_in_range(page_words, y, xmin, xmax) for name, (xmin, xmax) in columns.items()}
        date_bits = parse_date_cell(cells["date"])
        ref, race_class_stake = simple_cell_split(cells["ref_race"])
        distance, straight_or_turn, shoes_headgear = parse_dist_sh(cells["dist_sh"])
        official_mr, jockey = parse_mr_jockey(cells["mr_jockey"])
        pos800, len800 = split_position(cells["position_800"])
        pos400, len400 = split_position(cells["position_400"])
        finish_length, winner, winner_weight = parse_winner_cell(cells["winner"])
        winner_time, final_400 = parse_time_cell(cells["time"])
        hav, adj = parse_hav_adj(cells["hav_adj"])
        wr, comment = parse_wr_comment(cells["wr_comment"])
        raw = " | ".join(value for value in cells.values() if value)
        rows.append(
            {
                "race_number": race_number,
                "horse_number": runner["horse_number"],
                "horse_name": runner["horse_name"],
                **date_bits,
                "ref": ref,
                "race_class_stake": race_class_stake,
                "average_merit_rating": cells["average_merit_rating"],
                "distance": distance,
                "straight_or_turn": straight_or_turn,
                "shoes_headgear": shoes_headgear,
                "official_merit_rating": official_mr,
                "jockey": jockey,
                "weight_allowance": cells["weight_allowance"],
                "draw_runners": cells["draw_runners"],
                "opening_betting": cells["opening_betting"],
                "starting_price": cells["starting_price"],
                "position_800m": pos800,
                "lengths_800m": len800,
                "position_400m": pos400,
                "lengths_400m": len400,
                "finish_position": cells["finish_position"],
                "finish_length": finish_length,
                "winner_or_second": winner,
                "winner_weight": winner_weight,
                "winner_time": winner_time,
                "final_400": final_400,
                "finish_rank": cells["finish_rank"],
                "horse_adjusted_vs_average": hav,
                "adjusted_time_per_metre": adj,
                "speed_rating": cells["speed_rating"],
                "next_start_winners": wr,
                "comment": comment,
                "raw_text": raw,
            }
        )
    return rows


def detect_cards(data: dict[str, Any], pages: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    runners = {(runner["race_number"], runner["horse_number"]): runner for runner in data["runners"]}
    cards: dict[tuple[str, str], dict[str, Any]] = {}
    start_pattern = re.compile(r"^(?P<num>\d{1,2}|I)\s+(?P<name>[A-Z][A-Z'’ .-]+)$")

    for race_number, page_range in RACE_PAGE_RANGES.items():
        for page_number in page_range:
            page_key = str(page_number)
            page = pages[page_key]
            min_y = 1000 if page_key in SUMMARY_PROFILE_PAGES else 70
            for line in page["lines"]:
                text = norm_text(line["text"]).upper()
                x = float(line["x"])
                y = float(line["y"])
                if x > 260 or y < min_y:
                    continue
                match = start_pattern.match(text)
                if not match:
                    continue
                horse_number = "1" if match.group("num") == "I" else str(int(match.group("num")))
                runner = runners.get((race_number, horse_number))
                if not runner:
                    continue
                card_name = norm_text(match.group("name"))
                if not plausible_name(card_name, runner.get("horse_name", "")):
                    continue
                key = (race_number, horse_number)
                if key in cards:
                    continue
                cards[key] = {
                    "page": page_key,
                    "start_y": y,
                    "card_name": card_name,
                    "line": line,
                }

    by_page: dict[str, list[dict[str, Any]]] = {}
    for key, card in cards.items():
        card["key"] = key
        by_page.setdefault(card["page"], []).append(card)
    for page_cards in by_page.values():
        page_cards.sort(key=lambda item: item["start_y"])
        for idx, card in enumerate(page_cards):
            card["end_y"] = page_cards[idx + 1]["start_y"] - 4 if idx + 1 < len(page_cards) else 1950
    return cards


def reset_profile_dependent_fields(data: dict[str, Any]) -> None:
    for runner in data["runners"]:
        runner["career_record"] = dict(CAREER_DEFAULTS)
        runner["past_runs"] = []
        runner["derived_exposure"] = {}
        runner["days_since_last_race"] = ""
        runner["days_since_last_win"] = ""
        runner["owner"] = ""
        runner["draw_source_note"] = ""
        runner["breeding"] = {
            "sire": "",
            "dam": "",
            "damsire": "",
            "foaled": "",
            "gelded": "",
            "bred_by": "",
        }


def recover_profile_fields(data: dict[str, Any], pages: dict[str, Any]) -> dict[str, Any]:
    cards = detect_cards(data, pages)
    page_words = {page_key: words_for_page(page) for page_key, page in pages.items()}
    warnings: list[str] = []
    card_details: list[dict[str, Any]] = []

    # Verified from rendered profile-card crops in tmp_ocr/missing_draw_crop_sheet.png.
    verified_draw_one = {
        ("1", "1"),
        ("2", "1"),
        ("3", "1"),
        ("4", "1"),
        ("5", "1"),
        ("6", "1"),
        ("7", "1"),
        ("8", "1"),
        ("9", "1"),
        ("10", "1"),
        ("11", "1"),
        ("12", "1"),
    }

    runner_lookup = {(runner["race_number"], runner["horse_number"]): runner for runner in data["runners"]}
    for key, runner in runner_lookup.items():
        card = cards.get(key)
        if not card:
            warnings.append(f"No profile card matched for race {key[0]} runner {key[1]} {runner.get('horse_name', '')}.")
            continue
        page = pages[card["page"]]
        start_y = card["start_y"]
        end_y = card["end_y"]
        line_start_y = start_y - 8
        card_lines = [
            line
            for line in page["lines"]
            if line_start_y <= float(line["y"]) < end_y
        ]
        header_ys = [
            float(line["y"])
            for line in card_lines
            if float(line["x"]) < 120 and re.search(r"\bDate\b", line["text"], flags=re.I)
        ]
        header_y = min(header_ys) if header_ys else None
        top_lines = [line for line in card_lines if header_y is None or float(line["y"]) < header_y]
        details = parse_top_fields(top_lines, runner)
        if key in verified_draw_one and not runner.get("draw"):
            runner["draw"] = "1"
            runner["draw_source_note"] = "Recovered from rendered profile-card Draw cell; OCR text layer dropped the tiny digit."

        # Correct obvious summary OCR horse-name damage only when the profile heading is clear.
        card_name = card["card_name"]
        if runner["horse_name"].startswith("A ") and norm_name(card_name) == norm_name(runner["horse_name"][2:]):
            runner["horse_name"] = card_name
        if runner["horse_name"] == "OTTO LUYKEN" and card_name == "OTTO WYKEN":
            runner["horse_name"] = card_name
        details["race_number"] = key[0]
        details["horse_number"] = key[1]
        details["page"] = card["page"]
        details["start_y"] = start_y
        details["header_y"] = header_y
        card_details.append(details)

        if header_y is None:
            runner["past_runs"] = [
                {
                    "race_number": key[0],
                    "horse_number": key[1],
                    "horse_name": runner["horse_name"],
                    "date": "unclear",
                    "course": "unclear",
                    "raw_text": "Past-performance header not safely located in OCR profile card.",
                }
            ]
            continue
        rows = parse_past_rows(key[0], runner, page, page_words[card["page"]], start_y, end_y, header_y)
        if rows:
            for row in rows:
                row["horse_name"] = runner["horse_name"]
            runner["past_runs"] = rows
        else:
            runner["past_runs"] = [
                {
                    "race_number": key[0],
                    "horse_number": key[1],
                    "horse_name": runner["horse_name"],
                    "date": "unclear",
                    "course": "unclear",
                    "raw_text": "Past-performance rows not safely separable from OCR profile card.",
                }
            ]

    for race in data["races"]:
        for compact_runner in race.get("runners", []):
            full = runner_lookup.get((race["race_number"], compact_runner.get("horse_number", "")))
            if full:
                compact_runner["horse_name"] = full["horse_name"]

    data.setdefault("validation", {})["profile_card_parse_details"] = {
        "matched_cards": str(len(cards)),
        "expected_runner_cards": str(len(data["runners"])),
        "warnings": warnings,
        "source": "Rendered page OCR word boxes from tmp_ocr/g0704_all_wordboxes.json",
    }
    return data


def reconcile_life_records(data: dict[str, Any]) -> None:
    notes: list[str] = []
    for runner in data["runners"]:
        summary_counts = parse_summary_counts(runner.get("runs_wins_places", ""))
        if not summary_counts:
            continue
        career = runner.get("career_record", {})
        life_counts = parse_five_part_record(career.get("life", ""))
        if life_counts and life_counts[:4] == summary_counts:
            continue
        for field in ("surface", "turf", "poly", "headgear"):
            candidate = parse_five_part_record(career.get(field, ""))
            if candidate and candidate[:4] == summary_counts:
                career["life"] = "-".join(str(item) for item in candidate)
                notes.append(
                    f"Race {runner['race_number']} runner {runner['horse_number']} {runner['horse_name']}: "
                    f"LIFE repaired from {field} record because it matches summary starts/wins/places."
                )
                break
    data.setdefault("validation", {})["career_record_reconciliation_notes"] = notes


def repair_subrecords_against_life(data: dict[str, Any]) -> None:
    notes: list[str] = []
    fields = (
        "surface",
        "poly",
        "turf",
        "good",
        "wet",
        "normal",
        "rain",
        "course",
        "distance",
        "course_and_distance",
        "distance_category",
        "headgear",
    )
    for runner in data["runners"]:
        career = runner.get("career_record", {})
        life = parse_five_part_record(career.get("life", ""))
        if not life:
            continue
        life_start = life[0]
        for field in fields:
            value = career.get(field, "")
            prefix = ""
            record_text = value
            if ":" in value:
                prefix, record_text = value.split(":", 1)
                record_text = record_text.strip()
            record = parse_five_part_record(record_text)
            if not record or record[0] <= life_start:
                continue
            parts = record_text.split("-")
            merged = parts[0] + parts[1]
            life_text = str(life_start)
            if not merged.startswith(life_text):
                continue
            repaired_second = merged[len(life_text) :]
            if not repaired_second:
                continue
            candidate = [life_text, repaired_second] + parts[2:]
            candidate_nums = [int(item) for item in candidate]
            if candidate_nums[0] < sum(candidate_nums[1:]):
                continue
            repaired = "-".join(str(item) for item in candidate_nums)
            career[field] = f"{prefix}: {repaired}" if prefix else repaired
            notes.append(
                f"Race {runner['race_number']} runner {runner['horse_number']} {runner['horse_name']}: "
                f"{field} repaired against LIFE start count {life_start}."
            )
    data.setdefault("validation", {})["career_subrecord_repair_notes"] = notes


def add_derived_exposure(data: dict[str, Any]) -> None:
    race_distances = {race["race_number"]: race.get("distance", "") for race in data["races"]}
    for runner in data["runners"]:
        pairs = []
        course_counts: Counter[str] = Counter()
        surface_counts: Counter[str] = Counter()
        venue_counts: Counter[str] = Counter()
        distance_matches = 0
        target_distance = re.sub(r"\D", "", race_distances.get(runner["race_number"], ""))
        for row in runner.get("past_runs", []):
            course = (row.get("course") or "").upper()
            date = row.get("date") or ""
            if course and course != "UNCLEAR" and date and date != "unclear":
                pairs.append({"date": date, "course": course})
                course_counts[course] += 1
                surface = COURSE_SURFACE.get(course)
                if surface:
                    surface_counts[surface] += 1
                if course in GREYVILLE_CODES:
                    venue_counts["greyville"] += 1
            if target_distance and re.sub(r"\D", "", row.get("distance", "")) == target_distance:
                distance_matches += 1
        runner["derived_exposure"] = {
            "method": "Derived from structured OCR profile past-run rows. Use for exposure checks only; official POLY/CRSE/Dist/C&D splits come from career_record.",
            "date_course_pairs": pairs,
            "course_code_counts": dict(course_counts),
            "surface_counts": dict(surface_counts),
            "venue_counts": dict(venue_counts),
            "greyville_poly_starts_grp": str(course_counts.get("GRP", 0)),
            "greyville_turf_starts_gry": str(course_counts.get("GRY", 0)),
            "greyville_total_starts": str(course_counts.get("GRP", 0) + course_counts.get("GRY", 0)),
            "poly_starts_all_known_codes": str(sum(count for code, count in course_counts.items() if COURSE_SURFACE.get(code) == "poly")),
            "turf_starts_all_known_codes": str(sum(count for code, count in course_counts.items() if COURSE_SURFACE.get(code) == "turf")),
            "known_date_course_pairs": str(len(pairs)),
            "current_race_distance": race_distances.get(runner["race_number"], ""),
            "current_distance_token_matches": str(distance_matches),
            "course_and_distance_alignment": "Structured OCR row parse preserves row-level course and distance cells, but OCR numeric cells should still be spot-checked for high-stakes decisions.",
        }


def update_validation(data: dict[str, Any]) -> None:
    validation = data.setdefault("validation", {})
    validation["races_found"] = str(len(data["races"]))
    validation["runners_found_by_race"] = [
        {
            "race_number": race["race_number"],
            "runner_count": str(sum(1 for runner in data["runners"] if runner["race_number"] == race["race_number"])),
        }
        for race in data["races"]
    ]

    missing_fields = []
    for item in validation.get("missing_fields", []):
        if item.get("entity") == "runner" and item.get("field") == "draw":
            runner = next(
                (
                    row
                    for row in data["runners"]
                    if row.get("race_number") == item.get("race_number")
                    and row.get("horse_number") == item.get("horse_number")
                ),
                None,
            )
            if runner and runner.get("draw"):
                continue
        missing_fields.append(item)
    validation["missing_fields"] = missing_fields

    official_counts = {
        "career_life": 0,
        "career_surface": 0,
        "career_course": 0,
        "career_distance": 0,
        "career_course_and_distance": 0,
        "days_since_last_race": 0,
        "days_since_last_win": 0,
        "structured_past_run_rows": 0,
        "placeholder_past_run_rows": 0,
    }
    for runner in data["runners"]:
        career = runner.get("career_record", {})
        for field, key in (
            ("life", "career_life"),
            ("surface", "career_surface"),
            ("course", "career_course"),
            ("distance", "career_distance"),
            ("course_and_distance", "career_course_and_distance"),
        ):
            value = career.get(field, "")
            if value and "unclear" not in str(value).lower():
                official_counts[key] += 1
        if runner.get("days_since_last_race"):
            official_counts["days_since_last_race"] += 1
        if runner.get("days_since_last_win"):
            official_counts["days_since_last_win"] += 1
        for row in runner.get("past_runs", []):
            if row.get("date") and row.get("date") != "unclear" and row.get("course") and row.get("course") != "unclear":
                official_counts["structured_past_run_rows"] += 1
            else:
                official_counts["placeholder_past_run_rows"] += 1
    validation["quality_counts"] = {key: str(value) for key, value in official_counts.items()}
    warnings = validation.setdefault("warnings", [])
    warning = (
        "Greyville 2026-07-04 profile repair: official career splits and past-run rows were recovered "
        "from rendered profile-card OCR word boxes; tiny draw digits for runner #1 in each race were "
        "verified from rendered profile-card crop cells."
    )
    if warning not in warnings:
        warnings.append(warning)


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
    if not JSON_PATH.exists():
        print(f"Missing extraction JSON: {JSON_PATH}", file=sys.stderr)
        return 1
    if not WORDBOX_PATH.exists():
        print(f"Missing OCR word boxes: {WORDBOX_PATH}", file=sys.stderr)
        return 1

    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    pages = json.loads(WORDBOX_PATH.read_text(encoding="utf-8"))

    reset_profile_dependent_fields(data)
    recover_profile_fields(data, pages)
    reconcile_life_records(data)
    repair_subrecords_against_life(data)
    add_derived_exposure(data)
    update_validation(data)
    normalize_nested_runner_keys(data)

    paths = write_outputs(data, OUTPUT_DIR)
    matched = data["validation"]["profile_card_parse_details"]["matched_cards"]
    expected = data["validation"]["profile_card_parse_details"]["expected_runner_cards"]
    quality = data["validation"]["quality_counts"]
    print(f"Matched profile cards: {matched}/{expected}")
    print(f"Structured past rows: {quality['structured_past_run_rows']}")
    print(f"Placeholder past rows: {quality['placeholder_past_run_rows']}")
    print(f"Career C&D count: {quality['career_course_and_distance']}")
    print(paths["json"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
