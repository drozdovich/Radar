from pathlib import Path

import pytest

from telegram_project_radar import agent_review as review
from telegram_project_radar import content_recovery as recovery
from telegram_project_radar.pipeline import canonical, database, dump, read
from telegram_project_radar.telegram_runtime import load_runtime, SOURCE_COMMIT, SUPPORTED_VERSION


@pytest.fixture
def run(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'PROFESSIONAL_PROFILE.md').write_text('Three tracks')
    path = tmp_path / 'run'
    dump(path / 'state.json', {'run_id': 'run-recovery', 'collection_complete': True,
        'analysis_complete': False, 'delivery_complete': False, 'text_messages': 1, 'messages': 2,
        'sources': [{'chat_id': -100123, 'title': 'Source', 'verified_pass': 2}]})
    db = database(path / 'messages.sqlite3')
    for mid, kind, text in [(1, 'messageText', 'Hello'), (2, 'messageUnsupported', None)]:
        msg = {'chat_id': -100123, 'message_id': mid, 'sent_at': '2026-09-01T00:00:00+00:00',
               'content_type': kind, 'text': text}
        db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?)',
                   (-100123, 2, mid, msg['sent_at'], review.digest(msg), canonical(msg)))
    db.commit()
    db.close()
    review.prepare(path, project)
    return path, project


class Telegram:
    calls = 0

    def _request(self, request, timeout):
        assert request['@type'] == 'searchChatMessages'
        assert request['query'] == ''
        assert 'filter' not in request and 'sender_id' not in request
        self.calls += 1
        return {'messages': [{'chat_id': request['chat_id'], 'id': request['from_message_id'],
                             'date': 1788220800, 'content': {'@type': 'messageText',
                                                         'text': {'text': 'Need incoming call automation'}}}]}


def test_recovery_preserves_original_and_appends_once(run):
    path, project = run
    original_db = (path / 'messages.sqlite3').read_bytes()
    original_batch = (path / 'agent-review/batch-0001/input.json').read_bytes()
    tg = Telegram()
    assert recovery.gap_count(path) == 1
    assert recovery.recover(path, tg)['verified'] == 1
    assert tg.calls == 2
    # A recovered text not yet included in review must still block completion.
    assert recovery.gap_count(path) == 1
    assert recovery.append_review(path)['appended_text'] == 1
    assert recovery.gap_count(path) == 0
    assert recovery.append_review(path)['appended_text'] == 0
    assert review.status(path)['total_text'] == 2
    recovery.recover(path, tg)
    assert tg.calls == 2
    assert (path / 'agent-review/batch-0001/input.json').read_bytes() == original_batch
    assert (path / 'messages.sqlite3').read_bytes() == original_db


def test_changed_or_wrong_original_is_not_verified(run):
    path, _ = run
    class Changed(Telegram):
        def _request(self, request, timeout):
            result = super()._request(request, timeout)
            result['messages'][0]['content']['text']['text'] += str(self.calls)
            return result
    assert recovery.recover(path, Changed())['verified'] == 0
    assert recovery.append_review(path)['appended_text'] == 0
    class WrongChat(Telegram):
        def _request(self, request, timeout):
            result = super()._request(request, timeout)
            result['messages'][0]['chat_id'] = -999
            return result
    assert recovery.recover(path, WrongChat())['unresolved'] == 1


def test_existing_review_without_nontext_count_migrates_without_duplicates(run):
    path, _ = run
    file = path / 'agent-review/manifest.json'
    manifest = read(file)
    del manifest['nontext_messages']
    dump(file, manifest)
    recovery.recover(path, Telegram())
    assert recovery.append_review(path)['appended_text'] == 1
    assert read(file)['nontext_messages'] == 0
    assert recovery.append_review(path)['appended_text'] == 0
    assert len(read(file)['batches']) == 2


def test_all_old_text_reviewed_does_not_hide_unsupported_or_stale_audit(run):
    path, project = run
    review.show(path, 'batch-0001')
    review.submit(path, 'batch-0001', {'groups': [
        {'indices': '1', 'disposition': 'irrelevant', 'reason': 'Greeting'}]}, project)
    review.show(path, 'batch-0001', audit=True)
    review.accept_audit(path, 'batch-0001', {'1': 'Greeting checked'})
    assert not read(path / 'state.json')['analysis_complete']
    assert review.status(path)['unresolved_content'] == 1
    audit_path = path / 'agent-review/batch-0001/audit.json'
    audit = read(audit_path)
    audit['answer_sha256'] = 'tampered'
    dump(audit_path, audit)
    assert review.status(path)['audited'] == 0


