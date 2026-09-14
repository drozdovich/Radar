"""One bounded, resumable morning batch; production writes are create-only."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .config import DEFAULT_TIMEZONE, PRIVATE_ROOT, PROJECT_ROOT, TELEGRAM_SEARCH_SOURCE
from .company_lookup import CompanyLookup, read_fact, size_delivery_hold
from .card_names import apply_author_name, needs_author_name
from .detail_enrichment import enrich_run_details, is_allowed_detail_url
from .digest_details import load_digest_details
from .feedback_features import digest
from .feedback_store import write_private
from .inbox import build_project_inbox_items, project_inbox_payload, twenty_rich_text_summary
from .models import Source
from .normalization import approved_title_matches
from .selection_v2 import build_candidate_items, company_name_for_size
from .storage import Storage
from .tdlib_source import TdlibSource

from .config import SOURCE_CONFIG, require_live_sources
import os

SOURCES = {row['name']: row['chat_id'] for row in SOURCE_CONFIG['approved'][:2]}
QUEUE_URL = os.environ.get('RADAR_TWENTY_URL', 'https://twenty.example.invalid').rstrip('/') + '/objects/projectInbox'


def period(day: str | None, days: int, now: datetime | None = None):
    today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
    last = date.fromisoformat(day) if day else today - timedelta(days=1)
    if not 1 <= days <= 7 or last >= today:
        raise ValueError('Use 1–7 completed calendar days, ending before today')
    start = datetime.combine(last - timedelta(days=days - 1), time.min, ZoneInfo(DEFAULT_TIMEZONE))
    end = datetime.combine(last + timedelta(days=1), time.min, ZoneInfo(DEFAULT_TIMEZONE))
    return start, end


def reviewed_ids(path: Path) -> set[str]:
    data = json.loads(path.read_text())
    if data.get('schema_version') != 'morning-reviewed-v1':
        raise ValueError('Unsupported morning review exclusions')
    values = data['reviewed_candidate_ids']
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.startswith('radar-') for v in values):
        raise ValueError('Invalid reviewed candidate IDs')
    return set(values)


def delivery_hold(text: str) -> str | None:
    """Known user exclusions still missing from the general selector: hold delivery only."""
    if re.search(r'\b(?:aws|azure|gcp)\b|google\s+cloud', text, re.I):
        return 'cloud_platform_excluded'
    if re.search(r'\bfintech\b|финтех|плат[её]жн|payment\s+(?:platform|service|processing)', text, re.I):
        return 'fintech_or_payment_services_excluded'
    return None


class TwentyBridge:
    def call(self, action: str, **args):
        try:
            result = subprocess.run(
                ['node', str(PROJECT_ROOT / 'scripts/twenty-morning.mjs')],
                input=json.dumps({'action': action, **args}), capture_output=True,
                text=True, timeout=120,
            )
            response = json.loads(result.stdout)
            if result.returncode or not response.get('ok'):
                raise ValueError('CRM operation failed')
            return response
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise RuntimeError('Twenty unavailable; the saved batch can be resumed safely') from None

    def read(self):
        return self.call('read')['records']

    def create(self, payload):
        return self.call('create', payload=payload)


def crm_payload(item: dict) -> dict:
    cid = item['candidate_id']
    rich = lambda text, key: twenty_rich_text_summary(text, candidate_id=f'{cid}:{key}')
    types = {'opportunity': 'PROJECT', 'digest_item': 'DIGEST_POSITION'}
    return {
        'id': str(uuid.uuid5(uuid.NAMESPACE_URL, f'telegram-project-radar:{cid}')),
        'candidateId': cid, 'name': item['title'], 'candidateType': types[item['type']],
        'reviewStatus': 'NEW', 'fitScore': item['score'], 'summary': item['summary_twenty'],
        'source': item['source'], 'publishedAt': item['published_at'],
        'sourceLink': {'primaryLinkUrl': item['source_url'], 'primaryLinkLabel': 'Оригинал в Telegram', 'secondaryLinks': []},
        'rulesVersion': item['rules_version'],
        **{target: rich('\n'.join(item[source]), target) for target, source in
           [('whyFit', 'why_fit'), ('knownConditions', 'known_conditions'), ('risks', 'risks'), ('unknowns', 'unknowns')]},
    }


def deliver(items: list[dict], bridge, reviewed: set[str]) -> list[dict]:
    """Refetch before each create; a timeout is reconciled by identity, not blind retry."""
    outcomes = []
    for item in items:
        cid = item['candidate_id']
        records = bridge.read()
        existing = next((r for r in records if r['candidateId'] == cid), None)
        text_hash = hashlib.sha256(item['summary'].encode()).hexdigest()
        if existing or cid in reviewed or any(r.get('textHash') == text_hash for r in records):
            outcomes.append({'candidate_id': cid, 'action': 'preserved', 'record_id': existing['id'] if existing else None})
            continue
        hold = delivery_hold(item['summary']) or size_delivery_hold(item)
        if hold:
            outcomes.append({'candidate_id': cid, 'action': 'held', 'record_id': None, 'reason': hold})
            continue
        payload = crm_payload(item)
        try:
            result = bridge.create(payload)
            action = 'created' if result['created'] else 'preserved'
            record_id = result['id']
        except RuntimeError:
            saved = next((r for r in bridge.read() if r['candidateId'] == cid and r['id'] == payload['id']
                          and r.get('textHash') == text_hash and not r.get('deletedAt')), None)
            if not saved:
                raise
            action, record_id = 'recovered', saved['id']
        outcomes.append({'candidate_id': cid, 'action': action, 'record_id': record_id})
    return outcomes


def prepare(batch: Path, start: datetime, end: datetime, known: list[dict], reviewed: set[str],
            limit: int, descriptions=None, telegram_factory=TdlibSource, company_lookup=None) -> dict:
    """Build and seal a batch only after two complete matching collection passes."""
    attempt = batch / ('collection-' + uuid.uuid4().hex[:12])
    attempt.mkdir(mode=0o700)
    company_lookup = company_lookup or CompanyLookup(batch.parent / 'company-size-cache')
    with (attempt / 'runtime.log').open('w') as log, contextlib.redirect_stderr(log):
        if telegram_factory is TdlibSource:
            require_live_sources()
        with Storage(attempt / 'messages.sqlite3') as st, telegram_factory(TELEGRAM_SEARCH_SOURCE) as tg:
            tg.wait_connected()
            sources = []
            for name, chat_id in SOURCES.items():
                chat = tg._request(tg._TdApi.get_chat(chat_id), timeout=15)
                if chat['id'] != chat_id or not approved_title_matches(name, chat['title']):
                    raise RuntimeError('Approved source identity changed')
                source = Source(chat_id, chat['title'], chat['type']['@type'])
                st.upsert_source(name, source)
                sources.append(source)
            previous = None
            passes = []
            for _ in range(4):
                run = 'morning-' + uuid.uuid4().hex[:12]
                st.begin_run(run, 'bounded morning CRM batch', start, end)
                stats = []
                for source in sources:
                    result = tg.collect_chat(storage=st, run_id=run, source=source, start=start, end=end)
                    if not (result.ended_before_start or result.history_exhausted):
                        st.finish_source(run, result, status='incomplete')
                        st.finish_run(run, 'incomplete')
                        raise RuntimeError('Incomplete Telegram collection; no CRM delivery')
                    st.finish_source(run, result, status='complete')
                    stats.append({key: value.isoformat() if isinstance(value, datetime) else value
                                  for key, value in asdict(result).items()})
                st.finish_run(run, 'complete')
                keys = {s.chat_id: st.message_keys(run, s.chat_id) for s in sources}
                passes.append({'run_id': run, 'sources': stats})
                if previous == keys:
                    break
                previous = keys
            else:
                raise RuntimeError('Collection passes differ; no CRM delivery')
            rows = st.messages_for_run(run)
            enrichment = enrich_run_details(st, run_id=run, rows=rows, telegram=tg)
            rows = st.messages_for_run(run)
            items = build_candidate_items(rows, digest_details=descriptions)
            st.replace_candidate_items(run, items)
            candidates = build_project_inbox_items(st.report_candidate_items(run), run_id=run)
            payload = project_inbox_payload(candidates)
            known_ids = {r['candidateId'] for r in known} | reviewed
            known_hashes = {r.get('textHash') for r in known}
            by_locator = {(r['chat_id'], r['message_id']): r for r in rows}
            by_id = {s['candidate_id']: s for s in payload['learning_snapshots']}
            selected, deferred, seen, size_checks = [], [], set(), []
            already_reviewed = 0
            for item in payload['items']:
                if item['type'] not in {'opportunity', 'digest_item'}:
                    continue
                snapshot = by_id[item['candidate_id']]
                if item['candidate_id'] in known_ids or snapshot['text_sha256'] in known_hashes:
                    already_reviewed += 1
                    continue
                locator = snapshot['locator']
                row = by_locator[(locator['chat_id'], locator['message_id'])]
                has_description = bool(descriptions and any(k[:3] == (locator['chat_id'], locator['message_id'], locator['item_index']) for k in descriptions))
                # Digest short cards must not reintroduce unseen full-time/geography restrictions.
                if item['type'] == 'digest_item' and not has_description:
                    deferred.append({'candidate_id': item['candidate_id'], 'reason': 'digest_description_required'})
                    continue
                hold = delivery_hold(item['summary'] + '\n' + (row['detail_text'] or ''))
                if hold:
                    deferred.append({'candidate_id': item['candidate_id'], 'reason': hold})
                    continue
                urls = (row['detail_urls'] or '').splitlines()
                if any(is_allowed_detail_url(url) for url in urls) and not row['detail_text']:
                    deferred.append({'candidate_id': item['candidate_id'], 'reason': 'linked_description_unavailable'})
                    continue
                if len(selected) >= limit or snapshot['text_sha256'] in seen:
                    continue
                company = company_name_for_size(item['summary'])
                check = company_lookup.lookup(company)
                item['company_size_check'] = {**check, 'text_sha256': snapshot['text_sha256']}
                size_checks.append({'candidate_id': item['candidate_id'], **check})
                hold = size_delivery_hold(item)
                if hold:
                    deferred.append({'candidate_id': item['candidate_id'], 'reason': hold,
                                     'detail': check['reason']})
                    continue
                item['known_conditions'].extend(read_fact(f).citation() for f in check['evidence'])
                item['unknowns'] = [u for u in item['unknowns'] if not u.startswith('Размер компании неизвестен:')]
                link = tg.get_message_link(locator['chat_id'], locator['message_id'])
                if not link:
                    deferred.append({'candidate_id': item['candidate_id'], 'reason': 'telegram_link_unavailable'})
                    continue
                item['source_url'] = link
                if needs_author_name(item['type'], item['summary']):
                    apply_author_name(item, tg.get_message_author(locator['chat_id'], locator['message_id']))
                selected.append(item)
                seen.add(snapshot['text_sha256'])
            st.commit()
            write_private(attempt / 'selection.json', {'items': [asdict(i) for i in items]})
            selected_ids = {i['candidate_id'] for i in selected}
            return {
                'schema_version': 'morning-batch-v1', 'app_version': __version__,
                'start': start.isoformat(), 'end': end.isoformat(), 'limit': limit,
                'sources': SOURCES, 'run_id': run, 'passes': passes,
                'text_messages': len(rows), 'candidate_items': len(items),
                'already_reviewed': already_reviewed, 'enrichment': enrichment.as_dict(),
                'company_size_checks': size_checks,
                'company_size_searches': company_lookup.searches,
                'company_size_cache_hits': company_lookup.cache_hits,
                'deferred': deferred, 'items': selected,
                'learning_snapshots': [s for s in payload['learning_snapshots'] if s['candidate_id'] in selected_ids],
            }


def run_morning(args) -> int:
    os.umask(0o077)
    start, end = period(args.date, args.days)
    if not 1 <= args.limit <= 3:
        raise ValueError('Morning limit must be between 1 and 3')
    root = Path(args.output_dir).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # One writer across dates, including overlapping invocations on this host.
    with (root / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another morning run is active') from None
        reviewed = reviewed_ids(Path(args.reviewed))
        bridge = TwentyBridge()
        before = bridge.read()  # Fail before Telegram if the CRM is unavailable.
        batch = root / f'{start.date()}-to-{end.date()}'
        batch.mkdir(mode=0o700, exist_ok=True)
        manifest = batch / 'batch.json'
        reused = manifest.exists()
        description_hash = hashlib.sha256(Path(args.digest_details).read_bytes()).hexdigest() if args.digest_details else None
        if reused:
            sealed = json.loads(manifest.read_text())
            content = {k: v for k, v in sealed.items() if k != 'digest'}
            if sealed.get('digest') != digest(content) or content.get('schema_version') != 'morning-batch-v1':
                raise RuntimeError('Saved batch integrity check failed')
            if content['limit'] != args.limit:
                raise ValueError('A sealed batch keeps its original limit')
            if content['app_version'] != __version__ or content.get('description_hash') != description_hash:
                raise ValueError('Saved batch uses different code or descriptions; inspect it before preparing a replacement')
        else:
            descriptions = load_digest_details(Path(args.digest_details)) if args.digest_details else None
            content = prepare(batch, start, end, before, reviewed, args.limit, descriptions)
            content['description_hash'] = description_hash
            sealed = {**content, 'digest': digest(content)}
            write_private(manifest, sealed)
        outcomes = deliver(content['items'], bridge, reviewed)
        after = bridge.read()
        summary = {
            'app_version': __version__, 'batch_version': content['app_version'],
            'batch': str(manifest), 'reused_batch': reused,
            'period': [content['start'], content['end']], 'text_messages': content['text_messages'],
            'planned': len(content['items']), 'created': sum(o['action'] == 'created' for o in outcomes),
            'preserved': sum(o['action'] == 'preserved' for o in outcomes),
            'recovered': sum(o['action'] == 'recovered' for o in outcomes),
            'already_reviewed': content['already_reviewed'], 'deferred_count': len(content['deferred']),
            'company_size_checks': len(content.get('company_size_checks', [])),
            'company_size_searches': 0 if reused else content.get('company_size_searches', 0),
            'held': sum(o['action'] == 'held' for o in outcomes),
            'new_queue_count': sum(r['status'] == 'NEW' and not r['deletedAt'] for r in after),
            'queue_url': QUEUE_URL, 'outcomes': outcomes, 'schedule_enabled': False,
        }
        write_private(batch / ('delivery-' + uuid.uuid4().hex[:12] + '.json'), summary)
        print(json.dumps(summary, ensure_ascii=False))
    return 0


def add_morning_command(subparsers):
    command = subparsers.add_parser('morning', help='collect and deliver one resumable small CRM batch')
    command.add_argument('--date', help='last completed day YYYY-MM-DD; default yesterday in Madrid')
    command.add_argument('--days', type=int, default=1, help='1–7 completed days')
    command.add_argument('--limit', type=int, default=3, help='1–3 new records per sealed batch')
    command.add_argument('--output-dir', default=str(PRIVATE_ROOT / 'morning'))
    command.add_argument('--reviewed', default=str(PROJECT_ROOT / '.radar/feedback/morning-reviewed.json'))
    command.add_argument('--digest-details', help='verified full descriptions for exact digest blocks')
    command.set_defaults(handler=run_morning)
