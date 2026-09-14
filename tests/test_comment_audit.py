from telegram_project_radar.comment_audit import thread_pass
from telegram_project_radar.pipeline import dump
from telegram_project_radar.agent_review import status


def test_short_comment_pages_and_repeated_anchor_do_not_end_read():
    def msg(i):
        return {'id':i,'chat_id':-1000000000109,'date':1788300000,
                'content':{'@type':'messageText','text':{'text':str(i)}}}
    class Telegram:
        def __init__(self):
            self.pages=iter([[msg(9)],[msg(9)],[msg(5)],[]]);self.cursors=[]
        def _request(self,r):
            self.cursors.append(r['from_message_id']);return {'messages':next(self.pages)}
    tg=Telegram()
    assert len(thread_pass(tg,-1000000000109,99)) == 2
    assert tg.cursors == [0,9,8,5]


def test_weekly_completion_requires_matching_comment_audit(tmp_path):
    state={'run_id':'weekly','scope':'weekly_sources','start':'a','end':'b','sources':[]}
    dump(tmp_path/'state.json',state)
    dump(tmp_path/'agent-review/manifest.json',{'text_messages':0,'batches':[]})
    assert not status(tmp_path)['comments_verified']
    dump(tmp_path/'comment-verification.json',{**state,'verified':True,'run_id':'other'})
    assert not status(tmp_path)['comments_verified']
    dump(tmp_path/'comment-verification.json',{**state,'verified':True})
    assert status(tmp_path)['comments_verified']
