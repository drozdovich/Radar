import hashlib
import json
from datetime import date, timedelta

import pytest

from telegram_project_radar.company_lookup import CompanyLookup
from telegram_project_radar.company_profile import enrich_company, parse_company_profile, inbox_company_profile, website_url
from telegram_project_radar.semantic_delivery import payload

TODAY = date(2026, 9, 13)
URL = 'https://www.linkedin.com/company/example-studio'


def page(*, name='Example Studio', website='https://www.example.com', addresses=None, size=True):
    org = {'@type': 'Organization', 'name': name, 'url': URL,
           'address': addresses if addresses is not None else {'addressLocality': 'Barcelona', 'addressCountry': 'ES'}}
    return (f'<meta property="og:title" content="{name} | LinkedIn"><link rel="canonical" href="{URL}">'
            f'<dl><dt>Website</dt><dd><a href="{website}">{website} External link</a></dd>'
            + ('<dt>Company size</dt><dd>11-50 employees</dd>' if size else '')
            + f'</dl><script type="application/ld+json">{json.dumps(org)}</script>')


def item():
    return {'company_name': 'Example Studio', 'company_identity': {'company_name': 'Example Studio',
            'status': 'confirmed', 'source_url': URL, 'checked_on': TODAY.isoformat(),
            'reason': 'Same product described in the original company announcement.'},
            'source_text': 'Original announcement', 'candidate_id': 'radar-12345678901234567890',
            'kind': 'COMPANY', 'tracks': ['infinity', 'avans'], 'title': 'Example Studio — voice app',
            'source': 'Test', 'source_url': 'https://t.me/example/1', 'published_at': '2026-09-13T00:00:00Z',
            'rules_version': 'astra-three-tracks-v1', 'role': 'partner', 'fact': 'Voice app',
            'hypothesis': 'May buy telephony', 'unknowns': ['Call volume']}


class Provider:
    def __init__(self, html=None):
        self.html = page() if html is None else html
        self.fetches = 0
        self.searches = 0

    def fetch(self, _):
        self.fetches += 1
        return self.html

    def search(self, _):
        self.searches += 1
        return [('Example Studio', URL)]


def test_page_used_for_size_also_supplies_details_without_second_fetch(tmp_path):
    provider = Provider()
    lookup = CompanyLookup(tmp_path, provider=provider, registry={}, today=TODAY)
    assert lookup.lookup('Example Studio')['status'] == 'within_limit'
    i = item(); i['company_profile'] = enrich_company(i, lookup)
    assert provider.fetches == provider.searches == 1
    p = payload(i)
    assert p['reviewStatus'] == 'NEW'
    assert p['companyProfile']['domain'] == 'example.com'
    assert p['companyProfile']['linkedin_url'] == URL
    assert (p['companyProfile']['city'], p['companyProfile']['country']) == ('Barcelona', 'ES')
    assert p['companyProfile']['candidate_id'] == i['candidate_id']
    assert p['summary']['markdown'] == i['source_text']


def test_telephony_company_does_not_require_size_or_personal_size_limit(tmp_path):
    provider = Provider(page(size=False))
    lookup = CompanyLookup(tmp_path, provider=provider, registry={}, today=TODAY)
    i = item(); i['company_profile'] = enrich_company(i, lookup)
    assert payload(i)['reviewStatus'] == 'NEW'
    assert provider.searches == 0


@pytest.mark.parametrize('case', ['missing', 'namesake', 'stale'])
def test_context_identity_required_before_any_network(tmp_path, case):
    i = item()
    if case == 'missing': i.pop('company_identity')
    elif case == 'namesake': i['company_identity']['company_name'] = 'Other Studio'
    else: i['company_identity']['checked_on'] = (TODAY-timedelta(days=31)).isoformat()
    provider = Provider()
    i['company_profile'] = enrich_company(i, CompanyLookup(tmp_path, provider=provider, registry={}, today=TODAY))
    assert payload(i)['reviewStatus'] == 'NEED_INFO'
    assert provider.fetches == provider.searches == 0


def test_wrong_live_profile_or_missing_domain_never_ready(tmp_path):
    for html in [page(name='Namesake'), page(website=''), page(website='https://linkedin.com/in/someone')]:
        provider = Provider(html)
        i = item(); i['company_profile'] = enrich_company(i, CompanyLookup(tmp_path/str(len(html)), provider=provider, registry={}, today=TODAY))
        assert payload(i)['reviewStatus'] == 'NEED_INFO'


def test_ambiguous_address_and_other_organizations_not_used():
    addresses = [{'addressLocality': 'Barcelona', 'addressCountry': 'ES'}, {'addressLocality': 'Paris', 'addressCountry': 'FR'}]
    p = parse_company_profile(page(addresses=addresses), 'Example Studio', URL, TODAY)
    assert p['domain'] == 'example.com'
    assert p['city'] == p['country'] == ''
    p = parse_company_profile(page(addresses=[]), 'Example Studio', URL, TODAY)
    assert p['missing'] == ['city', 'country']


def test_profile_cannot_be_transplanted_to_another_source(tmp_path):
    i = item(); i['company_profile'] = enrich_company(i, CompanyLookup(tmp_path, provider=Provider(), registry={}, today=TODAY))
    i['source_text'] = 'A different company announcement'
    with pytest.raises(ValueError, match='another source'):
        inbox_company_profile(i)


def test_legacy_headcount_cache_is_upgraded_and_then_reused(tmp_path):
    provider = Provider()
    lookup = CompanyLookup(tmp_path, provider=provider, registry={}, today=TODAY)
    lookup.lookup('Example Studio')
    path = next(tmp_path.glob('*.json'))
    cached = json.loads(path.read_text());cached.pop('profile');path.write_text(json.dumps(cached))
    p = enrich_company(item(), lookup)
    assert p['domain'] == 'example.com'
    assert provider.fetches == 2
    assert enrich_company(item(), lookup) == p
    assert provider.fetches == 2
    assert path.stat().st_mode & 0o777 == 0o600


def test_public_website_normalization_and_bad_links():
    assert website_url('https://www.example.com/products?x=1') == ('https://example.com', 'example.com')
    assert website_url('https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fexample.com') == ('https://example.com', 'example.com')
    for value in ['javascript:alert(1)', 'https://localhost', 'https://127.0.0.1', 'https://user:pass@example.com', 'https://example.com:123', 'https://example.home.arpa', 'https://linkedin.com']:
        assert website_url(value) is None


def test_verified_official_site_can_replace_published_shortener_with_its_evidence(tmp_path):
    i = item()
    i['company_identity']['official_website'] = {'url': 'https://example.com/',
        'source_url': 'https://short.example/company', 'checked_on': TODAY.isoformat(),
        'reason': 'Published LinkedIn link redirects to the same official product website.'}
    i['company_profile'] = enrich_company(i, CompanyLookup(tmp_path, provider=Provider(page(website='https://short.example/company')), registry={}, today=TODAY))
    assert i['company_profile']['domain'] == 'example.com'
    assert i['company_profile']['domain_source']['source_url'] == 'https://short.example/company'
    assert payload(i)['reviewStatus'] == 'NEW'
