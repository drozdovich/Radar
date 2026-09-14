from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from telegram_project_radar.cli import build_parser, tune_selection
from telegram_project_radar.digest_details import load_digest_details, text_hash
from telegram_project_radar.models import Message, Source
from telegram_project_radar.selection_v2 import build_candidate_items, split_digest
from telegram_project_radar.storage import Storage


class DigestDetailsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = "Подборка вакансий\n\n" + "\n\n".join(
            ["DevOps engineer — Kubernetes hosting network infrastructure contract remote",
             "Senior SDR — Lead generation B2B SaaS outbound contract remote"]
            + [f"Graphic designer {i} — иллюстрации, оформление презентаций и баннеров для рекламных кампаний"
               for i in range(28)]
        )
        self.rows = [{"chat_id": 1, "message_id": 2, "text": self.source}]
        self.row = {
            "chat_id": 1, "message_id": 2, "item_index": 1,
            "source_text_sha256": text_hash(self.source),
            "block_text_sha256": text_hash(split_digest(self.source)[0]),
            "url": "https://docs.google.com/document/d/test/edit",
            "text": "Формат: гибрид в Калуге / удалённо в других городах",
        }

    def load(self, rows: list[dict]):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "details.json"
            path.write_text(json.dumps({"schema_version": 1, "details": rows}))
            return load_digest_details(path)

    def test_description_changes_only_its_own_block_and_preserves_fingerprint(self) -> None:
        before = build_candidate_items(self.rows)
        after = build_candidate_items(self.rows, digest_details=self.load([self.row]))
        changed = [(a, b) for a, b in zip(before, after) if a.evaluation != b.evaluation]
        self.assertEqual(len(changed), 1)
        a, b = changed[0]
        self.assertEqual(a.item_index, 1)
        self.assertEqual(a.evaluation.decision, "include")
        self.assertEqual(b.evaluation.decision, "exclude")
        self.assertEqual(a.evaluation.fingerprint, b.evaluation.fingerprint)
        self.assertIn("полном описании", b.evaluation.reasons[0])

    def test_whole_digest_detail_cannot_leak_to_every_position(self) -> None:
        rows = [{**self.rows[0], "detail_text": "Full time, onsite in Moscow"}]
        self.assertEqual(build_candidate_items(rows), build_candidate_items(self.rows))

    def test_identical_text_in_another_block_does_not_receive_description(self) -> None:
        source = self.source + "\n\n" + split_digest(self.source)[0]
        detail = {**self.row, "source_text_sha256": text_hash(source)}
        items = build_candidate_items([{**self.rows[0], "text": source}], digest_details=self.load([detail]))
        same_text = [i for i in items if text_hash(i.text) == self.row["block_text_sha256"]]
        self.assertEqual(len(same_text), 2)
        self.assertEqual(same_text[0].evaluation.decision, "exclude")
        self.assertEqual(same_text[1].evaluation.decision, "include")

    def test_changed_or_missing_source_fails_before_selection(self) -> None:
        for rows in ([], [{**self.rows[0], "text": self.source + " edited"}]):
            with self.subTest(rows=len(rows)), self.assertRaisesRegex(ValueError, "source is missing or changed"):
                build_candidate_items(rows, digest_details=self.load([self.row]))

    def test_unmatched_block_is_not_silently_ignored(self) -> None:
        detail = {**self.row, "block_text_sha256": text_hash("old block")}
        with self.assertRaisesRegex(ValueError, "exact current block"):
            build_candidate_items(self.rows, digest_details=self.load([detail]))

    def test_ambiguous_or_invalid_input_is_rejected(self) -> None:
        cases = [
            [self.row, self.row],
            [{**self.row, "source_text_sha256": "wrong"}],
            [{**self.row, "chat_id": "1"}],
            [{**self.row, "url": "file:///tmp/detail.txt"}],
            [{**self.row, "text": ""}],
        ]
        for rows in cases:
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                self.load(rows)

    def test_tune_uses_verified_description_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            database = base / "radar.sqlite3"
            description = base / "details.json"
            description.write_text(json.dumps({"schema_version": 1, "details": [self.row]}))
            start = datetime(2026, 9, 8, tzinfo=timezone.utc)
            end = datetime(2026, 9, 9, tzinfo=timezone.utc)
            with Storage(database) as storage:
                storage.upsert_source("test", Source(1, "test", "chatTypeSupergroup"))
                storage.begin_run("test", "test", start, end)
                storage.store_page("test", [Message(
                    1, 2, start, None, None, "messageText", self.source,
                    None, None, None, None,
                )])
            args = build_parser().parse_args([
                "tune", "--run-id", "test", "--database", str(database),
                "--digest-details", str(description), "--skip-detail-enrichment",
                "--skip-feedback", "--reports-dir", str(base / "reports"),
            ])
            telegram = MagicMock()
            telegram.get_message_link.return_value = None
            with patch("telegram_project_radar.cli.TdlibSource") as source, redirect_stdout(StringIO()):
                source.return_value.__enter__.return_value = telegram
                self.assertEqual(tune_selection(args), 0)
            telegram.get_message.assert_not_called()
            with Storage(database) as storage:
                row = storage.connection.execute(
                    "SELECT decision FROM candidate_items WHERE run_id='test' AND item_index=1"
                ).fetchone()
                self.assertEqual(row["decision"], "exclude")


if __name__ == "__main__":
    unittest.main()
