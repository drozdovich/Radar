import copy
import hashlib
import io
import json
import queue
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from telegram_project_radar import __version__
from telegram_project_radar import morning
from telegram_project_radar.company_lookup import CompanyLookup
from telegram_project_radar.company_size import CompanySize
from telegram_project_radar.models import Message, SourceCollection
from telegram_project_radar.inbox import twenty_rich_text_summary
from telegram_project_radar.tdlib_source import TdlibSource, TelegramRuntimeError


def item(number=1):
    cid = 'radar-' + hashlib.sha256(str(number).encode()).hexdigest()[:20]
    text = f'Freelance DevOps project {number}, VPS network setup, remote Spain.'
    check = dict(schema_version='company-size-check-v1', company='Example Studio', checked_on=date.today().isoformat(),
                 text_sha256=hashlib.sha256(text.encode()).hexdigest(), evidence=[dict(company='Example Studio',
                 minimum=11, maximum=50, source_url='https://example.org/about', source_excerpt='Company: 11–50 employees',
                 checked_on=date.today().isoformat(), scope='company')])
    return dict(candidate_id=cid, type='opportunity', title=f'Project {number}', summary=text, company_size_check=check,
                summary_twenty=twenty_rich_text_summary(text, candidate_id=cid), score=90,
                source='Example Builders', source_url=f'https://t.me/example/{number}',
                published_at='2026-09-01T12:00:00+00:00', rules_version='test-v1',
                why_fit=['Infrastructure project'], known_conditions=[], risks=[], unknowns=[])


class Bridge:
    def __init__(self, records=(), lose_response=False):
        self.records = copy.deepcopy(list(records))
        self.creates = 0
        self.lose_response = lose_response

    def read(self):
        return copy.deepcopy(self.records)

    def create(self, payload):
        self.creates += 1
        self.records.append(dict(id=payload['id'], candidateId=payload['candidateId'],
                                 status='NEW', deletedAt=None,
                                 textHash=hashlib.sha256(payload['summary']['markdown'].encode()).hexdigest()))
        if self.lose_response:
            raise RuntimeError('Connection dropped after server committed')
        return dict(id=payload['id'], created=True)


class MorningTests(unittest.TestCase):
    def test_previous_day_uses_madrid_calendar_across_dst(self):
        start, end = morning.period(None, 1, datetime(2026, 3, 30, 0, 30, tzinfo=timezone.utc))
        self.assertEqual(str(start.date()), '2026-03-29')
        self.assertEqual(end.astimezone(timezone.utc) - start.astimezone(timezone.utc), timedelta(hours=23))

    def test_current_day_and_unbounded_range_rejected(self):
        now = datetime(2026, 9, 12, tzinfo=timezone.utc)
        for day, days in [('2026-09-12', 1), ('2026-09-13', 1), ('2026-09-11', 8), ('2026-09-11', 0)]:
            with self.assertRaises(ValueError):
                morning.period(day, days, now)

    def test_all_existing_states_and_trash_are_never_changed(self):
        for status, deleted in [('NEW', None), ('APPROVE', None), ('REJECT', None), ('NEED_INFO', None), ('REJECT', 'deleted')]:
            candidate = item()
            del candidate['company_size_check']  # Old decisions survive even without a size proof.
            bridge = Bridge([dict(id='existing', candidateId=candidate['candidate_id'], status=status, deletedAt=deleted)])
            before = bridge.read()
            self.assertEqual(morning.deliver([candidate], bridge, set())[0]['action'], 'preserved')
            self.assertEqual(bridge.read(), before)
            self.assertEqual(bridge.creates, 0)

    def test_local_review_blocks_candidate_never_imported_to_crm(self):
        candidate = item(); bridge = Bridge()
        morning.deliver([candidate], bridge, {candidate['candidate_id']})
        self.assertEqual(bridge.creates, 0)

    def test_same_text_under_new_id_is_not_reintroduced(self):
        candidate = item()
        bridge = Bridge([dict(id='other', candidateId='radar-other', status='REJECT', deletedAt=None,
                              textHash=hashlib.sha256(candidate['summary'].encode()).hexdigest())])
        morning.deliver([candidate], bridge, set())
        self.assertEqual(bridge.creates, 0)

    def test_partial_network_failure_is_reconciled_and_rerun_is_safe(self):
        bridge = Bridge(lose_response=True)
        self.assertEqual(morning.deliver([item()], bridge, set())[0]['action'], 'recovered')
        bridge.records[0]['status'] = 'APPROVE'
        self.assertEqual(morning.deliver([item()], bridge, set())[0]['action'], 'preserved')
        self.assertEqual(bridge.creates, 1)
        self.assertEqual(bridge.records[0]['status'], 'APPROVE')

    def test_new_card_without_size_proof_never_reaches_crm(self):
        candidate = item(); del candidate['company_size_check']
        bridge = Bridge()
        self.assertEqual(morning.deliver([candidate], bridge, set())[0]['reason'], 'company_size_unverified')
        self.assertEqual(bridge.creates, 0)

    def test_hard_user_exclusions_held_without_rejecting_salary_word(self):
        for text in ['AWS contract', 'GCP project', 'Google Cloud', 'Fintech sales', 'платёжный сервис']:
            candidate = item(); candidate['summary'] = text
            bridge = Bridge()
            self.assertEqual(morning.deliver([candidate], bridge, set())[0]['action'], 'held')
            self.assertEqual(bridge.creates, 0)
        self.assertIsNone(morning.delivery_hold('Contract project, payment weekly, salary negotiable'))

    def test_sealed_batch_repeats_without_collecting_or_expanding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exclusions = root / 'reviewed.json'
            exclusions.write_text(json.dumps(dict(schema_version='morning-reviewed-v1', reviewed_candidate_ids=[])))
            args = Namespace(date=None, days=1, limit=3, output_dir=str(root / 'runs'), reviewed=str(exclusions), digest_details=None)
            start, end = morning.period(None, 1)
            content = dict(schema_version='morning-batch-v1', app_version=__version__, limit=3,
                           start=start.isoformat(), end=end.isoformat(), text_messages=10,
                           items=[item(1), item(2), item(3)], already_reviewed=0, deferred=[])
            bridge = Bridge()
            with patch.object(morning, 'TwentyBridge', return_value=bridge), patch.object(morning, 'prepare', return_value=content) as prepare:
                with redirect_stdout(io.StringIO()):
                    morning.run_morning(args)
                    bridge.records[0]['status'] = 'REJECT'
                    morning.run_morning(args)
                self.assertEqual(prepare.call_count, 1)
                self.assertEqual(bridge.creates, 3)
                self.assertEqual(bridge.records[0]['status'], 'REJECT')
                args.limit = 2
                with self.assertRaises(ValueError), redirect_stdout(io.StringIO()):
                    morning.run_morning(args)

    def test_failed_collection_does_not_seal_or_deliver(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); policy = root / 'reviewed.json'
            policy.write_text(json.dumps(dict(schema_version='morning-reviewed-v1', reviewed_candidate_ids=[])))
            args = Namespace(date=None, days=1, limit=3, output_dir=str(root / 'runs'), reviewed=str(policy), digest_details=None)
            bridge = Bridge()
            with patch.object(morning, 'TwentyBridge', return_value=bridge), patch.object(morning, 'prepare', side_effect=RuntimeError('incomplete')):
                with self.assertRaises(RuntimeError):
                    morning.run_morning(args)
            self.assertEqual(bridge.creates, 0)
            self.assertEqual(list(root.rglob('batch.json')), [])

    def test_fresh_collection_requires_connection_not_only_authorization(self):
        tg = object.__new__(TdlibSource)
        updates = queue.Queue()
        tg._session = SimpleNamespace(client=SimpleNamespace(updates=updates))
        updates.put({'@type': 'updateAuthorizationState'})
        with self.assertRaises(TelegramRuntimeError):
            tg.wait_connected(timeout=.001)
        updates.put({'@type': 'updateConnectionState', 'state': {'@type': 'connectionStateReady'}})
        tg.wait_connected(timeout=.1)


