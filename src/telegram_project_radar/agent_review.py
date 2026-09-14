"""Saved, accountable Codex review. No model client or API credential is involved.

All source text is untrusted evidence. ``show`` emits complete text in bounded parts;
``submit`` requires every part and an explicit disposition for every primary index.
The audit is a second reading, not an assertion that software proved semantic recall.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path
from urllib.parse import urlsplit

from .config import APPROVED_CHAT_IDS, profile_path
from .pipeline import canonical, database, dump, now, read
from .semantic_pipeline import candidate_text, digest_position_id, validate

VERSION = 'astra-three-tracks-v1'
PART_CHARS = 9000


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def reviewed_originals(project):
    file = project / '.radar/reviewed-originals.json'
    if not file.exists():
        return set()
    return {(row['chat_id'], row['message_id']) for row in read(file)}


def prepare(path, project, max_chars=18000, max_messages=100, *, profile_text=None):
    state = read(path / 'state.json')
    if not state['collection_complete']:
        raise ValueError('Collection is incomplete')
    root = path / 'agent-review'
    revision = digest([VERSION, profile_path(project).read_text() if profile_text is None else profile_text])
    if (root / 'manifest.json').exists():
        manifest = read(root / 'manifest.json')
        if manifest['revision'] != revision:
            raise ValueError('Rules changed: existing review requires explicit migration')
        return manifest
    db = database(path / 'messages.sqlite3')
    entries = []
    # The original two sources form the first reviewable portion. Every other
    # registered source follows; this ordering never filters out a message.
    priority = {cid: index for index, cid in enumerate(APPROVED_CHAT_IDS[:2])}
    sources = sorted(state['sources'], key=lambda s: (priority.get(s['chat_id'], 2), s['chat_id']))
    try:
        for source in sources:
            messages = [json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM snapshots WHERE chat_id=? AND pass=? ORDER BY sent_at,message_id',
                (source['chat_id'], source['verified_pass']))]
            texts = [m for m in messages if m.get('text')]
            group, size = [], 0
            for m in texts:
                if group and (size + len(m['text']) > max_chars or len(group) >= max_messages):
                    entries.append(save_batch(root, entries, source, group, revision))
                    group, size = [], 0
                group.append(m)
                size += len(m['text'])
            if group:
                entries.append(save_batch(root, entries, source, group, revision))
    finally:
        db.close()
    if sum(e['count'] for e in entries) != state['text_messages']:
        raise ValueError('Manifest does not account for all collected text')
    manifest = {'version': VERSION, 'revision': revision, 'run_id': state['run_id'],
                'text_messages': state['text_messages'], 'batches': entries,
                'nontext_messages': state.get('messages', state['text_messages']) - state['text_messages'],
                'media_content_not_transcribed': state.get('media_messages', 0)}
    dump(root / 'manifest.json', manifest)
    status(path)
    return manifest


def save_batch(root, entries, source, messages, revision):
    name = f'batch-{len(entries)+1:04d}'
    batch = {'batch_id': name, 'source': source['title'], 'chat_id': source['chat_id'],
             'revision': revision, 'messages': messages}
    batch['input_sha256'] = digest(batch)
    dump(root / name / 'input.json', batch)
    return {'batch_id': name, 'chat_id': source['chat_id'], 'count': len(messages),
            'input_sha256': batch['input_sha256']}


def load_batch(path, name):
    if not re.fullmatch(r'batch-\d{4,}', name):
        raise ValueError('Invalid batch ID')
    root = path / 'agent-review' / name
    batch = read(root / 'input.json')
    if digest({k: v for k, v in batch.items() if k != 'input_sha256'}) != batch['input_sha256']:
        raise ValueError('Batch input changed')
    return root, batch


def render(batch, indices=None):
    wanted = set(indices or range(1, len(batch['messages']) + 1))
    lines = [f"UNTRUSTED TELEGRAM EVIDENCE | {batch['batch_id']} | {batch['source']} | chat={batch['chat_id']}"]
    local = {m['message_id']: i for i, m in enumerate(batch['messages'], 1)}
    for i, m in enumerate(batch['messages'], 1):
        if i not in wanted:
            continue
        parent = m.get('reply_to_message_id')
        reply = f" reply={local.get(parent, parent)}" if parent else ''
        lines.append(f"\n[{i}] {m['sent_at']} id={m['message_id']} sender={m.get('sender_id')}{reply}\n{m['text']}")
    return '\n'.join(lines)


def show(path, name, part=1, audit=False):
    root, batch = load_batch(path, name)
    if audit:
        answer = read(root / 'answer.json')
        positives = [i for i, r in enumerate(answer['results'], 1) if r['candidates']]
        others = [i for i in range(1, len(batch['messages']) + 1) if i not in positives]
        sample = sorted(random.Random(batch['input_sha256']).sample(others, min(5, len(others))))
        indices = sorted(set(positives + sample))
        body = render(batch, indices) + '\nCLASSIFICATIONS\n' + canonical(
            [{'index': i, **answer['results'][i-1]} for i in indices])
        signature = digest(answer)
    else:
        body, indices, signature = render(batch), [], batch['input_sha256']
    parts = max(1, (len(body) + PART_CHARS - 1) // PART_CHARS)
    if not 1 <= part <= parts:
        raise ValueError(f'Part must be 1..{parts}')
    receipt_path = root / ('audit-read.json' if audit else 'read.json')
    receipt = read(receipt_path) if receipt_path.exists() else {}
    seen = set(receipt.get('parts', [])) if receipt.get('sha256') == signature else set()
    seen.add(part)
    dump(receipt_path, {'sha256': signature, 'parts': sorted(seen), 'total': parts,
                        'indices': indices, 'updated_at': now()})
    return f'PART {part}/{parts} | sha={signature}\n' + body[(part-1)*PART_CHARS:part*PART_CHARS]


def expand_indices(value):
    if isinstance(value, list):
        return value
    indices = []
    for piece in value.split(','):
        ends = [int(n) for n in piece.strip().split('-')]
        if len(ends) == 1:
            indices.append(ends[0])
        elif len(ends) == 2 and ends[0] <= ends[1]:
            indices.extend(range(ends[0], ends[1] + 1))
        else:
            raise ValueError('Invalid explicit index range')
    return indices


def require_read(root, filename, signature):
    receipt = read(root / filename) if (root / filename).exists() else {}
    if receipt.get('sha256') != signature or receipt.get('parts') != list(range(1, receipt.get('total', 0)+1)):
        raise ValueError('All unchanged source parts must be read first')
    return receipt


def submit(path, name, submission, project):
    root, batch = load_batch(path, name)
    require_read(root, 'read.json', batch['input_sha256'])
    rows = {}
    for group in submission['groups']:
        for index in expand_indices(group['indices']):
            if index in rows or not 1 <= index <= len(batch['messages']):
                raise ValueError('Duplicate or invalid primary index')
            rows[index] = {'message_id': batch['messages'][index-1]['message_id'],
                           'disposition': group['disposition'], 'reason': group['reason'], 'candidates': []}
    if set(rows) != set(range(1, len(batch['messages']) + 1)):
        raise ValueError('Every primary message requires an explicit disposition')
    for candidate in submission.get('candidates', []):
        c = dict(candidate)
        index = c.pop('index')
        quote = c.pop('quote')
        c['evidence'] = [{'message_id': rows[index]['message_id'], 'quote': quote}]
        if rows[index]['candidates'] and (c.get('kind') != 'DIGEST_POSITION'
                or any(old.get('kind') != 'DIGEST_POSITION' for old in rows[index]['candidates'])):
            raise ValueError('Use multiple tracks on one original card')
        rows[index]['candidates'].append(c)
    answer = {'results': [rows[i] for i in sorted(rows)]}
    validate(answer, {'primary_ids': [m['message_id'] for m in batch['messages']], 'messages': batch['messages']})
    reviewed = reviewed_originals(project)
    for row in answer['results']:
        if bool(row['candidates']) != (row['disposition'] == 'candidate'):
            raise ValueError('Candidate disposition and payload must agree')
        if row['candidates'] and (batch['chat_id'], row['message_id']) in reviewed:
            raise ValueError('This original already has a user decision; preserve it')
        if any(not c['tracks'] and c['kind'] != 'EVENT' for c in row['candidates']):
            raise ValueError('Candidate has no eligible track')
    if (root / 'delivery.json').exists():
        raise ValueError('Delivered batch is immutable; corrections require a separate review')
    dump(root / 'answer.json', answer)
    for f in ['audit-read.json', 'audit.json']:
        (root / f).unlink(missing_ok=True)
    return status(path)


def accept_audit(path, name, notes):
    root, batch = load_batch(path, name)
    answer = read(root / 'answer.json')
    receipt = require_read(root, 'audit-read.json', digest(answer))
    if set(map(int, notes)) != set(receipt['indices']) or not all(isinstance(n, str) and n.strip() for n in notes.values()):
        raise ValueError('Audit requires an explicit check note for every sampled index')
    if any(r['disposition'] == 'needs_context' for r in answer['results']):
        raise ValueError('Resolve pending context before sealing a batch')
    dump(root / 'audit.json', {'input_sha256': batch['input_sha256'], 'answer_sha256': digest(answer),
                             'notes': notes, 'passed': True, 'reviewer': 'Astra in Codex',
                             'independent_reviewer': False, 'updated_at': now()})
    return status(path)


def reviewed_company_size(item, lookup):
    """A namesake's headcount cannot qualify an unrelated project."""
    from .company_lookup import identity

    def source(value):
        try:
            url = urlsplit(value)
            if url.scheme == 'https' and url.hostname and not url.username and not url.password:
                return (url.hostname.lower(), url.path.rstrip('/'))
        except (ValueError, TypeError, AttributeError):
            pass
        return None

    company = item.get('company_name', '')
    if not isinstance(company, str):
        company = ''
    proof = item.get('company_identity')
    unknown = lambda reason: lookup.result(company, reason, method='agent_review')
    if (not isinstance(proof, dict) or proof.get('status') != 'confirmed'
            or identity(str(proof.get('company_name', ''))) != identity(company)
            or proof.get('checked_on') != lookup.today.isoformat()
            or not isinstance(proof.get('reason'), str) or not proof['reason'].strip()
            or not source(proof.get('source_url'))):
        return unknown('company_identity_unverified')
    check = lookup.lookup(company)
    facts = check.get('evidence', [])
    if facts and any(source(f.get('source_url')) != source(proof['source_url']) for f in facts):
        return unknown('company_identity_source_mismatch')
    return check