def test_tampered_recovery_receipt_reopens_gap(run):
    path, _ = run
    recovery.recover(path, Telegram())
    recovery.append_review(path)
    root = path / 'content-recovery'
    target = recovery.prepare(path)['targets'][0]
    file = recovery.receipt_path(root, target)
    data = read(file)
    data['message']['text'] = 'Modified'
    dump(file, data)
    assert recovery.gap_count(path) == 1


def test_structured_recovery_includes_original_polls_and_previous_decoder_gaps(run):
    path, _ = run
    target = recovery.prepare(path)['targets'][0]
    old_message = {'chat_id': -100123, 'message_id': 2, 'sent_at': '2026-09-01T00:00:00+00:00',
                   'content_type': 'messagePoll', 'text': None}
    file = recovery.receipt_path(path / 'content-recovery', target)
    dump(file, {**target, 'verified': True, 'message': old_message,
                'first_sha256': review.digest(old_message), 'second_sha256': review.digest(old_message)})
    original_receipt = file.read_bytes()
    db = database(path / 'messages.sqlite3')
    original_poll = {**old_message, 'message_id': 3}
    db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?)', (-100123, 2, 3,
               original_poll['sent_at'], review.digest(original_poll), canonical(original_poll)))
    db.commit(); db.close()
    state = read(path / 'state.json'); state['messages'] = 3; dump(path / 'state.json', state)
    manifest_path = path / 'agent-review/manifest.json'
    manifest = read(manifest_path); manifest['nontext_messages'] = 2; dump(manifest_path, manifest)
    original_db = (path / 'messages.sqlite3').read_bytes()
    class Polls(Telegram):
        def _request(self, request, timeout):
            result = super()._request(request, timeout)
            result['messages'][0]['content'] = {'@type': 'messagePoll', 'poll': {
                'question': {'@type': 'formattedText', 'text': 'Need call automation?'},
                'options': [{'text': {'text': 'Yes'}, 'voter_count': self.calls}]}}
            return result
    assert recovery.gap_count(path) == 2
    assert recovery.recover(path, Polls(), scope='structured')['verified'] == 2
    assert recovery.gap_count(path) == 2  # Retrieved, but still not queued.
    assert recovery.append_review(path, scope='structured')['appended_text'] == 2
    assert recovery.append_review(path, scope='structured')['appended_text'] == 0
    assert recovery.gap_count(path) == 0
    assert read(manifest_path)['nontext_messages'] == 0
    assert read(manifest_path)['text_messages'] == 3
    assert file.read_bytes() == original_receipt
    assert (path / 'messages.sqlite3').read_bytes() == original_db


def test_pinned_runtime_fails_closed_when_library_or_version_changes(tmp_path):
    import hashlib
    library = tmp_path / 'libtdjson.dylib'
    library.write_bytes(b'verified test library')
    config = {'version': SUPPORTED_VERSION, 'source_commit': SOURCE_COMMIT,
              'library': str(library), 'library_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
              'database_directory': str(tmp_path), 'files_directory': str(tmp_path)}
    manifest = tmp_path / 'runtime.json'
    assert load_runtime(manifest) is None
    dump(manifest, config)
    assert load_runtime(manifest) == config
    library.write_bytes(b'different library')
    with pytest.raises(ValueError, match='hash changed'):
        load_runtime(manifest)
    config['version'] = '1.8.0'
    dump(manifest, config)
    with pytest.raises(ValueError, match='Unverified'):
        load_runtime(manifest)


def test_telegram_history_fetches_full_rich_message_before_normalizing():
    from telegram_project_radar.tdlib_source import TdlibSource, TelegramRuntimeError
    from telegram_project_radar.pipeline import message_record
    class Session:
        full = True
        calls = []
        def request(self, request, timeout):
            self.calls.append(request)
            if request['@type'] == 'getFullRichMessage':
                assert request['chat_id'] == -100123 and request['message_id'] == 7
                return {'@type': 'richMessage', 'is_full': self.full, 'blocks': [
                    {'@type': 'pageBlockParagraph', 'text': {'@type': 'richTextPlain', 'text': 'Need a call centre'}}]}
            return {'messages': [{'@type': 'message', 'chat_id': -100123, 'id': 7, 'date': 1788220800,
                                  'content': {'@type': 'messageRichMessage', 'message': {'is_full': False}}}]}
    source = object.__new__(TdlibSource)
    source._session = Session()
    result = source._request({'@type': 'getChatHistory', 'chat_id': -100123})
    assert message_record(result['messages'][0])['text'] == 'Need a call centre'
    assert [r['@type'] for r in source._session.calls] == ['getChatHistory', 'getFullRichMessage']
    source._session.full = False
    with pytest.raises(TelegramRuntimeError, match='Full rich'):
        source._request({'@type': 'getChatHistory', 'chat_id': -100123})
