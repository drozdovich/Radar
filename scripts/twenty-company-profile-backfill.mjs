// Fill only empty details of existing Radar Companies. No approval or company creation.
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { api, remote } from './twenty-admin.mjs';
import { companyFields, companyFieldsMatch, missingCompanyFields } from '../apps/twenty-review/src/company-profile.ts';

const file = process.argv.find((v,i,a)=>a[i-1]==='--input');
if (!file) throw new Error('Expected --input reviewed-profiles.json [--apply]');
const rows = JSON.parse(readFileSync(file,'utf8'));
const apply = process.argv.includes('--apply');
// Reuse one short maintenance session in memory for this bounded operation.
const results = [];
for (const row of rows) {
  const source = (await api(`/rest/projectInbox/${row.id}`,undefined,'GET')).data.projectInboxItem;
  if (source.candidateType!=='COMPANY' || source.reviewStatus!=='APPROVE' || source.candidateId!==row.candidateId
    || !/^radar-[a-f0-9]{20}$/.test(source.candidateId)) throw new Error('Only previously approved Radar companies are in scope');
  const hash = createHash('sha256').update(source.summary.markdown).digest('hex');
  if (row.companyProfile.source_text_sha256!==hash) throw new Error('Source changed since profile review');
  const wanted = companyFields({...source,companyProfile:row.companyProfile});
  const path = `/rest/companies/${row.id}`;
  const before = (await api(path,undefined,'GET')).data.company;
  if (before.id!==source.id) throw new Error('Company identity mismatch');
  const patch = missingCompanyFields(before,wanted);
  const result = {id:row.id,name:source.name,fields:Object.keys(patch),applied:false};
  if (apply) {
    if (Object.keys(patch).length) await api(path,patch,'PATCH');
    await api(`/rest/projectInbox/${row.id}`,{companyProfile:row.companyProfile},'PATCH');
    const after = (await api(path,undefined,'GET')).data.company;
    const checked = (await api(`/rest/projectInbox/${row.id}`,undefined,'GET')).data.projectInboxItem;
    if (!companyFieldsMatch(after,patch) || after.name!==before.name
      || checked.reviewStatus!==source.reviewStatus || !isDeepStrictEqual(checked.summary,source.summary)
      || !isDeepStrictEqual(checked.companyProfile,row.companyProfile)) throw new Error('Profile backfill read-back failed');
    // Verify every pre-existing nonempty address/link value remains unchanged.
    for (const key of ['domainName','linkedinLink','address']) {
      for(const [sub,value] of Object.entries(before[key]??{})) if(value && (!Array.isArray(value)||value.length)) {
        if(!isDeepStrictEqual(after[key]?.[sub],value)) throw new Error('Existing company field changed');
      }
    }
    result.applied=true;
  }
  results.push(result);
  writeFileSync(file+(apply?'.applied.json':'.plan.json'),JSON.stringify(results,null,2),{mode:0o600});
}
console.log(JSON.stringify({mode:apply?'apply':'plan',results}));
delete process.env.RADAR_TWENTY_TOKEN;
