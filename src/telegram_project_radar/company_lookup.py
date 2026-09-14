"""Bounded public headcount lookup. Search discovers URLs; only a live profile proves size."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.request
from dataclasses import asdict
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .company_size import CompanySize, company_key, load_company_sizes

SCHEMA = 'company-size-check-v1'
FRESH_DAYS = 30
MAX_SEARCHES = 6
MAX_BYTES = 2_000_000
EXA_URL = 'https://mcp.exa.ai/mcp'


def public_company_name(value: str) -> bool:
    """A single bounded name, never a message, contact, URL, or search expression."""
    return bool(isinstance(value, str) and 2 <= len(value) <= 100
                and len(value.split()) <= 10
                and re.fullmatch(r"[\w .&'’()/-]+", value, re.UNICODE)
                and any(c.isalpha() for c in value))


def identity(value: str) -> str:
    value = re.sub(r'[^\w]+', ' ', value.casefold()).strip()
    return re.sub(r'(?:\s+(?:oü|ou|llc|ltd|limited|inc|incorporated|gmbh|sarl|llp))+$', '', value)


def profile_url(value: str) -> str | None:
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.username or url.password or url.port
                or not re.fullmatch(r'(?:www\.|[a-z]{2}\.)?linkedin\.com', url.hostname or '')
                or not re.fullmatch(r'/company/[a-zA-Z0-9_-]+/?', url.path)):
            return None
        return 'https://www.linkedin.com' + url.path.rstrip('/').lower()
    except ValueError:
        return None


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not profile_url(newurl):
            raise ValueError('Profile redirect outside public company pages')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicSearch:
    """Existing Exa remote MCP, no account mutation, key purchase, or model call."""
    def __init__(self):
        self.headers = {'Content-Type': 'application/json',
                        'Accept': 'application/json, text/event-stream',
                        'User-Agent': f'TelegramProjectRadar/{__version__}',
                        'MCP-Protocol-Version': '2024-11-05'}
        self.ready = False
        self.counter = 0

    def rpc(self, method: str, params: dict) -> dict:
        self.counter += 1
        body = json.dumps(dict(jsonrpc='2.0', id=self.counter, method=method, params=params)).encode()
        request = urllib.request.Request(EXA_URL, data=body, headers=self.headers)
        # Redirects to arbitrary hosts are never followed, even for the search request.
        with urllib.request.build_opener(PublicRedirects()).open(request, timeout=15) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('Search response too large')
            if response.headers.get('Mcp-Session-Id'):
                self.headers['Mcp-Session-Id'] = response.headers['Mcp-Session-Id']
        text = raw.decode('utf-8')
        messages = ([json.loads(line[5:].strip()) for line in text.splitlines() if line.startswith('data:')]
                    if text.lstrip().startswith(('event:', 'data:')) else [json.loads(text)])
        result = next((m for m in messages if m.get('id') == self.counter), {})
        if 'error' in result or 'result' not in result or result['result'].get('isError'):
            raise ValueError('Public search unavailable')
        return result['result']

    def search(self, company: str) -> list[tuple[str, str]]:
        if not public_company_name(company):
            raise ValueError('A company name is required')
        if not self.ready:
            self.rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                   'clientInfo': {'name': 'telegram-project-radar', 'version': __version__}})
            self.ready = True
        result = self.rpc('tools/call', {'name': 'web_search_exa', 'arguments': {
            'query': f'LinkedIn company profile for "{company}" with Company size', 'numResults': 6}})
        # Search highlights may contain stale headcounts: use only titles and profile URLs.
        text = '\n'.join(c.get('text', '') for c in result.get('content', []) if c.get('type') == 'text')
        return re.findall(r'^Title: ([^\n]+)\nURL: (https://[^\s]+)', text, re.M)

    def fetch(self, url: str) -> str:
        if not profile_url(url):
            raise ValueError('Public company profile required')
        request = urllib.request.Request(url, headers={'User-Agent': self.headers['User-Agent'],
                                                       'Accept-Language': 'en-US,en;q=0.9'})
        with urllib.request.build_opener(PublicRedirects()).open(request, timeout=15) as response:
            if profile_url(response.geturl()) != profile_url(url):
                raise ValueError('Company profile identity changed')
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES or 'html' not in response.headers.get_content_type():
                raise ValueError('Invalid public profile response')
            return raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')


class ProfileParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ''
        self.canonical = ''
        self.cell = None
        self.parts = []
        self.label = ''
        self.sizes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('property') == 'og:title':
            self.title = attrs.get('content', '').removesuffix(' | LinkedIn')
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonical = attrs.get('href', '')
        if tag in {'dt', 'dd'}:
            self.cell, self.parts = tag, []

    def handle_data(self, data):
        if self.cell:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == self.cell:
            value = ' '.join(' '.join(self.parts).split())
            if tag == 'dt':
                self.label = value.casefold()
            elif self.label == 'company size':
                self.sizes.append(value)
            self.cell = None


def parse_profile(html: str, company: str, url: str, today: date) -> CompanySize:
    parser = ProfileParser()
    parser.feed(html)
    if identity(parser.title) != identity(company) or profile_url(parser.canonical) != profile_url(url):
        raise ValueError('Public company identity not confirmed')
    if len(parser.sizes) != 1:
        raise ValueError('Company-wide size field missing or ambiguous')
    match = re.fullmatch(r'([\d,]+)(?:\s*[-–]\s*([\d,]+)|(\+))?\s+employees?', parser.sizes[0], re.I)
    if not match:
        raise ValueError('Unsupported company size field')
    low = int(match[1].replace(',', ''))
    high = int(match[2].replace(',', '')) if match[2] else None if match[3] else low
    if low < 1 or (high is not None and high < low):
        raise ValueError('Invalid company bounds')
    return CompanySize(company, low, high, profile_url(url), f'Company size: {parser.sizes[0]}', today)


def evidence(fact: CompanySize) -> dict:
    return {**asdict(fact), 'checked_on': fact.checked_on.isoformat(), 'scope': 'company'}


def read_fact(value: dict) -> CompanySize:
    low, high = value['minimum'], value['maximum']
    url = urlsplit(value['source_url'])
    if (value.get('scope') != 'company' or type(low) is not int or low < 1
            or (high is not None and (type(high) is not int or high < low))
            or url.scheme != 'https' or not url.hostname or url.username or url.password
            or not value.get('source_excerpt') or not public_company_name(value['company'])):
        raise ValueError('Invalid company evidence')
    return CompanySize(value['company'], low, high, value['source_url'], value['source_excerpt'],
                       date.fromisoformat(value['checked_on']))


def check_status(check: dict, today: date) -> str:
    """Recompute the decision; a saved status or score is never sufficient proof."""
    try:
        if check['schema_version'] != SCHEMA:
            return 'unknown'
        age = (today - date.fromisoformat(check['checked_on'])).days
        if not 0 <= age <= FRESH_DAYS:
            return 'stale'
        facts = [read_fact(f) for f in check['evidence']]
        if not facts:
            return 'unknown'
        if any(identity(f.company) != identity(check['company']) for f in facts):
            return 'unknown'
        if any(not 0 <= (today - f.checked_on).days <= FRESH_DAYS for f in facts):
            return 'stale'
        if len({(f.minimum, f.maximum) for f in facts}) != 1:
            return 'conflict'
        return facts[0].status(today)
    except (ValueError, KeyError, TypeError, AttributeError):
        return 'unknown'


def size_delivery_hold(item: dict, today: date | None = None) -> str | None:
    check = item.get('company_size_check') or {}
    if check.get('text_sha256') != hashlib.sha256(item['summary'].encode()).hexdigest():
        return 'company_size_unverified'
    status = check_status(check, today or date.today())
    return None if status == 'within_limit' else f'company_size_{status}'


class CompanyLookup:
    def __init__(self, cache: Path, *, provider=None, registry=None, today=None, max_searches=MAX_SEARCHES):
        self.cache = cache
        self.provider = provider if provider is not None else PublicSearch()
        self.registry = load_company_sizes() if registry is None else registry
        self.today = today or date.today()
        self.max_searches = max_searches
        self.searches = 0
        self.cache_hits = 0
        self.profile_fetches = 0

    def result(self, company, reason, facts=(), method='public_profile'):
        result = dict(schema_version=SCHEMA, company=company, checked_on=self.today.isoformat(),
                      method=method, reason=reason, evidence=[evidence(f) for f in facts])
        result['status'] = check_status(result, self.today)
        return result

    def lookup(self, company: str) -> dict:
        if not public_company_name(company):
            return self.result('', 'employer_not_named', method='local')
        key = hashlib.sha256(company_key(company).encode()).hexdigest()
        path = self.cache / f'{key}.json'
        fact = self.registry.get(company_key(company))
        registry_fresh = bool(fact and 0 <= (self.today - fact.checked_on).days <= FRESH_DAYS)
        cached = None
        try:
            cached = json.loads(path.read_text())
            age = (self.today - date.fromisoformat(cached['checked_on'])).days
            status = check_status(cached, self.today)
            if (not registry_fresh and cached['schema_version'] == SCHEMA and cached['company'] == company
                    and 0 <= age <= (FRESH_DAYS if status in {'within_limit', 'too_large'} else 0)
                    and status == cached['status']):
                self.cache_hits += 1
                return cached
        except (OSError, ValueError, KeyError, TypeError):
            cached = None
        if registry_fresh:
            result = self.result(company, 'verified_registry', [fact], 'operator_registry')
            if (cached and cached.get('company') == company
                    and check_status(cached, self.today) in {'within_limit', 'too_large', 'uncertain', 'conflict'}):
                other = [read_fact(f) for f in cached['evidence']]
                if any((f.minimum, f.maximum) != (fact.minimum, fact.maximum) for f in other):
                    result = self.result(company, 'public_registry_conflict', dict.fromkeys([fact, *other]))
        elif self.searches >= self.max_searches:
            return self.result(company, 'search_budget_reached', method='local')
        else:
            self.searches += 1
            try:
                hits = self.provider.search(company)
                urls = {profile_url(url) for title, url in hits if profile_url(url)
                        and (identity(title.removesuffix(' | LinkedIn')) == identity(company)
                             or identity(urlsplit(profile_url(url)).path.split('/')[-1]) == identity(company))}
                if len(urls) != 1:
                    result = self.result(company, 'ambiguous_company' if urls else 'company_profile_not_found')
                else:
                    url = next(iter(urls))
                    from .company_profile import parse_company_profile
                    html = self.provider.fetch(url)
                    profile = parse_company_profile(html, company, url, self.today)
                    try:
                        fact = parse_profile(html, company, url, self.today)
                        result = self.result(company, 'live_company_profile', [fact])
                    except ValueError:
                        result = self.result(company, 'company_size_unavailable')
                    result['profile'] = profile
            except (OSError, ValueError, TimeoutError, TypeError, KeyError, AttributeError):
                result = self.result(company, 'public_lookup_unavailable_or_unverified')
        if 'profile' not in result and cached and cached.get('company') == company and isinstance(cached.get('profile'), dict):
            result['profile'] = cached['profile']
        self.save(path, result)
        return result

    def lookup_profile(self, company: str, url: str) -> dict:
        """Read the already context-confirmed URL, sharing the headcount cache/page."""
        from .company_profile import SCHEMA as PROFILE_SCHEMA, parse_company_profile
        unknown = dict(schema_version=PROFILE_SCHEMA, company_name=company,
                       checked_on=self.today.isoformat(), status='needs_info',
                       reason='company_profile_unavailable', missing=['domain', 'linkedin_url', 'city', 'country'])
        if not public_company_name(company) or not profile_url(url):
            return unknown
        path = self.cache / (hashlib.sha256(company_key(company).encode()).hexdigest() + '.json')
        cached = {}
        try:
            cached = json.loads(path.read_text())
            p = cached.get('profile', {})
            age = (self.today - date.fromisoformat(p['checked_on'])).days
            if (p.get('schema_version') == PROFILE_SCHEMA and p.get('company_name') == company
                    and p.get('source_url') == url
                    and 0 <= age <= (FRESH_DAYS if p.get('status') == 'ready' else 0)):
                self.cache_hits += 1
                return p
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if self.profile_fetches >= self.max_searches:
            return {**unknown, 'reason': 'profile_budget_reached'}
        self.profile_fetches += 1
        try:
            html = self.provider.fetch(url)
            profile = parse_company_profile(html, company, url, self.today)
            # A profile lookup can also populate the same size evidence for a later project.
            try:
                fact = parse_profile(html, company, url, self.today)
                live = self.result(company, 'live_company_profile', [fact])
                if cached.get('company') == company and cached.get('evidence'):
                    existing = [read_fact(f) for f in cached['evidence'] if 0 <= (self.today - date.fromisoformat(f['checked_on'])).days <= FRESH_DAYS]
                    if any((f.minimum, f.maximum) != (fact.minimum, fact.maximum) for f in existing):
                        live = self.result(company, 'public_registry_conflict', dict.fromkeys([fact, *existing]))
                cached = live
            except (ValueError, KeyError, TypeError):
                cached = cached if cached.get('company') == company else self.result(company, 'company_size_unavailable')
        except (OSError, ValueError, TimeoutError, TypeError, KeyError, AttributeError):
            profile = {**unknown, 'source_url': url}
        cached['profile'] = profile
        self.save(path, cached)
        return profile

    def save(self, path, result):
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.check-', dir=self.cache)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
