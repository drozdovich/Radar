"""Explainable proposals and reproducible offline regression; no active rules writes."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from . import __version__
from .feedback import evaluate_feedback
from .feedback_features import digest, instant, validate_snapshot
from .feedback_store import effective_labels, normalize_event
from .models import CandidateItem, Evaluation

REASON_FEATURE = {
    "not_relevant_role": "role", "level_mismatch": "level",
    "tool_or_stack_mismatch": "stack", "full_time_or_permanent": "engagement",
}
VISIBLE = {"include", "review"}


def propose_rules(revision: dict) -> tuple[list[dict], list[dict]]:
    snapshots = {s["snapshot_id"]: s for s in revision["snapshots"]}
    proposals, pending = [], []
    for label in revision["labels"]:
        if label["decision"] != "reject":
            continue
        if label["issue"]:
            pending.append({"candidate_id": label["candidate_id"], "reason": label["issue"]})
            continue
        snapshot = snapshots[label["snapshot_id"]]
        scope = label["scope"]
        conditions = {"candidate_id": label["candidate_id"]} if scope == "this_item" else {}
        if scope != "this_item":
            field = REASON_FEATURE.get(label["reason"])
            values = snapshot["features"].get(field, [])
            # A permanent/full-time reason never turns "contract" into a ban.
            if (len(values) != 1 or (field == "engagement" and values != ["full_time"])):
                pending.append({"candidate_id": label["candidate_id"], "reason": "specific_feature_required"})
                continue
            conditions = {"item_type": snapshot["item_type"], field: values[0]}
            if scope == "similar":
                secondary = next((k for k in ("role", "stack", "engagement", "work_mode", "level")
                                  if k != field and len(snapshot["features"][k]) == 1), None)
                if not secondary:
                    pending.append({"candidate_id": label["candidate_id"], "reason": "second_similarity_feature_required"})
                    continue
                conditions[secondary] = snapshot["features"][secondary][0]
        proposal = {"scope": scope, "conditions": conditions,
                    "action": "demote" if scope == "similar" else "exclude",
                    "score_delta": -10 if scope == "similar" else 0,
                    "reason": label["reason"], "source_event": label["event_id"],
                    "source_snapshot": label["snapshot_id"], "source_rules_version": label["rules_version"]}
        proposal["rule_id"] = "rule-" + digest(proposal)[:20]
        proposals.append(proposal)
    return proposals, pending


def matches(rule: dict, snapshot: dict) -> bool:
    for key, value in rule["conditions"].items():
        if key in {"candidate_id", "item_type"}:
            if snapshot[key] != value:
                return False
        elif value not in snapshot["features"][key]:
            return False
    return True


def outcome(snapshot: dict, rules: list[dict]) -> dict:
    result = {"decision": snapshot["decision"], "score": snapshot["score"]}
    if result["decision"] not in VISIBLE:
        return result
    matched = [r for r in rules if matches(r, snapshot)]
    if any(r["action"] == "exclude" for r in matched):
        result["decision"] = "exclude"
    if any(r["action"] == "demote" for r in matched):
        # Repeated examples do not accumulate an unlimited penalty.
        result["score"] = max(0, result["score"] - 10)
    return result


def label_check(label: dict, before: dict, after: dict) -> bool | None:
    if label["issue"]:
        return None
    if label["decision"] == "approve":
        return after["decision"] in VISIBLE and after["score"] >= before["score"]
    if label["decision"] != "reject":
        return None
    if label["scope"] == "similar":
        return after["decision"] not in VISIBLE or after["score"] < before["score"]
    return after["decision"] not in VISIBLE


def review_checks(labels: list[dict], snapshots: dict, rules: list[dict]) -> dict:
    rows = []
    for label in labels:
        snapshot = snapshots.get(label["snapshot_id"])
        before = outcome(snapshot, []) if snapshot else None
        after = outcome(snapshot, rules) if snapshot else None
        rows.append({"candidate_id": label["candidate_id"], "decision": label["decision"],
                     "issue": label["issue"],
                     "before_matched": label_check(label, before, before) if snapshot else None,
                     "after_matched": label_check(label, before, after) if snapshot else None})
    regressions = [r["candidate_id"] for r in rows if r["before_matched"] is True and r["after_matched"] is False]
    return {"rows": rows, "regressions": regressions,
            "incomplete": sum(bool(r["issue"]) for r in rows),
            "evaluated": sum(r["after_matched"] is not None for r in rows),
            "matched_after": sum(r["after_matched"] is True for r in rows)}


def legacy_checks(datasets: list[dict], snapshots: list[dict], rules: list[dict]) -> dict:
    results = []
    for dataset in datasets:
        rows = [s for s in snapshots if s["run_id"] == dataset["dataset_run_id"] and s["locator"]]
        keys = {(s["locator"]["chat_id"], s["locator"]["message_id"]) for s in rows}
        versions = {}
        for snapshot in rows:
            key = tuple(snapshot["locator"][k] for k in ("chat_id", "message_id", "item_index"))
            signature = {k: snapshot[k] for k in ("decision", "score", "features", "text_sha256", "item_type")}
            versions.setdefault(key, set()).add(digest(signature))
        ambiguous = sum(len(v) > 1 for v in versions.values())
        missing = sum((int(l["chat_id"]), int(l["message_id"])) not in keys for l in dataset["labels"])
        available = {"labels": [l for l in dataset["labels"]
                                if (int(l["chat_id"]), int(l["message_id"])) in keys]}
        def items(applied):
            return [CandidateItem(s["locator"]["chat_id"], s["locator"]["message_id"],
                                  s["locator"]["item_index"], s["item_type"], "",
                                  Evaluation(outcome(s, applied)["score"], outcome(s, applied)["decision"],
                                             (), (), (), s["text_sha256"])) for s in rows]
        before, after = evaluate_feedback(available, items([])), evaluate_feedback(available, items(rules))
        # Compare each label, not aggregate counts that could hide a lost approval.
        regressions = []
        for label in available["labels"]:
            one = {"labels": [label]}
            if (evaluate_feedback(one, items([]))["matched"] == 1
                    and evaluate_feedback(one, items(rules))["matched"] == 0):
                regressions.append(label["report_no"])
        results.append({"dataset_sha256": digest(dataset), "run_id": dataset["dataset_run_id"],
                        "total_labels": len(dataset["labels"]), "missing_snapshots": missing,
                        "ambiguous_snapshots": ambiguous, "before": before, "after": after,
                        "regressions": regressions})
    return {"datasets": results, "complete": bool(results) and not any(r["missing_snapshots"] or r["ambiguous_snapshots"] for r in results),
            "regressions": sum(len(r["regressions"]) for r in results)}


def build_preview(revision: dict, candidates: list[dict], *, holdout: list[dict] | None = None,
                  holdout_events: list[dict] | None = None, legacy: list[dict] | None = None,
                  selected_rules: list[str] | None = None) -> dict:
    holdout = [validate_snapshot(s) for s in (holdout or [])]
    corpus = {s["snapshot_id"]: validate_snapshot(s) for s in revision["snapshots"] + candidates + holdout}
    training_labels = revision["labels"]
    holdout_labels, _ = effective_labels([normalize_event(e) for e in (holdout_events or [])], holdout,
                                          set(revision["exclusions"]))
    # Exclude QA/non-Radar candidates from impact and regression as well as training.
    from .feedback_store import excluded
    corpus = {key: s for key, s in corpus.items()
              if not excluded(s, set(revision["exclusions"]))}
    proposals, pending = propose_rules(revision)
    if selected_rules is not None:
        if set(selected_rules) - {p["rule_id"] for p in proposals}:
            raise ValueError("selected proposal does not exist in this label revision")
        proposals = [p for p in proposals if p["rule_id"] in selected_rules]
    # Evaluate the proposed effects before protecting positives; a conflict must
    # remain visible and fail the gate, not disappear behind an approval exception.
    attempted_training = review_checks(training_labels, corpus, proposals)
    attempted_holdout = review_checks(holdout_labels, corpus, proposals)
    approvals = [l for l in training_labels + holdout_labels if l["decision"] == "approve" and not l["issue"]]
    approval_ids = {l["candidate_id"] for l in approvals}
    for rule in proposals:
        rule["blocked_by_approvals"] = sorted({s["candidate_id"] for s in corpus.values()
            if s["candidate_id"] in approval_ids and outcome(s, [rule]) != outcome(s, [])})
    safe = [r for r in proposals if not r["blocked_by_approvals"]]
    rows = []
    for key, snapshot in sorted(corpus.items(), key=lambda pair: (pair[1]["published_at"], pair[1]["candidate_id"], pair[0])):
        before, after = outcome(snapshot, []), outcome(snapshot, safe)
        rows.append({"candidate_id": snapshot["candidate_id"], "snapshot_id": key,
                     "day": instant(snapshot["published_at"]).astimezone(ZoneInfo("Europe/Madrid")).date().isoformat(),
                     "before": before, "after": after, "changed": before != after,
                     "rules": [r["rule_id"] for r in safe if matches(r, snapshot) and before != after]})
    training = review_checks(training_labels, corpus, safe)
    new_days = review_checks(holdout_labels, corpus, safe)
    training_snapshots = [corpus[l["snapshot_id"]] for l in training_labels if l["snapshot_id"] in corpus]
    cutoff = max((instant(s["published_at"]).astimezone(ZoneInfo("Europe/Madrid")).date() for s in training_snapshots), default=None)
    seen_ids = {l["candidate_id"] for l in training_labels}
    seen_text = {s["text_sha256"] for s in training_snapshots}
    known_legacy_keys = {(int(l["chat_id"]), int(l["message_id"]))
                         for dataset in (legacy or []) for l in dataset["labels"]}
    separated = bool(cutoff and holdout) and all(
        instant(s["published_at"]).astimezone(ZoneInfo("Europe/Madrid")).date() > cutoff
        and s["candidate_id"] not in seen_ids and s["text_sha256"] not in seen_text
        and (not s["locator"] or (s["locator"]["chat_id"], s["locator"]["message_id"]) not in known_legacy_keys)
        for s in holdout)
    new_days.update(separate_new_days=separated,
                    candidate_count=len(holdout), labelled_count=len(holdout_labels),
                    attempted_regressions=attempted_holdout["regressions"])
    legacy_result = legacy_checks(legacy or [], list(corpus.values()), safe)
    attempted_legacy = legacy_checks(legacy or [], list(corpus.values()), proposals)
    blockers = []
    if not proposals:
        blockers.append("no_proposals")
    if pending or any(l["issue"] for l in training_labels):
        blockers.append("incomplete_training_examples")
    if any(r["blocked_by_approvals"] for r in proposals):
        blockers.append("approval_conflict")
    if not legacy_result["complete"]:
        blockers.append("accumulated_legacy_regression_incomplete")
    if legacy_result["regressions"] or attempted_legacy["regressions"] or attempted_training["regressions"] or attempted_holdout["regressions"]:
        blockers.append("regression_detected")
    if not separated:
        blockers.append("no_independent_new_days")
    if (new_days["incomplete"] or not {"approve", "reject"}.issubset({l["decision"] for l in holdout_labels if not l["issue"]})
            or new_days["matched_after"] != new_days["evaluated"]):
        blockers.append("new_day_labels_incomplete_or_mismatched")
    if not any(l["decision"] == "approve" and not l["issue"] for l in training_labels):
        blockers.append("no_training_approvals")
    return {"schema_version": "feedback-preview-v1", "report_format": "feedback-markdown-v1", "app_version": __version__,
            "label_revision": revision["revision"], "proposals": proposals, "pending": pending,
            "impact": rows, "changed_count": sum(r["changed"] for r in rows),
            "approvals_preserved": sorted({l["candidate_id"] for l in approvals
                if l["snapshot_id"] in corpus and outcome(corpus[l["snapshot_id"]], safe) == outcome(corpus[l["snapshot_id"]], [])}),
            "training": training, "attempted_training_regressions": attempted_training["regressions"],
            "legacy_regression": legacy_result, "attempted_legacy_regressions": attempted_legacy["regressions"],
            "new_days": new_days,
            "gate": {"ready_for_local_trial": not blockers, "blockers": blockers,
                     "production_changed": False, "production_application_available": False},
            "quality_claim": "not_established",
            "limitations": ["Preview replays frozen baseline features, not a retrained model.",
                            "Only explicit heading features are eligible; industry, company and notes are not predicates.",
                            "Synthetic checks establish behavior only; real quality improvement is not measured."],
            "input_hashes": {"candidates": digest(candidates), "holdout": digest(holdout),
                             "holdout_events": digest(holdout_events or []), "legacy": digest(legacy or [])}}
