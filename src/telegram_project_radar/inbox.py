"""Build clean, stable Project Inbox records from selected candidates."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .models import ProjectInboxItem
from . import __version__
from .feedback_features import digest, make_snapshot
from .normalization import compact_text
from .selection import BUDGET_RE, FLEXIBLE_FORMAT, REMOTE, fold


INBOX_SCHEMA_VERSION = "project-inbox-v4"
RULES_VERSION = f"deterministic-v2-no-llm@{__version__}"
REVIEW_STATUSES = ("new", "approve", "reject", "need_info")
FEEDBACK_SCOPES = ("this_item", "similar", "always")
REJECTION_REASONS = (
    "event_topic", "event_date", "event_cost", "event_language",
    "not_relevant_role",
    "wrong_industry_or_context",
    "full_time_or_permanent",
    "geography_or_onsite",
    "level_mismatch",
    "tool_or_stack_mismatch",
    "advertisement_or_no_concrete_ask",
    "duplicate",
    "other",
)
BUDGET_HINT_RE = re.compile(
    r"(?:[$€£]\s*\d|\d[\d.,]*\s*k?\s*(?:eur|usd|gbp|€|\$|£)\b)",
    re.IGNORECASE,
)


def stable_candidate_id(
    *, chat_id: int, message_id: int, item_index: int, fingerprint: str
) -> str:
    material = f"{chat_id}:{message_id}:{item_index}:{fingerprint}".encode("utf-8")
    return f"radar-{hashlib.sha256(material).hexdigest()[:20]}"


def twenty_rich_text_summary(text: str, *, candidate_id: str, detail_url: str | None = None) -> dict[str, str]:
    """Build a Twenty rich-text value whose card preview keeps all source text visible."""
    block_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"telegram-project-radar:{candidate_id}:summary",
        )
    )
    block = {
        "id": block_id,
        "type": "paragraph",
        "props": {
            "textColor": "default",
            "backgroundColor": "default",
            "textAlignment": "left",
        },
        # Twenty clips a multi-block rich-text preview in the record card. Keeping
        # the exact source in one block makes the complete text readable in place.
        "content": [{"type": "text", "text": text, "styles": {}}],
        "children": [],
    }
    markdown = text
    label = "👉 Контакты и полное описание"
    if detail_url and label in text:
        from urllib.parse import urlsplit, quote
        parsed = urlsplit(detail_url)
        if parsed.scheme == "https" and parsed.hostname == "app.rvc.global" and parsed.path.startswith("/vacancy/view/"):
            before, after = text.split(label, 1)
            block["content"] = [
                {"type": "text", "text": before, "styles": {}},
                {"type": "link", "href": detail_url, "content": [{"type": "text", "text": label, "styles": {}}]},
                {"type": "text", "text": after, "styles": {}},
            ]
            markdown = before + "[" + label + "](" + quote(detail_url, safe=":/?&=%#-._~") + ")" + after
    return {
        "blocknote": json.dumps(
            [block], ensure_ascii=False, separators=(",", ":")
        ),
        "markdown": markdown,
    }


def _json_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    decoded = json.loads(str(value))
    return tuple(str(item) for item in decoded)


def _title(text: str) -> str:
    from .card_names import GREETING
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]
    first_line = next((line for line in lines if not GREETING.match(line)), '')
    if not first_line:
        return 'Автор не указан'
    title = re.sub(r"^[^\w]+", "", first_line, flags=re.UNICODE).strip()
    title = re.split(
        r"\s+(?:компания\s+ищет|ищут|ищем|требуется)\b",
        title,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    title = compact_text(title or text)
    return title[:120].rstrip()


def _known_conditions(text: str, reasons: tuple[str, ...]) -> tuple[str, ...]:
    value = fold(text)
    conditions: list[str] = []
    if any(term in value for term in REMOTE):
        conditions.append("Remote указан")
    if any(term in value for term in FLEXIBLE_FORMAT) or any(
        "гибк" in fold(reason) or "проектн" in fold(reason) for reason in reasons
    ):
        conditions.append("Гибкий, проектный или контрактный формат указан")
    if BUDGET_RE.search(text) or BUDGET_HINT_RE.search(text):
        conditions.append("Бюджет или ставка указаны в исходном сообщении")
    return tuple(conditions)


def build_project_inbox_items(
    rows: Iterable[Mapping[str, object]],
    *,
    run_id: str,
    rules_version: str = RULES_VERSION,
) -> list[ProjectInboxItem]:
    from .card_names import author_name, needs_author_name
    items: list[ProjectInboxItem] = []
    for row in rows:
        text = str(row["item_text"])
        chat_id = int(row["chat_id"])
        message_id = int(row["message_id"])
        item_index = int(row["item_index"])
        fingerprint = str(row["fingerprint"])
        selector_version = (str(row["selector_version"] or "unknown")
                            if "selector_version" in row.keys() else "unknown")
        fit_reasons = _json_tuple(row["reasons_json"])
        items.append(
            ProjectInboxItem(
                candidate_id=stable_candidate_id(
                    chat_id=chat_id,
                    message_id=message_id,
                    item_index=item_index,
                    fingerprint=fingerprint,
                ),
                run_id=run_id,
                chat_id=chat_id,
                message_id=message_id,
                item_index=item_index,
                item_type=str(row["item_type"]),
                title=author_name(None, text) if needs_author_name(str(row['item_type']), text) else _title(text),
                summary=text,
                score=int(row["score"]),
                known_conditions=_known_conditions(text, fit_reasons),
                fit_reasons=fit_reasons,
                risks=_json_tuple(row["risks_json"]),
                unknowns=_json_tuple(row["unknowns_json"]),
                source_name=str(row["approved_name"]),
                source_url=str(row["message_link"]) if row["message_link"] else None,
                published_at=str(row["sent_at"]),
                rules_version=(f"deterministic-v2-no-llm@{selector_version}"
                               if rules_version == RULES_VERSION else rules_version),
                selection_decision=str(row["decision"]) if "decision" in row.keys() else "include",
                selector_version=selector_version,
            )
        )
    return items


def project_inbox_payload(items: Iterable[ProjectInboxItem]) -> dict[str, object]:
    rows = list(items)
    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": INBOX_SCHEMA_VERSION,
        "generated_at": generated_at,
        "app_version": __version__,
        # Local provenance only. CRM writers continue sending the explicit items fields.
        "learning_snapshots": [make_snapshot({
            "candidate_id": item.candidate_id, "run_id": item.run_id,
            "rules_version": item.rules_version, "title": item.title, "summary": item.summary,
            "published_at": item.published_at, "type": item.item_type, "score": item.score,
            "selection_decision": item.selection_decision,
            "locator": {"chat_id": item.chat_id, "message_id": item.message_id, "item_index": item.item_index},
        }, generated_at, selector_version=item.selector_version) for item in rows],
        "items": [
            {
                "candidate_id": item.candidate_id,
                "run_id": item.run_id,
                "type": item.item_type,
                "title": item.title,
                "summary": item.summary,
                "summary_twenty": twenty_rich_text_summary(
                    item.summary, candidate_id=item.candidate_id
                ),
                "score": item.score,
                "known_conditions": list(item.known_conditions),
                "why_fit": list(item.fit_reasons),
                "risks": list(item.risks),
                "unknowns": list(item.unknowns),
                "source": item.source_name,
                "source_url": item.source_url,
                "published_at": item.published_at,
                "rules_version": item.rules_version,
                "review_status": item.review_status,
            }
            for item in rows
        ],
    }


def write_project_inbox_export(
    items: Iterable[ProjectInboxItem], output_path: Path
) -> Path:
    rows = list(items)
    if not rows and output_path.exists():
        try:
            previous_payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous_payload = {}
        if previous_payload.get("items"):
            raise ValueError(
                "refusing to replace a non-empty Project Inbox export with an empty one"
            )
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_path.parent.chmod(0o700)
    payload = project_inbox_payload(rows)
    # Preserve original feature versions before a later export replaces this file.
    from .feedback_store import write_private
    manifest = {"snapshots": payload["learning_snapshots"]}
    write_private(output_path.parent / "learning-snapshots" / f"{digest(manifest)}.json", manifest)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_path.chmod(0o600)
    return output_path
