from __future__ import annotations

import json
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from telegram_project_radar.inbox import (
    build_project_inbox_items,
    project_inbox_payload,
    stable_candidate_id,
    twenty_rich_text_summary,
    write_project_inbox_export,
)
from telegram_project_radar.models import FeedbackDecision, ProjectInboxItem
from telegram_project_radar.storage import Storage


def inbox_item(candidate_id: str = "radar-test") -> ProjectInboxItem:
    return ProjectInboxItem(
        candidate_id=candidate_id,
        run_id="run-1",
        chat_id=-1001,
        message_id=42,
        item_index=0,
        item_type="opportunity",
        title="Senior Founding SDR",
        summary="B2B SaaS outbound contract role",
        score=90,
        known_conditions=("Гибкий, проектный или контрактный формат указан",),
        fit_reasons=("Подтверждена technical GTM / sales systems задача",),
        risks=(),
        unknowns=("Не указан бюджет или ставка",),
        source_name="Example Careers",
        source_url="https://t.me/example/42",
        published_at="2026-09-03T13:00:00+00:00",
        rules_version="deterministic-v2-no-llm",
    )


class InboxBuilderTests(unittest.TestCase):
    def test_builds_clean_stable_item(self) -> None:
        row = {
            "chat_id": -1001,
            "message_id": 42,
            "item_index": 0,
            "item_type": "opportunity",
            "item_text": "🔹 Senior Founding SDR Компания ищет специалиста\nRemote contract for B2B SaaS outbound.",
            "original_message": "Исходный заголовок\n\n🔹 Senior Founding SDR Компания ищет специалиста\nRemote contract for B2B SaaS outbound.\n\nКонтакт: @example",
            "score": 90,
            "reasons_json": '["Technical GTM"]',
            "risks_json": "[]",
            "unknowns_json": '["Budget unknown"]',
            "fingerprint": "abc123",
            "approved_name": "Example Careers",
            "message_link": "https://t.me/example/42",
            "sent_at": "2026-09-03T13:00:00+00:00",
        }

        first = build_project_inbox_items([row], run_id="run-1")[0]
        second = build_project_inbox_items([row], run_id="run-1")[0]

        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertTrue(first.candidate_id.startswith("radar-"))
        self.assertEqual(first.title, "Senior Founding SDR")
        self.assertIn("Remote указан", first.known_conditions)
        self.assertIn(
            "Гибкий, проектный или контрактный формат указан",
            first.known_conditions,
        )
        self.assertEqual(first.review_status, "new")
        self.assertEqual(first.summary, row["item_text"])

    def test_stable_id_changes_for_digest_item(self) -> None:
        first = stable_candidate_id(
            chat_id=-1001, message_id=42, item_index=1, fingerprint="same"
        )
        second = stable_candidate_id(
            chat_id=-1001, message_id=42, item_index=2, fingerprint="same"
        )
        self.assertNotEqual(first, second)

    def test_known_conditions_use_evaluation_and_k_budget(self) -> None:
        row = {
            "chat_id": -1001,
            "message_id": 43,
            "item_index": 0,
            "item_type": "opportunity",
            "item_text": "DevOps Engineer\n60k - 65k EUR в год\nRemote",
            "original_message": "DevOps Engineer\n60k - 65k EUR в год\nRemote",
            "score": 100,
            "reasons_json": '["Явно указан гибкий или проектный формат"]',
            "risks_json": "[]",
            "unknowns_json": "[]",
            "fingerprint": "budget",
            "approved_name": "RVC",
            "message_link": "https://t.me/example/43",
            "sent_at": "2026-09-03T17:00:00+00:00",
        }
        item = build_project_inbox_items([row], run_id="run-1")[0]
        self.assertIn(
            "Гибкий, проектный или контрактный формат указан",
            item.known_conditions,
        )
        self.assertIn(
            "Бюджет или ставка указаны в исходном сообщении",
            item.known_conditions,
        )

    def test_private_json_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "private" / "inbox.json"
            write_project_inbox_export([inbox_item()], output)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "project-inbox-v4")
            self.assertEqual(payload["items"][0]["candidate_id"], "radar-test")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_feature_snapshots_preserve_review_and_survive_reexport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "inbox.json"
            first = replace(inbox_item(), selection_decision="review")
            write_project_inbox_export([first], output)
            old_snapshot = json.loads(output.read_text())["learning_snapshots"][0]
            self.assertEqual(old_snapshot["decision"], "review")
            self.assertNotIn("summary", old_snapshot)
            self.assertNotIn(first.summary, json.dumps(old_snapshot))
            write_project_inbox_export([replace(first, score=60)], output)
            archived = [json.loads(p.read_text())["snapshots"][0] for p in (output.parent / "learning-snapshots").glob("*.json")]
            self.assertEqual(len(archived), 2)
            self.assertIn(old_snapshot, archived)

    def test_empty_export_does_not_replace_existing_nonempty_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "private" / "inbox.json"
            write_project_inbox_export([inbox_item()], output)
            original = output.read_text(encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "refusing to replace"):
                write_project_inbox_export([], output)

            self.assertEqual(output.read_text(encoding="utf-8"), original)

    def test_payload_keeps_original_in_summary_without_telegram_metadata(self) -> None:
        payload = project_inbox_payload([inbox_item()])
        self.assertEqual(
            payload["items"][0]["summary"], "B2B SaaS outbound contract role"
        )
        self.assertNotIn("chat_id", payload["items"][0])
        self.assertNotIn("message_id", payload["items"][0])
        self.assertNotIn("raw_text", payload["items"][0])

    def test_twenty_summary_uses_one_block_and_keeps_exact_text(self) -> None:
        text = "Первая строка\nВторая строка\nТретья строка"
        first = twenty_rich_text_summary(text, candidate_id="radar-test")
        second = twenty_rich_text_summary(text, candidate_id="radar-test")
        blocks = json.loads(first["blocknote"])

        self.assertEqual(first, second)
        self.assertEqual(first["markdown"], text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["content"][0]["text"], text)

        payload = project_inbox_payload([inbox_item()])
        self.assertEqual(
            payload["items"][0]["summary_twenty"]["markdown"],
            payload["items"][0]["summary"],
        )

    def test_original_candidate_text_is_not_truncated(self) -> None:
        long_message = "Начало\n" + ("полный исходный текст " * 100)
        row = {
            "chat_id": -1001,
            "message_id": 44,
            "item_index": 0,
            "item_type": "opportunity",
            "item_text": long_message,
            "original_message": "Заголовок дайджеста\n\n" + long_message + "\n\nДругая вакансия",
            "score": 90,
            "reasons_json": "[]",
            "risks_json": "[]",
            "unknowns_json": "[]",
            "fingerprint": "long-message",
            "approved_name": "Example",
            "message_link": "https://t.me/example/44",
            "sent_at": "2026-09-03T17:00:00+00:00",
        }

        item = build_project_inbox_items([row], run_id="run-1")[0]

        self.assertEqual(item.summary, long_message)
        self.assertGreater(len(item.summary), 700)


