from __future__ import annotations

import io
import tempfile
import unittest
from email.message import Message as Headers
from unittest.mock import patch
from urllib.response import addinfourl
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

from telegram_project_radar.detail_enrichment import (
    DetailFetcher,
    DetailFetchResult,
    enrich_run_details,
    html_to_text,
    is_allowed_detail_url,
)
from telegram_project_radar.models import Message, Source
from telegram_project_radar.storage import Storage


DETAIL_URL = "https://app.rvc.global/vacancy/view/devops-test"


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        final_url: str = DETAIL_URL,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self._body = io.BytesIO(body)
        self._final_url = final_url
        self.status = 200
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def geturl(self) -> str:
        return self._final_url

    def getcode(self) -> int:
        return self.status

    def read(self, amount: int) -> bytes:
        return self._body.read(amount)


class FakeTelegram:
    def __init__(self, message: Message) -> None:
        self.message = message
        self.calls = 0

    def get_message(self, chat_id: int, message_id: int) -> Message:
        self.calls += 1
        return self.message


class FakeFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, url: str) -> DetailFetchResult:
        self.calls += 1
        return DetailFetchResult(
            url=url,
            final_url=url,
            host="app.rvc.global",
            status="complete",
            http_status=200,
            content_type="text/html; charset=utf-8",
            content_text="Employment type: Full Time. Office in Moscow.",
            error=None,
        )


class DetailEnrichmentTests(unittest.TestCase):
    def test_allowlist_requires_https_exact_host_and_vacancy_path(self) -> None:
        self.assertTrue(is_allowed_detail_url(DETAIL_URL))
        self.assertFalse(is_allowed_detail_url("http://app.rvc.global/vacancy/view/test"))
        self.assertFalse(is_allowed_detail_url("https://app.rvc.global.evil.test/vacancy/view/test"))
        self.assertFalse(is_allowed_detail_url("https://app.rvc.global/profile/test"))

    def test_html_parser_ignores_scripts_and_keeps_visible_vacancy_text(self) -> None:
        text = html_to_text(
            "<html><body><nav>Moscow site-wide filter</nav>"
            "<main><h1>DevOps Engineer</h1>"
            "<p>Employment type: Full Time</p></main>"
            "<footer>Remote jobs directory</footer>"
            "<script>Moscow hidden script noise</script></body></html>"
        )
        self.assertIn("DevOps Engineer", text)
        self.assertIn("Employment type: Full Time", text)
        self.assertNotIn("hidden script noise", text)
        self.assertNotIn("site-wide filter", text)
        self.assertNotIn("jobs directory", text)

    def test_fetcher_rejects_redirect_outside_allowlist(self) -> None:
        fetcher = DetailFetcher(
            transport=lambda request, timeout: FakeResponse(
                b"<main>A sufficiently long vacancy description for testing.</main>",
                final_url="https://evil.test/vacancy/view/test",
            )
        )
        self.assertEqual(fetcher.fetch(DETAIL_URL).status, "failed")

    def test_fetcher_marks_missing_page_as_permanent_not_found(self) -> None:
        def missing(request, timeout):
            raise HTTPError(DETAIL_URL, 404, "Not Found", {}, None)

        result = DetailFetcher(transport=missing).fetch(DETAIL_URL)
        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.http_status, 404)

    def test_default_transport_checks_each_redirect_before_requesting_it(self) -> None:
        for target in ["http://127.0.0.1:1234/private", "https://outside.example/vacancy/view/x",
                       "https://app.rvc.global/profile/x", "https://app.rvc.global:444/vacancy/view/x",
                       DETAIL_URL + "-second"]:
            with self.subTest(target=target):
                visited = []
                def respond(handler, request):
                    visited.append(request.full_url)
                    headers = Headers()
                    if request.full_url == DETAIL_URL:
                        headers['Location'] = target
                        response = addinfourl(io.BytesIO(b''), headers, request.full_url, 302)
                        response.msg = 'Found'
                    else:
                        headers['Content-Type'] = 'text/html'
                        response = addinfourl(io.BytesIO(b'<main>A sufficiently long synthetic vacancy description.</main>'),
                                              headers, request.full_url, 200)
                        response.msg = 'OK'
                    return response
                with patch('urllib.request.HTTPSHandler.https_open', respond), patch('urllib.request.HTTPHandler.http_open', respond):
                    result = DetailFetcher().fetch(DETAIL_URL)
                allowed = is_allowed_detail_url(target)
                self.assertEqual(visited, [DETAIL_URL, target] if allowed else [DETAIL_URL])
                self.assertEqual(result.status, 'complete' if allowed else 'failed')

    def test_enrichment_backfills_hidden_url_and_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "radar.sqlite3"
            with Storage(database) as storage:
                start = datetime(2026, 8, 1, tzinfo=timezone.utc)
                end = datetime(2026, 9, 1, tzinfo=timezone.utc)
                storage.upsert_source(
                    "Example Remote Jobs",
                    Source(-1001, "RVC", "chatTypeSupergroup"),
                )
                storage.begin_run("test", "test", start, end)
                short_text = (
                    "DevOps Engineer\nCompany | B2B SaaS\nWorldwide Remote\n"
                    "Required languages: English\nSkills: terraform"
                )
                stored_message = Message(
                    -1001, 1048576, start, None, None, "messageText", short_text,
                    None, None, None, None,
                )
                storage.store_page("test", [stored_message])
                storage.connection.execute(
                    "UPDATE messages SET url_entities_scanned_at = NULL"
                )
                storage.commit()
                telegram_message = Message(
                    -1001, 1048576, start, None, None, "messageText", short_text,
                    None, None, None, None, (DETAIL_URL,),
                )
                telegram = FakeTelegram(telegram_message)
                fetcher = FakeFetcher()

                first = enrich_run_details(
                    storage,
                    run_id="test",
                    rows=storage.messages_for_run("test"),
                    telegram=telegram,
                    fetcher=fetcher,
                )
                second = enrich_run_details(
                    storage,
                    run_id="test",
                    rows=storage.messages_for_run("test"),
                    telegram=telegram,
                    fetcher=fetcher,
                )

                self.assertEqual(first.fetched, 1)
                self.assertEqual(second.cache_hits, 1)
                self.assertEqual(telegram.calls, 1)
                self.assertEqual(fetcher.calls, 1)
                self.assertIn(
                    "Full Time", storage.messages_for_run("test")[0]["detail_text"]
                )


if __name__ == "__main__":
    unittest.main()
