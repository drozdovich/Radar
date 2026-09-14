// Real REST integration of the same Approve function, on one disposable QA record.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { api, remote } from './twenty-admin.mjs';
import { approveTransfer } from '../apps/twenty-review/src/approve-transfer.ts';
import { decisionPatch } from '../apps/twenty-review/src/review-model.ts';

const id=randomUUID(), cid='radar-qa-company-profile-v021', today=new Date().toISOString().slice(0,10);
const root='.radar/company-profile-2026-09-13';
writeFileSync(root+'/qa-id.json',JSON.stringify({id,candidateId:cid}),{mode:0o600});
const linkedin='https://www.linkedin.com/company/radar-qa-example';
const domain='qa-'+id+'.example', website='https://'+domain;
const item={id,candidateId:cid,candidateType:'COMPANY',name:'Radar QA — company profile',reviewStatus:'NEW',
  summary:{markdown:'Synthetic company-profile QA. Not a real lead.'},source:'QA',
  companyProfile:{schema_version:'company-profile-v1',status:'ready',candidate_id:cid,company_name:'Radar QA',
    website_url:website,domain,linkedin_url:linkedin,source_url:linkedin,
    city:'Barcelona',country:'ES',checked_on:today,
    identity:{status:'confirmed',company_name:'Radar QA',source_url:linkedin,checked_on:today,reason:'Synthetic QA fixture, no external discovery.'}}};
const client={
  async get(path){try{return await api(path,undefined,'GET');}catch(e){try{e.status=JSON.parse(e.message).status;}catch{}throw e;}},
  post:(path,body)=>api(path,body),patch:(path,body)=>api(path,body,'PATCH'),delete:(path)=>api(path,undefined,'DELETE'),
};
await api('/rest/projectInbox',item);
await approveTransfer(client,item,decisionPatch('APPROVE'));
const company=(await client.get(`/rest/companies/${id}`)).data.company;
assert.equal(company.domainName.primaryLinkUrl,website);
assert.equal(company.linkedinLink.primaryLinkUrl,linkedin);
assert.equal(company.address.addressCity,'Barcelona');assert.equal(company.address.addressCountry,'ES');
const source=(await client.get(`/rest/projectInbox/${id}`)).data.projectInboxItem;
assert.equal(source.reviewStatus,'APPROVE');assert.deepEqual(source.companyProfile,item.companyProfile);
await client.patch(`/rest/companies/${id}`,{address:{...company.address,addressCity:'Madrid'}});
await approveTransfer(client,item,decisionPatch('APPROVE'));
assert.equal((await client.get(`/rest/companies/${id}`)).data.company.address.addressCity,'Madrid');
const secondId=randomUUID();
const second={...item,id:secondId,candidateId:cid+'-second',companyProfile:{...item.companyProfile,candidate_id:cid+'-second'}};
writeFileSync(root+'/qa-second-id.json',JSON.stringify({id:secondId,candidateId:second.candidateId}),{mode:0o600});
await api('/rest/projectInbox',second);
assert.equal((await approveTransfer(client,second,decisionPatch('APPROVE'))).id,id);
assert.equal((await client.get(`/rest/noteTargets/${secondId}`)).data.noteTarget.targetCompanyId,id);
for(const plural of ['noteTargets','notes','projectInbox']) await client.delete(`/rest/${plural}/${secondId}?soft_delete=true`);
writeFileSync(root+'/qa-result.json',JSON.stringify({id,passed:true,repeat_preserved_manual_city:true,version:'0.21.0'},null,2),{mode:0o600});
if (!process.argv.includes('--keep')) {
  for(const plural of ['noteTargets','notes','companies','projectInbox']) await client.delete(`/rest/${plural}/${id}?soft_delete=true`);
}
console.log(JSON.stringify({passed:true,id,kept:process.argv.includes('--keep'),checks:['domain','linkedin','city','country','source profile','Approve','repeat preserves manual city']}));
delete process.env.RADAR_TWENTY_TOKEN;