class InboxStorageTests(unittest.TestCase):
    def test_feedback_is_append_only_and_updates_current_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "radar.sqlite3") as storage:
                start = datetime(2026, 9, 3, tzinfo=timezone.utc)
                end = datetime(2026, 9, 4, tzinfo=timezone.utc)
                storage.begin_run("run-1", "test", start, end)
                item = inbox_item()
                storage.upsert_project_inbox_items([item])
                storage.record_feedback_decision(
                    FeedbackDecision(
                        candidate_id=item.candidate_id,
                        decision="need_info",
                        decided_at="2026-09-04T08:00:00+00:00",
                        note="Нужно уточнить загрузку",
                    )
                )
                storage.record_feedback_decision(
                    FeedbackDecision(
                        candidate_id=item.candidate_id,
                        decision="approve",
                        decided_at="2026-09-04T09:00:00+00:00",
                    )
                )

                self.assertEqual(
                    storage.project_inbox_items()[0]["review_status"], "approve"
                )
                self.assertEqual(len(storage.feedback_history(item.candidate_id)), 2)

                storage.upsert_project_inbox_items([item])
                self.assertEqual(
                    storage.project_inbox_items()[0]["review_status"], "approve"
                )

    def test_reject_requires_reason_and_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "radar.sqlite3") as storage:
                start = datetime(2026, 9, 3, tzinfo=timezone.utc)
                end = datetime(2026, 9, 4, tzinfo=timezone.utc)
                storage.begin_run("run-1", "test", start, end)
                item = inbox_item()
                storage.upsert_project_inbox_items([item])
                with self.assertRaisesRegex(ValueError, "reason_code"):
                    storage.record_feedback_decision(
                        FeedbackDecision(
                            candidate_id=item.candidate_id,
                            decision="reject",
                            decided_at="2026-09-04T08:00:00+00:00",
                        )
                    )


if __name__ == "__main__":
    unittest.main()
