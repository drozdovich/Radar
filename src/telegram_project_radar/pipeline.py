"""Resumable full-history runs. Private text stays on the collector host."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .config import TELEGRAM_SEARCH_SOURCE, APPROVED_CHAT_IDS, WEEKLY_CHAT_IDS, require_live_sources
from .normalization import normalize_message
from .tdlib_source import TdlibSource, TelegramRuntimeError

TRACKS = ['projects', 'infinity', 'avans']

def scope_ids(scope):
    ids = {'pilot': APPROVED_CHAT_IDS[:2], 'approved_sources': APPROVED_CHAT_IDS,
           'weekly_sources': WEEKLY_CHAT_IDS}[scope]
    if not ids:
        raise ValueError('No approved sources configured for this scope')
    return ids



class ModelNotConfigured(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w') as f:
        os.chmod(temp, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
    temp.replace(path)


def read(path: Path):
    return json.loads(path.read_text())


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def message_record(raw):
    message = normalize_message(raw)
    if message is None:
        raise ValueError('Unusable Telegram message identifiers; collection is incomplete')
    row = asdict(message)
    for k in ['sent_at', 'edit_date']:
        if row[k] is not None:
            row[k] = row[k].isoformat()
    return row


def window(spec, clock=None):
    clock = clock or datetime.now(timezone.utc)
    if spec.get('mode') == 'daily':
        end = clock.astimezone(ZoneInfo('Europe/Madrid')).replace(hour=5, minute=0, second=0, microsecond=0)
        if end > clock:
            end -= timedelta(days=1)
        start = end - timedelta(days=1)
    else:
        start, end = (datetime.fromisoformat(spec[k]) for k in ('start', 'end'))
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError('Timezone required')
    if start < datetime(2026, 9, 1, tzinfo=ZoneInfo('Europe/Madrid')) or end > clock:
        raise ValueError('Outside authorized September-and-later window')
    if not timedelta(0) < end - start <= timedelta(days=35):
        raise ValueError('Use a positive interval of at most 35 days')
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def create_run(root: Path, spec, clock=None):
    scope = spec.get('scope', 'approved_sources')
    if scope not in ('pilot', 'approved_sources', 'weekly_sources'):
        raise ValueError('Only explicitly approved sources may be collected')
    if spec.get('mode') == 'daily':
        # Resume only this authorized scope; archived broad runs stay untouched.
        runs = [read(p) for p in root.glob('run-*/state.json')]
        runs = [s for s in runs if s['scope'] == scope]
        # Waiting for review/delivery must not stall the independent collection cursor.
        unfinished = sorted((s for s in runs if not s['collection_complete']),
                            key=lambda s: datetime.fromisoformat(s['start']))
        if unfinished:
            return root / unfinished[0]['run_id']
        if runs:
            _, end = window(spec, clock)
            latest = max(runs, key=lambda s: datetime.fromisoformat(s['end']))
            start = datetime.fromisoformat(latest['end'])
            if start >= end:
                return root / latest['run_id']
            spec = {**spec, 'mode': 'catchup', 'start': start.isoformat(),
                    'end': min(end, start + timedelta(days=35)).isoformat()}
    start, end = window(spec, clock)
    identity = {'start': start.isoformat(), 'end': end.isoformat(), 'scope': scope, 'tracks': TRACKS}
    run_id = 'run-' + hashlib.sha256(canonical(identity).encode()).hexdigest()[:24]
    path = root / run_id
    if not (path / 'state.json').exists():
        dump(path / 'state.json', {**identity, 'run_id': run_id, 'app_version': __version__,
            'created_at': now(), 'stage': 'created', 'status': 'ready', 'sources': [],
            'collection_complete': False, 'analysis_complete': False, 'delivery_complete': False})
    return path


def approved_inventory(tg, scope):
    ids = scope_ids(scope)
    chats = []
    for cid in ids:
        chat = tg._request({'@type': 'getChat', 'chat_id': cid}, timeout=15)
        typ = chat.get('type', {}).get('@type')
        if typ not in ('chatTypeBasicGroup', 'chatTypeSupergroup'):
            raise RuntimeError('Approved source is unavailable or not a group/channel')
        chats.append({'chat_id': cid, 'title': chat['title'], 'type': typ,
                      'included': True, 'reason': 'explicit_owner_allowlist'})
    return {'captured_at': now(), 'lists': [], 'chats': chats}


def inventory(tg):
    """Exhaust both lists; getChats alone returns only locally loaded chats."""
    ids = set()
    lists = []
    for kind in ['chatListMain', 'chatListArchive']:
        exhausted = False
        for page in range(1000):
            try:
                tg._request({'@type': 'loadChats', 'chat_list': {'@type': kind}, 'limit': 100})
            except TelegramRuntimeError as exc:
                if 'TDLib error 404:' in str(exc):
                    exhausted = True
                    break
                raise
        if not exhausted:
            raise RuntimeError('Chat-list enumeration did not reach its end')
        result = tg._request({'@type': 'getChats', 'chat_list': {'@type': kind}, 'limit': 100000})
        if len(result.get('chat_ids', [])) >= 100000:
            raise RuntimeError('Chat-list limit reached')
        ids.update(int(x) for x in result.get('chat_ids', []))
        lists.append({'list': kind, 'count': len(result.get('chat_ids', [])), 'complete': True})
    chats = []
    for chat_id in sorted(ids):
        try:
            c = tg._request({'@type': 'getChat', 'chat_id': chat_id}, timeout=15)
            typ = c.get('type', {}).get('@type', 'unknown')
            included = typ in ('chatTypeBasicGroup', 'chatTypeSupergroup')
            chats.append({'chat_id': chat_id, 'title': c['title'], 'type': typ,
                'included': included, 'reason': 'current_group_or_channel' if included else 'outside_automatic_group_channel_scope'})
        except Exception as exc:
            # An unresolved listed chat is a coverage gap, never silently excluded.
            chats.append({'chat_id': chat_id, 'title': '', 'type': 'unknown', 'included': True,
                          'reason': 'lookup_failed', 'error': type(exc).__name__})
    return {'captured_at': now(), 'lists': lists, 'chats': chats}


def database(path):
    db = sqlite3.connect(path)
    os.chmod(path, 0o600)
    db.executescript('''
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS snapshots(
        chat_id INTEGER, pass INTEGER, message_id INTEGER, sent_at TEXT, fingerprint TEXT,
        payload TEXT, PRIMARY KEY(chat_id,pass,message_id));
      CREATE TABLE IF NOT EXISTS checkpoints(chat_id INTEGER, pass INTEGER, cursor INTEGER,
        pages INTEGER, finished INTEGER, boundary TEXT, PRIMARY KEY(chat_id,pass));
      CREATE TABLE IF NOT EXISTS contexts(chat_id INTEGER, message_id INTEGER, status TEXT,
        payload TEXT, PRIMARY KEY(chat_id,message_id));
    ''')
    return db


def collect_pass(tg, db, chat_id, number, start, end):
    row = db.execute('SELECT cursor,pages,finished,boundary FROM checkpoints WHERE chat_id=? AND pass=?', (chat_id, number)).fetchone()
    cursor, pages = row[:2] if row else (0, 0)
    if row and row[2]:
        return row[3]
    visited = set()
    while True:
        if cursor in visited:
            raise RuntimeError('History pagination made no progress')
        visited.add(cursor)
        raw = tg._request({'@type': 'getChatHistory', 'chat_id': chat_id,
            'from_message_id': cursor, 'offset': 0, 'limit': 100, 'only_local': False}, timeout=60).get('messages', [])
        # A short nonempty page is not end-of-history.
        boundary = None
        if not raw:
            boundary = 'history_exhausted'
        normalized = [message_record(m) for m in raw]
        for m in normalized:
            if m['chat_id'] != chat_id:
                raise RuntimeError('History source identity mismatch')
            date = datetime.fromisoformat(m['sent_at'])
            if start <= date < end:
                payload = canonical(m)
                db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?)',
                    (chat_id, number, m['message_id'], m['sent_at'], hashlib.sha256(payload.encode()).hexdigest(), payload))
            if date < start:
                boundary = 'before_start'
        pages += 1
        if normalized:
            next_cursor = min(m['message_id'] for m in normalized)
            if next_cursor == cursor and boundary is None:
                # TDLib may include only the anchor on the last page; advance below it.
                if len(normalized) == 1:
                    next_cursor = cursor - 1
                else:
                    raise RuntimeError('History pagination repeated its boundary')
            cursor = next_cursor
        db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?,?)',
            (chat_id, number, cursor, pages, int(boundary is not None), boundary))
        db.commit()
        if boundary:
            return boundary


def fingerprints(db, chat_id, number):
    return dict(db.execute('SELECT message_id,fingerprint FROM snapshots WHERE chat_id=? AND pass=?', (chat_id, number)))


def collection(root, run_id, factory=TdlibSource):
    path = root / run_id
    state = read(path / 'state.json')
    if state['scope'] not in ('pilot', 'approved_sources', 'weekly_sources'):
        raise ValueError('Broad collection is paused by the owner')
    if state['collection_complete']:
        return state
    state.update(stage='collect', status='running')
    dump(path / 'state.json', state)
    start, end = (datetime.fromisoformat(state[k]) for k in ['start', 'end'])
    try:
        if factory is TdlibSource:
            require_live_sources()
        with factory(TELEGRAM_SEARCH_SOURCE) as tg:
            tg.wait_connected()
            inv = read(path / 'inventory.json') if (path / 'inventory.json').exists() else approved_inventory(tg, state['scope'])
            dump(path / 'inventory.json', inv)
            selected = [c for c in inv['chats'] if c['included']]
            expected = set(scope_ids(state['scope']))
            if {c['chat_id'] for c in selected} != expected or len(selected) != len(expected):
                raise RuntimeError('Inventory differs from the owner-approved sources')
            if any(c['chat_id'] not in expected for c in state['sources']):
                raise RuntimeError('Stored progress includes unauthorized sources')
            db = database(path / 'messages.sqlite3')
            try:
                stats = {x['chat_id']: x for x in state['sources']}
                for c in selected:
                    cid = c['chat_id']
                    if stats.get(cid, {}).get('complete'):
                        continue
                    entry = {'chat_id': cid, 'title': c['title'], 'complete': False}
                    try:
                        previous = None
                        first = 1
                        if stats.get(cid, {}).get('error') == 'history_changed_between_passes':
                            # Completed unstable snapshots cannot heal by rereading the
                            # same cache. Append fresh passes on an explicit retry.
                            last = db.execute('SELECT MAX(pass) FROM checkpoints WHERE chat_id=?', (cid,)).fetchone()[0] or 0
                            first = last + 1
                        for number in range(first, first + 4):
                            boundary = collect_pass(tg, db, cid, number, start, end)
                            current = fingerprints(db, cid, number)
                            if previous == current:
                                entry.update(complete=True, verified_pass=number, messages=len(current), boundary=boundary,
                                    verification='two_matching_id_and_content_passes')
                                break
                            previous = current
                        if not entry['complete']:
                            entry['error'] = 'history_changed_between_passes'
                        else:
                            payloads = [json.loads(row[0]) for row in db.execute('SELECT payload FROM snapshots WHERE chat_id=? AND pass=?', (cid, number))]
                            entry['text_messages'] = sum(bool(m['text']) for m in payloads)
                            entry['nontext_messages'] = len(payloads) - entry['text_messages']
                            entry['media_messages'] = sum(m['content_type'] != 'messageText' for m in payloads)
                    except Exception as exc:
                        entry['error'] = str(exc)[:300]
                        # Rate limits stop the whole attempt; leave checkpoints for resume.
                        if '429' in str(exc) or 'FLOOD_WAIT' in str(exc):
                            stats[cid] = entry
                            state['sources'] = list(stats.values())
                            raise
                    stats[cid] = entry
                    state['sources'] = list(stats.values())
                    state['expected_sources'] = len(selected)
                    state['updated_at'] = now()
                    dump(path / 'state.json', state)
                complete = len(stats) == len(selected) and all(x.get('complete') for x in stats.values())
                state.update(collection_complete=complete, status='complete' if complete else 'partial', stage='collect',
                    expected_sources=len(selected), messages=sum(x.get('messages', 0) for x in stats.values()),
                    text_messages=sum(x.get('text_messages', 0) for x in stats.values()),
                    media_messages=sum(x.get('media_messages', 0) for x in stats.values()), updated_at=now())
            finally:
                db.close()
    except Exception as exc:
        state.update(status='failed', error=str(exc)[:300], updated_at=now())
    dump(path / 'state.json', state)
    return state


def public_status(state):
    """No Telegram text, names or raw errors cross the n8n transport."""
    keys = ['run_id', 'app_version', 'start', 'end', 'scope', 'tracks', 'stage', 'status',
            'collection_complete', 'analysis_complete', 'delivery_complete', 'expected_sources',
            'messages', 'text_messages', 'media_messages', 'updated_at', 'analysis_processed', 'candidates']
    result = {k: state[k] for k in keys if k in state}
    result['sources_complete'] = sum(x.get('complete', False) for x in state['sources'])
    result['has_error'] = bool(state.get('error'))
    return result
