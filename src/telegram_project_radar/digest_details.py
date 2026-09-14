"""Operator-verified local descriptions tied to one unchanged digest block.

No network access. URLs document the direct source; they are not fetched here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DigestDetail:
    source_text_sha256: str
    url: str
    text: str


DigestDetails = dict[tuple[int, int, int, str], DigestDetail]


def load_digest_details(path: Path | None = None) -> DigestDetails:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("details"), list):
        raise ValueError("Unsupported digest-detail file")
    result: DigestDetails = {}
    for row in payload["details"]:
        for field in ("chat_id", "message_id", "item_index"):
            if type(row.get(field)) is not int:
                raise ValueError("Digest detail requires integer message identifiers")
        if row["item_index"] < 1:
            raise ValueError("Digest detail requires a positive block index")
        for field in ("source_text_sha256", "block_text_sha256"):
            value = row.get(field)
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Digest detail requires exact source and block hashes")
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ValueError("Digest description must be nonempty text")
        url = urlsplit(row.get("url", ""))
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Digest detail requires its direct HTTPS source URL")
        key = (row["chat_id"], row["message_id"], row["item_index"], row["block_text_sha256"])
        if key in result:
            raise ValueError("Ambiguous duplicate description for a digest block")
        result[key] = DigestDetail(row["source_text_sha256"], row["url"], row["text"])
    return result


def validate_digest_sources(rows: list, details: DigestDetails) -> None:
    """Fail on edited/missing sources instead of silently dropping a known restriction."""
    sources = {(int(row["chat_id"]), int(row["message_id"])): str(row["text"]) for row in rows}
    for (chat_id, message_id, _, _), detail in details.items():
        source = sources.get((chat_id, message_id))
        if source is None or text_hash(source) != detail.source_text_sha256:
            raise ValueError("Digest detail source is missing or changed; recheck its direct link")