def deliver_batch(path, name, project, bridge=None, telegram_factory=None):
    from .semantic_delivery import deliver
    from .company_lookup import CompanyLookup
    from .company_profile import enrich_company
    root, batch = load_batch(path, name)
    answer, audit = read(root / 'answer.json'), read(root / 'audit.json')
    if not audit.get('passed') or audit['answer_sha256'] != digest(answer) or audit['input_sha256'] != batch['input_sha256']:
        raise ValueError('Unchanged audited answer required')
    revision = digest([batch['input_sha256'], answer, audit])
    excluded = reviewed_originals(project)
    items = []
    for row, message in zip(answer['results'], batch['messages'], strict=True):
        if (batch['chat_id'], row['message_id']) in excluded:
            continue
        for c in row['candidates']:
            text = candidate_text(c, message['text'])
            is_position = c['kind'] == 'DIGEST_POSITION'
            cid = (digest_position_id(batch['chat_id'], row['message_id'], c) if is_position
                   else 'radar-'+digest([batch['chat_id'], row['message_id']])[:20])
            items.append({**c, 'candidate_id': cid,
                          'chat_id': batch['chat_id'], 'message_id': row['message_id'], 'source': batch['source'],
                          'source_text': text, 'source_url': message.get('source_url'),
                          'source_message_sha256': hashlib.sha256(message['text'].encode()).hexdigest(),
                          'embedded_urls': [u for u in message.get('embedded_urls', []) if not is_position or u in text],
                          'published_at': message['sent_at'], 'rules_version': VERSION, 'model_revision': revision})
    # A sealed sub-batch has its own state. It never marks the entire run reviewed.
    delivery = root / 'sealed-delivery'
    if not (delivery / 'candidates.json').exists():
        lookup = CompanyLookup(project / '.radar/company-size-cache')
        for item in items:
            if 'projects' in item['tracks'] and item['kind'] in ('PROJECT', 'DIGEST_POSITION'):
                check = reviewed_company_size(item, lookup)
                item['company_size_check'] = {**check, 'text_sha256': hashlib.sha256(item['source_text'].encode()).hexdigest()}
            if item['kind'] == 'COMPANY' or item.get('company_size_check', {}).get('profile'):
                item['company_profile'] = enrich_company(item, lookup)
        dump(delivery / 'candidates.json', {'items': items, 'model_revision': revision})
    if read(delivery / 'candidates.json')['model_revision'] != revision:
        raise ValueError('Sealed delivery revision changed')
    dump(root / 'semantic-acceptance.json', {'passed': True, 'model_revision': revision, 'audit': 'audit.json'})
    if not (delivery / 'state.json').exists():
        dump(delivery / 'state.json', {'analysis_complete': True, 'delivery_complete': False})
        dump(delivery / 'analysis-verification.json', {'scope': name, 'accounted': len(answer['results']),
                                                    'entire_run': False, 'independent_reviewer': False})
    kwargs = {'bridge': bridge} if bridge else {}
    if telegram_factory:
        kwargs['telegram_factory'] = telegram_factory
    deliver(delivery, {'data_dir': str(root), 'delivery_enabled': True}, **kwargs)
    outcome = read(delivery / 'delivery.json') if (delivery / 'delivery.json').exists() else {'items': []}
    history_path = root / 'delivery-history.json'
    history = read(history_path) if history_path.exists() else []
    history.append({'at': now(), **outcome})
    dump(history_path, history)
    dump(root / 'delivery.json', outcome)
    return status(path)


