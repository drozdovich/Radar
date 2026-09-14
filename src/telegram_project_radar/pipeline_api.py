"""Private control surface. No arbitrary commands or raw-text endpoints."""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .pipeline import ModelNotConfigured, collection, create_run, dump, now, public_status, read

RUN_ID = re.compile(r'run-[0-9a-f]{24}')


def validate_bind(config):
    try:
        address = ipaddress.IPv4Address(config.get('bind', '127.0.0.1'))
        if address.is_unspecified or address.is_multicast or not address.is_private:
            raise ValueError
        peers = config.get('allowed_peers', ['127.0.0.1'])
        if not address.is_loopback and 'allowed_peers' not in config:
            raise ValueError
        if not isinstance(peers, list) or not all(isinstance(peer, str) for peer in peers):
            raise ValueError
        for peer in peers:
            ipaddress.IPv4Address(peer)
        return str(address)
    except (ValueError, TypeError):
        raise ValueError('Use a specific private IPv4/loopback bind and an explicit peer list for network access') from None


class Service:
    def __init__(self, config):
        self.config = config
        self.root = Path(config['data_dir'])
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.busy = None

    def path(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise ValueError('Invalid run ID')
        p = self.root / run_id
        if not (p / 'state.json').exists():
            raise FileNotFoundError('Run not found')
        return p

    def start(self, run_id, stage):
        path = self.path(run_id)
        state = read(path / 'state.json')
        completed = {'collect': 'collection_complete', 'analyze': 'analysis_complete', 'deliver': 'delivery_complete'}
        if state.get(completed[stage]):
            return public_status(state)
        with self.lock:
            if self.busy:
                if self.busy != (run_id, stage):
                    raise RuntimeError('Another stage owns the collector; retry later')
                return public_status(read(path / 'state.json'))
            if stage == 'analyze' and not state['collection_complete']:
                raise ValueError('Collection must be complete before analysis')
            if stage == 'deliver' and not state['analysis_complete']:
                raise ValueError('Analysis must be complete before delivery')
            self.busy = (run_id, stage)
            state.update(stage=stage, status='running', updated_at=now())
            state.pop('error', None)
            dump(path / 'state.json', state)
            threading.Thread(target=self.work, args=(run_id, stage), daemon=True).start()
        return public_status(state)

    def work(self, run_id, stage):
        path = self.path(run_id)
        try:
            if stage == 'collect':
                collection(self.root, run_id)
            elif stage == 'analyze':
                from .semantic_pipeline import analyze
                analyze(path, self.config)
            elif stage == 'deliver':
                from .semantic_pipeline import deliver
                deliver(path, self.config)
        except Exception as exc:
            state = read(path / 'state.json')
            state.update(stage=stage, status='blocked' if isinstance(exc, ModelNotConfigured) else 'failed',
                         error=str(exc)[:300], updated_at=now())
            dump(path / 'state.json', state)
        finally:
            with self.lock:
                self.busy = None


def handler(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            allowed = service.config.get('allowed_peers', ['127.0.0.1'])
            if self.client_address[0] not in allowed:
                self.send(403, {'error': 'peer_not_allowed'})
                return False
            expected = 'Bearer ' + service.config['token']
            if not hmac.compare_digest(self.headers.get('Authorization', ''), expected):
                self.send(401, {'error': 'unauthorized'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == '/health':
                self.send(200, {'service': 'radar-pipeline', 'version': __version__, 'busy': bool(service.busy),
                    'analysis_executor': service.config.get('analysis_executor', 'model_api'),
                    'model_configured': bool(service.config.get('model', {}).get('api_key'))})
            elif self.path.startswith('/runs/'):
                try:
                    path = service.path(self.path.removeprefix('/runs/'))
                    self.send(200, public_status(read(path / 'state.json')))
                except (ValueError, FileNotFoundError):
                    self.send(404, {'error': 'run_not_found'})
            else:
                self.send(404, {'error': 'not_found'})

        def do_POST(self):
            if not self.authorized():
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16384:
                    raise ValueError('Body must be 1..16384 bytes')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Object required')
                if self.path == '/runs':
                    allowed = {'start', 'end', 'mode', 'scope'}
                    if set(body) - allowed:
                        raise ValueError('Unexpected request fields')
                    with service.lock:
                        path = create_run(service.root, body)
                    self.send(200, public_status(read(path / 'state.json')))
                else:
                    parts = self.path.strip('/').split('/')
                    if len(parts) != 3 or parts[0] != 'runs' or parts[2] not in ('collect', 'analyze', 'deliver'):
                        self.send(404, {'error': 'not_found'})
                        return
                    self.send(200, service.start(parts[1], parts[2]))
            except (ValueError, KeyError, json.JSONDecodeError):
                self.send(400, {'error': 'invalid_request'})
            except FileNotFoundError:
                self.send(404, {'error': 'run_not_found'})
            except RuntimeError:
                self.send(409, {'error': 'stage_busy'})
            except Exception:
                self.send(500, {'error': 'internal_error'})
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    if args.config.stat().st_mode & 0o077:
        raise SystemExit('Config must be private (mode 0600)')
    config = read(args.config)
    if len(config.get('token', '')) < 32:
        raise SystemExit('A strong control token is required')
    config['bind'] = validate_bind(config)
    os.umask(0o077)
    service = Service(config)
    # Interrupted stages are retried explicitly by the operator; never report them complete.
    for p in service.root.glob('run-*/state.json'):
        s = read(p)
        if s['status'] == 'running':
            s.update(status='interrupted', updated_at=now())
            dump(p, s)
    ThreadingHTTPServer((config['bind'], config.get('port', 8766)), handler(service)).serve_forever()


if __name__ == '__main__':
    main()
