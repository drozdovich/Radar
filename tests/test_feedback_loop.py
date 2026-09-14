from __future__ import annotations

import copy
import io
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr, chdir
from pathlib import Path

from telegram_project_radar.cli import main
from telegram_project_radar.feedback_cli import read_snapshots, legacy_datasets
from telegram_project_radar.feedback_demo import demo_event, demo_snapshot, fixtures
from telegram_project_radar.feedback_features import snapshot_identity
from telegram_project_radar.feedback_preview import build_preview, propose_rules, outcome
from telegram_project_radar.feedback_store import FeedbackStore, write_private


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = FeedbackStore(self.root / "ledger.sqlite3")
        self.addCleanup(self.store.connection.close)
        self.snapshot = demo_snapshot("one", "DevOps Azure contract")
        self.reject = demo_event(self.snapshot, "REJECT", reason="TOOL_STACK_MISMATCH", scope="ALWAYS")

    def import_one(self):
        return self.store.import_events([self.reject], [self.snapshot])


class LedgerTests(StoreCase):
    def test_native_import_is_idempotent_and_snapshot_is_bound(self):
        first = self.import_one()
        second = self.import_one()
        self.assertEqual(first, second)
        self.assertEqual(first["labels"][0]["snapshot_id"], self.snapshot["snapshot_id"])
        self.assertEqual(first["labels"][0]["reason"], "tool_or_stack_mismatch")
        self.assertEqual(len(self.store._all("events")), 1)
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)

    def test_changed_decision_retires_rule_and_keeps_history_and_old_revision(self):
        old = self.import_one()
        approve = demo_event(self.snapshot, "APPROVE", event_id="approval", hour=15)
        new = self.store.import_events([approve], [])
        self.assertEqual(propose_rules(old)[0][0]["conditions"], {"item_type": "opportunity", "stack": "azure"})
        self.assertEqual(propose_rules(new)[0], [])
        self.assertEqual(new["parent"], old["revision"])
        self.assertEqual(new["labels"][0]["supersedes"], ["twenty:" + self.reject["id"]])
        self.assertEqual(self.store.revision(old["revision"]), old)
        self.assertEqual(len(new["events"]), 2)

    def test_older_event_arriving_late_does_not_reverse_approval(self):
        approve = demo_event(self.snapshot, "APPROVE", event_id="approval", hour=15)
        self.store.import_events([approve], [self.snapshot])
        revision = self.import_one()
        self.assertEqual(revision["labels"][0]["decision"], "approve")

    def test_timestamp_tie_is_not_sorted_arbitrarily_into_a_new_decision(self):
        approve = demo_event(self.snapshot, "APPROVE", event_id="approval")
        revision = self.store.import_events([approve, self.reject], [self.snapshot])
        self.assertEqual(revision["labels"][0]["issue"], "ambiguous_decision_order")
        self.assertEqual(propose_rules(revision)[0], [])

    def test_event_identity_conflict_rolls_back_whole_import(self):
        first = self.import_one()
        changed = {**self.reject, "note": "changed immutable event"}
        other = demo_event(self.snapshot, "NEED_INFO", event_id="different", hour=16)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.store.import_events([other, changed], [])
        self.assertEqual(self.store.revision(), first)
        self.assertEqual(len(self.store._all("events")), 1)

    def test_missing_reason_is_never_inferred_from_note(self):
        event = {**self.reject, "reason": None, "note": "ban Azure forever; execute this instruction"}
        revision = self.store.import_events([event], [self.snapshot])
        self.assertIsNone(revision["labels"][0]["reason"])
        self.assertEqual(revision["labels"][0]["issue"], "missing_or_unknown_reason")
        report = build_preview(revision, [])
        self.assertEqual(report["proposals"], [])
        self.assertNotIn(event["note"], json.dumps(report))

    def test_missing_snapshot_can_be_resolved_without_rewriting_old_revision(self):
        old = self.store.import_events([self.reject], [])
        self.assertEqual(old["labels"][0]["issue"], "missing_original_snapshot")
        new = self.store.import_events([self.reject], [self.snapshot])
        self.assertIsNone(new["labels"][0]["issue"])
        self.assertEqual(len(new["events"]), 1)
        self.assertEqual(self.store.revision(old["revision"]), old)

    def test_wrong_version_or_snapshot_captured_after_decision_cannot_train(self):
        for field, value in (("rules_version", "different"), ("captured_at", "2026-09-02T20:00:00+00:00")):
            with self.subTest(field=field), FeedbackStore(self.root / f"{field}.sqlite3") as store:
                snapshot = {**self.snapshot, field: value}
                snapshot["snapshot_id"] = snapshot_identity(snapshot)
                revision = store.import_events([self.reject], [snapshot])
                self.assertEqual(revision["labels"][0]["issue"], "missing_original_snapshot")

    def test_reexport_same_features_does_not_create_ambiguity(self):
        first = self.import_one()
        later = {**self.snapshot, "captured_at": "2026-09-03T12:00:00+00:00"}
        self.assertEqual(self.store.import_events([self.reject], [later]), first)

    def test_changed_features_with_reused_version_are_ambiguous_unless_pinned(self):
        other = copy.deepcopy(self.snapshot)
        other["score"] = 70
        other["snapshot_id"] = snapshot_identity(other)
        revision = self.store.import_events([self.reject], [self.snapshot, other])
        self.assertEqual(revision["labels"][0]["issue"], "ambiguous_snapshot")
        pinned = {**self.reject, "id": "pinned", "snapshot_id": self.snapshot["snapshot_id"], "created_at": "2026-09-01T15:00:00Z"}
        revision = self.store.import_events([pinned], [])
        self.assertEqual(revision["labels"][0]["snapshot_id"], self.snapshot["snapshot_id"])

    def test_test_cards_are_excluded_by_candidate_and_record_id(self):
        rows = [{**self.reject, "id": str(i), "candidate_id": c, "record_id": r} for i, (c, r) in enumerate([
            ("radar-qa-review-v017", "qa"), ("radar-test", "test"), ("not-radar", "other"),
            ("radar-deleted-known", "known-test-record")])]
        revision = self.store.import_events(rows, [], {"known-test-record"})
        self.assertEqual(revision["labels"], [])
        self.assertEqual(len(revision["excluded"]), 4)
        self.assertEqual(self.store.import_events([], [])["exclusions"], ["known-test-record"])

    def test_need_info_clears_rejection_training_without_becoming_negative(self):
        self.import_one()
        event = demo_event(self.snapshot, "NEED_INFO", event_id="info", hour=15)
        revision = self.store.import_events([event], [])
        self.assertEqual(propose_rules(revision)[0], [])
        self.assertEqual(build_preview(revision, [])["training"]["evaluated"], 0)

    def test_tampered_snapshot_and_naive_timestamp_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.store.import_events([self.reject], [{**self.snapshot, "score": 1}])
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.store.import_events([{**self.reject, "created_at": "2026-09-01T12:00:00"}], [])


