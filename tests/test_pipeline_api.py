import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from telegram_project_radar.pipeline import dump, read
from telegram_project_radar.pipeline_api import Service, handler


def test_http_auth_boundaries_and_model_failure(tmp_path):
    cfg = {'data_dir': str(tmp_path), 'allowed_peers': ['127.0.0.1'], 'token': 'x'*48, 'model': {}}
    service = Service(cfg)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def call(path, body=None, token='x'*48):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'})
        return json.load(urllib.request.urlopen(request, timeout=3))
    try:
        with pytest.raises(urllib.error.HTTPError) as exc: call('/health', token='wrong')
        assert exc.value.code == 401
        assert call('/health')['model_configured'] is False
        with pytest.raises(urllib.error.HTTPError) as exc: call('/runs', {'command':'arbitrary'})
        assert exc.value.code == 400
        spec={'start':'2026-09-01T00:00:00+02:00','end':'2026-09-02T00:00:00+02:00'}
        run=call('/runs',spec)
        assert call('/runs',spec)['run_id']==run['run_id']
        with pytest.raises(urllib.error.HTTPError) as exc: call('/runs/'+run['run_id']+'/analyze',{})
        assert exc.value.code == 400
        p=service.path(run['run_id'])/'state.json';state=read(p);state['collection_complete']=True;dump(p,state)
        service.work(run['run_id'],'analyze')
        status=call('/runs/'+run['run_id'])
        assert status['status']=='blocked'
        assert not status['analysis_complete'] and not status['delivery_complete']
        assert 'error' not in status
        cfg['allowed_peers']=[]
        with pytest.raises(urllib.error.HTTPError) as exc: call('/health')
        assert exc.value.code==403
    finally:
        server.shutdown();server.server_close();thread.join()
