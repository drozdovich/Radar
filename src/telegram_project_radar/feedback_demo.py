"""Repeatable synthetic walkthrough, with no access to private Telegram data."""

from pathlib import Path

from .feedback_cli import legacy_datasets, save_report
from .feedback_features import make_snapshot
from .feedback_preview import build_preview
from .feedback_store import FeedbackStore, write_private


def demo_snapshot(name: str, title: str, *, day: int = 1, item_type: str = "opportunity", message: int = 1) -> dict:
    return make_snapshot({"candidate_id": "radar-demo-" + name, "run_id": f"synthetic-day-{day}",
                          "rules_version": "synthetic-rules-v1", "title": title,
                          "summary": f"Synthetic fixture {name}: {title}", "type": item_type,
                          "score": 80, "published_at": f"2026-09-{day:02}T10:00:00+00:00",
                          "locator": {"chat_id": -1, "message_id": message, "item_index": 0},
                          "synthetic": True}, f"2026-09-{day:02}T12:00:00+00:00")


def demo_event(snapshot: dict, decision: str, *, event_id: str | None = None,
               scope: str | None = None, reason: str | None = None, hour: int = 14) -> dict:
    return {"id": event_id or snapshot["candidate_id"], "candidate_id": snapshot["candidate_id"],
            "decision": decision, "reason": reason, "scope": scope,
            "rules_version": snapshot["rules_version"],
            "created_at": snapshot["published_at"][:10] + f"T{hour:02}:00:00+00:00"}


def fixtures() -> tuple[list, list, list, list, dict]:
    definitions = [
        ("this", "DevOps AWS contract"), ("this-neighbour", "DevOps AWS contract"),
        ("similar", "Recruiter contract"), ("similar-neighbour", "Senior Recruiter contract"),
        ("always", "DevOps Azure contract"), ("always-neighbour", "Network Engineer Azure remote"),
        ("approved", "Network Engineer AWS remote"), ("need-info", "Designer contract"),
        ("changed", "SRE Kubernetes contract"), ("legacy-no-reason", "SRE contract"),
    ]
    snapshots = [demo_snapshot(name, title, message=i) for i, (name, title) in enumerate(definitions, 1)]
    snapshots.append(demo_snapshot("company-stays", "DevOps Azure contract", item_type="company", message=11))
    by_name = {s["candidate_id"].removeprefix("radar-demo-"): s for s in snapshots}
    events = [
        demo_event(by_name["this"], "REJECT", scope="THIS_ITEM", reason="OTHER"),
        demo_event(by_name["similar"], "REJECT", scope="SIMILAR", reason="NOT_RELEVANT_ROLE"),
        demo_event(by_name["always"], "REJECT", scope="ALWAYS", reason="TOOL_STACK_MISMATCH"),
        demo_event(by_name["approved"], "APPROVE"),
        demo_event(by_name["need-info"], "NEED_INFO"),
        demo_event(by_name["changed"], "REJECT", event_id="changed-first", scope="ALWAYS", reason="TOOL_STACK_MISMATCH"),
        demo_event(by_name["changed"], "APPROVE", event_id="changed-second", hour=15),
        demo_event(by_name["legacy-no-reason"], "REJECT", scope="ALWAYS"),
        {"id": "missing", "candidate_id": "radar-demo-missing", "decision": "REJECT",
         "reason": "NOT_RELEVANT_ROLE", "scope": "ALWAYS", "rules_version": "synthetic-rules-v1", "created_at": "2026-09-01T15:00:00+00:00"},
        {"id": "qa", "candidate_id": "radar-qa-review-v017", "decision": "REJECT", "reason": "OTHER",
         "scope": "ALWAYS", "created_at": "2026-09-01T15:00:00+00:00"},
    ]
    holdout = [demo_snapshot("new-approved", "SRE AWS remote", day=2, message=21),
               demo_snapshot("new-rejected", "DevOps Azure remote", day=2, message=22)]
    new_events = [demo_event(holdout[0], "APPROVE"),
                  demo_event(holdout[1], "REJECT", scope="ALWAYS", reason="TOOL_STACK_MISMATCH")]
    legacy = {"dataset_run_id": "synthetic-day-1", "labels": [
        {"report_no": 1, "chat_id": -1, "message_id": 7, "verdict": "keep", "expected_type": "opportunity"},
        {"report_no": 2, "chat_id": -1, "message_id": 9, "verdict": "keep", "expected_type": "opportunity"},
    ]}
    return snapshots, events, holdout, new_events, legacy


def run_demo(directory: Path) -> dict:
    snapshots, events, holdout, new_events, legacy = fixtures()
    for name, data in (("snapshots", {"snapshots": snapshots}), ("events", {"events": events}),
                       ("new-days", {"snapshots": holdout}), ("new-day-events", {"events": new_events})):
        write_private(directory / f"{name}.json", data)
    with FeedbackStore(directory / "ledger.sqlite3") as store:
        revision = store.import_events(events, snapshots)
        assert store.import_events(list(reversed(events)), snapshots)["revision"] == revision["revision"]
        write_private(directory / f"{revision['revision']}.json", revision)
        report = build_preview(revision, snapshots, holdout=holdout, holdout_events=new_events,
                               legacy=[legacy])
        version, json_path, md_path = save_report(store, report, directory)
    return {"synthetic_only": True, "revision": revision["revision"], "preview_version": version,
            "changed": report["changed_count"], "approvals_preserved": len(report["approvals_preserved"]),
            "excluded_events": len(revision["excluded"]), "gate": report["gate"],
            "report": str(md_path.resolve()), "json": str(json_path.resolve())}