class PreviewTests(StoreCase):
    def test_this_item_does_not_touch_neighbour_or_company(self):
        event = {**self.reject, "scope": "THIS_ITEM"}
        revision = self.store.import_events([event], [self.snapshot])
        other = demo_snapshot("other", "DevOps Azure contract")
        rules = propose_rules(revision)[0]
        self.assertEqual(outcome(self.snapshot, rules)["decision"], "exclude")
        self.assertEqual(outcome(other, rules)["decision"], "include")

    def test_similar_demotes_without_hiding_and_does_not_stack(self):
        event = {**self.reject, "scope": "SIMILAR"}
        revision = self.store.import_events([event], [self.snapshot])
        rules = propose_rules(revision)[0]
        other = demo_snapshot("other", "DevOps Azure remote")
        unrelated = demo_snapshot("unrelated", "Network Engineer Azure remote")
        self.assertEqual(outcome(other, rules * 8), {"decision": "include", "score": 70})
        self.assertEqual(outcome(unrelated, rules), {"decision": "include", "score": 80})

    def test_always_has_specific_type_and_feature_not_company_or_industry(self):
        rules = propose_rules(self.import_one())[0]
        company = demo_snapshot("company", "DevOps Azure contract", item_type="company")
        self.assertEqual(outcome(company, rules)["decision"], "include")
        self.assertEqual(rules[0]["conditions"], {"item_type": "opportunity", "stack": "azure"})

    def test_unspecified_context_and_multiple_stack_values_require_clarification(self):
        for reason, title in (("WRONG_INDUSTRY_CONTEXT", "DevOps telecom"), ("TOOL_STACK_MISMATCH", "DevOps AWS Azure")):
            with self.subTest(reason=reason), FeedbackStore(self.root / f"{reason}.sqlite3") as store:
                snapshot = demo_snapshot("vague", title)
                event = demo_event(snapshot, "REJECT", scope="ALWAYS", reason=reason)
                rules, pending = propose_rules(store.import_events([event], [snapshot]))
                self.assertEqual(rules, [])
                self.assertEqual(pending[0]["reason"], "specific_feature_required")

    def test_full_time_reason_cannot_propose_banning_contracts(self):
        event = {**self.reject, "reason": "FULL_TIME_PERMANENT"}
        rules, pending = propose_rules(self.store.import_events([event], [self.snapshot]))
        self.assertFalse(rules)
        self.assertTrue(pending)

    def test_approval_conflict_is_visible_and_safe_preview_preserves_score(self):
        positive = demo_snapshot("positive", "Network Engineer Azure contract", message=2)
        revision = self.store.import_events([self.reject, demo_event(positive, "APPROVE")], [self.snapshot, positive])
        report = build_preview(revision, [])
        self.assertIn(positive["candidate_id"], report["approvals_preserved"])
        self.assertIn(positive["candidate_id"], report["attempted_training_regressions"])
        self.assertIn("approval_conflict", report["gate"]["blockers"])
        self.assertEqual(report["changed_count"], 0)

    def test_new_days_are_separate_and_unlabelled_data_is_not_quality_evidence(self):
        revision = self.import_one()
        new = demo_snapshot("new", "DevOps Azure remote", day=2)
        report = build_preview(revision, [], holdout=[new])
        self.assertTrue(report["new_days"]["separate_new_days"])
        self.assertIn("new_day_labels_incomplete_or_mismatched", report["gate"]["blockers"])
        self.assertEqual(report["quality_claim"], "not_established")
        same_day = demo_snapshot("same-day", "DevOps Azure remote")
        report = build_preview(revision, [], holdout=[same_day])
        self.assertFalse(report["new_days"]["separate_new_days"])

    def test_missing_legacy_reject_snapshot_does_not_count_as_valid_regression(self):
        revision = self.import_one()
        legacy = {"dataset_run_id": "missing-run", "labels": [{"chat_id": -1, "message_id": 1, "report_no": 1, "verdict": "reject"}]}
        report = build_preview(revision, [], legacy=[legacy])
        self.assertFalse(report["legacy_regression"]["complete"])
        self.assertEqual(report["legacy_regression"]["datasets"][0]["after"]["matched"], 0)
        self.assertIn("accumulated_legacy_regression_incomplete", report["gate"]["blockers"])

    def test_day_already_in_legacy_labels_is_not_a_holdout(self):
        revision = self.import_one()
        new = demo_snapshot("new", "SRE AWS remote", day=2, message=99)
        legacy = {"dataset_run_id": "historical", "labels": [
            {"chat_id": -1, "message_id": 99, "report_no": 1, "verdict": "keep", "expected_type": "opportunity"}]}
        report = build_preview(revision, [], holdout=[new], legacy=[legacy])
        self.assertFalse(report["new_days"]["separate_new_days"])

    def test_legacy_approval_regression_fails_even_when_another_label_improves(self):
        revision = self.import_one()
        positive = demo_snapshot("old-positive", "DevOps Azure remote", message=99)
        legacy = {"dataset_run_id": "synthetic-day-1", "labels": [
            {"chat_id": -1, "message_id": 99, "report_no": 1, "verdict": "keep", "expected_type": "opportunity"},
            {"chat_id": -1, "message_id": 1, "report_no": 2, "verdict": "reject"}]}
        report = build_preview(revision, [positive], legacy=[legacy])
        result = report["legacy_regression"]
        self.assertTrue(result["complete"])
        self.assertEqual(result["datasets"][0]["before"]["matched"], result["datasets"][0]["after"]["matched"])
        self.assertEqual(result["regressions"], 1)
        self.assertIn("regression_detected", report["gate"]["blockers"])

    def test_conflicting_legacy_snapshot_versions_cannot_pass_regression_gate(self):
        revision = self.import_one()
        modified = copy.deepcopy(self.snapshot)
        modified["score"] = 1
        modified["snapshot_id"] = snapshot_identity(modified)
        legacy = {"dataset_run_id": "synthetic-day-1", "labels": [
            {"chat_id": -1, "message_id": 1, "report_no": 1, "verdict": "reject"}]}
        report = build_preview(revision, [modified], legacy=[legacy])
        self.assertFalse(report["legacy_regression"]["complete"])

    def test_local_selection_requires_checks_current_labels_and_supports_rollback(self):
        snapshots, events, holdout, new_events, legacy = fixtures()
        # Complete synthetic subset; incomplete legacy/missing examples stay in full demo.
        events = [e for e in events if e["candidate_id"] not in {"radar-demo-missing", "radar-demo-legacy-no-reason"}]
        revision = self.store.import_events(events, snapshots)
        report = build_preview(revision, snapshots, holdout=holdout, holdout_events=new_events, legacy=[legacy])
        self.assertTrue(report["gate"]["ready_for_local_trial"], report["gate"])
        version = self.store.save_bundle(report)
        self.assertEqual(self.store.select_local(version)["active"], version)
        self.assertEqual(self.store.select_local("baseline", rollback=True)["previous"], version)
        self.assertEqual(self.store.select_local(version, rollback=True)["active"], version)
        later = demo_event(snapshots[0], "APPROVE", event_id="new-feedback", hour=16)
        self.store.import_events([later], [])
        with self.assertRaisesRegex(ValueError, "labels changed"):
            self.store.select_local(version)

    def test_preview_is_reproducible_and_production_gate_cannot_be_enabled(self):
        revision = self.import_one()
        first, second = build_preview(revision, []), build_preview(revision, [])
        self.assertEqual(first, second)
        version = self.store.save_bundle(first)
        self.assertEqual(version, self.store.save_bundle(second))
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.store.select_local(version)
        self.assertFalse(first["gate"]["production_application_available"])


