"""Small domain models shared by collection, storage, and reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from . import __version__


@dataclass(frozen=True, slots=True)
class Source:
    chat_id: int
    title: str
    chat_type: str


@dataclass(frozen=True, slots=True)
class Message:
    chat_id: int
    message_id: int
    sent_at: datetime
    edit_date: datetime | None
    sender_id: int | None
    content_type: str
    text: str | None
    reply_to_chat_id: int | None
    reply_to_message_id: int | None
    message_thread_id: int | None
    media_album_id: int | None
    embedded_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceCollection:
    chat_id: int
    pages: int
    messages_seen: int
    messages_in_interval: int
    text_messages_in_interval: int
    ended_before_start: bool
    history_exhausted: bool
    first_message_at: datetime | None
    last_message_at: datetime | None


@dataclass(frozen=True, slots=True)
class Evaluation:
    score: int
    decision: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    unknowns: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class CandidateItem:
    chat_id: int
    message_id: int
    item_index: int
    item_type: str
    text: str
    evaluation: Evaluation


@dataclass(frozen=True, slots=True)
class ProjectInboxItem:
    candidate_id: str
    run_id: str
    chat_id: int
    message_id: int
    item_index: int
    item_type: str
    title: str
    summary: str
    score: int
    known_conditions: tuple[str, ...]
    fit_reasons: tuple[str, ...]
    risks: tuple[str, ...]
    unknowns: tuple[str, ...]
    source_name: str
    source_url: str | None
    published_at: str
    rules_version: str
    review_status: str = "new"
    selection_decision: str = "include"
    selector_version: str = __version__


@dataclass(frozen=True, slots=True)
class FeedbackDecision:
    candidate_id: str
    decision: str
    decided_at: str
    reason_code: str | None = None
    scope: str | None = None
    note: str | None = None
    source: str = "twenty"
