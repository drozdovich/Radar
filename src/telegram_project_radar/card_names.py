"""Names for personal cards, kept separate from the original message."""
from __future__ import annotations

import re

GREETING = re.compile(r'^(?:всем\s+)?(?:привет|добрый\s+(?:день|вечер)|доброе\s+утро|hello|hi\b)', re.I)


def first_content_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')), '')


def needs_author_name(kind: str, text: str) -> bool:
    return kind == 'person' or (kind == 'opportunity' and bool(GREETING.match(first_content_line(text))))


def self_introduction(text: str) -> str:
    match = re.search(r'(?:[Мм]еня зовут|\b[Яя])\s+([А-ЯЁA-Z][а-яёa-z]{1,30})\b', text)
    return match[1] if match else ''


def author_name(author: dict | None, text: str) -> str:
    author = author or {}
    first = ' '.join(str(author.get('first_name') or '').split())
    last = ' '.join(str(author.get('last_name') or '').split())
    if len(first) > 1:
        return ' '.join(part for part in (first, last) if part)[:120]
    usernames = author.get('usernames') or []
    nick = next((n for n in usernames if isinstance(n, str) and re.fullmatch(r'[A-Za-z0-9_]{5,32}', n)), '')
    if nick:
        return '@' + nick
    return self_introduction(text) or ' '.join(part for part in (first, last) if part) or 'Автор не указан'


def apply_author_name(item: dict, author: dict | None) -> None:
    if not needs_author_name(item['type'], item['summary']):
        return
    item['title'] = author_name(author, item['summary'])
    if not author:
        item['unknowns'].append('Имя автора Telegram не удалось проверить; фамилия и ник не додумываются.')
        return
    profile = ' '.join(str(author.get(k) or '').strip() for k in ('first_name', 'last_name')).strip()
    if profile:
        item['known_conditions'].append(f'Автор в Telegram: {profile}. Имя профиля проверено по исходному сообщению.')
    if not author.get('last_name') or len(str(author.get('last_name', ''))) <= 1:
        item['unknowns'].append('Полная фамилия автора в Telegram не указана.')


def person_profile(candidate_id: str, author: dict | None, text: str, source_url: str) -> dict:
    """Keep verified display-name parts separate from the editorial card title."""
    author = author or {}
    clean = lambda value: ' '.join(str(value or '').split())
    first = re.split(r'\s*[|·]\s*|\s+[—–]\s+', clean(author.get('first_name')))[0]
    last = clean(author.get('last_name'))
    # A domain or a promotional mixed-case handle is not a surname.
    valid = lambda value: bool(re.fullmatch(r"[A-Za-zА-Яа-яЁёÀ-ž][A-Za-zА-Яа-яЁёÀ-ž .’'-]{0,79}", value))
    if not valid(first):
        first = ''
    if not valid(last) or re.search(r'\.[A-Za-z]{2,}|[a-z][A-Z]', last):
        last = ''
    if not first:
        first = self_introduction(text)
        last = ''
    if not first:
        first = next(('@'+n for n in author.get('usernames', []) if re.fullmatch(r'[A-Za-z0-9_]{5,32}', n)), '')
    return {'schema_version': 'person-profile-v1', 'candidate_id': candidate_id,
            'first_name': first, 'last_name': last, 'source_url': source_url,
            'source': 'telegram_profile' if author else 'self_introduction',
            'telegram_display_name': {k: author.get(k, '') for k in ('first_name', 'last_name')},
            'telegram_user_id': author.get('user_id'),
            'telegram_username': next((n for n in author.get('usernames', []) if re.fullmatch(r'[A-Za-z0-9_]{5,32}', n)), ''),
            'status': 'ready' if first else 'unknown'}
