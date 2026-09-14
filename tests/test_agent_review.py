import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from telegram_project_radar import agent_review as review
from telegram_project_radar.pipeline import canonical, database, dump, read
from telegram_project_radar.semantic_pipeline import analyze


@pytest.fixture
def run(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'PROFESSIONAL_PROFILE.md').write_text('Projects: part time; telephony tracks independent.')
    path = tmp_path / 'run'
    dump(path / 'state.json', {'run_id': 'run-test', 'collection_complete': True,
        'analysis_complete': False, 'delivery_complete': False, 'text_messages': 3,
        'sources': [{'chat_id': -100123, 'title': 'Source', 'verified_pass': 2}]})
    db = database(path / 'messages.sqlite3')
    for i, text in enumerate(['Need help handling incoming calls', 'Weather today', ''], 1):
        message = {'chat_id': -100123, 'message_id': i, 'sent_at': f'2026-09-0{i}T00:00:00+00:00',
                   'text': text, 'source_url': 'https://t.me/example/'+str(i)}
        for p in [1, 2]:
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?)',
                       (-100123, p, i, message['sent_at'], review.digest(message), canonical(message)))
    # A long third text verifies that no output-part can be silently skipped.
    message = {'chat_id': -100123, 'message_id': 4, 'sent_at': '2026-09-04T00:00:00+00:00',
               'text': 'long text ' * 1200, 'source_url': 'https://t.me/example/4'}
    db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?)', (-100123, 2, 4, message['sent_at'], review.digest(message), canonical(message)))
    db.commit()
    db.close()
    review.prepare(path, project, max_messages=2)
    return path, project


def submission(candidate=False):
    data = {'groups': [{'indices': '1', 'disposition': 'candidate' if candidate else 'related', 'reason': 'Calls'},
                       {'indices': '2', 'disposition': 'irrelevant', 'reason': 'Weather'}]}
    if candidate:
        data['candidates'] = [{'index': 1, 'quote': 'handling incoming calls', 'title': 'Incoming calls',
            'tracks': ['avans'], 'kind': 'PROJECT', 'role': 'customer', 'market': 'unknown',
            'fact': 'Needs help with incoming calls', 'hypothesis': 'Voice automation pilot', 'unknowns': ['Volume']}]
    return data


def test_manifest_counts_all_text_not_all_snapshots(run):
    path, _ = run
    manifest = read(path / 'agent-review/manifest.json')
    assert sum(b['count'] for b in manifest['batches']) == 3
    assert review.status(path)['accounted'] == 0


def test_missing_parts_and_omitted_indices_cannot_pass(run):
    path, project = run
    with pytest.raises(ValueError, match='parts'):
        review.submit(path, 'batch-0001', submission(), project)
    review.show(path, 'batch-0001')
    incomplete = submission()
    incomplete['groups'].pop()
    with pytest.raises(ValueError, match='Every primary'):
        review.submit(path, 'batch-0001', incomplete, project)
    review.show(path, 'batch-0002', 1)
    with pytest.raises(ValueError, match='parts'):
        review.submit(path, 'batch-0002', {'groups': [{'indices': '1', 'disposition': 'irrelevant', 'reason': 'Long text'}]}, project)


def test_quotes_and_old_decisions_enforced(run):
    path, project = run
    review.show(path, 'batch-0001')
    data = submission(True)
    data['candidates'][0]['quote'] = 'invented quote'
    with pytest.raises(ValueError, match='quote'):
        review.submit(path, 'batch-0001', data, project)
    dump(project / '.radar/reviewed-originals.json', [{'chat_id': -100123, 'message_id': 1}])
    with pytest.raises(ValueError, match='user decision'):
        review.submit(path, 'batch-0001', submission(True), project)


def test_sealed_delivery_requires_second_read_and_keeps_run_incomplete(run):
    path, project = run
    review.show(path, 'batch-0001')
    review.submit(path, 'batch-0001', submission(True), project)
    with pytest.raises(ValueError, match='parts'):
        review.accept_audit(path, 'batch-0001', {'1': 'Quote checked', '2': 'Weather'})
    review.show(path, 'batch-0001', audit=True)
    review.accept_audit(path, 'batch-0001', {'1': 'Quote checked; actual calls; country unknown', '2': 'Weather'})
    class Bridge:
        records = []
        calls = 0
        def read(self):
            return self.records
        def create(self, payload):
            self.calls += 1
            self.records.append({'id': payload['id'], 'candidateId': payload['candidateId'],
                                 'textHash': hashlib.sha256(payload['summary']['markdown'].encode()).hexdigest()})
            return {'created': True}
    bridge = Bridge()
    review.deliver_batch(path, 'batch-0001', project, bridge=bridge)
    review.deliver_batch(path, 'batch-0001', project, bridge=bridge)
    assert bridge.calls == 1
    assert review.status(path)['accounted'] == 2
    assert not read(path / 'state.json')['analysis_complete']
    assert not read(path / 'state.json')['delivery_complete']
    with pytest.raises(ValueError, match='immutable'):
        review.submit(path, 'batch-0001', submission(), project)


def test_codex_executor_never_calls_external_model(run):
    path, project = run
    def forbidden(*args):
        raise AssertionError('Model API must never be called')
    result = analyze(path, {'analysis_executor': 'codex', 'project_root': str(project)}, model_fn=forbidden)
    assert result['status'] == 'awaiting_agent'
    assert result['analysis_executor'] == 'codex'


