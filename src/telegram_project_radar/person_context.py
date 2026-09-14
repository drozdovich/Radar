"""Source-bound PERSON details and an explicit clarification action, without model calls."""
from __future__ import annotations

import copy
import re
from datetime import date

ACTION_FIELDS = ('title', 'evidence_quote', 'questions', 'useful_result', 'draft')


def prepare_person_context(item: dict, profile: dict) -> dict:
    p = copy.deepcopy(profile)
    text = item.get('source_text', item.get('summary', ''))
    source = item.get('source_url', '')
    p.update(source_url=source, checked_on=date.today().isoformat(), enrichment_source=source)
    # Operator-reviewed details carry the exact quote and explicitly identify its subject.
    for field, claim in (item.get('person_details') or {}).items():
        if field not in ('email', 'linkedin_url', 'job_title', 'company_name', 'last_name'):
            continue
        if not isinstance(claim, dict) or claim.get('subject') != 'author' or claim.get('source_url') != source:
            raise ValueError('Person detail must identify author and source')
        if not claim.get('quote') or claim['quote'] not in text or not claim.get('value'):
            raise ValueError('Person detail evidence missing')
        if claim['value'] not in claim['quote']:
            raise ValueError('Person detail value is not quoted')
        p[field] = claim['value']
        p.setdefault('field_sources', {})[field] = {**claim, 'checked_on': p['checked_on']}
    # A self-introduction with exactly one labelled/self-published profile or email.
    self_authored = bool(re.search(r'(?i)(?:\bя\s|меня зовут|обо мне|my linkedin|мой линкдин)', text))
    patterns = {'linkedin_url': r'(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?',
                'email': r'(?im)^(?:мой\s+)?(?:e-?mail|почта)\s*:\s*([^\s<>@]+@[^\s<>@]+\.[A-Za-z]{2,})\s*$'}
    if self_authored and not re.search(r'(?i)(?:его|её|коллег|подруг|знакомого)', text):
        for field, pattern in patterns.items():
            found = list(re.finditer(pattern, text))
            if len(found) == 1 and not p.get(field):
                m = found[0]; value = m[1] if field == 'email' else m[0]
                if field == 'linkedin_url' and not value.startswith('http'): value = 'https://' + value
                p[field] = value.rstrip('/') if field == 'linkedin_url' else value
                p.setdefault('field_sources', {})[field] = {'source_url':source, 'quote':m[0], 'checked_on':p['checked_on'], 'subject':'author'}
    a = copy.deepcopy(item.get('next_action') or p.get('next_action'))
    if not a:
        # Existing review outputs remain usable; unknowns define a grounded clarification,
        # never a claim of buying intent or an invented commercial proposal.
        quotes = [e.get('quote') for e in item.get('evidence', []) if e.get('quote') and e['quote'] in text]
        quote = next(iter(quotes), next((line for line in text.splitlines() if line.strip() and not line.startswith('#')), ''))
        unknowns = item.get('unknowns') or ['Какую совместную задачу автор готов обсуждать сейчас?']
        questions = '\n'.join(unknowns)
        name = p.get('first_name') or 'автора'
        focus = unknowns[0].rstrip('.?')
        a = {'title': f'Уточнить у {name}: {focus}', 'evidence_quote':quote,
             'questions':questions,
             'useful_result':'Проверить гипотезу: ' + item.get('hypothesis', 'есть ли конкретная совместная задача и подходящий формат участия') + '. Спрос пока не подтверждён.',
             'draft':f'{name}, привет! Увидел твоё сообщение: «{quote}». Хотел уточнить: {focus}? Интересно понять, есть ли тема для совместной работы.'}
    if any(not isinstance(a.get(k), str) or not a[k].strip() for k in ACTION_FIELDS):
        raise ValueError('Person next action is incomplete')
    if a['evidence_quote'] not in text:
        raise ValueError('Person action quote does not match original')
    p['next_action'] = a
    p.setdefault('verification_limit', 'Сведения из исходного сообщения; актуальность и готовность сотрудничать отдельно не подтверждены.')
    return p
