import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from telegram_project_radar.company_lookup import (
    CompanyLookup, PublicRedirects, PublicSearch, check_status, evidence,
    identity, parse_profile, profile_url, public_company_name, size_delivery_hold,
)
from telegram_project_radar.company_size import CompanySize

TODAY = date(2026, 9, 12)
URL = 'https://www.linkedin.com/company/example-studio'


def html(name='Example Studio', size='11-50 employees', url=URL):
    return (f'<meta property="og:title" content="{name} | LinkedIn">'
            f'<link rel="canonical" href="{url}">'
            '<script>var employees = 9;</script><a>View all 8 employees</a>'
            f'<dl><dt>Company size</dt><dd>\n<span>{size}</span>\n</dd></dl>')


class Provider:
    def __init__(self, size='11-50 employees', hits=None):
        self.size = size
        self.hits = hits if hits is not None else [('Example Studio', URL)]
        self.queries = []
        self.fetches = []

    def search(self, company):
        self.queries.append(company)
        return self.hits

    def fetch(self, url):
        self.fetches.append(url)
        return html(size=self.size)


class ProfileTests(unittest.TestCase):
    def test_company_field_only_and_threshold_boundaries(self):
        for size, low, high, status in [('200 employees', 200, 200, 'within_limit'),
                                      ('201-500 employees', 201, 500, 'too_large'),
                                      ('51–200 employees', 51, 200, 'within_limit'),
                                      ('100-500 employees', 100, 500, 'uncertain'),
                                      ('10,001+ employees', 10001, None, 'too_large')]:
            fact = parse_profile(html(size=size), 'Example Studio', URL, TODAY)
            self.assertEqual((fact.minimum, fact.maximum, fact.status(TODAY)), (low, high, status))

    def test_member_count_team_size_login_and_wrong_company_never_prove_size(self):
        for page in ['<a>View all 8 employees</a>', html().replace('Company size', 'Team size'),
                     html(name='Another Studio'), html(url=URL + '-other'),
                     html(size='approximately 50 employees'), html() + '<dt>Company size</dt><dd>501 employees</dd>']:
            with self.assertRaises(ValueError):
                parse_profile(page, 'Example Studio', URL, TODAY)

    def test_public_url_allowlist_rejects_private_hosts_and_redirects(self):
        for url in ['http://www.linkedin.com/company/x', 'https://linkedin.com.evil.test/company/x',
                    'https://127.0.0.1/company/x', 'https://linkedin.com@localhost/company/x',
                    'https://linkedin.com:443/company/x', 'https://linkedin.com/in/someone',
                    'https://linkedin.com/company/x/../../login']:
            self.assertIsNone(profile_url(url))
            with self.assertRaises(ValueError):
                PublicRedirects().redirect_request(None, None, 302, '', {}, url)
        self.assertEqual(profile_url('https://de.linkedin.com/company/example-studio/'), URL)

    def test_legal_suffix_allowed_but_similar_brand_not_equal(self):
        self.assertEqual(identity('Fluffy Corp OÜ'), identity('Fluffy Corp.'))
        self.assertNotEqual(identity('Fluffy'), identity('Fluffy Corp.'))
        for value in ['', 'Company: X\nsecret message', 'Acme https://private', 'a@b.com', 'x' * 101]:
            self.assertFalse(public_company_name(value))


