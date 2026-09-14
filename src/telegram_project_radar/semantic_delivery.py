"""Delivery of reviewed pipeline output through the existing Twenty boundary."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from .config import TELEGRAM_SEARCH_SOURCE
from .company_lookup import size_delivery_hold
from .company_profile import inbox_company_profile
from .events import event_data
from .card_names import person_profile
from .person_context import prepare_person_context
from .inbox import twenty_rich_text_summary
from .morning import TwentyBridge, delivery_hold
from .pipeline import dump, now, read
from .tdlib_source import TdlibSource


def payload(item):
    cid=item['candidate_id']
    rich=lambda text,key:twenty_rich_text_summary(text,candidate_id=cid+':'+key)
    item = dict(item)
    if item['kind'] == 'PERSON' and item.get('person_profile'):
        item['person_profile'] = prepare_person_context(item, item['person_profile'])
    profile = item.get('person_profile') or {}
    name = item['title']
    if item['kind'] == 'PERSON' and profile.get('status') == 'ready':
        name = ' '.join(filter(None, (profile['first_name'], profile['last_name'])))
    track_names={'projects':'Проекты / подработка','infinity':'Infinity','avans':'Аванс'}
    return {
        'id':str(uuid.uuid5(uuid.NAMESPACE_URL,'telegram-project-radar:'+cid)),
        'candidateId':cid,'name':name,'candidateType':item['kind'],'reviewStatus':'NEW',
        'source':item['source'],'publishedAt':item['published_at'],'rulesVersion':item['rules_version'],
        'summary':twenty_rich_text_summary(item['source_text'], candidate_id=cid+':summary',
            detail_url=next((u for u in item.get('embedded_urls', []) if u.startswith('https://app.rvc.global/vacancy/view/')), None)),
        'sourceLink':{'primaryLinkUrl':item['source_url'],'primaryLinkLabel':'Оригинал в Telegram','secondaryLinks':[]},
        'whyFit':rich('Направления: '+', '.join(track_names[t] for t in item['tracks'])+'\nГипотеза: '+item['hypothesis'],'whyFit'),
        'knownConditions':rich(('Описание находки: '+item['title']+'\n' if item['kind'] == 'PERSON' else '')+'Факт: '+item['fact']+'\nРоль: '+{'customer':'возможный клиент','partner':'возможный партнёр','reference':'пример рынка','opportunity':'проект / контакт'}[item['role']],'knownConditions'),
        'risks':rich('Смысловой отбор для разбора человеком. Спрос и готовность купить не подтверждены.','risks'),
        'unknowns':rich('\n'.join(item['unknowns']),'unknowns'),
        **inbox_company_profile(item),
        **({'personProfile': item['person_profile']} if item['kind'] == 'PERSON' and item.get('person_profile') else {}),
        **({'eventData': event_data(item), 'whyFit': rich(item['hypothesis'], 'whyFit')} if item['kind'] == 'EVENT' else {}),
        **({'reviewStatus':'NEED_INFO'} if item['kind'] == 'COMPANY' and item.get('company_profile', {}).get('status') != 'ready' else {}),
    }


def eligible_routes(item):
    item={**item,'tracks':list(item['tracks'])}
    if item['kind'] == 'EVENT':
        return item, None
    hold=delivery_hold(item['source_text']) if 'projects' in item['tracks'] else None
    if not hold and 'projects' in item['tracks'] and item['kind'] in ('PROJECT', 'DIGEST_POSITION') and item.get('rules_version') == 'astra-three-tracks-v1':
        hold=size_delivery_hold({**item,'summary':item['source_text']})
    if hold:
        item['tracks'].remove('projects')
    return item,hold


def deliver(path:Path, config, bridge=None, telegram_factory=TdlibSource):
    state=read(path/'state.json')
    if not state['analysis_complete']:
        raise ValueError('Incomplete analysis cannot be delivered')
    batch=read(path/'candidates.json')
    acceptance_path=Path(config['data_dir'])/'semantic-acceptance.json'
    acceptance=read(acceptance_path) if acceptance_path.exists() else {}
    if not config.get('delivery_enabled') or not acceptance.get('passed') or acceptance.get('model_revision') != batch['model_revision']:
        state.update(stage='deliver',status='blocked',error='semantic_quality_acceptance_required',updated_at=now())
        dump(path/'state.json',state)
        return state
    items=batch['items']
    link_items=[item for item in items if item['kind'] == 'EVENT' or eligible_routes(item)[0]['tracks']]
    if any(not item.get('source_url') or (item['kind'] == 'PERSON' and not item.get('person_profile')) for item in link_items):
        with telegram_factory(TELEGRAM_SEARCH_SOURCE) as tg:
            tg.wait_connected()
            for item in link_items:
                if item['kind'] == 'PERSON' and not item.get('person_profile'):
                    item['person_profile'] = person_profile(item['candidate_id'], tg.get_message_author(item['chat_id'], item['message_id']), item['source_text'], item.get('source_url', ''))
                    dump(path/'candidates.json',batch)
                if item.get('source_url'):
                    continue
                result=tg._request({'@type':'getMessageLink','chat_id':item['chat_id'],'message_id':item['message_id'],
                    'media_timestamp':0,'for_album':False,'in_message_thread':False},timeout=30)
                link=result.get('link','')
                if not link.startswith('https://t.me/'):
                    raise RuntimeError('Source link unavailable; no unlinked card is created')
                item.update(source_url=link,needs_source_link=False)
                if item.get('person_profile'):
                    item['person_profile']['source_url'] = link
                dump(path/'candidates.json',batch)
    bridge=bridge or TwentyBridge()
    outcomes=[]
    for item in items:
        # Personal exclusions never suppress a valid Infinity or Avans route.
        item,hold=eligible_routes(item)
        if not item['tracks'] and item['kind'] != 'EVENT':
            outcomes.append({'candidate_id':item['candidate_id'],'action':'held','reason':hold})
            dump(path/'delivery.json',{'items':outcomes})
            continue
        records=bridge.read()
        digest=hashlib.sha256(item['source_text'].encode()).hexdigest()
        existing=next((r for r in records if r['candidateId']==item['candidate_id'] or r.get('textHash')==digest),None)
        if existing:
            outcomes.append({'candidate_id':item['candidate_id'],'action':'preserved','record_id':existing['id']})
        else:
            # On timeout, the next run refetches identities including soft-deleted rows.
            result=bridge.create(payload(item))
            records=bridge.read()
            saved=next((r for r in records if r['candidateId']==item['candidate_id']),None)
            if not saved or saved.get('textHash') != digest:
                raise RuntimeError('Twenty delivery read-back did not verify')
            outcomes.append({'candidate_id':item['candidate_id'],'action':'created' if result.get('created') else 'preserved','record_id':saved['id']})
        dump(path/'delivery.json',{'items':outcomes})
    state.update(stage='deliver',status='complete',delivery_complete=True,updated_at=now())
    state.pop('error',None)
    dump(path/'state.json',state)
    verification=read(path/'analysis-verification.json')
    verification.update(semantic_quality_verified=True,acceptance=acceptance_path.name)
    dump(path/'analysis-verification.json',verification)
    return state
