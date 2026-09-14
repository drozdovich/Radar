import unittest
from telegram_project_radar.events import event_data
from telegram_project_radar.semantic_delivery import payload, eligible_routes
from telegram_project_radar.semantic_pipeline import validate

class EventsTest(unittest.TestCase):
    def item(self):
        return dict(candidate_id='radar-'+'a'*20,kind='EVENT',tracks=[],title='Hack Day',source='QA',published_at='2026-09-14T00:00:00Z',rules_version='astra-three-tracks-v1',source_text='Full-time AWS event at a company with 10000 people',source_url='https://t.me/qa/1',fact='Event',hypothesis='Potential professional contacts, attendance not confirmed',role='reference',market='unknown',unknowns=['Время','Цена'],event=dict(name='Hack Day',startDate='2026-10-24',city='Barcelona',eventFormat='Офлайн'))
    def test_unknown_time_and_new_inbox(self):
        p=payload(self.item());self.assertEqual(p['reviewStatus'],'NEW');self.assertEqual(p['candidateType'],'EVENT');self.assertIsNone(p['eventData']['startTime']);self.assertIsNone(p['eventData']['cost']);self.assertEqual(p['summary']['markdown'],self.item()['source_text'])
    def test_job_filters_do_not_apply(self):
        item=self.item();item['tracks']=['projects'];self.assertIsNone(eligible_routes(item)[1]);self.assertEqual(eligible_routes(item)[0]['tracks'],['projects'])
    def test_occurrence_identity(self):
        a=self.item();b={**a,'candidate_id':'radar-'+'b'*20,'event':{**a['event'],'name':'HACK   DAY'}}
        self.assertEqual(event_data(a)['eventKey'],event_data(b)['eventKey'])
        b['event']['startDate']='2026-10-25';self.assertNotEqual(event_data(a)['eventKey'],event_data(b)['eventKey'])
        a['event']['startDate']=None;b['event']['startDate']=None;self.assertNotEqual(event_data(a)['eventKey'],event_data(b)['eventKey'])
    def test_empty_commercial_tracks_are_valid(self):
        a=self.item();a['evidence']=[{'message_id':1,'quote':'Full-time'}]
        validate({'results':[{'message_id':1,'reason':'QA','disposition':'candidate','candidates':[a]}]},{'primary_ids':[1],'messages':[{'message_id':1,'text':a['source_text']}]})
    def test_invalid_date_or_time(self):
        a=self.item();a['event']['startDate']='2026-02-30'
        with self.assertRaises(ValueError):event_data(a)
        a['event']['startDate']=None;a['event']['startTime']='28:00'
        with self.assertRaises(ValueError):event_data(a)


def test_event_saved_batch_reaches_inbox_and_repeat_preserves_decision(tmp_path):
    import hashlib
    from telegram_project_radar.pipeline import dump, read
    from telegram_project_radar.semantic_delivery import deliver
    item=EventsTest().item()
    dump(tmp_path/'state.json',{'analysis_complete':True,'delivery_complete':False})
    dump(tmp_path/'candidates.json',{'items':[item],'model_revision':'event-test'})
    dump(tmp_path/'analysis-verification.json',{})
    dump(tmp_path/'semantic-acceptance.json',{'passed':True,'model_revision':'event-test'})
    class Bridge:
        rows=[]
        writes=0
        def read(self):return self.rows
        def create(self,p):
            assert p['candidateType']=='EVENT' and p['reviewStatus']=='NEW'
            assert p['eventData']['startDate']=='2026-10-24'
            self.writes+=1
            self.rows=[{'id':p['id'],'candidateId':p['candidateId'],'status':'APPROVE','textHash':hashlib.sha256(p['summary']['markdown'].encode()).hexdigest()}]
            return {'created':True}
    b=Bridge();cfg={'data_dir':str(tmp_path),'delivery_enabled':True}
    assert deliver(tmp_path,cfg,b)['delivery_complete']
    assert deliver(tmp_path,cfg,b)['delivery_complete']
    assert b.writes==1 and b.rows[0]['status']=='APPROVE'
    assert read(tmp_path/'delivery.json')['items'][0]['action']=='preserved'
