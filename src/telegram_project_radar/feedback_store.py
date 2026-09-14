"""Private append-only feedback ledger, independent of production selection."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .feedback_features import digest, instant, validate_snapshot
from .inbox import FEEDBACK_SCOPES, REJECTION_REASONS

REASON_ALIASES = {
    "wrong_industry_context": "wrong_industry_or_context",
    "full_time_permanent": "full_time_or_permanent",
    "geography_onsite": "geography_or_onsite",
    "tool_stack_mismatch": "tool_or_stack_mismatch",
    "advertisement_no_concrete_ask": "advertisement_or_no_concrete_ask",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_private(path: Path, value: dict) -> Path:
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_text() != data:
            raise ValueError("refusing to replace an existing feedback artifact") from None
        return path
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(data)
    return path


def enum(value: object) -> str | None:
    return str(value).strip().lower().replace(" ", "_") if value else None


def normalize_event(row: dict) -> dict:
    candidate = row.get("candidate_id")
    if not isinstance(candidate, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,200}", candidate):
        raise ValueError("feedback event requires a candidate_id")
    decision = enum(row.get("decision"))
    if decision not in {"approve", "reject", "need_info", "new"}:
        raise ValueError("unsupported feedback decision")
    timestamp = instant(row.get("created_at") or row.get("decided_at")).astimezone(timezone.utc).isoformat()
    reason = enum(row.get("reason") or row.get("reason_code"))
    result = {
        "candidate_id": candidate, "decision": decision, "decided_at": timestamp,
        "reason": REASON_ALIASES.get(reason, reason), "scope": enum(row.get("scope")),
        "note": row.get("note"), "rules_version": row.get("rules_version"),
        "record_id": row.get("record_id"), "snapshot_id": row.get("snapshot_id"),
        "actor": row.get("actor_workspace_member_id"),
        "previous_status": enum(row.get("previous_status")), "review_app_version": row.get("app_version"),
    }
    result["event_id"] = "twenty:" + str(row.get("id") or row.get("feedback_id") or digest(result))
    return result


def excluded(event: dict, exclusions: set[str]) -> bool:
    candidate = event["candidate_id"].lower()
    return (not candidate.startswith("radar-")
            or candidate.startswith(("radar-qa-", "radar-test"))
            or event["candidate_id"] in exclusions or event.get("record_id") in exclusions)


def effective_labels(events: list[dict], snapshots: list[dict], exclusions: set[str]) -> tuple[list, list]:
    grouped = defaultdict(list)
    ignored = []
    for event in events:
        if excluded(event, exclusions):
            ignored.append({"event_id": event["event_id"], "reason": "test_or_non_radar"})
        else:
            grouped[event["candidate_id"]].append(event)
    labels = []
    for candidate, history in sorted(grouped.items()):
        history.sort(key=lambda e: (instant(e["decided_at"]), e["event_id"]))
        event = history[-1]
        label = {k: event[k] for k in ("candidate_id", "event_id", "decision", "reason", "scope", "decided_at", "rules_version")}
        label.update(snapshot_id=None, issue=None, supersedes=[e["event_id"] for e in history[:-1]])
        simultaneous = [e for e in history if instant(e["decided_at"]) == instant(event["decided_at"])]
        if len({digest({k: v for k, v in e.items() if k not in {"event_id", "actor"}}) for e in simultaneous}) > 1:
            label["issue"] = "ambiguous_decision_order"
        matches = [s for s in snapshots if s["candidate_id"] == candidate
                   and s["rules_version"] == event["rules_version"]
                   and instant(s["captured_at"]) <= instant(event["decided_at"])
                   and (not event["snapshot_id"] or s["snapshot_id"] == event["snapshot_id"])]
        if len(matches) == 1:
            label["snapshot_id"] = matches[0]["snapshot_id"]
            if matches[0]["selector_version"] == "unknown":
                label["issue"] = label["issue"] or "unverified_selector_version"
        else:
            label["issue"] = label["issue"] or ("ambiguous_snapshot" if matches else "missing_original_snapshot")
        if event["decision"] == "reject":
            if event["reason"] not in REJECTION_REASONS:
                label["issue"] = label["issue"] or "missing_or_unknown_reason"
            if event["scope"] not in FEEDBACK_SCOPES:
                label["issue"] = label["issue"] or "missing_or_unknown_scope"
        labels.append(label)
    return labels, ignored


class FeedbackStore:
    def __init__(self, path: Path):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        path.chmod(0o600)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bundles (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL,
                action TEXT NOT NULL, previous TEXT, next TEXT NOT NULL);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()

    def _all(self, table: str) -> list[dict]:
        assert table in {"snapshots", "events"}
        return [json.loads(r[0]) for r in self.connection.execute(f"SELECT data FROM {table} ORDER BY id")]

    def _state(self, name: str, default: str = "") -> str:
        row = self.connection.execute("SELECT value FROM state WHERE name=?", (name,)).fetchone()
        return row[0] if row else default

    def _set(self, name: str, value: str):
        self.connection.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (name, value))

    def _insert(self, table: str, key: str, value: dict):
        assert table in {"snapshots", "events", "revisions", "bundles"}
        data = json.dumps(value, ensure_ascii=False, sort_keys=True)
        row = self.connection.execute(f"SELECT data FROM {table} WHERE id=?", (key,)).fetchone()
        if row:
            previous = json.loads(row[0])
            if table == "snapshots":
                # Preserve the first stored capture; never rewrite provenance.
                previous.pop("captured_at")
                current = {k: v for k, v in value.items() if k != "captured_at"}
            else:
                current = value
            if previous != current:
                raise ValueError("immutable feedback identity has conflicting content")
        else:
            self.connection.execute(f"INSERT INTO {table} VALUES (?,?)", (key, data))

    def import_events(self, rows: list[dict], snapshots: list[dict], exclusions: set[str] | None = None) -> dict:
        normalized = [normalize_event(r) for r in rows]
        checked = [validate_snapshot(s) for s in snapshots]
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            for snapshot in checked:
                self._insert("snapshots", snapshot["snapshot_id"], snapshot)
            for event in normalized:
                self._insert("events", event["event_id"], event)
            exclusions = set(json.loads(self._state("exclusions", "[]"))) | (exclusions or set())
            all_events, all_snapshots = self._all("events"), self._all("snapshots")
            labels, ignored = effective_labels(all_events, all_snapshots, exclusions)
            content = {"schema_version": "feedback-labels-v1", "app_version": __version__,
                       "events": all_events, "snapshots": all_snapshots, "labels": labels,
                       "excluded": ignored, "exclusions": sorted(exclusions)}
            revision = "labels-" + digest(content)
            old = self._state("labels")
            if old == revision:
                return self.revision(old)
            content.update(revision=revision, parent=old or None)
            self._insert("revisions", revision, content)
            self._set("labels", revision)
            self._set("exclusions", json.dumps(sorted(exclusions)))
            self.connection.execute("INSERT INTO audit(at,action,previous,next) VALUES (?,?,?,?)",
                                    (now(), "import", old or None, revision))
        return content

    def revision(self, revision: str | None = None) -> dict:
        row = self.connection.execute("SELECT data FROM revisions WHERE id=?", (revision or self._state("labels"),)).fetchone()
        if not row:
            raise ValueError("feedback revision not found; import decisions first")
        return json.loads(row[0])

    def save_bundle(self, report: dict) -> str:
        key = "preview-" + digest(report)
        with self.connection:
            self._insert("bundles", key, report)
        return key

    def select_local(self, version: str, *, rollback: bool = False) -> dict:
        """A sandbox pointer only; the collector/selector never reads this state."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if version != "baseline":
                row = self.connection.execute("SELECT data FROM bundles WHERE id=?", (version,)).fetchone()
                if not row:
                    raise ValueError("preview version not found")
                report = json.loads(row[0])
                if rollback:
                    if not self.connection.execute("SELECT 1 FROM audit WHERE action='select_local' AND next=?", (version,)).fetchone():
                        raise ValueError("rollback target was never selected locally")
                elif not report["gate"]["ready_for_local_trial"] or report["label_revision"] != self._state("labels"):
                    raise ValueError("local selection blocked: checks incomplete, failed, or labels changed")
            previous = self._state("active", "baseline")
            if previous != version:
                self._set("active", version)
                self.connection.execute("INSERT INTO audit(at,action,previous,next) VALUES (?,?,?,?)",
                                        (now(), "rollback" if rollback else "select_local", previous, version))
        return {"previous": previous, "active": version, "scope": "local_sandbox_only", "production_changed": False}
