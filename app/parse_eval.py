"""Resume-parsing accuracy against a hand-labelled golden set (PLAN.md §11 #1).

A case is a JSON file: {"resume_path": "...", "expected": {field: value, ...}}. Only the fields
present in "expected" are scored, so a case can label as much or as little as the author wants.
"""

import json
import re
from pathlib import Path

LIST_FIELDS = ("skills", "domains", "target_titles")
TEXT_FIELDS = ("name", "current_role", "seniority")


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", str(value or "").lower()).strip()


def _f1(expected: list, actual: list) -> float:
    want, got = {_norm(v) for v in expected if _norm(v)}, {_norm(v) for v in actual or [] if _norm(v)}
    if not want and not got:
        return 1.0
    if not want or not got:
        return 0.0
    overlap = len(want & got)
    precision, recall = overlap / len(got), overlap / len(want)
    return 0.0 if overlap == 0 else round(2 * precision * recall / (precision + recall), 3)


def _text(expected, actual) -> float:
    want, got = set(_norm(expected).split()), set(_norm(actual).split())
    if not want:
        return 1.0 if not got else 0.0
    return round(len(want & got) / len(want | got), 3)  # word overlap: "ML Engineer" vs "Machine Learning Engineer" partial


def score_case(expected: dict, actual: dict) -> dict:
    fields = {}
    for field, want in expected.items():
        got = actual.get(field)
        if field in LIST_FIELDS:
            fields[field] = _f1(want, got)
        elif field == "years_experience":
            try:
                fields[field] = 1.0 if abs(float(got) - float(want)) <= 1 else 0.0
            except (TypeError, ValueError):
                fields[field] = 0.0
        elif field in TEXT_FIELDS:
            fields[field] = _text(want, got)
    overall = round(sum(fields.values()) / len(fields), 3) if fields else None
    return {"overall": overall, "fields": fields}


def load_cases(directory: Path) -> list[tuple[str, dict]]:
    return [(path.stem, json.loads(path.read_text(encoding="utf-8"))) for path in sorted(directory.glob("*.json"))]
