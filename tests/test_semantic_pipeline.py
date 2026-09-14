import hashlib
from datetime import datetime, timezone

import pytest

from telegram_project_radar.pipeline import canonical, create_run, database, dump, read
from telegram_project_radar.semantic_delivery import deliver, payload
from telegram_project_radar.semantic_pipeline import analyze, chunks, reply_context


class Telegram:
    def __init__(self,*args):self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def wait_connected(self):pass
    def _request(self,r,timeout=None):
        self.calls.append(r)
        if r['@type']=='getMessage':
            return {'id':r['message_id'],'chat_id':r['chat_id'],'date':1788000000,
                'content':{'@type':'messageText','text':{'text':'Контекст до начала периода'}}}
        return {'link':'https://t.me/example/1'}


def candidate():
    return {'candidate_id':'radar-'+'a'*20,'title':'Автоматизация записи','tracks':['projects','avans'],
        'kind':'PROJECT','role':'customer','market':'russia','fact':'Не успевают подтверждать запись',
        'hypothesis':'Проверить голосового помощника','unknowns':['Объём'],
        'chat_id':-1001,'message_id':5,'source':'Test','source_text':'Не успеваем подтверждать запись',
        'source_url':'https://t.me/example/1','published_at':'2026-09-02T00:00:00+00:00','rules_version':'test'}


def prepared(tmp_path):
    root=tmp_path/'runs'
    run=create_run(root,{'start':'2026-09-01T00:00:00+02:00','end':'2026-09-03T00:00:00+02:00'},datetime(2026,9,4,tzinfo=timezone.utc))
    state=read(run/'state.json');state.update(collection_complete=True,analysis_complete=True)
    dump(run/'state.json',state)
    dump(run/'candidates.json',{'items':[candidate()],'model_revision':'test-revision'})
    dump(run/'analysis-verification.json',{'all_text_processed':True,'semantic_quality_verified':False})
    return run,{'data_dir':str(root),'delivery_enabled':True}


def test_outside_period_parent_is_context_not_primary(tmp_path):
    db=database(tmp_path/'m.db');tg=Telegram()
    primary=[{'message_id':5,'text':'Нужна помощь','reply_to_message_id':1,'reply_to_chat_id':-1001}]
    extra,gaps=reply_context(tg,db,-1001,primary)
    assert not gaps and len(tg.calls)==1
    chunk=next(chunks(primary,extra_context=extra))
    assert chunk['primary_ids']==[5]
    assert {m['message_id'] for m in chunk['messages']}=={1,5}
    reply_context(tg,db,-1001,primary)
    assert len(tg.calls)==1
    db.close()


def test_delivery_stays_blocked_without_matching_quality_acceptance(tmp_path):
    run,cfg=prepared(tmp_path)
    dump(run.parent/'semantic-acceptance.json',{'passed':True,'model_revision':'old'})
    assert deliver(run,cfg)['status']=='blocked'
    assert not read(run/'state.json')['delivery_complete']


def test_delivery_recovers_ambiguous_write_and_preserves_reject(tmp_path):
    run,cfg=prepared(tmp_path)
    dump(run.parent/'semantic-acceptance.json',{'passed':True,'model_revision':'test-revision'})
    class Bridge:
        def __init__(self):self.rows=[];self.writes=0
        def read(self):return self.rows
        def create(self,p):
            self.writes+=1
            self.rows.append({'id':p['id'],'candidateId':p['candidateId'],'status':'REJECT','deletedAt':'2026-09-03',
                'textHash':hashlib.sha256(p['summary']['markdown'].encode()).hexdigest()})
            raise RuntimeError('Connection lost after write')
    bridge=Bridge()
    with pytest.raises(RuntimeError):deliver(run,cfg,bridge,Telegram)
    assert not read(run/'state.json')['delivery_complete']
    assert deliver(run,cfg,bridge,Telegram)['delivery_complete']
    assert bridge.writes==1 and bridge.rows[0]['status']=='REJECT'
    assert read(run/'delivery.json')['items'][0]['action']=='preserved'
    rendered=payload(candidate())
    assert rendered['summary']['markdown']==candidate()['source_text']
    assert 'Аванс' in rendered['whyFit']['markdown']
    assert 'fitScore' not in rendered


def test_every_primary_text_accounted_and_reviewed_source_not_reissued(tmp_path):
    run,cfg=prepared(tmp_path)
    cfg['project_root']=str(tmp_path)
    (tmp_path/'PROFESSIONAL_PROFILE.md').write_text('Тестовый профиль')
    review=tmp_path/'.radar/telephony-2026-09-12'
    dump(review/'findings.json',{'cards':[{'chat_id':-1001,'message_id':5,'card_id':'TEL-01'}]})
    dump(review/'review/queue.json',{'decisions':{'TEL-01':{'decision':'reject'}}})
    db=database(run/'messages.sqlite3')
    for mid in [5,6]:
        row={'chat_id':-1001,'message_id':mid,'sent_at':'2026-09-02T00:00:00+00:00','text':'Не успеваем подтверждать запись','content_type':'messageText'}
        db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?)',(-1001,2,mid,row['sent_at'],'hash',canonical(row)))
    db.commit();db.close()
    state=read(run/'state.json');state.update(analysis_complete=False,text_messages=2,sources=[{'chat_id':-1001,'title':'Test','verified_pass':2,'complete':True}]);dump(run/'state.json',state)
    def model(chunk,*args):
        c=candidate();c['evidence']=[{'message_id':5,'quote':'подтверждать запись'}]
        return {'results':[{'message_id':mid,'disposition':'candidate','reason':'Неявная потребность','candidates':[c]} for mid in chunk['primary_ids']]}
    result=analyze(run,cfg,model,Telegram)
    assert result['analysis_processed']==2 and result['analysis_complete']
    items=read(run/'candidates.json')['items']
    assert len(items)==1 and items[0]['message_id']==6
    assert not read(run/'analysis-verification.json')['semantic_quality_verified']


def test_person_delivery_checks_exact_author_and_persists_separate_name(tmp_path):
    run,cfg=prepared(tmp_path)
    dump(run.parent/'semantic-acceptance.json',{'passed':True,'model_revision':'test-revision'})
    batch=read(run/'candidates.json');batch['items'][0].update(kind='PERSON',title='Anysite — данные для B2B-ресерча')
    dump(run/'candidates.json',batch)
    calls=[]
    class PersonalTelegram(Telegram):
        def get_message_author(self,chat_id,message_id):
            calls.append((chat_id,message_id))
            return {'first_name':'Sviatoslav','last_name':'Dvoretskii'}
    class Bridge:
        def __init__(self):self.rows=[];self.sent=[]
        def read(self):return self.rows
        def create(self,p):
            self.sent.append(p)
            self.rows.append({'id':p['id'],'candidateId':p['candidateId'],'textHash':hashlib.sha256(p['summary']['markdown'].encode()).hexdigest()})
            return {'created':True}
    bridge=Bridge()
    assert deliver(run,cfg,bridge,PersonalTelegram)['delivery_complete']
    assert calls==[(-1001,5)]
    assert bridge.sent[0]['name']=='Sviatoslav Dvoretskii'
    assert bridge.sent[0]['personProfile']['last_name']=='Dvoretskii'
    assert read(run/'candidates.json')['items'][0]['person_profile']['status']=='ready'
    deliver(run,cfg,bridge,PersonalTelegram)
    assert len(calls)==1 and len(bridge.sent)==1
