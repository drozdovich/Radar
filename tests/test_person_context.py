import pytest
from telegram_project_radar.person_context import prepare_person_context
from telegram_project_radar.card_names import person_profile


def fixture():
    text='Я Павел. Создаю CRM для звонков.\nLinkedIn: linkedin.com/in/pavel-example\nМой email: pavel@example.com'
    item={'candidate_id':'radar-one','source_url':'https://t.me/example/1','source_text':text,'hypothesis':'Партнёр по CRM','unknowns':['Нужна ли интеграция звонков?'],'evidence':[{'quote':'Создаю CRM для звонков.'}]}
    profile=person_profile('radar-one',{'user_id':123,'first_name':'Pavel','usernames':['pavel_example']},text,item['source_url'])
    return item,profile


def test_preserves_original_and_prepares_source_bound_fields_and_specific_clarification():
    i,p=fixture();original=i['source_text'];r=prepare_person_context(i,p)
    assert i['source_text']==original and 'next_action' not in p
    assert r['telegram_user_id']==123 and r['telegram_username']=='pavel_example'
    assert r['email']=='pavel@example.com'
    assert r['linkedin_url']=='https://linkedin.com/in/pavel-example'
    assert r['next_action']['evidence_quote']=='Создаю CRM для звонков.'
    assert 'интеграция звонков' in r['next_action']['title']
    assert 'company_name' not in r and 'job_title' not in r


def test_does_not_guess_email_from_domain_or_linkedin_from_name():
    i,p=fixture();i['source_text']='Я Павел. Создаю CRM для звонков. Сайт example.com';r=prepare_person_context(i,p)
    assert not r.get('email') and not r.get('linkedin_url')


def test_rejects_other_person_claim_and_nonverbatim_action():
    i,p=fixture();i['person_details']={'job_title':{'subject':'mentioned_person','source_url':i['source_url'],'value':'CEO','quote':'CEO'}}
    with pytest.raises(ValueError):prepare_person_context(i,p)
    i.pop('person_details');p['next_action']={k:'not in text' for k in ('title','evidence_quote','questions','useful_result','draft')}
    with pytest.raises(ValueError):prepare_person_context(i,p)


def test_keeps_operator_action_and_fields_with_provenance():
    i,p=fixture();i['person_details']={'job_title':{'subject':'author','source_url':i['source_url'],'value':'Создаю CRM','quote':'Создаю CRM для звонков.'}}
    a=prepare_person_context(i,p)['next_action'];i['next_action']={**a,'title':'Проверить конкретную интеграцию с CRM'}
    r=prepare_person_context(i,p);assert r['next_action']['title']==i['next_action']['title'];assert r['field_sources']['job_title']['source_url']==i['source_url']


def test_does_not_assign_a_mentioned_colleagues_contact_to_author():
    i,p=fixture();i['source_text']='Я Павел. Вот LinkedIn моего коллеги: https://linkedin.com/in/someone-else\nEmail: colleague@example.com'
    r=prepare_person_context(i,p)
    assert not r.get('linkedin_url') and not r.get('email')
