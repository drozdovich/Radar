import json
from datetime import datetime, timezone

import pytest

from telegram_project_radar.pipeline import (
    canonical, collect_pass, create_run, database, fingerprints, inventory, public_status, window,
)
from telegram_project_radar.semantic_pipeline import chunks, validate
from telegram_project_radar.tdlib_source import TelegramRuntimeError


def raw(mid, text, date=1788300000):
    return {'id':mid,'chat_id':-1001,'date':date,'content':{'@type':'messageText','text':{'text':text}}}


class History:
    def __init__(self, pages): self.pages=iter(pages);self.requests=[]
    def _request(self, request, timeout=None):
        self.requests.append(request)
        return {'messages':next(self.pages)}


def test_full_history_no_keyword_filter_and_short_pages(tmp_path):
    tg=History([[raw(5,'администраторы не успевают подтверждать запись')],[raw(4,'проект без названия технологии')],[raw(3,'граница',1788000000)]])
    db=database(tmp_path/'m.db')
    start=datetime(2026,9,1,tzinfo=timezone.utc);end=datetime(2026,9,3,tzinfo=timezone.utc)
    assert collect_pass(tg,db,-1001,1,start,end)=='before_start'
    assert len(fingerprints(db,-1001,1))==2
    assert all(x['only_local'] is False for x in tg.requests)
    # Completed collection is reusable without another network request.
    assert collect_pass(History([]),db,-1001,1,start,end)=='before_start'
    db.close()


def test_changed_text_detected_with_same_ids(tmp_path):
    db=database(tmp_path/'m.db');a=datetime(2026,9,1,tzinfo=timezone.utc);b=datetime(2026,9,3,tzinfo=timezone.utc)
    for number,text in [(1,'условия до правки'),(2,'условия изменены')]:
        collect_pass(History([[raw(5,text)],[]]),db,-1001,number,a,b)
    assert set(fingerprints(db,-1001,1))==set(fingerprints(db,-1001,2))
    assert fingerprints(db,-1001,1)!=fingerprints(db,-1001,2)
    db.close()


def test_interrupted_page_resumes(tmp_path):
    class Interrupted(History):
        def _request(self, request, timeout=None):
            if self.requests: raise TelegramRuntimeError('network unavailable')
            return super()._request(request,timeout)
    db=database(tmp_path/'m.db');a=datetime(2026,9,1,tzinfo=timezone.utc);b=datetime(2026,9,3,tzinfo=timezone.utc)
    with pytest.raises(TelegramRuntimeError):collect_pass(Interrupted([[raw(5,'первая страница')]]),db,-1001,1,a,b)
    resumed=History([[raw(4,'вторая страница')],[]]);collect_pass(resumed,db,-1001,1,a,b)
    assert resumed.requests[0]['from_message_id']==5
    assert len(fingerprints(db,-1001,1))==2
    db.close()


def test_unknown_message_cannot_disappear(tmp_path):
    db=database(tmp_path/'m.db')
    with pytest.raises(ValueError):collect_pass(History([[{'id':5}]]),db,-1001,1,datetime(2026,9,1,tzinfo=timezone.utc),datetime(2026,9,3,tzinfo=timezone.utc))
    assert not fingerprints(db,-1001,1)
    db.close()


def test_inventory_exhausts_main_archive_and_explicitly_excludes_private():
    class Chats:
        def __init__(self):self.calls=[]
        def _request(self,r,timeout=None):
            self.calls.append(r)
            if r['@type']=='loadChats':raise TelegramRuntimeError('TDLib error 404: all loaded')
            if r['@type']=='getChats':return {'chat_ids':[-1001,12] if r['chat_list']['@type']=='chatListMain' else [-1001,-1002]}
            return {'title':str(r['chat_id']),'type':{'@type':'chatTypePrivate' if r['chat_id']==12 else 'chatTypeSupergroup'}}
    inv=inventory(Chats())
    assert len(inv['chats'])==3
    assert sum(c['included'] for c in inv['chats'])==2
    assert all(x['complete'] for x in inv['lists'])