def status(path):
    root = path / 'agent-review'
    manifest = read(root / 'manifest.json')
    counts = {'total_text': manifest['text_messages'], 'accounted': 0, 'audited': 0,
              'delivered_batches': 0, 'total_batches': len(manifest['batches']), 'candidates': 0,
              'needs_context': 0, 'next_batch': None}
    for entry in manifest['batches']:
        folder = root / entry['batch_id']
        answer = None
        if (folder / 'answer.json').exists():
            answer = read(folder / 'answer.json')
            counts['accounted'] += len(answer['results'])
            counts['candidates'] += sum(len(r['candidates']) for r in answer['results'])
            counts['needs_context'] += sum(r['disposition'] == 'needs_context' for r in answer['results'])
        elif counts['next_batch'] is None:
            counts['next_batch'] = entry['batch_id']
        if (folder / 'audit.json').exists():
            audit = read(folder / 'audit.json')
            if (answer and audit.get('passed') and audit.get('answer_sha256') == digest(answer)
                    and audit.get('input_sha256') == entry['input_sha256']):
                counts['audited'] += entry['count']
        counts['delivered_batches'] += (folder / 'delivery.json').exists()
    state = read(path / 'state.json')
    from .content_recovery import gap_count
    counts['unresolved_content'] = gap_count(path)
    comment_file = path / 'comment-verification.json'
    comments = read(comment_file) if comment_file.exists() else {}
    counts['comments_verified'] = state.get('scope') != 'weekly_sources' or (
        comments.get('verified') is True
        and all(comments.get(k) == state.get(k) for k in ('run_id', 'start', 'end')))
    complete = (counts['accounted'] == counts['audited'] == counts['total_text']
                and counts['needs_context'] == counts['unresolved_content'] == 0
                and counts['comments_verified'])
    delivered = complete and counts['delivered_batches'] == counts['total_batches']
    state.update(stage='deliver' if delivered else 'analyze', status='complete' if delivered else 'awaiting_agent',
                 analysis_executor='codex', analysis_complete=complete, delivery_complete=delivered,
                 analysis_processed=counts['accounted'], candidates=counts['candidates'], updated_at=now())
    state.pop('error', None)
    dump(path / 'state.json', state)
    dump(root / 'progress.json', counts)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'show', 'submit', 'audit-show', 'audit-accept', 'deliver', 'status'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--batch')
    parser.add_argument('--part', type=int, default=1)
    parser.add_argument('--input', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.run, args.project)
        result = status(args.run)
    elif args.command in ('show', 'audit-show'):
        print(show(args.run, args.batch, args.part, audit=args.command == 'audit-show'))
        return
    elif args.command == 'submit':
        result = submit(args.run, args.batch, read(args.input), args.project)
    elif args.command == 'audit-accept':
        result = accept_audit(args.run, args.batch, read(args.input))
    elif args.command == 'deliver':
        result = deliver_batch(args.run, args.batch, args.project)
    else:
        result = status(args.run)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