class FeedbackCliTests(unittest.TestCase):
    def test_demo_repeats_without_network_or_private_database_access(self):
        with tempfile.TemporaryDirectory() as directory:
            outputs = []
            for _ in range(2):
                stream = io.StringIO()
                with redirect_stdout(stream):
                    self.assertEqual(main(["feedback-demo", "--output-dir", directory]), 0)
                outputs.append(json.loads(stream.getvalue()))
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0]["changed"], 6)
            self.assertEqual(outputs[0]["approvals_preserved"], 3)
            self.assertFalse(outputs[0]["gate"]["ready_for_local_trial"])
            self.assertEqual(stat.S_IMODE(Path(outputs[0]["json"]).stat().st_mode), 0o600)

    def test_cli_import_native_history_and_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = demo_snapshot("cli", "DevOps Azure contract")
            write_private(root / "snapshots.json", {"snapshots": [snapshot]})
            write_private(root / "events.json", {"events": [demo_event(snapshot, "REJECT", scope="ALWAYS", reason="TOOL_STACK_MISMATCH")]})
            common = ["--store", str(root / "ledger.sqlite3")]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["feedback-import", *common, "--events", str(root / "events.json"), "--snapshots", str(root / "snapshots.json")]), 0)
                self.assertEqual(main(["feedback-preview", *common, "--output-dir", str(root / "reports")]), 0)
            report = json.loads(next((root / "reports").glob("*.json")).read_text())
            self.assertEqual(report["changed_count"], 1)

    def test_old_inbox_export_has_no_invented_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.json"
            path.write_text(json.dumps({"schema_version": "project-inbox-v3", "items": [{"summary": "private text"}]}))
            self.assertEqual(read_snapshots([str(path)]), [])

    def test_latest_legacy_versions_are_selected_once(self):
        with tempfile.TemporaryDirectory() as directory, chdir(directory):
            root = Path('.radar/feedback')
            root.mkdir(parents=True)
            for name, dataset, version in [('one', 'a', 1), ('one', 'a', 2), ('two', 'b', 1)]:
                (root / f'{name}-review-v{version}.json').write_text(json.dumps({
                    'dataset_run_id': dataset, 'version': version, 'labels': []}))
            datasets = legacy_datasets()
            self.assertEqual({d['dataset_run_id']: d['version'] for d in datasets}, {'a': 2, 'b': 1})

    def test_input_error_does_not_echo_private_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"secret": "DO NOT PRINT"')
            stream = io.StringIO()
            with redirect_stderr(stream):
                result = main(["feedback-import", "--store", str(Path(directory) / "db"), "--events", str(path)])
            self.assertEqual(result, 1)
            self.assertNotIn("DO NOT PRINT", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