def test_window_and_idempotent_run(tmp_path):
    clock=datetime(2026,9,13,10,tzinfo=timezone.utc)
    spec={'start':'2026-09-01T00:00:00+02:00','end':'2026-09-13T10:00:00+00:00'}
    p=create_run(tmp_path,spec,clock);assert create_run(tmp_path,spec,clock)==p
    a,b=window({'mode':'daily'},clock)
    assert a.isoformat()=='2026-09-12T03:00:00+00:00'
    assert b.isoformat()=='2026-09-13T03:00:00+00:00'
    with pytest.raises(ValueError):window({'start':'2026-09-01','end':'2026-09-02'},clock)
    with pytest.raises(ValueError):create_run(tmp_path,{**spec,'scope':'arbitrary'},clock)


def test_context_keeps_every_message_and_long_reply_parent():
    ms=[{'message_id':i,'text':'x'*200 if i==1 else 'неявная потребность','reply_to_message_id':1 if i==30 else None} for i in range(1,31)]
    cs=list(chunks(ms,max_chars=500,max_messages=4))
    assert [i for c in cs for i in c['primary_ids']]==list(range(1,31))
    assert 1 in [m['message_id'] for m in cs[-1]['messages']]
    assert cs[0]['messages'][0]['text']=='x'*200


def test_model_must_account_and_ground_evidence():
    chunk={'primary_ids':[1,2],'messages':[{'message_id':1,'text':'Операторы не успевают'},{'message_id':2,'text':'Звонят клиентам в РФ'}]}
    candidate={'title':'Автоматизация','tracks':['infinity','avans'],'kind':'PROJECT','role':'customer','market':'russia','fact':'Есть операторы','hypothesis':'Проверить автоматизацию','unknowns':[],'evidence':[{'message_id':1,'quote':'Операторы'}]}
    good={'results':[{'message_id':1,'disposition':'candidate','reason':'задача','candidates':[candidate]},{'message_id':2,'disposition':'related','reason':'контекст','candidates':[]}]}
    assert validate(good,chunk)[0]['candidates'][0]['tracks']==['avans']
    with pytest.raises(ValueError):validate({'results':good['results'][:1]},chunk)
    candidate['evidence'][0]['quote']='выдуманная цитата'
    with pytest.raises(ValueError):validate(good,chunk)


def test_public_report_has_no_private_text_or_chat_names():
    state={'run_id':'x','stage':'collect','status':'failed','sources':[{'title':'private title','complete':False}],'error':'raw detail','text':'secret'}
    rendered=canonical(public_status(state))
    assert 'private title' not in rendered and 'raw detail' not in rendered and 'secret' not in rendered


def test_daily_resumes_and_catches_missed_days(tmp_path):
    from telegram_project_radar.pipeline import dump, read
    clock=datetime(2026,9,15,10,tzinfo=timezone.utc)
    p=create_run(tmp_path,{'start':'2026-09-01T00:00:00+02:00','end':'2026-09-13T01:00:00+02:00'},clock)
    assert create_run(tmp_path,{'mode':'daily'},clock)==p
    state=read(p/'state.json');state.update(collection_complete=True,delivery_complete=True);dump(p/'state.json',state)
    q=create_run(tmp_path,{'mode':'daily'},clock)
    resumed=read(q/'state.json')
    assert resumed['start']==state['end']
    assert resumed['end']=='2026-09-15T03:00:00+00:00'


def test_tdlib_raised_errors_use_adapter_error_type():
    from telegram_project_radar.tdlib_source import TdlibSource
    class Session:
        def request(self, request, timeout):
            raise RuntimeError('TDLib error 404: Not Found')
    source=TdlibSource.__new__(TdlibSource);source._session=Session()
    with pytest.raises(TelegramRuntimeError, match='404'):
        source._request({'@type':'loadChats'})


def test_daily_never_resumes_archived_broad_scope(tmp_path):
    from telegram_project_radar.pipeline import dump, read
    clock = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)
    dump(tmp_path / "run-old" / "state.json", {"run_id": "run-old", "scope": "all_groups_channels", "delivery_complete": False, "start": "2026-09-01T00:00:00+00:00"})
    path = create_run(tmp_path, {"mode": "daily"}, clock)
    assert path.name != "run-old"
    assert read(path / "state.json")["scope"] == "approved_sources"
    with pytest.raises(ValueError):
        create_run(tmp_path, {"mode": "daily", "scope": "all_groups_channels"}, clock)