def test_company_profile_is_enriched_before_first_crm_write(run, monkeypatch):
    from telegram_project_radar.company_lookup import CompanyLookup
    path, project = run
    profile_url = 'https://www.linkedin.com/company/example-company'
    calls = []
    def lookup_profile(self, company, url):
        calls.append((company, url))
        return {'schema_version': 'company-profile-v1', 'status': 'ready', 'company_name': company,
                'linkedin_url': url, 'source_url': url, 'website_url': 'https://example.com',
                'domain': 'example.com', 'city': 'Barcelona', 'country': 'ES',
                'checked_on': date.today().isoformat(), 'missing': []}
    monkeypatch.setattr(CompanyLookup, 'lookup_profile', lookup_profile)
    data = submission(True)
    data['candidates'][0].update(kind='COMPANY', company_name='Example Company', tracks=['infinity','avans'],
        company_identity={'company_name':'Example Company','status':'confirmed','source_url':profile_url,
                          'checked_on':date.today().isoformat(),'reason':'Same voice product in the source.'})
    review.show(path,'batch-0001');review.submit(path,'batch-0001',data,project)
    review.show(path,'batch-0001',audit=True);review.accept_audit(path,'batch-0001',{'1':'Company and calls verified','2':'Weather'})
    class Bridge:
        records=[]
        def read(self):return self.records
        def create(self, p):
            assert p['reviewStatus']=='NEW'
            assert p['companyProfile']['domain']=='example.com'
            assert p['companyProfile']['identity']['source_url']==profile_url
            self.records.append({'id':p['id'],'candidateId':p['candidateId'],'textHash':hashlib.sha256(p['summary']['markdown'].encode()).hexdigest()})
            return {'created':True}
    bridge=Bridge()
    review.deliver_batch(path,'batch-0001',project,bridge=bridge)
    review.deliver_batch(path,'batch-0001',project,bridge=bridge)
    assert len(calls)==len(bridge.records)==1


def test_stale_audit_cannot_deliver(run):
    path, project = run
    review.show(path, 'batch-0001')
    review.submit(path, 'batch-0001', submission(True), project)
    review.show(path, 'batch-0001', audit=True)
    review.accept_audit(path, 'batch-0001', {'1': 'Quote checked', '2': 'Weather'})
    root = path / 'agent-review/batch-0001'
    answer = read(root / 'answer.json')
    answer['results'][0]['candidates'][0]['title'] = 'Changed after audit'
    dump(root / 'answer.json', answer)
    with pytest.raises(ValueError, match='audited answer'):
        review.deliver_batch(path, 'batch-0001', project)


def test_unknown_company_size_holds_projects_but_keeps_avans(run):
    path, project = run
    review.show(path, 'batch-0001')
    data = submission(True)
    data['candidates'][0]['tracks'] = ['projects', 'avans']
    review.submit(path, 'batch-0001', data, project)
    review.show(path, 'batch-0001', audit=True)
    review.accept_audit(path, 'batch-0001', {'1': 'Incoming calls checked', '2': 'Weather'})
    class Bridge:
        records = []
        def read(self):
            return self.records
        def create(self, payload):
            assert 'Проекты' not in payload['whyFit']['markdown']
            assert 'Аванс' in payload['whyFit']['markdown']
            self.records.append({'id': payload['id'], 'candidateId': payload['candidateId'],
                                 'textHash': hashlib.sha256(payload['summary']['markdown'].encode()).hexdigest()})
            return {'created': True}
    review.deliver_batch(path, 'batch-0001', project, bridge=Bridge())


@pytest.mark.parametrize('identity_case,expected_action,expected_lookups', [
    ('missing', 'held', 0),
    ('different_profile', 'held', 1),
    ('confirmed', 'created', 1),
])
def test_project_delivery_requires_matching_company_identity(run, monkeypatch, identity_case,
                                                           expected_action, expected_lookups):
    from telegram_project_radar.company_lookup import CompanyLookup
    from telegram_project_radar.company_size import CompanySize

    path, project = run
    calls = []
    profile = 'https://www.linkedin.com/company/carwash-operator'

    def lookup(self, company):
        calls.append(company)
        # The public search returns a valid size, but that alone is not identity proof.
        fact = CompanySize(company, 2, 10, profile, 'Company size: 2-10 employees', date.today())
        return self.result(company, 'live_company_profile', [fact])

    monkeypatch.setattr(CompanyLookup, 'lookup', lookup)
    data = submission(True)
    c = data['candidates'][0]
    c.update(tracks=['projects'], company_name='i-Line')
    if identity_case != 'missing':
        c['company_identity'] = {'status': 'confirmed', 'company_name': 'i-Line',
            'source_url': profile if identity_case == 'confirmed' else 'https://www.linkedin.com/company/i-line',
            'checked_on': date.today().isoformat(),
            'reason': 'Profile describes the same carwash queue product linked by the employer.'}
    review.show(path, 'batch-0001')
    review.submit(path, 'batch-0001', data, project)
    review.show(path, 'batch-0001', audit=True)
    review.accept_audit(path, 'batch-0001', {'1': 'Source and company identity checked', '2': 'Weather'})

    class Bridge:
        records = []

        def read(self):
            return self.records

        def create(self, payload):
            self.records.append({'id': payload['id'], 'candidateId': payload['candidateId'],
                'textHash': hashlib.sha256(payload['summary']['markdown'].encode()).hexdigest()})
            return {'created': True}

    bridge = Bridge()
    review.deliver_batch(path, 'batch-0001', project, bridge=bridge)
    root = path / 'agent-review/batch-0001'
    assert read(root / 'delivery.json')['items'][0]['action'] == expected_action
    assert len(calls) == expected_lookups
    assert len(bridge.records) == (expected_action == 'created')
    if expected_action == 'held':
        item = read(root / 'sealed-delivery/candidates.json')['items'][0]
        assert item['company_size_check']['status'] == 'unknown'
