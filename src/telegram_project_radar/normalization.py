"""Convert bounded TDLib message objects to the Radar schema."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping

from .models import Message


VISIBLE_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
STRUCTURED_CONTENT_TYPES = frozenset({
    'messageRichMessage', 'messagePoll', 'messagePollOptionAdded', 'messageChecklist',
})
_TEXT_FIELDS = ('title', 'subtitle', 'header', 'subheader', 'kicker', 'author',
                'label', 'text', 'footer', 'expression', 'alternative_text',
                'cover', 'items', 'blocks', 'cells', 'articles', 'description',
                'caption', 'credit', 'button', 'buttons')


def _text_tree(node: object) -> str:
    """Read displayed text fields, never binary/media metadata or vote counts."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return '\n'.join(filter(None, (_text_tree(n) for n in node)))
    if not isinstance(node, Mapping):
        return ''
    kind = node.get('@type', '')
    if kind == 'pageBlockUnsupported':
        raise ValueError('Unsupported text block requires content recovery')
    if kind == 'richTexts':
        return ''.join(_text_tree(n) for n in node.get('texts', []))
    if kind == 'richTextDiff':
        return '[Было] ' + _text_tree(node.get('old_text')) + '\n[Стало] ' + _text_tree(node.get('text'))
    return '\n'.join(filter(None, (_text_tree(node.get(k)) for k in _TEXT_FIELDS)))


def _structured_nodes(content: Mapping) -> list:
    kind = content.get('@type')
    if kind == 'messageRichMessage':
        message = content.get('message') or {}
        if message.get('is_full') is False:
            raise ValueError('Incomplete rich message requires content recovery')
        return message.get('blocks', [])
    if kind == 'messagePoll':
        poll = content.get('poll') or {}
        return [poll.get('question'), content.get('description'),
                *[o.get('text') for o in poll.get('options', [])],
                (poll.get('type') or {}).get('explanation')]
    if kind == 'messageChecklist':
        checklist = content.get('list') or {}
        return [checklist.get('title'), *[t.get('text') for t in checklist.get('tasks', [])]]
    return [content.get('text')]


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(normalized.split())


def approved_title_matches(approved_name: str, actual_title: str) -> bool:
    approved = normalize_title(approved_name)
    actual = normalize_title(actual_title)
    if actual == approved:
        return True
    approved_words = re.findall(r"[\w-]+", approved, flags=re.UNICODE)
    actual_words = set(re.findall(r"[\w-]+", actual, flags=re.UNICODE))
    return bool(approved_words) and all(word in actual_words for word in approved_words)


def extract_text(content: object) -> tuple[str | None, str]:
    if not isinstance(content, Mapping):
        return None, "unknown"
    content_type = str(content.get("@type", "unknown"))
    if content_type in STRUCTURED_CONTENT_TYPES:
        text = _text_tree(_structured_nodes(content))
        return (text if text.strip() else None), content_type
    formatted = content.get("text") if content_type == "messageText" else content.get("caption")
    if not isinstance(formatted, Mapping):
        return None, content_type
    text = formatted.get("text")
    return (text if isinstance(text, str) and text.strip() else None), content_type


def extract_embedded_urls(content: object) -> tuple[str, ...]:
    if not isinstance(content, Mapping):
        return ()
    content_type = str(content.get("@type", "unknown"))
    if content_type in STRUCTURED_CONTENT_TYPES:
        found = []
        def visit(node):
            if isinstance(node, list):
                for child in node:
                    visit(child)
            elif isinstance(node, Mapping):
                if node.get('@type') == 'formattedText':
                    found.extend(extract_embedded_urls({'@type': 'messageText', 'text': node}))
                    return
                url = node.get('url')
                if isinstance(url, str) and url.startswith(('https://', 'http://')):
                    found.append(html.unescape(url))
                for key in (*_TEXT_FIELDS, 'texts', 'old_text'):
                    visit(node.get(key))
            elif isinstance(node, str):
                found.extend(html.unescape(m.group(0)).rstrip('.,;:!?)\"]}') for m in VISIBLE_URL_RE.finditer(node))
        visit(_structured_nodes(content))
        return tuple(dict.fromkeys(found))
    formatted = content.get("text") if content_type == "messageText" else content.get("caption")
    if not isinstance(formatted, Mapping):
        return ()

    found: list[str] = []
    entities = formatted.get("entities")
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, Mapping):
                continue
            entity_type = entity.get("type")
            if not isinstance(entity_type, Mapping):
                continue
            if entity_type.get("@type") != "textEntityTypeTextUrl":
                continue
            url = entity_type.get("url")
            if isinstance(url, str) and url.strip():
                found.append(html.unescape(url.strip()))

    text = formatted.get("text")
    if isinstance(text, str):
        found.extend(
            html.unescape(match.group(0)).rstrip(".,;:!?)\"]}")
            for match in VISIBLE_URL_RE.finditer(text)
        )
    return tuple(dict.fromkeys(found))


def normalize_message(raw: Mapping[str, Any]) -> Message | None:
    chat_id = int(raw.get("chat_id", 0))
    message_id = int(raw.get("id", 0))
    timestamp = int(raw.get("date", 0))
    if chat_id == 0 or message_id <= 0 or timestamp <= 0:
        return None

    content = raw.get("content")
    text, content_type = extract_text(content)
    embedded_urls = extract_embedded_urls(content)
    sender = raw.get("sender_id")
    sender_id = None
    if isinstance(sender, Mapping):
        if sender.get("@type") == "messageSenderUser":
            sender_id = int(sender.get("user_id", 0)) or None
        elif sender.get("@type") == "messageSenderChat":
            sender_id = int(sender.get("chat_id", 0)) or None

    reply = raw.get("reply_to")
    reply_chat_id = None
    reply_message_id = None
    if isinstance(reply, Mapping) and reply.get("@type") == "messageReplyToMessage":
        reply_chat_id = int(reply.get("chat_id", chat_id)) or chat_id
        reply_message_id = int(reply.get("message_id", 0)) or None
    elif int(raw.get("reply_to_message_id", 0) or 0) > 0:
        reply_chat_id = chat_id
        reply_message_id = int(raw["reply_to_message_id"])

    edit_timestamp = int(raw.get("edit_date", 0) or 0)
    return Message(
        chat_id=chat_id,
        message_id=message_id,
        sent_at=datetime.fromtimestamp(timestamp, timezone.utc),
        edit_date=(
            datetime.fromtimestamp(edit_timestamp, timezone.utc)
            if edit_timestamp > 0
            else None
        ),
        sender_id=sender_id,
        content_type=content_type,
        text=text,
        reply_to_chat_id=reply_chat_id,
        reply_to_message_id=reply_message_id,
        message_thread_id=int(raw.get("message_thread_id", 0) or 0) or None,
        media_album_id=int(raw.get("media_album_id", 0) or 0) or None,
        embedded_urls=embedded_urls,
    )


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
