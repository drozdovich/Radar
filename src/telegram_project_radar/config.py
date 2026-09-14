"""Private installation settings. A fresh checkout has no live source access."""

from __future__ import annotations

import os
import json
from pathlib import Path


PROJECT_ROOT = Path(os.environ.get("RADAR_PROJECT_ROOT", Path.cwd())).expanduser().resolve()
PRIVATE_ROOT = Path(
    os.environ.get("RADAR_DATA_DIR", PROJECT_ROOT / ".radar")
).expanduser().resolve()
DATABASE_PATH = PRIVATE_ROOT / "radar.sqlite3"
REPORTS_DIR = PRIVATE_ROOT / "reports"
INBOX_EXPORTS_DIR = PRIVATE_ROOT / "inbox"
TELEGRAM_SEARCH_SOURCE = Path(
    os.environ.get(
        "TGSEARCH_SOURCE_DIR",
        Path.home() / "Applications" / "TelegramSearchMCP" / "src",
    )
).expanduser().resolve()
FEEDBACK_PATH = PRIVATE_ROOT / "feedback" / "review.json"

def load_sources(path: Path) -> dict:
    if not path.exists():
        return {"approved": [], "additional": [], "comment_pairs": []}
    try:
        data = json.loads(path.read_text())
        def require(valid):
            if not valid:
                raise ValueError("Invalid source configuration")
        require(isinstance(data, dict) and data.get("schema_version") == 1)
        require(isinstance(data.get("synthetic", False), bool))
        groups = [data.get(key, []) for key in ("approved", "additional")]
        require(all(isinstance(group, list) for group in groups))
        rows = [row for group in groups for row in group]
        require(all(isinstance(row, dict) and type(row.get("chat_id")) is int
                   and row["chat_id"] < 0 and isinstance(row.get("name"), str)
                   and row["name"].strip() for row in rows))
        ids = [row["chat_id"] for row in rows]
        names = [row["name"] for row in rows]
        require(len(set(ids)) == len(ids) and len(set(names)) == len(names))
        pairs = data.get("comment_pairs", [])
        additional_ids = {row["chat_id"] for row in groups[1]}
        require(isinstance(pairs, list))
        require(all(isinstance(pair, dict) and type(pair.get("channel_id")) is int
                   and type(pair.get("discussion_id")) is int
                   and pair["channel_id"] != pair["discussion_id"]
                   and {pair["channel_id"], pair["discussion_id"]} <= additional_ids
                   for pair in pairs))
        require(len({pair["channel_id"] for pair in pairs}) == len(pairs))
        return {**data, "approved": groups[0], "additional": groups[1], "comment_pairs": pairs}
    except (ValueError, TypeError, KeyError):
        raise ValueError("Invalid sources configuration; see examples/sources.example.json") from None


SOURCE_CONFIG = load_sources(Path(os.environ.get("RADAR_SOURCES_FILE", PRIVATE_ROOT / "sources.json")).expanduser())
APPROVED_SOURCE_NAMES = tuple(row["name"] for row in SOURCE_CONFIG["approved"])
APPROVED_CHAT_IDS = tuple(row["chat_id"] for row in SOURCE_CONFIG["approved"])
WEEKLY_CHAT_IDS = tuple(row["chat_id"] for row in SOURCE_CONFIG["additional"])
COMMENT_PAIRS = {row["channel_id"]: row["discussion_id"] for row in SOURCE_CONFIG["comment_pairs"]}


def require_live_sources() -> None:
    if SOURCE_CONFIG.get("synthetic") or not (APPROVED_CHAT_IDS or WEEKLY_CHAT_IDS):
        raise ValueError("Configure your own approved sources in .radar/sources.json before live collection")


def profile_path(project: Path) -> Path:
    return Path(os.environ.get("RADAR_PROFILE_FILE", project / "PROFESSIONAL_PROFILE.md")).expanduser()
DEFAULT_TIMEZONE = "Europe/Madrid"
DEFAULT_FROM_DATE = "2026-08-01"
DEFAULT_TO_DATE = "2026-09-01"
