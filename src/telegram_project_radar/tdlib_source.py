"""Read-only adapter over the already-authorized local TDLib runtime."""

from __future__ import annotations

import sys
import queue
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import Message, Source, SourceCollection
from .normalization import approved_title_matches, normalize_message
from .storage import Storage


class TelegramRuntimeError(RuntimeError):
    pass


class TdlibSource:
    """Own exactly one existing TelegramSearchMCP TDLib profile while open."""

    def __init__(self, source_dir: Path, profile: str = "default"):
        if not source_dir.is_dir():
            raise TelegramRuntimeError(f"Telegram runtime source not found: {source_dir}")
        source_text = str(source_dir)
        if source_text not in sys.path:
            sys.path.insert(0, source_text)

        from telegram_search_mcp.policy import Policy
        from telegram_search_mcp.tdjson import TdApi
        from telegram_search_mcp.tdlib_backend import TdlibSession
        from .config import PRIVATE_ROOT
        from .telegram_runtime import load_runtime, pinned_session

        self._TdApi = TdApi
        runtime = load_runtime(PRIVATE_ROOT / 'telegram-runtime.json')
        policy = Policy.load(profile)
        self._session = (pinned_session(policy, profile, runtime) if runtime
                         else TdlibSession(policy, profile))
        try:
            self._session.require_ready(timeout=45.0)
        except BaseException:
            self._session.close()
            raise

    def close(self) -> None:
        self._session.close()

    def wait_connected(self, timeout: float = 35.0) -> None:
        """Authorization alone may expose stale cached history at startup."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                update = self._session.client.updates.get(
                    timeout=min(0.5, max(0, deadline - time.monotonic()))
                )
            except queue.Empty:
                continue
            if (update.get('@type') == 'updateConnectionState'
                    and update.get('state', {}).get('@type') == 'connectionStateReady'):
                return
        raise TelegramRuntimeError('Telegram connection is not ready; no morning delivery made')

    def __enter__(self) -> "TdlibSource":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _request(self, request: dict[str, Any], timeout: float = 45.0) -> dict[str, Any]:
        try:
            response = self._session.request(request, timeout=timeout)
        except RuntimeError as exc:
            # The current TDLib client raises TdlibError before returning an error
            # object. Normalize it so pagination can recognize the documented 404.
            if str(exc).startswith('TDLib error '):
                raise TelegramRuntimeError(str(exc)) from exc
            raise
        if response.get("@type") == "error":
            code = int(response.get("code", 0))
            message = str(response.get("message", "TDLib request failed"))
            raise TelegramRuntimeError(f"TDLib error {code}: {message}")
        messages = ([response] if response.get('@type') == 'message'
                    else response.get('messages', []))
        for raw in messages:
            content = raw.get('content') or {}
            if (content.get('@type') == 'messageRichMessage'
                    and (content.get('message') or {}).get('is_full') is False):
                full = self._request({'@type': 'getFullRichMessage', 'chat_id': raw['chat_id'],
                                      'message_id': raw['id']}, timeout=timeout)
                if full.get('@type') != 'richMessage' or full.get('is_full') is not True:
                    raise TelegramRuntimeError('Full rich message was not returned')
                raw['content'] = {**content, 'message': full}
        return response

    def resolve_exact_source(self, approved_name: str) -> Source:
        chat_ids: set[int] = set()
        queries = [approved_name]
        queries.extend(word for word in approved_name.split() if len(word) >= 4)
        for query in dict.fromkeys(queries):
            for request_type in ("searchChats", "searchChatsOnServer"):
                try:
                    response = self._request(
                        {"@type": request_type, "query": query, "limit": 100}
                    )
                except TelegramRuntimeError:
                    if request_type == "searchChatsOnServer":
                        continue
                    raise
                chat_ids.update(int(value) for value in response.get("chat_ids", ()))

        exact: dict[int, Source] = {}
        for chat_id in chat_ids:
            chat = self._request(self._TdApi.get_chat(chat_id), timeout=15.0)
            title = str(chat.get("title", ""))
            if not approved_title_matches(approved_name, title):
                continue
            chat_type = str((chat.get("type") or {}).get("@type", "unknown"))
            if chat_type == "chatTypeSecret":
                continue
            exact[chat_id] = Source(chat_id=chat_id, title=title, chat_type=chat_type)

        if not exact:
            raise TelegramRuntimeError(
                f"No exact Telegram chat title matched approved source: {approved_name}"
            )
        if len(exact) > 1:
            raise TelegramRuntimeError(
                f"Approved source title is ambiguous ({len(exact)} exact matches): {approved_name}"
            )
        return next(iter(exact.values()))

    def collect_chat(
        self,
        *,
        storage: Storage,
        run_id: str,
        source: Source,
        start: datetime,
        end: datetime,
    ) -> SourceCollection:
        from_message_id = 0
        visited_boundaries: set[int] = set()
        pages = 0
        messages_seen = 0
        interval_count = 0
        text_count = 0
        ended_before_start = False
        history_exhausted = False
        first_at: datetime | None = None
        last_at: datetime | None = None

        while True:
            if from_message_id in visited_boundaries:
                raise TelegramRuntimeError("TDLib history pagination stopped making progress")
            visited_boundaries.add(from_message_id)
            response = self._request(
                self._TdApi.get_chat_history(
                    source.chat_id,
                    from_message_id=from_message_id,
                    offset=0,
                    limit=100,
                    only_local=False,
                ),
                timeout=60.0,
            )
            raw_messages = [
                item for item in response.get("messages", ()) if isinstance(item, Mapping)
            ]
            pages += 1
            if not raw_messages:
                history_exhausted = True
                break

            messages_seen += len(raw_messages)
            normalized = [normalize_message(item) for item in raw_messages]
            valid = [item for item in normalized if item is not None]
            in_interval = [item for item in valid if start <= item.sent_at < end]
            stored, stored_text = storage.store_page(run_id, in_interval)
            interval_count += stored
            text_count += stored_text
            for item in in_interval:
                first_at = item.sent_at if first_at is None else min(first_at, item.sent_at)
                last_at = item.sent_at if last_at is None else max(last_at, item.sent_at)

            dated = [item for item in valid if item.sent_at < start]
            if dated:
                ended_before_start = True
                break

            oldest = min(valid, key=lambda item: (item.sent_at, item.message_id), default=None)
            if oldest is None:
                raise TelegramRuntimeError("TDLib returned a page without usable message identifiers")
            from_message_id = oldest.message_id

        return SourceCollection(
            chat_id=source.chat_id,
            pages=pages,
            messages_seen=messages_seen,
            messages_in_interval=interval_count,
            text_messages_in_interval=text_count,
            ended_before_start=ended_before_start,
            history_exhausted=history_exhausted,
            first_message_at=first_at,
            last_message_at=last_at,
        )

    def get_message_link(self, chat_id: int, message_id: int) -> str | None:
        try:
            response = self._request(
                {
                    "@type": "getMessageLink",
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "media_timestamp": 0,
                    "for_album": False,
                    "in_message_thread": False,
                },
                timeout=15.0,
            )
        except TelegramRuntimeError:
            return None
        link = response.get("link")
        return str(link) if isinstance(link, str) and link.startswith("https://") else None

    def get_message(self, chat_id: int, message_id: int) -> Message | None:
        try:
            response = self._request(
                self._TdApi.get_message(chat_id, message_id), timeout=15.0
            )
        except TelegramRuntimeError:
            return None
        return normalize_message(response)

    def get_message_author(self, chat_id: int, message_id: int) -> dict | None:
        """Read the original author identity; never retain phone/status/private profile data."""
        try:
            message = self._request(self._TdApi.get_message(chat_id, message_id), timeout=15.0)
            if message.get('chat_id') != chat_id or message.get('id') != message_id or message.get('forward_info'):
                return None  # A forwarder is not proof of the author's identity.
            sender = message.get('sender_id') or {}
            if sender.get('@type') != 'messageSenderUser' or not sender.get('user_id'):
                return None
            user = self._request({'@type': 'getUser', 'user_id': sender['user_id']}, timeout=15.0)
            if user.get('id') != sender['user_id']:
                return None
            return {'user_id': user['id'], 'first_name': user.get('first_name', ''), 'last_name': user.get('last_name', ''),
                    'usernames': (user.get('usernames') or {}).get('active_usernames', [])}
        except TelegramRuntimeError:
            return None