class LookupTests(unittest.TestCase):
    def lookup(self, root, provider, today=TODAY, **kwargs):
        return CompanyLookup(Path(root), provider=provider, registry={}, today=today, **kwargs)

    def test_live_result_cached_with_source_and_date_not_search_snippet(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = Provider(); lookup = self.lookup(temp, provider)
            result = lookup.lookup('Example Studio')
            self.assertEqual(result['status'], 'within_limit')
            self.assertEqual(result['evidence'][0]['source_url'], URL)
            self.assertEqual(result['checked_on'], TODAY.isoformat())
            self.assertEqual(lookup.lookup('Example Studio'), result)
            self.assertEqual(provider.queries, ['Example Studio'])
            self.assertEqual(provider.fetches, [URL])
            self.assertEqual(lookup.cache_hits, 1)
            self.assertEqual(next(Path(temp).glob('*.json')).stat().st_mode & 0o777, 0o600)

    def test_ambiguous_profiles_and_missing_company_are_held_without_guessing(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = Provider(hits=[('Example Studio', URL), ('Example Studio', URL + '-other')])
            lookup = self.lookup(temp, provider)
            self.assertEqual(lookup.lookup('Example Studio')['reason'], 'ambiguous_company')
            self.assertEqual(lookup.lookup('')['reason'], 'employer_not_named')
            self.assertEqual(provider.queries, ['Example Studio'])
            self.assertEqual(provider.fetches, [])

    def test_search_title_is_only_discovery_live_profile_must_confirm_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = Provider(hits=[('Example Studio Labs', URL)])
            self.assertEqual(self.lookup(temp, provider).lookup('Example Studio')['status'], 'within_limit')

    def test_fresh_operator_fact_overrides_unknown_but_conflicting_counts_hold(self):
        for size in (None, '201-500 employees'):
            with tempfile.TemporaryDirectory() as temp:
                provider = Provider(size=size or '11-50 employees', hits=[] if size is None else None)
                self.lookup(temp, provider).lookup('Example Studio')
                fact = CompanySize('Example Studio', 11, 50, URL, 'Company size: 11–50', TODAY)
                lookup = CompanyLookup(Path(temp), registry={'example studio': fact}, provider=provider, today=TODAY)
                for _ in range(2):
                    self.assertEqual(lookup.lookup('Example Studio')['status'], 'within_limit' if size is None else 'conflict')

    def test_network_error_is_cached_today_retried_tomorrow(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = Mock(); provider.search.side_effect = TimeoutError()
            lookup = self.lookup(temp, provider)
            self.assertEqual(lookup.lookup('Example Studio')['status'], 'unknown')
            lookup.lookup('Example Studio')
            self.assertEqual(provider.search.call_count, 1)
            next_day = self.lookup(temp, Provider(), TODAY + timedelta(days=1))
            self.assertEqual(next_day.lookup('Example Studio')['status'], 'within_limit')
            self.assertEqual(next_day.searches, 1)

    def test_old_cache_is_refreshed_and_budget_cannot_be_bypassed(self):
        with tempfile.TemporaryDirectory() as temp:
            self.lookup(temp, Provider()).lookup('Example Studio')
            lookup = self.lookup(temp, Provider(size='201-500 employees'), TODAY + timedelta(days=31), max_searches=1)
            self.assertEqual(lookup.lookup('Example Studio')['status'], 'too_large')
            self.assertEqual(lookup.lookup('Other Studio')['reason'], 'search_budget_reached')
            self.assertEqual(lookup.searches, 1)

    def test_corrupt_cache_and_wrong_profile_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            lookup = self.lookup(temp, Provider())
            lookup.lookup('Example Studio')
            path = next(Path(temp).glob('*.json')); path.write_text('{}')
            lookup.provider = Mock()
            lookup.provider.search.return_value = [('Example Studio', URL)]
            lookup.provider.fetch.return_value = html(name='Wrong Studio')
            self.assertEqual(lookup.lookup('Example Studio')['status'], 'unknown')

    def test_registry_retains_original_evidence_date_and_expires(self):
        with tempfile.TemporaryDirectory() as temp:
            fact = CompanySize('Example Studio', 11, 50, URL, 'Company size: 11-50 employees', TODAY)
            provider = Provider(size='201-500 employees')
            lookup = CompanyLookup(Path(temp), registry={'example studio': fact}, provider=provider, today=TODAY + timedelta(days=29))
            result = lookup.lookup('Example Studio')
            self.assertEqual(result['method'], 'operator_registry')
            self.assertEqual(result['evidence'][0]['checked_on'], TODAY.isoformat())
            self.assertEqual(provider.queries, [])
            lookup = CompanyLookup(Path(temp), registry={'example studio': fact}, provider=provider, today=TODAY + timedelta(days=31))
            self.assertEqual(lookup.lookup('Example Studio')['status'], 'too_large')

    def test_delivery_rechecks_proof_not_status_and_binds_to_original(self):
        with tempfile.TemporaryDirectory() as temp:
            check = self.lookup(temp, Provider()).lookup('Example Studio')
            item = {'summary': 'original source', 'company_size_check': check}
            self.assertEqual(size_delivery_hold(item, TODAY), 'company_size_unverified')
            check['text_sha256'] = hashlib.sha256(item['summary'].encode()).hexdigest()
            self.assertIsNone(size_delivery_hold(item, TODAY))
            self.assertEqual(size_delivery_hold(item, TODAY + timedelta(days=31)), 'company_size_stale')
            check['evidence'][0]['maximum'] = 500
            self.assertEqual(size_delivery_hold(item, TODAY), 'company_size_uncertain')
            check['evidence'][0]['minimum'] = 201
            self.assertEqual(size_delivery_hold(item, TODAY), 'company_size_too_large')
            check['evidence'].append(evidence(CompanySize('Example Studio', 11, 50, URL, 'other', TODAY)))
            self.assertEqual(check_status(check, TODAY), 'conflict')

    def test_exa_transport_searches_only_name_and_ignores_highlight_counts(self):
        provider = PublicSearch()
        with patch.object(provider, 'rpc', side_effect=[{}, {'content': [{'type': 'text', 'text':
            'Title: Example Studio\nURL: ' + URL + '\nHighlights:\nEmployees: 7\nCompany Size: 1-10'}]}]) as rpc:
            self.assertEqual(provider.search('Example Studio'), [('Example Studio', URL)])
            self.assertEqual(rpc.call_args.args[1]['arguments']['query'],
                             'LinkedIn company profile for "Example Studio" with Company size')


if __name__ == '__main__':
    unittest.main()
