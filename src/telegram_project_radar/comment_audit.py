"""Verify channel comment threads against the collected discussion history."""
import json
from datetime import datetime
from .pipeline import read, dump, database, message_record, canonical, now
from .config import TELEGRAM_SEARCH_SOURCE, COMMENT_PAIRS, require_live_sources
from .tdlib_source import TdlibSource


def thread_pass(tg, cid, mid):
    cursor, seen, messages = 0, set(), {}
    while True:
        if cursor in seen:
            raise ValueError('Comment pagination stalled')
        seen.add(cursor)
        rows = tg._request({'@type': 'getMessageThreadHistory', 'chat_id': cid,
            'message_id': mid, 'from_message_id': cursor, 'offset': 0, 'limit': 100}).get('messages', [])
        if not rows:
            return messages
        for raw in rows:
            m = message_record(raw)
            messages[(m['chat_id'], m['message_id'])] = m
        nxt = min(m['id'] for m in rows)
        cursor = nxt - 1 if nxt == cursor else nxt


def verify(path, factory=TdlibSource):
    state = read(path / 'state.json')
    if state['scope'] != 'weekly_sources' or not state['collection_complete']:
        raise ValueError('Completed weekly collection required')
    start, end = (datetime.fromisoformat(state[k]) for k in ['start', 'end'])
    db = database(path / 'messages.sqlite3')
    collected = {}
    for source in state['sources']:
        for row in db.execute('SELECT payload FROM snapshots WHERE chat_id=? AND pass=?', (source['chat_id'], source['verified_pass'])):
            m = json.loads(row[0]); collected[(m['chat_id'], m['message_id'])] = m
    db.close()
    if not COMMENT_PAIRS:
        raise ValueError('Configure channel/discussion pairs before auditing comments')
    posts = [m for m in collected.values() if m['chat_id'] in COMMENT_PAIRS]
    outcomes = []
    if factory is TdlibSource:
        require_live_sources()
    with factory(TELEGRAM_SEARCH_SOURCE) as tg:
        tg.wait_connected()
        for post in posts:
            cid, mid = post['chat_id'], post['message_id']
            file = path / 'comment-audit' / f'{mid}.json'
            if file.exists() and read(file).get('verified'):
                outcomes.append(read(file)); continue
            result = {'channel_message_id': mid, 'verified': False}
            try:
                prop = tg._request({'@type': 'getMessageProperties', 'chat_id': cid, 'message_id': mid})
                if not prop.get('can_get_message_thread'):
                    raw = tg._request({'@type': 'getMessage', 'chat_id': cid, 'message_id': mid})
                    if (raw.get('interaction_info') or {}).get('reply_info'):
                        raise ValueError('Replies exist but thread is inaccessible')
                    result.update(verified=True, availability='no_thread_exposed', fetched_replies=0,
                                  properties=prop, reply_info=None)
                    dump(file, result); outcomes.append(result); continue
                info = tg._request({'@type': 'getMessageThread', 'chat_id': cid, 'message_id': mid})
                if info['chat_id'] != COMMENT_PAIRS[cid]:
                    raise ValueError('Unexpected discussion source')
                first = thread_pass(tg, info['chat_id'], info['message_thread_id']); second = thread_pass(tg, info['chat_id'], info['message_thread_id'])
                if canonical(list(first.values())) != canonical(list(second.values())):
                    raise ValueError('Thread changed between reads')
                roots = {(m['chat_id'], m['id']) for m in info['messages']}
                replies = {k: m for k, m in second.items() if k not in roots}
                expected = info.get('reply_info', {}).get('reply_count', 0)
                if len(replies) < expected:
                    raise ValueError(f'Incomplete replies: {len(replies)} < {expected}')
                missing = [k for k,m in replies.items() if start <= datetime.fromisoformat(m['sent_at']) < end and (k not in collected or collected[k]['text'] != m['text'])]
                if missing:
                    raise ValueError(f'{len(missing)} comments missing/changed in collected snapshot')
                result.update(verified=True, reply_count=expected, fetched_replies=len(replies),
                              thread_id=info['message_thread_id'], comments=list(second.values()))
            except Exception as exc:
                result['error'] = str(exc)[:250]
            dump(file, result); outcomes.append(result)
    report = {**{k: state[k] for k in ('run_id', 'start', 'end')},
              'at': now(), 'channel_posts': len(posts), 'verified_threads': sum(x['verified'] for x in outcomes),
              'verified': all(x['verified'] for x in outcomes),
              'replies': sum(x.get('fetched_replies', 0) for x in outcomes),
              'errors': [{k:v for k,v in x.items() if k != 'comments'} for x in outcomes if not x['verified']]}
    dump(path / 'comment-verification.json', report)
    return report
