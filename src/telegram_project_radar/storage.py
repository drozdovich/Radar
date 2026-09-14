"""Private SQLite storage for the reversible stage-one spike."""

from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .models import (
    Evaluation,
    FeedbackDecision,
    Message,
    ProjectInboxItem,
    Source,
    SourceCollection,
)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sources (
    chat_id INTEGER PRIMARY KEY,
    approved_name TEXT NOT NULL,
    resolved_title TEXT NOT NULL,
    chat_type TEXT NOT NULL,
    resolved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    interval_start TEXT NOT NULL,
    interval_end TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS run_sources (
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    chat_id INTEGER NOT NULL REFERENCES sources(chat_id),
    pages INTEGER NOT NULL,
    messages_seen INTEGER NOT NULL,
    messages_in_interval INTEGER NOT NULL,
    text_messages_in_interval INTEGER NOT NULL,
    ended_before_start INTEGER NOT NULL,
    history_exhausted INTEGER NOT NULL,
    first_message_at TEXT,
    last_message_at TEXT,
    status TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY (run_id, chat_id)
);
CREATE TABLE IF NOT EXISTS messages (
    chat_id INTEGER NOT NULL REFERENCES sources(chat_id),
    message_id INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    edit_date TEXT,
    sender_id INTEGER,
    content_type TEXT NOT NULL,
    text TEXT,
    reply_to_chat_id INTEGER,
    reply_to_message_id INTEGER,
    message_thread_id INTEGER,
    media_album_id INTEGER,
    url_entities_scanned_at TEXT,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS message_urls (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    url TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id, url),
    FOREIGN KEY (chat_id, message_id) REFERENCES messages(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_message_urls_url ON message_urls(url);
CREATE TABLE IF NOT EXISTS message_details (
    url TEXT PRIMARY KEY,
    final_url TEXT,
    host TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    content_text TEXT,
    error TEXT,
    parser_version TEXT,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_messages (
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (run_id, chat_id, message_id),
    FOREIGN KEY (chat_id, message_id) REFERENCES messages(chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS candidates (
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    score INTEGER NOT NULL,
    decision TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    unknowns_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    message_link TEXT,
    evaluated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, chat_id, message_id),
    FOREIGN KEY (chat_id, message_id) REFERENCES messages(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_sent_at ON messages(sent_at);
CREATE INDEX IF NOT EXISTS idx_candidates_decision_score ON candidates(run_id, decision, score DESC);
CREATE TABLE IF NOT EXISTS candidate_items (
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    item_index INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_text TEXT NOT NULL,
    score INTEGER NOT NULL,
    decision TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    unknowns_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    message_link TEXT,
    evaluated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, chat_id, message_id, item_index),
    FOREIGN KEY (chat_id, message_id) REFERENCES messages(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_candidate_items_type_score
    ON candidate_items(run_id, decision, item_type, score DESC);
CREATE TABLE IF NOT EXISTS project_inbox_items (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    item_index INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    score INTEGER NOT NULL,
    known_conditions_json TEXT NOT NULL,
    fit_reasons_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    unknowns_json TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT,
    published_at TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'new'
        CHECK(review_status IN ('new', 'approve', 'reject', 'need_info')),
    crm_record_id TEXT,
    crm_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, chat_id, message_id, item_index)
);
CREATE INDEX IF NOT EXISTS idx_project_inbox_status_published
    ON project_inbox_items(review_status, published_at DESC);
CREATE TABLE IF NOT EXISTS feedback_decisions (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES project_inbox_items(candidate_id),
    decision TEXT NOT NULL CHECK(decision IN ('approve', 'reject', 'need_info')),
    reason_code TEXT,
    scope TEXT CHECK(scope IS NULL OR scope IN ('this_item', 'similar', 'always')),
    note TEXT,
    decided_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_candidate_decided
    ON feedback_decisions(candidate_id, decided_at DESC);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage(AbstractContextManager["Storage"]):
    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.executescript(SCHEMA)
        self._migrate_schema()
        self.connection.commit()
        path.chmod(0o600)

    def _migrate_schema(self) -> None:
        candidate_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(candidate_items)")
        }
        if "selector_version" not in candidate_columns:
            # Historical runs have unknown provenance; never backfill today's version.
            self.connection.execute("ALTER TABLE candidate_items ADD COLUMN selector_version TEXT")
        columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(messages)")
        }
        if "url_entities_scanned_at" not in columns:
            self.connection.execute(
                "ALTER TABLE messages ADD COLUMN url_entities_scanned_at TEXT"
            )
        detail_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(message_details)")
        }
        if "parser_version" not in detail_columns:
            self.connection.execute(
                "ALTER TABLE message_details ADD COLUMN parser_version TEXT"
            )

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()

    def begin_run(self, run_id: str, purpose: str, start: datetime, end: datetime) -> None:
        self.connection.execute(
            """INSERT INTO collection_runs
               (run_id, purpose, interval_start, interval_end, started_at, status)
               VALUES (?, ?, ?, ?, ?, 'running')""",
            (run_id, purpose, start.isoformat(), end.isoformat(), utc_now()),
        )
        self.connection.commit()

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        self.connection.execute(
            "UPDATE collection_runs SET completed_at = ?, status = ?, error = ? WHERE run_id = ?",
            (utc_now(), status, error, run_id),
        )
        self.connection.commit()

    def upsert_source(self, approved_name: str, source: Source) -> None:
        self.connection.execute(
            """INSERT INTO sources
               (chat_id, approved_name, resolved_title, chat_type, resolved_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 approved_name = excluded.approved_name,
                 resolved_title = excluded.resolved_title,
                 chat_type = excluded.chat_type,
                 resolved_at = excluded.resolved_at""",
            (source.chat_id, approved_name, source.title, source.chat_type, utc_now()),
        )
        self.connection.commit()

    def store_page(self, run_id: str, messages: Iterable[Message]) -> tuple[int, int]:
        stored = 0
        text_count = 0
        ingested_at = utc_now()
        with self.connection:
            for message in messages:
                self.connection.execute(
                    """INSERT INTO messages
                       (chat_id, message_id, sent_at, edit_date, sender_id, content_type,
                        text, reply_to_chat_id, reply_to_message_id, message_thread_id,
                        media_album_id, url_entities_scanned_at, ingested_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(chat_id, message_id) DO UPDATE SET
                         sent_at = excluded.sent_at,
                         edit_date = excluded.edit_date,
                         sender_id = excluded.sender_id,
                         content_type = excluded.content_type,
                         text = excluded.text,
                         reply_to_chat_id = excluded.reply_to_chat_id,
                         reply_to_message_id = excluded.reply_to_message_id,
                         message_thread_id = excluded.message_thread_id,
                         media_album_id = excluded.media_album_id,
                         url_entities_scanned_at = excluded.url_entities_scanned_at,
                         ingested_at = excluded.ingested_at""",
                    (
                        message.chat_id,
                        message.message_id,
                        message.sent_at.isoformat(),
                        message.edit_date.isoformat() if message.edit_date else None,
                        message.sender_id,
                        message.content_type,
                        message.text,
                        message.reply_to_chat_id,
                        message.reply_to_message_id,
                        message.message_thread_id,
                        message.media_album_id,
                        ingested_at,
                        ingested_at,
                    ),
                )
                self.connection.execute(
                    "DELETE FROM message_urls WHERE chat_id = ? AND message_id = ?",
                    (message.chat_id, message.message_id),
                )
                self.connection.executemany(
                    """INSERT INTO message_urls
                       (chat_id, message_id, position, url, discovered_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    [
                        (
                            message.chat_id,
                            message.message_id,
                            position,
                            url,
                            ingested_at,
                        )
                        for position, url in enumerate(message.embedded_urls)
                    ],
                )
                self.connection.execute(
                    "INSERT OR IGNORE INTO run_messages (run_id, chat_id, message_id) VALUES (?, ?, ?)",
                    (run_id, message.chat_id, message.message_id),
                )
                stored += 1
                text_count += int(message.text is not None)
        return stored, text_count

    def finish_source(
        self,
        run_id: str,
        result: SourceCollection,
        status: str = "complete",
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO run_sources
               (run_id, chat_id, pages, messages_seen, messages_in_interval,
                text_messages_in_interval, ended_before_start, history_exhausted,
                first_message_at, last_message_at, status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                result.chat_id,
                result.pages,
                result.messages_seen,
                result.messages_in_interval,
                result.text_messages_in_interval,
                int(result.ended_before_start),
                int(result.history_exhausted),
                result.first_message_at.isoformat() if result.first_message_at else None,
                result.last_message_at.isoformat() if result.last_message_at else None,
                status,
                error,
            ),
        )
        self.connection.commit()

    def message_keys(self, run_id: str, chat_id: int) -> set[tuple[int, int]]:
        rows = self.connection.execute(
            "SELECT chat_id, message_id FROM run_messages WHERE run_id = ? AND chat_id = ?",
            (run_id, chat_id),
        )
        return {(int(row["chat_id"]), int(row["message_id"])) for row in rows}

    def messages_for_run(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT m.*, s.approved_name, s.resolved_title,
                          (SELECT GROUP_CONCAT(md.content_text, char(10) || char(10))
                             FROM message_urls mu
                             JOIN message_details md USING (url)
                            WHERE mu.chat_id = m.chat_id
                              AND mu.message_id = m.message_id
                              AND md.status = 'complete') AS detail_text,
                          (SELECT GROUP_CONCAT(mu.url, char(10))
                             FROM message_urls mu
                            WHERE mu.chat_id = m.chat_id
                              AND mu.message_id = m.message_id) AS detail_urls
                   FROM run_messages rm
                   JOIN messages m USING (chat_id, message_id)
                   JOIN sources s USING (chat_id)
                   WHERE rm.run_id = ? AND m.text IS NOT NULL
                   ORDER BY m.sent_at, m.chat_id, m.message_id""",
                (run_id,),
            )
        )

    def message_urls_scanned(self, chat_id: int, message_id: int) -> bool:
        row = self.connection.execute(
            """SELECT url_entities_scanned_at FROM messages
               WHERE chat_id = ? AND message_id = ?""",
            (chat_id, message_id),
        ).fetchone()
        return bool(row and row["url_entities_scanned_at"])

    def replace_message_urls(
        self, chat_id: int, message_id: int, urls: Sequence[str]
    ) -> None:
        discovered_at = utc_now()
        with self.connection:
            self.connection.execute(
                "DELETE FROM message_urls WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            )
            self.connection.executemany(
                """INSERT INTO message_urls
                   (chat_id, message_id, position, url, discovered_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (chat_id, message_id, position, url, discovered_at)
                    for position, url in enumerate(dict.fromkeys(urls))
                ],
            )
            self.connection.execute(
                """UPDATE messages SET url_entities_scanned_at = ?
                   WHERE chat_id = ? AND message_id = ?""",
                (discovered_at, chat_id, message_id),
            )

    def message_urls(self, chat_id: int, message_id: int) -> tuple[str, ...]:
        rows = self.connection.execute(
            """SELECT url FROM message_urls
               WHERE chat_id = ? AND message_id = ? ORDER BY position""",
            (chat_id, message_id),
        )
        return tuple(str(row["url"]) for row in rows)

    def message_detail(self, url: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM message_details WHERE url = ?", (url,)
        ).fetchone()

    def store_message_detail(
        self,
        *,
        url: str,
        final_url: str | None,
        host: str,
        status: str,
        http_status: int | None,
        content_type: str | None,
        content_text: str | None,
        error: str | None,
        parser_version: str | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO message_details
               (url, final_url, host, status, http_status, content_type,
                content_text, error, parser_version, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                 final_url = excluded.final_url,
                 host = excluded.host,
                 status = excluded.status,
                 http_status = excluded.http_status,
                 content_type = excluded.content_type,
                 content_text = excluded.content_text,
                 error = excluded.error,
                 parser_version = excluded.parser_version,
                 fetched_at = excluded.fetched_at""",
            (
                url,
                final_url,
                host,
                status,
                http_status,
                content_type,
                content_text,
                error,
                parser_version,
                utc_now(),
            ),
        )
        self.connection.commit()

    def replace_evaluations(
        self,
        run_id: str,
        rows: Sequence[tuple[int, int, Evaluation]],
    ) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM candidates WHERE run_id = ?", (run_id,))
            self.connection.executemany(
                """INSERT INTO candidates
                   (run_id, chat_id, message_id, score, decision, reasons_json,
                    risks_json, unknowns_json, fingerprint, evaluated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        run_id,
                        chat_id,
                        message_id,
                        evaluation.score,
                        evaluation.decision,
                        json.dumps(evaluation.reasons, ensure_ascii=False),
                        json.dumps(evaluation.risks, ensure_ascii=False),
                        json.dumps(evaluation.unknowns, ensure_ascii=False),
                        evaluation.fingerprint,
                        utc_now(),
                    )
                    for chat_id, message_id, evaluation in rows
                ],
            )

    def set_message_link(self, run_id: str, chat_id: int, message_id: int, link: str) -> None:
        self.connection.execute(
            """UPDATE candidates SET message_link = ?
               WHERE run_id = ? AND chat_id = ? AND message_id = ?""",
            (link, run_id, chat_id, message_id),
        )

    def commit(self) -> None:
        self.connection.commit()

    def candidate_keys(self, run_id: str) -> list[tuple[int, int]]:
        rows = self.connection.execute(
            """SELECT chat_id, message_id FROM candidates
               WHERE run_id = ? AND decision IN ('include', 'review')
               ORDER BY score DESC""",
            (run_id,),
        )
        return [(int(row[0]), int(row[1])) for row in rows]

    def run_summary(self, run_id: str) -> dict[str, object]:
        run = self.connection.execute(
            "SELECT * FROM collection_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        sources = list(
            self.connection.execute(
                """SELECT rs.*, s.approved_name, s.resolved_title
                   FROM run_sources rs JOIN sources s USING (chat_id)
                   WHERE rs.run_id = ? ORDER BY s.approved_name""",
                (run_id,),
            )
        )
        decisions = {
            row["decision"]: int(row["count"])
            for row in self.connection.execute(
                "SELECT decision, COUNT(*) AS count FROM candidates WHERE run_id = ? GROUP BY decision",
                (run_id,),
            )
        }
        quality_row = self.connection.execute(
            """SELECT
                 COUNT(*) AS text_messages_evaluated,
                 SUM(decision = 'include' AND reasons_json LIKE '%инфраструктурная%') AS infrastructure_candidates,
                 SUM(decision = 'include' AND reasons_json LIKE '%technical GTM%') AS gtm_candidates,
                 SUM(decision = 'include' AND reasons_json LIKE '%инфраструктурная%' AND reasons_json LIKE '%technical GTM%') AS both_profile_types,
                 SUM(decision = 'include' AND reasons_json LIKE '%гибкий формат%') AS explicit_flexible_format,
                 SUM(decision = 'include' AND unknowns_json LIKE '%part-time%') AS format_unknown,
                 SUM(decision = 'include' AND unknowns_json LIKE '%бюджет%') AS budget_unknown,
                 SUM(decision = 'exclude' AND reasons_json LIKE '%full-time%') AS explicit_full_time_excluded,
                 SUM(decision = 'include' AND message_link IS NOT NULL) AS candidates_with_links
               FROM candidates WHERE run_id = ?""",
            (run_id,),
        ).fetchone()
        return {
            "run": dict(run) if run else None,
            "sources": [dict(row) for row in sources],
            "decisions": decisions,
            "quality": {
                key: int(quality_row[key] or 0) for key in quality_row.keys()
            } if quality_row else {},
        }

    def report_candidates(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT c.*, m.sent_at, m.text, m.content_type,
                          s.approved_name, s.resolved_title
                   FROM candidates c
                   JOIN messages m USING (chat_id, message_id)
                   JOIN sources s USING (chat_id)
                   WHERE c.run_id = ? AND c.decision IN ('include', 'review')
                   ORDER BY CASE c.decision WHEN 'include' THEN 0 ELSE 1 END,
                            c.score DESC, m.sent_at DESC""",
                (run_id,),
            )
        )

    def replace_candidate_items(self, run_id: str, items: Sequence[object]) -> None:
        from . import __version__
        with self.connection:
            self.connection.execute("DELETE FROM candidate_items WHERE run_id = ?", (run_id,))
            self.connection.executemany(
                """INSERT INTO candidate_items
                   (run_id, chat_id, message_id, item_index, item_type, item_text,
                    score, decision, reasons_json, risks_json, unknowns_json,
                    fingerprint, evaluated_at, selector_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        run_id,
                        item.chat_id,
                        item.message_id,
                        item.item_index,
                        item.item_type,
                        item.text,
                        item.evaluation.score,
                        item.evaluation.decision,
                        json.dumps(item.evaluation.reasons, ensure_ascii=False),
                        json.dumps(item.evaluation.risks, ensure_ascii=False),
                        json.dumps(item.evaluation.unknowns, ensure_ascii=False),
                        item.evaluation.fingerprint,
                        utc_now(),
                        __version__,
                    )
                    for item in items
                ],
            )

    def candidate_item_message_keys(self, run_id: str) -> list[tuple[int, int]]:
        rows = self.connection.execute(
            """SELECT DISTINCT chat_id, message_id FROM candidate_items
               WHERE run_id = ? AND decision IN ('include', 'review')""",
            (run_id,),
        )
        return [(int(row[0]), int(row[1])) for row in rows]

    def set_candidate_item_link(
        self, run_id: str, chat_id: int, message_id: int, link: str
    ) -> None:
        self.connection.execute(
            """UPDATE candidate_items SET message_link = ?
               WHERE run_id = ? AND chat_id = ? AND message_id = ?""",
            (link, run_id, chat_id, message_id),
        )

    def report_candidate_items(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT ci.*, m.sent_at,
                          s.approved_name, s.resolved_title,
                          (SELECT mu.url
                             FROM message_urls mu
                             JOIN message_details md USING (url)
                            WHERE mu.chat_id = ci.chat_id
                              AND mu.message_id = ci.message_id
                              AND md.status = 'complete'
                            ORDER BY mu.position LIMIT 1) AS detail_url,
                          (SELECT md.status
                             FROM message_urls mu
                             JOIN message_details md USING (url)
                            WHERE mu.chat_id = ci.chat_id
                              AND mu.message_id = ci.message_id
                            ORDER BY mu.position LIMIT 1) AS detail_status
                   FROM candidate_items ci
                   JOIN messages m USING (chat_id, message_id)
                   JOIN sources s USING (chat_id)
                   WHERE ci.run_id = ? AND ci.decision IN ('include', 'review')
                   ORDER BY CASE ci.item_type
                              WHEN 'opportunity' THEN 0
                              WHEN 'digest_item' THEN 1
                              WHEN 'person' THEN 2
                              WHEN 'company' THEN 3
                              ELSE 3 END,
                            CASE ci.decision WHEN 'include' THEN 0 ELSE 1 END,
                            ci.score DESC, m.sent_at DESC, ci.item_index""",
                (run_id,),
            )
        )

    def candidate_item_summary(self, run_id: str) -> dict[str, object]:
        by_type = {
            row["item_type"]: int(row["count"])
            for row in self.connection.execute(
                """SELECT item_type, COUNT(*) AS count FROM candidate_items
                   WHERE run_id = ? AND decision IN ('include', 'review')
                   GROUP BY item_type""",
                (run_id,),
            )
        }
        decisions = {
            row["decision"]: int(row["count"])
            for row in self.connection.execute(
                """SELECT decision, COUNT(*) AS count FROM candidate_items
                   WHERE run_id = ? GROUP BY decision""",
                (run_id,),
            )
        }
        links = self.connection.execute(
            """SELECT COUNT(*) FROM candidate_items
               WHERE run_id = ? AND decision IN ('include', 'review')
                 AND message_link IS NOT NULL""",
            (run_id,),
        ).fetchone()[0]
        return {"by_type": by_type, "decisions": decisions, "items_with_links": int(links)}

    def upsert_project_inbox_items(
        self, items: Sequence[ProjectInboxItem]
    ) -> None:
        now = utc_now()
        with self.connection:
            self.connection.executemany(
                """INSERT INTO project_inbox_items
                   (candidate_id, run_id, chat_id, message_id, item_index, item_type,
                    title, summary, score, known_conditions_json, fit_reasons_json,
                    risks_json, unknowns_json, source_name, source_url, published_at,
                    rules_version, review_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(candidate_id) DO UPDATE SET
                     title = excluded.title,
                     summary = excluded.summary,
                     score = excluded.score,
                     known_conditions_json = excluded.known_conditions_json,
                     fit_reasons_json = excluded.fit_reasons_json,
                     risks_json = excluded.risks_json,
                     unknowns_json = excluded.unknowns_json,
                     source_name = excluded.source_name,
                     source_url = excluded.source_url,
                     published_at = excluded.published_at,
                     rules_version = excluded.rules_version,
                     updated_at = excluded.updated_at""",
                [
                    (
                        item.candidate_id,
                        item.run_id,
                        item.chat_id,
                        item.message_id,
                        item.item_index,
                        item.item_type,
                        item.title,
                        item.summary,
                        item.score,
                        json.dumps(item.known_conditions, ensure_ascii=False),
                        json.dumps(item.fit_reasons, ensure_ascii=False),
                        json.dumps(item.risks, ensure_ascii=False),
                        json.dumps(item.unknowns, ensure_ascii=False),
                        item.source_name,
                        item.source_url,
                        item.published_at,
                        item.rules_version,
                        item.review_status,
                        now,
                        now,
                    )
                    for item in items
                ],
            )

    def project_inbox_items(
        self, review_status: str | None = None
    ) -> list[sqlite3.Row]:
        if review_status is None:
            return list(
                self.connection.execute(
                    """SELECT * FROM project_inbox_items
                       ORDER BY published_at DESC, score DESC, candidate_id"""
                )
            )
        return list(
            self.connection.execute(
                """SELECT * FROM project_inbox_items
                   WHERE review_status = ?
                   ORDER BY published_at DESC, score DESC, candidate_id""",
                (review_status,),
            )
        )

    def mark_project_inbox_synced(
        self, candidate_id: str, crm_record_id: str
    ) -> None:
        now = utc_now()
        self.connection.execute(
            """UPDATE project_inbox_items
               SET crm_record_id = ?, crm_synced_at = ?, updated_at = ?
               WHERE candidate_id = ?""",
            (crm_record_id, now, now, candidate_id),
        )
        self.connection.commit()

    def record_feedback_decision(self, decision: FeedbackDecision) -> None:
        if decision.decision not in {"approve", "reject", "need_info"}:
            raise ValueError(f"unsupported feedback decision: {decision.decision}")
        if decision.scope not in {None, "this_item", "similar", "always"}:
            raise ValueError(f"unsupported feedback scope: {decision.scope}")
        if decision.decision == "reject" and not decision.reason_code:
            raise ValueError("reject feedback requires reason_code")
        if decision.decision == "reject" and not decision.scope:
            raise ValueError("reject feedback requires scope")
        if decision.decision != "reject" and decision.scope is not None:
            raise ValueError("feedback scope is only valid for reject")

        with self.connection:
            cursor = self.connection.execute(
                """UPDATE project_inbox_items
                   SET review_status = ?, updated_at = ?
                   WHERE candidate_id = ?""",
                (decision.decision, utc_now(), decision.candidate_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"unknown candidate_id: {decision.candidate_id}")
            self.connection.execute(
                """INSERT INTO feedback_decisions
                   (candidate_id, decision, reason_code, scope, note, decided_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.candidate_id,
                    decision.decision,
                    decision.reason_code,
                    decision.scope,
                    decision.note,
                    decision.decided_at,
                    decision.source,
                ),
            )

    def feedback_history(self, candidate_id: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT * FROM feedback_decisions
                   WHERE candidate_id = ?
                   ORDER BY decided_at, feedback_id""",
                (candidate_id,),
            )
        )
