"""Independent read-only audit of the materialized full-history collection."""
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


def verify(path):
    state=json.loads((path/'state.json').read_text())
    inventory=json.loads((path/'inventory.json').read_text())
    expected={c['chat_id'] for c in inventory['chats'] if c['included']}
    if state['scope']=='pilot':
        from telegram_project_radar.pipeline import scope_ids
        expected &= set(scope_ids('pilot'))
    actual={c['chat_id'] for c in state['sources']}
    errors=[]
    if actual != expected:errors.append('source_set_mismatch')
    start,end=(datetime.fromisoformat(state[k]) for k in ['start','end'])
    db=sqlite3.connect('file:'+str(path/'messages.sqlite3')+'?mode=ro',uri=True)
    totals={'messages':0,'text_messages':0,'media_messages':0,'text_characters':0}
    per_source=[]
    try:
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok':errors.append('sqlite_integrity_failed')
        for source in state['sources']:
            cid=source['chat_id'];number=source.get('verified_pass')
            if not source.get('complete') or not number:
                errors.append(f'unverified_source:{cid}');continue
            passes=db.execute('SELECT pass,finished,boundary FROM checkpoints WHERE chat_id=? AND pass IN (?,?)',(cid,number-1,number)).fetchall()
            if len(passes)!=2 or any(not row[1] or row[2] not in ('before_start','history_exhausted') for row in passes):
                errors.append(f'missing_boundaries:{cid}')
            for a,b in [(number-1,number),(number,number-1)]:
                differences=db.execute('SELECT message_id,fingerprint FROM snapshots WHERE chat_id=? AND pass=? EXCEPT SELECT message_id,fingerprint FROM snapshots WHERE chat_id=? AND pass=?',(cid,a,cid,b)).fetchall()
                if differences:errors.append(f'pass_difference:{cid}')
            counts={'messages':0,'text_messages':0,'media_messages':0,'text_characters':0}
            for mid,date,fingerprint,payload in db.execute('SELECT message_id,sent_at,fingerprint,payload FROM snapshots WHERE chat_id=? AND pass=?',(cid,number)):
                message=json.loads(payload)
                if message['chat_id']!=cid or message['message_id']!=mid or not start <= datetime.fromisoformat(date) < end:
                    errors.append(f'identity_or_date:{cid}:{mid}')
                if hashlib.sha256(payload.encode()).hexdigest()!=fingerprint:
                    errors.append(f'payload_hash:{cid}:{mid}')
                counts['messages']+=1
                counts['text_messages']+=bool(message['text'])
                counts['media_messages']+=message['content_type']!='messageText'
                counts['text_characters']+=len(message['text'] or '')
            for key in ['messages','text_messages','media_messages']:
                if source.get(key)!=counts[key]:errors.append(f'source_count:{cid}:{key}')
            for key in totals:totals[key]+=counts[key]
            per_source.append({'chat_id':cid,'title':source['title'],**counts})
        for key in ['messages','text_messages','media_messages']:
            if state.get(key)!=totals[key]:errors.append('run_count:'+key)
    finally:db.close()
    return {'run_id':state['run_id'],'collection_verified':not errors and state['collection_complete'],
        'expected_sources':len(expected),'verified_sources':len(per_source),'start':state['start'],'end':state['end'],
        **totals,'all_text_messages_classified':state['analysis_complete'],'media_transcribed':False,
        'errors':errors,'sources':per_source}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report=verify(args.run)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');args.output.chmod(0o600)
    print(json.dumps({k:v for k,v in report.items() if k!='sources'},ensure_ascii=False))
    raise SystemExit(0 if report['collection_verified'] else 1)