def test_approved_inventory_never_enumerates_account():
    from telegram_project_radar.pipeline import APPROVED_CHAT_IDS, approved_inventory
    class Telegram:
        def __init__(self): self.ids = []
        def _request(self, req, **kwargs):
            assert req["@type"] == "getChat"
            assert req["chat_id"] in APPROVED_CHAT_IDS
            self.ids.append(req["chat_id"])
            return {"title": "allowed", "type": {"@type": "chatTypeSupergroup"}}
    tg = Telegram()
    result = approved_inventory(tg, "approved_sources")
    assert tuple(tg.ids) == APPROVED_CHAT_IDS
    assert len(result["chats"]) == 3


def test_weekly_scope_has_separate_cursor_and_exact_inventory(tmp_path):
    from telegram_project_radar.pipeline import dump, read, approved_inventory, WEEKLY_CHAT_IDS
    clock = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
    daily = create_run(tmp_path, {'start':'2026-09-01T00:00:00+02:00','end':'2026-09-20T00:00:00+02:00'}, clock)
    weekly = create_run(tmp_path, {'scope':'weekly_sources','start':'2026-09-01T00:00:00+02:00','end':'2026-09-13T00:00:00+02:00'}, clock)
    assert create_run(tmp_path, {'mode':'daily','scope':'weekly_sources'}, clock) == weekly
    state = read(weekly/'state.json'); state.update(collection_complete=True, delivery_complete=True); dump(weekly/'state.json', state)
    next_run = create_run(tmp_path, {'mode':'daily','scope':'weekly_sources'}, clock)
    assert read(next_run/'state.json')['start'] == state['end']
    assert create_run(tmp_path, {'mode':'daily'}, clock) == daily
    class Telegram:
        def _request(self, req, **kwargs):
            assert req['@type'] == 'getChat'
            assert req['chat_id'] in WEEKLY_CHAT_IDS
            return {'title':'allowed','type':{'@type':'chatTypeSupergroup'}}
    assert {c['chat_id'] for c in approved_inventory(Telegram(),'weekly_sources')['chats']} == set(WEEKLY_CHAT_IDS)


def test_daily_five_am_boundary_dst_and_early_run():
    a,b=window({'mode':'daily'},datetime(2026,10,25,8,tzinfo=timezone.utc))
    assert a.isoformat()=='2026-10-24T03:00:00+00:00'
    assert b.isoformat()=='2026-10-25T04:00:00+00:00'
    a,b=window({'mode':'daily'},datetime(2026,9,15,1,tzinfo=timezone.utc))
    assert b.isoformat()=='2026-09-14T03:00:00+00:00'

def test_five_am_transition_retains_midnight_gap(tmp_path):
    from telegram_project_radar.pipeline import dump, read
    clock=datetime(2026,9,15,7,tzinfo=timezone.utc)
    for scope in ['approved_sources','weekly_sources']:
        p=create_run(tmp_path,{'scope':scope,'start':'2026-09-13T20:00:00+02:00','end':'2026-09-14T00:00:00+02:00'},clock)
        s=read(p/'state.json');s.update(collection_complete=True,delivery_complete=False);dump(p/'state.json',s)
        q=create_run(tmp_path,{'mode':'daily','scope':scope},clock)
        assert read(q/'state.json')['start']=='2026-09-13T22:00:00+00:00'
        assert read(q/'state.json')['end']=='2026-09-15T03:00:00+00:00'
        assert not read(p/'state.json')['delivery_complete']
        assert not read(p/'state.json')['analysis_complete']
        assert create_run(tmp_path,{'mode':'daily','scope':scope},clock)==q
        latest=read(q/'state.json');latest['collection_complete']=True;dump(q/'state.json',latest)
        assert create_run(tmp_path,{'mode':'daily','scope':scope},clock)==q


def test_daily_collection_cursor_uses_instants_and_bounds_long_catchup(tmp_path):
    from telegram_project_radar.pipeline import dump, read
    for name, end in [('offset', '2026-09-14T05:00:00+02:00'),
                      ('utc', '2026-09-14T04:00:00+00:00')]:
        dump(tmp_path/f'run-{name}'/'state.json', {'run_id':f'run-{name}',
            'scope':'approved_sources', 'start':'2026-09-01T00:00:00+02:00', 'end':end,
            'collection_complete':True, 'analysis_complete':False, 'delivery_complete':False})
    path=create_run(tmp_path,{'mode':'daily'},datetime(2026,11,1,10,tzinfo=timezone.utc))
    state=read(path/'state.json')
    assert state['start']=='2026-09-14T04:00:00+00:00'
    assert state['end']=='2026-10-19T04:00:00+00:00'
