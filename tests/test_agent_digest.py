import copy
import hashlib

import pytest

from telegram_project_radar import agent_review as review
from telegram_project_radar.pipeline import canonical, database, dump, read
from telegram_project_radar.semantic_delivery import eligible_routes
from telegram_project_radar.semantic_pipeline import validate


TEXT = ('📌 Position A: incoming calls. https://app.rvc.global/vacancy/view/a\n\n'
        'Position B: outgoing calls. https://app.rvc.global/vacancy/view/b')
BLOCKS = TEXT.split('\n\n')


def candidates():
    result = []
    for block in BLOCKS:
        start = TEXT.index(block)
        result.append({'index': 1, 'quote': block, 'kind': 'DIGEST_POSITION',
            'source_span': {'start': start, 'end': start + len(block)},
            'title': block.split(':')[0], 'tracks': ['avans'], 'role': 'customer',
            'market': 'unknown', 'fact': block, 'hypothesis': 'Check a voice pilot', 'unknowns': ['Volume']})
    return result


def answer(values):
    return {'results': [{'message_id': 1, 'disposition': 'candidate', 'reason': 'Separate positions',
        'candidates': [{k: v for k, v in c.items() if k not in {'index', 'quote'}} |
                       {'evidence': [{'message_id': 1, 'quote': c['quote']}]} for c in values]}]}


CHUNK = {'primary_ids': [1], 'messages': [{'message_id': 1, 'text': TEXT}]}


@pytest.mark.parametrize('problem', ['missing', 'bounds', 'bool', 'overlap', 'wrong_quote', 'mixed'])
def test_digest_spans_and_evidence_must_belong_to_the_position(problem):
    values = candidates()
    if problem == 'missing':
        del values[0]['source_span']
    elif problem == 'bounds':
        values[0]['source_span']['end'] = len(TEXT) + 1
    elif problem == 'bool':
        values[0]['source_span']['start'] = False
    elif problem == 'overlap':
        values[1] = copy.deepcopy(values[0])
    elif problem == 'wrong_quote':
        values[0]['quote'] = BLOCKS[1]
    else:
        values[1]['kind'] = 'PROJECT'
        del values[1]['source_span']
    with pytest.raises(ValueError):
        validate(answer(values), CHUNK)


def test_digest_delivery_keeps_exact_blocks_distinct_ids_and_old_decisions(tmp_path):
    project, path = tmp_path / 'project', tmp_path / 'run'
    project.mkdir()
    (project / 'PROFESSIONAL_PROFILE.md').write_text('Synthetic profile')
    dump(path / 'state.json', {'run_id': 'test', 'collection_complete': True,
        'analysis_complete': False, 'delivery_complete': False, 'text_messages': 1,
        'sources': [{'chat_id': -100123, 'title': 'Synthetic', 'verified_pass': 2}]})
    message = {'chat_id': -100123, 'message_id': 1, 'text': TEXT,
               'sent_at': '2026-09-01T00:00:00+00:00', 'source_url': 'https://t.me/example/1',
               'embedded_urls': ['https://app.rvc.global/vacancy/view/a', 'https://app.rvc.global/vacancy/view/b']}
    db = database(path / 'messages.sqlite3')
    for number in [1, 2]:
        db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?)',
                   (-100123, number, 1, message['sent_at'], review.digest(message), canonical(message)))
    db.commit()
    db.close()
    review.prepare(path, project)
    review.show(path, 'batch-0001')
    review.submit(path, 'batch-0001', {'groups': [{'indices': '1', 'disposition': 'candidate',
                  'reason': 'Two independent positions'}], 'candidates': candidates()}, project)
    review.show(path, 'batch-0001', audit=True)
    review.accept_audit(path, 'batch-0001', {'1': 'Both exact blocks checked separately'})

    class Bridge:
        def __init__(self): self.records, self.payloads = [], []
        def read(self): return self.records
        def create(self, payload):
            self.payloads.append(payload)
            self.records.append({'id': payload['id'], 'candidateId': payload['candidateId'],
                'reviewStatus': 'REJECT', 'textHash': hashlib.sha256(payload['summary']['markdown'].encode()).hexdigest()})
            return {'created': True}
    bridge = Bridge()
    review.deliver_batch(path, 'batch-0001', project, bridge=bridge)
    review.deliver_batch(path, 'batch-0001', project, bridge=bridge)
    assert [p['summary']['markdown'] for p in bridge.payloads] == BLOCKS
    assert len({p['candidateId'] for p in bridge.payloads}) == 2
    assert all(p['candidateType'] == 'DIGEST_POSITION' for p in bridge.payloads)
    assert all(r['reviewStatus'] == 'REJECT' for r in bridge.records)
    saved = read(path / 'agent-review/batch-0001/sealed-delivery/candidates.json')['items']
    assert [i['embedded_urls'] for i in saved] == [[message['embedded_urls'][0]], [message['embedded_urls'][1]]]
    assert all(i['source_message_sha256'] == hashlib.sha256(TEXT.encode()).hexdigest() for i in saved)
    assert read(path / 'state.json')['delivery_complete']


def test_digest_personal_project_cannot_skip_company_size_gate():
    item = {'kind': 'DIGEST_POSITION', 'tracks': ['projects', 'avans'],
            'source_text': 'Need help with incoming calls', 'rules_version': 'astra-three-tracks-v1'}
    routed, reason = eligible_routes(item)
    assert reason
    assert routed['tracks'] == ['avans']
