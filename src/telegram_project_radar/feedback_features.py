"""Conservative, local feature snapshots. Text and notes are never instructions."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from . import __version__

FEATURE_VERSION = "feedback-features-v1"
FEATURE_VALUES = {
    "role": ("devops", "sre", "network_engineer", "sales_development", "recruiter", "designer"),
    "level": ("junior", "senior", "staff", "cto"),
    "stack": ("azure", "salesforce", "kubernetes", "aws"),
    "engagement": ("full_time", "contract"),
    "work_mode": ("remote", "onsite", "hybrid"),
}
PATTERNS = {
    "role": {
        "devops": r"\bdevops\b", "sre": r"\bsre\b|site reliability engineer",
        "network_engineer": r"network engineer|сетевой инженер",
        "sales_development": r"\bsdr\b|sales development",
        "recruiter": r"\brecruiter\b|рекрутер", "designer": r"\bdesigner\b|дизайнер",
    },
    "level": {v: rf"\b{v}\b" for v in FEATURE_VALUES["level"]},
    "stack": {v: rf"\b{v}\b" for v in FEATURE_VALUES["stack"]},
    "engagement": {
        "full_time": r"\bfull[ -]?time\b|полная занятость",
        "contract": r"\bcontract\b|\bfreelance\b|проектная работа",
    },
    "work_mode": {
        "remote": r"\bremote\b|удал[её]нно", "onsite": r"\bon[ -]?site\b",
        "hybrid": r"\bhybrid\b|гибрид",
    },
}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def instant(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except (TypeError, ValueError, AttributeError):
        raise ValueError("feedback timestamp must be an ISO timestamp with timezone") from None


def snapshot_identity(snapshot: dict) -> str:
    # A re-export at a later time must not create an ambiguous duplicate snapshot.
    return digest({k: v for k, v in snapshot.items() if k not in {"snapshot_id", "captured_at"}})


def make_snapshot(item: dict, captured_at: str, *, selector_version: str = __version__) -> dict:
    """Called at Inbox export time, not retrospectively at decision import time.

    Restrict matching to the heading: a stack word buried in an unrelated job's
    skills or an imperative in a note is not evidence for a global exclusion.
    """
    heading = str(item["title"]).casefold()
    features = {key: sorted(v for v, pattern in patterns.items()
                            if re.search(pattern, heading))
                for key, patterns in PATTERNS.items()}
    result = {
        "candidate_id": item["candidate_id"], "run_id": item["run_id"],
        "rules_version": item["rules_version"], "selector_version": selector_version,
        "feature_version": FEATURE_VERSION, "captured_at": captured_at,
        "published_at": item["published_at"], "item_type": item["type"],
        "score": item["score"], "decision": item.get("selection_decision", "include"),
        "features": features, "text_sha256": hashlib.sha256(item["summary"].encode()).hexdigest(),
        "locator": item.get("locator"), "synthetic": bool(item.get("synthetic", False)),
    }
    result["snapshot_id"] = snapshot_identity(result)
    return validate_snapshot(result)


def validate_snapshot(value: dict) -> dict:
    required = {"snapshot_id", "candidate_id", "run_id", "rules_version", "selector_version",
                "feature_version", "captured_at", "published_at", "item_type", "score",
                "decision", "features", "text_sha256", "locator", "synthetic"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("unsupported snapshot schema; use an Inbox learning_snapshots export")
    if value["snapshot_id"] != snapshot_identity(value):
        raise ValueError("snapshot integrity check failed")
    if value["feature_version"] != FEATURE_VERSION:
        raise ValueError("unsupported feature version")
    if value["item_type"] not in {"opportunity", "digest_item", "company", "person", "digest", "noise", "advertisement"}:
        raise ValueError("unsupported snapshot candidate type")
    if value["decision"] not in {"include", "review", "exclude", "duplicate"}:
        raise ValueError("unsupported snapshot selection decision")
    if type(value["score"]) is not int or not 0 <= value["score"] <= 100:
        raise ValueError("snapshot score must be an integer between 0 and 100")
    for field in ("candidate_id", "run_id", "rules_version", "selector_version", "text_sha256"):
        if not isinstance(value[field], str) or not value[field]:
            raise ValueError("snapshot identifiers and versions must be nonempty strings")
    for field in ("candidate_id", "run_id"):
        if not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", value[field]):
            raise ValueError("snapshot identifiers must contain only safe identifier characters")
    instant(value["captured_at"])
    instant(value["published_at"])
    if not isinstance(value["features"], dict) or set(value["features"]) != set(FEATURE_VALUES):
        raise ValueError("unsupported snapshot feature keys")
    for key, values in value["features"].items():
        if not isinstance(values, list) or any(v not in FEATURE_VALUES[key] for v in values):
            raise ValueError("unsupported snapshot feature value")
    if value["locator"] is not None:
        if (not isinstance(value["locator"], dict)
                or set(value["locator"]) != {"chat_id", "message_id", "item_index"}
                or any(type(v) is not int for v in value["locator"].values())):
            raise ValueError("invalid snapshot locator")
    return value
