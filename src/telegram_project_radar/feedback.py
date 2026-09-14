"""Evaluate selector behavior against explicit user review without reading message text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

from .models import CandidateItem


def load_feedback(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_feedback(
    feedback: Mapping[str, object], items: Iterable[CandidateItem]
) -> dict[str, object]:
    surfaced: dict[tuple[int, int], set[str]] = {}
    classified: dict[tuple[int, int], set[str]] = {}
    for item in items:
        classified.setdefault((item.chat_id, item.message_id), set()).add(item.item_type)
        if item.evaluation.decision in {"include", "review"}:
            surfaced.setdefault((item.chat_id, item.message_id), set()).add(item.item_type)

    counts = {"matched": 0, "mismatched": 0}
    mismatches: list[dict[str, object]] = []
    for label in feedback["labels"]:
        key = (int(label["chat_id"]), int(label["message_id"]))
        types = surfaced.get(key, set())
        verdict = str(label["verdict"])
        expected_types = {
            str(value)
            for value in label.get("expected_types", [label.get("expected_type")])
            if value
        }
        rejected_types = {str(value) for value in label.get("rejected_types", [])}
        if verdict == "types":
            matched = expected_types.issubset(types) and not (rejected_types & types)
        elif verdict == "keep":
            matched = bool(types & (expected_types | {"digest_item"}))
        elif verdict == "split_digest":
            matched = (
                "digest_item" in classified.get(key, set())
                and "digest" not in types
            )
        elif verdict == "reject":
            matched = not types
        else:
            matched = True
        counts["matched" if matched else "mismatched"] += 1
        if not matched:
            mismatches.append(
                {
                    "report_no": int(label["report_no"]),
                    "verdict": verdict,
                    "expected_types": sorted(expected_types),
                    "rejected_types": sorted(rejected_types),
                    "surfaced_types": sorted(types),
                }
            )
    return {
        **counts,
        "reviewed": len(feedback["labels"]),
        "mismatches": mismatches,
    }
