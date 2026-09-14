"""Read allowlisted vacancy pages linked directly from Telegram messages."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import Message
from .selection_v2 import needs_detail_enrichment
from .storage import Storage


ALLOWED_DETAIL_HOSTS = {"app.rvc.global": ("/vacancy/view/",)}
MAX_DETAIL_BYTES = 2_000_000
MAX_DETAIL_TEXT = 500_000
DETAIL_PARSER_VERSION = "rvc-main-v1"


class TelegramMessageReader(Protocol):
    def get_message(self, chat_id: int, message_id: int) -> Message | None: ...


@dataclass(frozen=True, slots=True)
class DetailFetchResult:
    url: str
    final_url: str | None
    host: str
    status: str
    http_status: int | None
    content_type: str | None
    content_text: str | None
    error: str | None
    parser_version: str = DETAIL_PARSER_VERSION


@dataclass(slots=True)
class DetailEnrichmentStats:
    targeted_messages: int = 0
    telegram_messages_read: int = 0
    messages_without_allowed_url: int = 0
    allowed_urls: int = 0
    cache_hits: int = 0
    cached_failures: int = 0
    fetched: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {key: int(value) for key, value in asdict(self).items()}


def is_allowed_detail_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    prefixes = ALLOWED_DETAIL_HOSTS.get(host)
    return bool(prefixes and any(parsed.path.startswith(prefix) for prefix in prefixes))


class _VisibleTextParser(HTMLParser):
    _IGNORED = {"script", "style", "noscript", "svg", "template"}
    _BLOCKS = {
        "article", "aside", "br", "div", "footer", "h1", "h2", "h3", "h4",
        "header", "li", "main", "nav", "p", "section", "table", "td", "th", "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._main_depth = 0
        self._parts: list[str] = []
        self._main_parts: list[str] = []

    def _append(self, value: str) -> None:
        self._parts.append(value)
        if self._main_depth:
            self._main_parts.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._IGNORED:
            self._ignored_depth += 1
        elif self._ignored_depth == 0:
            if tag == "main":
                self._main_depth += 1
            if tag in self._BLOCKS:
                self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._ignored_depth == 0 and tag in self._BLOCKS:
            self._append("\n")
            if tag == "main" and self._main_depth:
                self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0 and data.strip():
            self._append(data)

    def text(self) -> str:
        selected = self._main_parts if self._main_parts else self._parts
        compact_lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in "".join(selected).splitlines()
        ]
        lines: list[str] = []
        for line in compact_lines:
            if line and (not lines or line != lines[-1]):
                lines.append(line)
        return "\n".join(lines)[:MAX_DETAIL_TEXT]


def html_to_text(value: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(value)
    parser.close()
    return parser.text()


class AllowlistedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_detail_url(newurl):
            raise ValueError("redirect left the detail allowlist")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class DetailFetcher:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        transport: Callable[..., object] | None = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport if transport is not None else build_opener(AllowlistedRedirectHandler()).open

    def fetch(self, url: str) -> DetailFetchResult:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if not is_allowed_detail_url(url):
            return DetailFetchResult(
                url, None, host, "rejected", None, None, None, "URL is not allowlisted"
            )

        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "TelegramProjectRadar/0.9 (+local-read-only-enricher)",
            },
            method="GET",
        )
        try:
            with self.transport(request, timeout=self.timeout) as response:
                final_url = str(response.geturl())
                if not is_allowed_detail_url(final_url):
                    raise ValueError("redirect left the detail allowlist")
                response_status = getattr(response, "status", None)
                http_status = int(
                    response_status if response_status is not None else response.getcode()
                )
                content_type = str(response.headers.get("Content-Type", ""))
                if http_status != 200:
                    raise ValueError(f"unexpected HTTP status {http_status}")
                if "text/html" not in content_type.casefold():
                    raise ValueError("response is not HTML")
                payload = response.read(MAX_DETAIL_BYTES + 1)
                if len(payload) > MAX_DETAIL_BYTES:
                    raise ValueError("response exceeds the size limit")
                charset_match = re.search(r"charset=([\w.-]+)", content_type, re.IGNORECASE)
                charset = charset_match.group(1) if charset_match else "utf-8"
                text = html_to_text(payload.decode(charset, errors="replace"))
                if len(text) < 40:
                    raise ValueError("page contains too little visible text")
                return DetailFetchResult(
                    url, final_url, host, "complete", http_status, content_type, text, None
                )
        except HTTPError as exc:
            http_status = int(exc.code)
            status = "not_found" if http_status in {404, 410} else "failed"
            result = DetailFetchResult(
                url,
                str(exc.geturl()) if exc.geturl() else None,
                host,
                status,
                http_status,
                str(exc.headers.get("Content-Type", "")) if exc.headers else None,
                None,
                f"HTTP {http_status}: {exc.reason}"[:500],
            )
            exc.close()
            return result
        except Exception as exc:
            return DetailFetchResult(
                url, None, host, "failed", None, None, None, str(exc)[:500]
            )


def enrich_run_details(
    storage: Storage,
    *,
    run_id: str,
    rows: list[Mapping[str, object]],
    telegram: TelegramMessageReader,
    fetcher: DetailFetcher | None = None,
) -> DetailEnrichmentStats:
    fetcher = fetcher or DetailFetcher()
    stats = DetailEnrichmentStats()
    targets = {
        (int(row["chat_id"]), int(row["message_id"]))
        for row in rows
        if needs_detail_enrichment(str(row["text"]))
    }
    stats.targeted_messages = len(targets)

    for chat_id, message_id in sorted(targets):
        if not storage.message_urls_scanned(chat_id, message_id):
            message = telegram.get_message(chat_id, message_id)
            stats.telegram_messages_read += 1
            if message is None:
                stats.messages_without_allowed_url += 1
                continue
            storage.replace_message_urls(chat_id, message_id, message.embedded_urls)

        allowed_urls = tuple(
            url for url in storage.message_urls(chat_id, message_id)
            if is_allowed_detail_url(url)
        )
        if not allowed_urls:
            stats.messages_without_allowed_url += 1
            continue

        stats.allowed_urls += len(allowed_urls)
        for url in allowed_urls:
            cached = storage.message_detail(url)
            if (
                cached is not None
                and cached["status"] == "complete"
                and cached["parser_version"] == DETAIL_PARSER_VERSION
            ):
                stats.cache_hits += 1
                continue
            if (
                cached is not None
                and cached["status"] == "not_found"
                and cached["parser_version"] == DETAIL_PARSER_VERSION
            ):
                stats.cached_failures += 1
                continue
            result = fetcher.fetch(url)
            storage.store_message_detail(
                url=result.url,
                final_url=result.final_url,
                host=result.host,
                status=result.status,
                http_status=result.http_status,
                content_type=result.content_type,
                content_text=result.content_text,
                error=result.error,
                parser_version=result.parser_version,
            )
            if result.status == "complete":
                stats.fetched += 1
            else:
                stats.failed += 1
    return stats
