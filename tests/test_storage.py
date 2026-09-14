from __future__ import annotations

import tempfile
import unittest
import stat
from datetime import datetime, timezone
from pathlib import Path

from telegram_project_radar.models import CandidateItem, Evaluation, Message, Source, SourceCollection
from telegram_project_radar import __version__
from telegram_project_radar.inbox import build_project_inbox_items, project_inbox_payload
from telegram_project_radar.storage import Storage


class StorageTests(unittest.TestCase):
    def test_repeated_message_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "radar.sqlite3") as storage:
                mode = stat.S_IMODE(storage.path.stat().st_mode)
                self.assertEqual(mode, 0o600)
                source = Source(-1001, "Example Builders", "chatTypeSupergroup")
                storage.upsert_source("Example Builders", source)
                start = datetime(2026, 8, 1, tzinfo=timezone.utc)
                end = datetime(2026, 9, 1, tzinfo=timezone.utc)
                storage.begin_run("test", "test", start, end)
                message = Message(
                    chat_id=-1001,
                    message_id=1048576,
                    sent_at=start,
                    edit_date=None,
                    sender_id=1,
                    content_type="messageText",
                    text="Need freelance DevOps",
                    reply_to_chat_id=None,
                    reply_to_message_id=None,
                    message_thread_id=None,
                    media_album_id=None,
                    embedded_urls=("https://app.rvc.global/vacancy/view/test",),
                )
                storage.store_page("test", [message, message])
                storage.replace_candidate_items("test", [CandidateItem(-1001, 1048576, 0,
                    "opportunity", message.text, Evaluation(70, "review", (), (), (), "fingerprint"))])
                candidate = storage.report_candidate_items("test")[0]
                self.assertEqual(candidate["selector_version"], __version__)
                item = build_project_inbox_items([candidate], run_id="test")[0]
                self.assertEqual(item.selector_version, __version__)
                self.assertEqual(project_inbox_payload([item])["learning_snapshots"][0]["decision"], "review")
                storage.connection.execute("UPDATE candidate_items SET selector_version=NULL")
                legacy_item = build_project_inbox_items(storage.report_candidate_items("test"), run_id="test")[0]
                self.assertEqual(legacy_item.selector_version, "unknown")
                self.assertEqual(storage.message_keys("test", -1001), {(-1001, 1048576)})
                self.assertTrue(storage.message_urls_scanned(-1001, 1048576))
                self.assertEqual(
                    storage.message_urls(-1001, 1048576),
                    ("https://app.rvc.global/vacancy/view/test",),
                )

                storage.store_message_detail(
                    url="https://app.rvc.global/vacancy/view/test",
                    final_url="https://app.rvc.global/vacancy/view/test",
                    host="app.rvc.global",
                    status="complete",
                    http_status=200,
                    content_type="text/html; charset=utf-8",
                    content_text="Full Time role in Moscow",
                    error=None,
                    parser_version="test-v1",
                )
                stored = storage.messages_for_run("test")[0]
                self.assertEqual(stored["detail_text"], "Full Time role in Moscow")


if __name__ == "__main__":
    unittest.main()
