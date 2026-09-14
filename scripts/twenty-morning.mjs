// Small operator-run bridge: read Radar identities, create NEW records only.
// Uses the existing owner-authorized SSH/session; never emits source text or tokens.
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { api, sql } from './twenty-admin.mjs';
import { verifiedCompanyProfile } from '../apps/twenty-review/src/company-profile.ts';

function records() {
  const result = sql(`BEGIN READ ONLY;
    SET LOCAL statement_timeout = '10s';
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'id', id, 'candidateId', "candidateId", 'status', "reviewStatus",
      'deletedAt', "deletedAt", 'text', "summaryMarkdown")), '[]'::jsonb)
    FROM workspace_radar."_projectInboxItem"
    WHERE "candidateId" LIKE 'radar-%'
      AND "candidateId" NOT LIKE 'radar-qa-%'
      AND "candidateId" NOT LIKE 'radar-test%';
    ROLLBACK;`);
  const line = result.split('\n').find(line => line.startsWith('['));
  if (!line) throw new Error('CRM identity read failed');
  return JSON.parse(line).map(({ text, ...record }) => ({
    ...record,
    textHash: text ? createHash('sha256').update(text.replace(/\[(👉 Контакты и полное описание)\]\(https:\/\/app\.rvc\.global\/vacancy\/view\/[^\s)]+\)/g, '$1')).digest('hex') : null,
  }));
}

try {
  const request = JSON.parse(readFileSync(0, 'utf8'));
  if (request.action === 'read') {
    process.stdout.write(JSON.stringify({ ok: true, records: records() }));
  } else if (request.action === 'create') {
    const p = request.payload;
    if (!/^radar-[a-f0-9]{20}$/.test(p?.candidateId ?? '') || !(p.reviewStatus === 'NEW' || (p.candidateType === 'COMPANY' && p.reviewStatus === 'NEED_INFO'))
      || !p.summary?.markdown || !/^https:\/\/t\.me\//.test(p.sourceLink?.primaryLinkUrl ?? '')) {
      throw new Error('Invalid morning record');
    }
    if (p.candidateType === 'COMPANY' && p.reviewStatus === 'NEW' && !verifiedCompanyProfile(p)) throw new Error('Company profile unverified');
    // An existing record (including trash) is never patched or restored.
    const existing = records().find(r => r.candidateId === p.candidateId);
    if (existing) {
      process.stdout.write(JSON.stringify({ ok: true, id: existing.id, created: false }));
    } else {
      const response = await api('/rest/projectInbox', p);
      const record = response.data?.createProjectInboxItem;
      if (record?.id !== p.id || record.candidateId !== p.candidateId) throw new Error('Create unverified');
      const saved = (await api(`/rest/projectInbox/${p.id}`, undefined, 'GET')).data?.projectInboxItem;
      if (saved?.summary?.markdown !== p.summary.markdown
        || saved?.sourceLink?.primaryLinkUrl !== p.sourceLink.primaryLinkUrl
        || (p.eventData && !isDeepStrictEqual(saved.eventData, p.eventData))
        || (p.personProfile && !isDeepStrictEqual(saved.personProfile, p.personProfile))
        || (p.companyProfile && !isDeepStrictEqual(saved.companyProfile, p.companyProfile))) throw new Error('Content unverified');
      process.stdout.write(JSON.stringify({ ok: true, id: p.id, created: true }));
    }
  } else {
    throw new Error('Unsupported operation');
  }
} catch {
  // API exceptions can contain raw request data. Keep the public error bounded.
  process.stdout.write(JSON.stringify({ ok: false, error: 'Twenty read/create failed; rerun safely to reconcile.' }));
  process.exitCode = 1;
}
