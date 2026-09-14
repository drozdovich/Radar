"""Offline product walkthrough using invented messages and scripted review decisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from . import __version__, agent_review as review
from .pipeline import canonical, database, dump, read


BLOCKS = (
    "📌 Project A — A small repair shop needs incoming calls routed to the right team. "
    "A paid two-week automation pilot; call volume and budget still need confirmation.",
    "Project B — A local studio wants appointment reminders by phone. "
    "Looking for a contractor to prototype an opt-in voice workflow; schedule is not specified.",
)
MESSAGES = ("\n\n".join(BLOCKS), "Anyone around for lunch today?")


class DemoInbox:
    """In-memory boundary standing in for Twenty, never a network client."""

    def __init__(self):
        self.records = []
        self.cards = []

    def read(self):
        return self.records

    def create(self, payload):
        self.cards.append(payload)
        self.records.append({
            "id": payload["id"], "candidateId": payload["candidateId"], "reviewStatus": "NEW",
            "textHash": hashlib.sha256(payload["summary"]["markdown"].encode()).hexdigest(),
        })
        return {"created": True}


def scripted_candidates():
    result = []
    for index, block in enumerate(BLOCKS):
        start = MESSAGES[0].index(block)
        result.append({
            "index": 1, "quote": block, "kind": "DIGEST_POSITION",
            "source_span": {"start": start, "end": start + len(block)},
            "title": ("Incoming-call routing pilot", "Appointment reminder prototype")[index],
            "tracks": ["avans"], "role": "customer", "market": "unknown",
            "fact": block, "hypothesis": "A voice-automation prototype may solve the stated problem.",
            "unknowns": ["Budget", "Call volume", "Confirmation from the author"],
        })
    return result


def run_demo(directory: Path) -> dict:
    directory = directory.expanduser().resolve()
    # Isolated temporary state also makes repeat runs safe: no user's ledger is read.
    with TemporaryDirectory(prefix="radar-demo-") as temp:
        project = Path(temp)
        path = project / "run"
        (project / "PROFESSIONAL_PROFILE.md").write_text(
            "Synthetic demo: automation projects; preserve evidence and uncertainty.\n")
        dump(path / "state.json", {
            "run_id": "synthetic-demo", "collection_complete": True, "analysis_complete": False,
            "delivery_complete": False, "text_messages": 2,
            "sources": [{"chat_id": -1, "title": "Invented demo channel", "verified_pass": 2}],
        })
        db = database(path / "messages.sqlite3")
        for mid, text in enumerate(MESSAGES, 1):
            message = {"chat_id": -1, "message_id": mid, "text": text,
                       "sent_at": "2026-09-01T09:00:00+00:00",
                       "source_url": f"https://example.invalid/messages/{mid}", "embedded_urls": []}
            for number in (1, 2):
                db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?)",
                           (-1, number, mid, message["sent_at"], review.digest(message), canonical(message)))
        db.commit()
        db.close()
        review.prepare(path, project, profile_text="Synthetic demo: preserve evidence and uncertainty.")
        review.show(path, "batch-0001")
        review.submit(path, "batch-0001", {
            "groups": [
                {"indices": "1", "disposition": "candidate", "reason": "Two separate automation projects"},
                {"indices": "2", "disposition": "irrelevant", "reason": "Social chat without a project"},
            ], "candidates": scripted_candidates(),
        }, project)
        review.show(path, "batch-0001", audit=True)
        review.accept_audit(path, "batch-0001", {"1": "Each exact position has its own card", "2": "Social message checked"})
        inbox = DemoInbox()
        review.deliver_batch(path, "batch-0001", project, bridge=inbox)
        first = read(path / "agent-review/batch-0001/delivery.json")["items"]
        # Scripted examples of human decisions, not actual CRM approvals or messages.
        inbox.records[0]["reviewStatus"] = "APPROVE"
        inbox.records[1]["reviewStatus"] = "REJECT"
        review.deliver_batch(path, "batch-0001", project, bridge=inbox)
        retry = read(path / "agent-review/batch-0001/delivery.json")["items"]
        report = {
            "synthetic_only": True, "version": __version__,
            "review_mode": "scripted example; no AI or network call",
            "messages": list(MESSAGES), "cards": inbox.cards, "decisions": inbox.records,
            "created": sum(row["action"] == "created" for row in first),
            "preserved_on_retry": sum(row["action"] == "preserved" for row in retry),
            "messages_accounted": 2, "non_candidates": 1,
        }
    dump(directory / "demo.json", report)
    lines = ["# Radar — offline walkthrough", "", f"Version {__version__}", "",
             "All messages, organisations and decisions below are invented. Review is scripted; "
             "this does not measure AI selection quality or exercise the live Twenty UI.", "",
             "## Input messages", ""]
    for index, message in enumerate(MESSAGES, 1):
        lines += [f"### Message {index}", "", message, ""]
    lines += ["## Reviewed cards", ""]
    for card, decision in zip(report["cards"], report["decisions"], strict=True):
        lines += ["### " + card["name"], "", "**Exact source block**", "",
                  "> " + card["summary"]["markdown"], "",
                  "**Unknown:** budget, call volume, author confirmation.", "",
                  f"**Scripted decision:** {decision['reviewStatus']}", ""]
    lines += ["## Verified behaviour", "", "- Both input messages receive an explicit review result.",
              "- Two non-overlapping source positions produce two cards with stable IDs.",
              "- A retry creates no duplicates and preserves both previous decisions.",
              "- Nothing is collected from Telegram or written to Twenty.", ""]
    (directory / "demo.md").write_text("\n".join(lines))
    return {key: report[key] for key in ("synthetic_only", "version", "messages_accounted", "created", "preserved_on_retry")} | {
        "report": str(directory / "demo.md"), "json": str(directory / "demo.json")}


def command(args) -> int:
    result = run_demo(Path(args.output_dir))
    print("Radar offline demo — synthetic messages and scripted decisions")
    print(f"{result['messages_accounted']} messages reviewed → {result['created']} cards")
    print(f"Retry: {result['preserved_on_retry']} existing decisions preserved, 0 duplicates")
    print(f"Report: {result['report']}")
    print(f"JSON: {result['json']}")
    return 0
