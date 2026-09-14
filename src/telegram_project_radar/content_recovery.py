"""Recover unsupported Telegram content without rewriting the original snapshot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import database, dump, message_record, now, read
from .agent_review import digest, save_batch
from .normalization import STRUCTURED_CONTENT_TYPES


def recovery_root(path, scope):
    if scope not in ('unsupported', 'structured'):
        raise ValueError('Invalid recovery scope')
    return path / ('content-recovery' if scope == 'unsupported' else 'structured-content-recovery')


def inventory(path, scope='unsupported'):
    recovery_root(path, scope)
    state = read(path / 'state.json')
    db = database(path / 'messages.sqlite3')
    targets = []
    try:
        for source in state['sources']:
            for row in db.execute('SELECT payload FROM snapshots WHERE chat_id=? AND pass=? ORDER BY sent_at,message_id',
                                  (source['chat_id'], source['verified_pass'])):
                message = json.loads(row[0])
                wanted = (message.get('content_type') == 'messageUnsupported' if scope == 'unsupported'
                          else message.get('content_type') in STRUCTURED_CONTENT_TYPES and not message.get('text'))
                if wanted:
                    targets.append({'chat_id': source['chat_id'], 'message_id': message['message_id'],
                                    'source': source['title'], 'original_sha256': digest(message)})
    finally:
        db.close()
    # A previous decoder recovery can expose a structured type that the original
    # text extractor did not understand. Its receipt stays immutable; this second
    # recovery has its own inventory and proofs.
    if scope == 'structured':
        root = recovery_root(path, 'unsupported')
        manifest = root / 'manifest.json'
        existing = {(t['chat_id'], t['message_id']) for t in targets}
        for target in read(manifest)['targets'] if manifest.exists() else []:
            receipt = verified_receipt(root, target)
            if not receipt:
                continue
            message = receipt['message']
            key = target['chat_id'], target['message_id']
            if key not in existing and message.get('content_type') in STRUCTURED_CONTENT_TYPES and not message.get('text'):
                targets.append({**target, 'original_sha256': digest(message)})
                existing.add(key)
    return targets


def prepare(path, scope='unsupported'):
    root = recovery_root(path, scope)
    targets = inventory(path, scope)
    manifest = {'targets': targets, 'inventory_sha256': digest(targets)}
    target = root / 'manifest.json'
    if target.exists() and read(target) != manifest:
        raise ValueError('Original unsupported-message inventory changed')
    if not target.exists():
        dump(target, manifest)
    return manifest


def receipt_path(root, target):
    return root / 'messages' / f"{target['chat_id']}_{target['message_id']}.json"


def fresh_message(source, target):
    # Empty query + no sender/filter uses a server history search in TDLib.
    # getMessage alone can return the old cached unsupported object immediately.
    result = source._request({'@type': 'searchChatMessages', 'chat_id': target['chat_id'],
                              'query': '', 'from_message_id': target['message_id'],
                              'offset': -1, 'limit': 3}, timeout=45.0)
    for raw in result.get('messages', []):
        if raw.get('chat_id') == target['chat_id'] and raw.get('id') == target['message_id']:
            message = message_record(raw)
            if message['content_type'] == 'messageUnsupported':
                raise ValueError('Current Telegram runtime still cannot decode this message')
            return message
    raise ValueError('Exact original is absent from the current server response')


def verified_receipt(root, target):
    file = receipt_path(root, target)
    if not file.exists():
        return None
    item = read(file)
    if (item.get('verified') and item.get('original_sha256') == target['original_sha256']
            and item.get('first_sha256') == item.get('second_sha256') == digest(item.get('message'))
            and item['message'].get('chat_id') == target['chat_id']
            and item['message'].get('message_id') == target['message_id']
            and item['message'].get('content_type') != 'messageUnsupported'):
        return item
    return None


def recover(path, source, limit=100, scope='unsupported'):
    manifest = prepare(path, scope)
    root = recovery_root(path, scope)
    attempted = 0
    for target in manifest['targets']:
        if verified_receipt(root, target):
            continue
        if attempted >= limit:
            break
        attempted += 1
        try:
            first = fresh_message(source, target)
            second = fresh_message(source, target)
            if digest(first) != digest(second):
                raise ValueError('Message changed between the two recovery reads')
            item = {**target, 'verified': True, 'message': second,
                    'first_sha256': digest(first), 'second_sha256': digest(second), 'at': now()}
        except (ValueError, RuntimeError, TimeoutError) as exc:
            item = {**target, 'verified': False, 'error': str(exc), 'at': now()}
        dump(receipt_path(root, target), item)
        if not item['verified'] and ('429' in item['error'] or 'FLOOD' in item['error']
                                     or 'Timed out' in item['error']):
            break
    return recovery_status(path, scope)


def recovery_status(path, scope='unsupported'):
    manifest = prepare(path, scope)
    root = recovery_root(path, scope)
    items = [verified_receipt(root, t) for t in manifest['targets']]
    verified = [i for i in items if i]
    report = {'inventory_sha256': manifest['inventory_sha256'], 'targets': len(items),
              'verified': len(verified), 'unresolved': len(items) - len(verified),
              'recovered_text': sum(bool(i['message'].get('text')) for i in verified),
              'nontext': sum(not i['message'].get('text') for i in verified),
              'at': now()}
    dump(root / 'progress.json', report)
    return report


def append_review(path, scope='unsupported'):
    """Append recovered text exactly once; previously sealed batches stay identical."""
    recovery = prepare(path, scope)
    root = path / 'agent-review'
    manifest = read(root / 'manifest.json')
    # Reviews prepared before content recovery did not persist this count.
    # Derive it before appending, from the same frozen collection window.
    nontext = manifest.get('nontext_messages')
    if nontext is None:
        nontext = read(path / 'state.json')['messages'] - manifest['text_messages']
    existing = set()
    for batch in manifest['batches']:
        for message in read(root / batch['batch_id'] / 'input.json')['messages']:
            existing.add((message['chat_id'], message['message_id']))
    sources = {}
    for target in recovery['targets']:
        receipt = verified_receipt(recovery_root(path, scope), target)
        key = target['chat_id'], target['message_id']
        if receipt and receipt['message'].get('text') and key not in existing:
            sources.setdefault((target['chat_id'], target['source']), []).append(receipt['message'])
    added = 0
    for (chat_id, title), messages in sources.items():
        source = {'chat_id': chat_id, 'title': title}
        group, chars = [], 0
        for message in messages:
            if group and (len(group) >= 100 or chars + len(message['text']) > 18000):
                manifest['batches'].append(save_batch(root, manifest['batches'], source, group, manifest['revision']))
                added += len(group)
                group, chars = [], 0
            group.append(message)
            chars += len(message['text'])
        if group:
            manifest['batches'].append(save_batch(root, manifest['batches'], source, group, manifest['revision']))
            added += len(group)
    manifest['text_messages'] += added
    manifest['recovered_text_messages'] = manifest.get('recovered_text_messages', 0) + added
    manifest['nontext_messages'] = nontext - added
    key = 'content_recovery_inventory_sha256' if scope == 'unsupported' else 'structured_recovery_inventory_sha256'
    manifest[key] = recovery['inventory_sha256']
    dump(root / 'manifest.json', manifest)
    from .agent_review import status
    return {'appended_text': added, **status(path)}


def gap_count(path):
    """Recompute evidence instead of trusting a manually edited completion flag."""
    targets = [(scope, target) for scope in ('unsupported', 'structured') for target in inventory(path, scope)]
    queued = set()
    if targets:
        review_root = path / 'agent-review'
        for batch in read(review_root / 'manifest.json')['batches']:
            for m in read(review_root / batch['batch_id'] / 'input.json')['messages']:
                queued.add((m['chat_id'], m['message_id']))
    gaps = 0
    for scope, target in targets:
        receipt = verified_receipt(recovery_root(path, scope), target)
        if not receipt or (receipt['message'].get('text') and
                           (target['chat_id'], target['message_id']) not in queued):
            gaps += 1
    return gaps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'recover', 'append-review'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--scope', choices=['unsupported', 'structured'], default='unsupported')
    args = parser.parse_args()
    if args.command == 'recover':
        from .config import TELEGRAM_SEARCH_SOURCE
        from .tdlib_source import TdlibSource
        with TdlibSource(TELEGRAM_SEARCH_SOURCE) as source:
            source.wait_connected()
            result = recover(args.run, source, args.limit, args.scope)
    elif args.command == 'append-review':
        result = append_review(args.run, args.scope)
    else:
        result = recovery_status(args.run, args.scope)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
