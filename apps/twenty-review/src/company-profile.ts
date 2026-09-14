type Item = { candidateId: string; companyProfile?: any };

function site(value: unknown): URL | null {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port
      || !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$/i.test(url.hostname)
      || /(?:\.local|\.localhost|\.internal|\.home\.arpa)$/.test(url.hostname)) return null;
    return url;
  } catch { return null; }
}

export function verifiedCompanyProfile(item: Item, now = new Date()) {
  const p = item.companyProfile;
  const website = site(p?.website_url);
  const today = Date.parse(now.toLocaleDateString('sv-SE', { timeZone: 'Europe/Madrid' }));
  const age = (today - Date.parse(p?.checked_on)) / 86400000;
  const proof = p?.identity;
  const proofAge = (today - Date.parse(proof?.checked_on)) / 86400000;
  if (p?.schema_version !== 'company-profile-v1' || p.status !== 'ready'
    || p.candidate_id !== item.candidateId || !website
    || website.hostname.replace(/^www\./, '') !== p.domain
    || !/^https:\/\/www\.linkedin\.com\/company\/[a-z0-9_-]+$/.test(p.linkedin_url ?? '')
    || p.source_url !== p.linkedin_url || !Number.isFinite(age) || age < 0 || age > 30
    || proof?.status !== 'confirmed' || proof.company_name !== p.company_name
    || proof.source_url !== p.source_url || typeof proof.reason !== 'string' || !proof.reason.trim()
    || !Number.isFinite(proofAge) || proofAge < 0 || proofAge > 30
    || ['linkedin.com', 'facebook.com', 'instagram.com', 't.me'].includes(p.domain)) return null;
  return p;
}

export function companyFields(item: Item) {
  const p = verifiedCompanyProfile(item);
  if (!p) throw new Error('Реквизиты компании ещё не подтверждены или устарели. Оставь карточку «На исследование»: нужен проверенный домен и LinkedIn этой компании.');
  return {
    domainName: { primaryLinkUrl: p.website_url, primaryLinkLabel: p.domain, secondaryLinks: [] },
    linkedinLink: { primaryLinkUrl: p.linkedin_url, primaryLinkLabel: 'LinkedIn', secondaryLinks: [] },
    ...((p.city || p.country) ? { address: {
      ...(typeof p.city === 'string' && p.city ? { addressCity: p.city } : {}),
      ...(typeof p.country === 'string' && p.country ? { addressCountry: p.country } : {}),
    } } : {}),
  };
}

export function missingCompanyFields(existing: any, wanted: ReturnType<typeof companyFields>) {
  const host = (value: string) => site(value)?.hostname.replace(/^www\./, '');
  if ((existing.domainName?.primaryLinkUrl && host(existing.domainName.primaryLinkUrl) !== host(wanted.domainName.primaryLinkUrl))
    || (existing.linkedinLink?.primaryLinkUrl && existing.linkedinLink.primaryLinkUrl.replace(/\/$/, '') !== wanted.linkedinLink.primaryLinkUrl)) return {};
  const patch: any = {};
  for (const key of ['domainName', 'linkedinLink'] as const) {
    if (!existing[key]?.primaryLinkUrl) patch[key] = { ...wanted[key], secondaryLinks: existing[key]?.secondaryLinks ?? [] };
  }
  const address = { ...existing.address };
  for (const [key, value] of Object.entries(wanted.address ?? {})) if (!address[key] && value) address[key] = value;
  if (Object.keys(address).some(key => address[key] !== existing.address?.[key])) patch.address = address;
  return patch;
}

export function companyFieldsMatch(saved: any, wanted: any) {
  return ['domainName', 'linkedinLink'].every(key => !wanted[key]
    || saved?.[key]?.primaryLinkUrl === wanted[key].primaryLinkUrl)
    && Object.entries(wanted.address ?? {}).every(([key, value]) => saved?.address?.[key] === value);
}
