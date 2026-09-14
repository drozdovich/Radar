"""Grounded event facts for the existing saved-review → Inbox boundary."""
from datetime import date, datetime
import hashlib
import re
from urllib.parse import urlsplit

TEXT_FIELDS = ('name', 'startTime', 'endTime', 'timeZone', 'city', 'venue', 'eventFormat',
               'language', 'cost', 'currency', 'priceStatus', 'registrationDeadline',
               'organizer', 'officialUrl', 'whyAttend', 'audience', 'announcedParticipants',
               'eventCancellation')


def event_data(item):
    data = item.get('event')
    if not isinstance(data, dict):
        raise ValueError('EVENT requires structured event facts (unknown values may be null)')
    out = {'schema': 'radar-event-v1'}
    for key in TEXT_FIELDS:
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError('Invalid event field: ' + key)
        out[key] = value.strip() or None if value else None
    out['name'] = out['name'] or item['title']
    for key in ('startDate', 'endDate'):
        value = data.get(key)
        if value:
            date.fromisoformat(value)
        out[key] = value or None
    for key in ('startTime','endTime'):
        if out[key] and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', out[key]):
            raise ValueError('Event time must be HH:MM without invented timezone')
    if out['officialUrl']:
        u = urlsplit(out['officialUrl'])
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
            raise ValueError('Invalid official event link')
    if out['eventFormat'] not in (None,'Офлайн','Онлайн','Гибрид'):
        raise ValueError('Unknown event format')
    if out['priceStatus'] not in (None,'Бесплатно','Платно','Неизвестно'):
        raise ValueError('Unknown price status')
    out['unknowns'] = '\n'.join(item.get('unknowns', []))
    out['checkedAt'] = data.get('checkedAt')
    if out['checkedAt']:
        datetime.fromisoformat(out['checkedAt'].replace('Z','+00:00'))
    # Explicit occurrence identity supports known aliases; its date is always part of the key.
    normalized = lambda s: ' '.join(re.findall(r'\w+', (s or '').lower()))
    identity = data.get('identityName') or out['name']
    if out['startDate'] and (out['city'] or out['eventFormat'] == 'Онлайн'):
        key = '|'.join([normalized(identity),out['startDate'],normalized(out['city'] or 'online')])
    else:
        key = 'source|' + item['candidate_id']
    out['eventKey'] = 'event-' + hashlib.sha256(key.encode()).hexdigest()
    out['decisionProvenance'] = data.get('decisionProvenance')
    return out