class FakeTelegram:
    incomplete = False
    change_every_pass = False
    _TdApi = SimpleNamespace(get_chat=lambda cid: {'chat_id': cid})

    def __init__(self, *_):
        self.calls = 0

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def wait_connected(self): pass

    def _request(self, request, **_):
        cid = request['chat_id']
        return {'id': cid, 'title': next(n for n, c in morning.SOURCES.items() if c == cid), 'type': {'@type': 'chatTypeSupergroup'}}

    def collect_chat(self, *, storage, run_id, source, start, end):
        self.calls += 1
        number = self.calls if self.change_every_pass else 1
        text = 'Ищем DevOps engineer для freelance VPS infrastructure project. Remote Spain.\nCompany: Example Studio'
        messages = [Message(source.chat_id, number, start + timedelta(hours=12), None, None,
                            'messageText', text, None, None, None, None)] if source.chat_id == next(iter(morning.SOURCES.values())) else []
        storage.store_page(run_id, messages)
        return SourceCollection(source.chat_id, 1, len(messages), len(messages), len(messages), not self.incomplete, False,
                                messages[0].sent_at if messages else None, messages[-1].sent_at if messages else None)

    def get_message_link(self, cid, mid): return f'https://t.me/example/{mid}'


class PreparationTests(unittest.TestCase):
    def test_real_preparation_seals_exact_original_snapshot_before_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            start, end = morning.period(None, 1)
            fact = CompanySize('Example Studio', 11, 50, 'https://example.org/about', 'Company size: 11–50', date.today())
            lookup = CompanyLookup(Path(temp) / 'cache', registry={'example studio': fact})
            content = morning.prepare(Path(temp), start, end, [], set(), 3, telegram_factory=FakeTelegram, company_lookup=lookup)
            json.dumps(content)  # Real collection statistics contain datetimes before serialization.
            self.assertEqual(len(content['passes']), 2)
            self.assertEqual(len(content['items']), 1)
            snapshot = content['learning_snapshots'][0]
            self.assertEqual(snapshot['text_sha256'], hashlib.sha256(content['items'][0]['summary'].encode()).hexdigest())
            self.assertIn(__version__, snapshot['rules_version'])
            self.assertIn('https://example.org/about', content['items'][0]['known_conditions'][-1])
            self.assertFalse(any(u.startswith('Размер компании неизвестен:') for u in content['items'][0]['unknowns']))

    def test_preparation_holds_unknown_size_before_any_crm_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            start, end = morning.period(None, 1)
            lookup = CompanyLookup(Path(temp) / 'cache', registry={}, max_searches=0)
            content = morning.prepare(Path(temp), start, end, [], set(), 3, telegram_factory=FakeTelegram, company_lookup=lookup)
            self.assertEqual(content['items'], [])
            self.assertEqual(content['deferred'][0]['reason'], 'company_size_unknown')

    def test_incomplete_or_changing_sources_abort_preparation(self):
        for flag in ['incomplete', 'change_every_pass']:
            with tempfile.TemporaryDirectory() as temp, patch.object(FakeTelegram, flag, True):
                start, end = morning.period(None, 1)
                with self.assertRaises(RuntimeError):
                    morning.prepare(Path(temp), start, end, [], set(), 3, telegram_factory=FakeTelegram)


if __name__ == '__main__':
    unittest.main()
