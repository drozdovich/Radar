"""Company details from a context-confirmed public LinkedIn company profile."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import date
from urllib.parse import parse_qs, urlsplit

from .company_lookup import FRESH_DAYS, ProfileParser, identity, profile_url, public_company_name

SCHEMA = 'company-profile-v1'


def website_url(value):
    """Normalize a published website; never guess it from a company name."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        u = urlsplit(value if '://' in value else 'https://' + value)
        if u.hostname in {'www.linkedin.com', 'linkedin.com'} and u.path == '/redir/redirect':
            value = parse_qs(u.query).get('url', [''])[0]
            u = urlsplit(value)
        host = (u.hostname or '').encode('idna').decode().lower().removeprefix('www.')
        if (u.scheme not in {'http', 'https'} or u.username or u.password or u.port
                or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}', host)
                or host.endswith(('.local', '.localhost', '.internal', '.home.arpa'))
                or host in {'linkedin.com', 'facebook.com', 'instagram.com', 't.me'}):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        return f'{u.scheme}://{host}', host
    except (ValueError, UnicodeError):
        return None


class DetailsParser(ProfileParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.websites = []
        self.structured = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attrs = dict(attrs)
        if tag == 'a' and self.cell == 'dd' and self.label == 'website':
            self.websites.append(attrs.get('href', ''))
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.script = []

    def handle_data(self, data):
        super().handle_data(data)
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == 'dd' and self.cell == 'dd':
            self.fields.setdefault(self.label, []).append(' '.join(' '.join(self.parts).split()))
        if tag == 'script' and self.script is not None:
            try:
                self.structured.append(json.loads(''.join(self.script)))
            except (ValueError, TypeError):
                pass
            self.script = None
        super().handle_endtag(tag)


def organizations(value):
    if isinstance(value, list):
        for item in value:
            yield from organizations(item)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if isinstance(types, str):
            types = [types]
        if 'Organization' in types or 'Corporation' in types:
            yield value
        yield from organizations(value.get('@graph', []))


def parse_company_profile(html, company, url, today):
    p = DetailsParser()
    p.feed(html)
    canonical = profile_url(url)
    if not canonical or identity(p.title) != identity(company) or profile_url(p.canonical) != canonical:
        raise ValueError('Company profile identity not confirmed')
    published = {v for s in [*p.websites, *p.fields.get('website', [])] if (v := website_url(s))}
    # A single website hostname; links for employees/related companies are not evidence.
    sites = {host for _, host in published}
    website = sorted(published, reverse=True)[0][0] if len(sites) == 1 else ''
    addresses = []
    for org in organizations(p.structured):
        if identity(str(org.get('name', ''))) != identity(company):
            continue
        if org.get('url') and profile_url(org['url']) != canonical:
            continue
        raw = org.get('address', [])
        addresses.extend(raw if isinstance(raw, list) else [raw])
    locations = set()
    for a in addresses:
        if not isinstance(a, dict):
            continue
        city, country = a.get('addressLocality', ''), a.get('addressCountry', '')
        if isinstance(country, dict):
            country = country.get('name', '')
        if isinstance(city, str) and isinstance(country, str) and (city.strip() or country.strip()):
            locations.add((city.strip(), country.strip()))
    city, country = next(iter(locations)) if len(locations) == 1 else ('', '')
    missing = [field for field, value in [('domain', website), ('city', city), ('country', country)] if not value]
    return dict(schema_version=SCHEMA, company_name=company, linkedin_url=canonical,
                website_url=website, domain=next(iter(sites)) if len(sites) == 1 else '',
                city=city, country=country, source_url=canonical, checked_on=today.isoformat(),
                missing=missing, status='ready' if website else 'needs_info')


def valid_identity(proof, company, today):
    try:
        return (isinstance(proof, dict) and proof.get('status') == 'confirmed'
                and public_company_name(company)
                and identity(proof['company_name']) == identity(company)
                and 0 <= (today - date.fromisoformat(proof['checked_on'])).days <= FRESH_DAYS
                and isinstance(proof.get('reason'), str) and bool(proof['reason'].strip())
                and bool(profile_url(proof['source_url'])))
    except (ValueError, KeyError, TypeError):
        return False


def enrich_company(item, lookup):
    """Reuse lookup evidence, but require matching the company to its original signal."""
    company = item.get('company_name', '')
    proof = item.get('company_identity')
    if not company and isinstance(proof, dict):
        company = proof.get('company_name', '')
    result = dict(schema_version=SCHEMA, company_name=company, status='needs_info',
                  reason='company_identity_unverified', checked_on=lookup.today.isoformat(),
                  missing=['domain', 'linkedin_url', 'city', 'country'])
    if valid_identity(proof, company, lookup.today):
        result = lookup.lookup_profile(company, profile_url(proof['source_url']))
        result = {**result, 'identity': {**proof, 'company_name': company,
                                       'source_url': profile_url(proof['source_url'])}}
        # LinkedIn may publish a careers site or a shortened redirect. A separately
        # read official site can replace it, with explicit field-level provenance.
        official = proof.get('official_website', {})
        if (result.get('linkedin_url') and isinstance(official, dict)
                and official.get('checked_on') == lookup.today.isoformat()
                and isinstance(official.get('reason'), str) and official['reason'].strip()
                and website_url(official.get('source_url')) and website_url(official.get('url'))):
            website, domain = website_url(official['url'])
            result.update(website_url=website, domain=domain, status='ready',
                          domain_source={**official},
                          missing=[v for v in result.get('missing', []) if v != 'domain'])
    result['source_text_sha256'] = hashlib.sha256(item['source_text'].encode()).hexdigest()
    return result


def inbox_company_profile(item):
    value = item.get('company_profile')
    if not value:
        return {}
    if value.get('source_text_sha256') != hashlib.sha256(item['source_text'].encode()).hexdigest():
        raise ValueError('Company profile belongs to another source')
    return {'companyProfile': {**value, 'candidate_id': item['candidate_id']}}
