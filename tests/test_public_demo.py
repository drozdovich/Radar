import copy
import json
import socket
import subprocess
import sys

import pytest

from telegram_project_radar import config
from telegram_project_radar.demo import BLOCKS, run_demo
from telegram_project_radar.pipeline_api import validate_bind


def test_demo_is_offline_preserves_sources_and_decisions(tmp_path, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError('The public demo must never contact external systems')
    monkeypatch.setattr(socket, 'socket', deny_network)
    monkeypatch.setattr(subprocess, 'run', deny_network)
    monkeypatch.setattr(subprocess, 'Popen', deny_network)
    monkeypatch.setenv('RADAR_PROFILE_FILE', str(tmp_path / 'unreadable-private-profile.md'))
    first = run_demo(tmp_path / 'demo')
    second = run_demo(tmp_path / 'demo')
    assert first == second
    report = json.loads((tmp_path / 'demo/demo.json').read_text())
    assert report['synthetic_only'] and report['messages_accounted'] == 2
    assert report['created'] == report['preserved_on_retry'] == 2
    assert [c['summary']['markdown'] for c in report['cards']] == list(BLOCKS)
    assert [c['reviewStatus'] for c in report['decisions']] == ['APPROVE', 'REJECT']
    assert len({c['candidateId'] for c in report['cards']}) == 2


def test_missing_sources_never_enable_live_collection(tmp_path):
    empty = config.load_sources(tmp_path / 'absent.json')
    assert empty == {'approved': [], 'additional': [], 'comment_pairs': []}
    with pytest.raises(ValueError, match='own approved sources'):
        config.require_live_sources()  # The test registry is explicitly synthetic.


@pytest.mark.parametrize('problem', ['boolean_id', 'duplicate', 'discussion_outside_allowlist'])
def test_invalid_source_configuration_is_rejected(tmp_path, problem):
    value = copy.deepcopy(config.SOURCE_CONFIG)
    if problem == 'boolean_id':
        value['approved'][0]['chat_id'] = True
    elif problem == 'duplicate':
        value['additional'].append(value['approved'][0])
    else:
        value['comment_pairs'][0]['discussion_id'] = -987654321
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='Invalid sources configuration'):
        config.load_sources(path)


@pytest.mark.parametrize('bind', ['0.0.0.0', '8.8.8.8', '224.0.0.1', '::', 'hostname'])
def test_control_api_does_not_bind_public_or_wildcard_addresses(bind):
    with pytest.raises(ValueError, match='specific private'):
        validate_bind({'bind': bind, 'allowed_peers': []})


def test_network_binding_requires_explicit_peers():
    assert validate_bind({}) == '127.0.0.1'
    with pytest.raises(ValueError, match='explicit peer list'):
        validate_bind({'bind': '10.0.0.2'})
    assert validate_bind({'bind': '10.0.0.2', 'allowed_peers': ['10.0.0.3']}) == '10.0.0.2'
