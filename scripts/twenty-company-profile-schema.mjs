// One additive Inbox field. Default is a read-only plan; --apply creates only this field.
import { api } from './twenty-admin.mjs';
import { mkdirSync, writeFileSync } from 'node:fs';

const query = '{ objects(paging:{first:100}) {edges {node {id nameSingular fields(paging:{first:100}) {edges {node {id name type}}}}}} }';
const objects = (await api('/metadata', {query})).data.objects.edges.map(x=>x.node);
const inbox = objects.find(o=>o.nameSingular==='projectInboxItem');
if (!inbox) throw new Error('Radar Inbox metadata not found');
const field = inbox.fields.edges.find(x=>x.node.name==='companyProfile')?.node;
if (field && field.type !== 'RAW_JSON') throw new Error('Existing companyProfile has an incompatible type');
const plan = {object:'projectInboxItem',field:'companyProfile',type:'RAW_JSON',action:field?'preserve':'create',deletions:0};
console.log(JSON.stringify(plan));
if (process.argv.includes('--apply') && !field) {
  const root = '.radar/company-profile-2026-09-13';mkdirSync(root,{recursive:true,mode:0o700});
  writeFileSync(root+'/schema-before.json',JSON.stringify(inbox,null,2),{mode:0o600});
  const input = {field:{objectMetadataId:inbox.id,type:'RAW_JSON',name:'companyProfile',
    label:'Реквизиты компании',description:'Проверенные сайт, LinkedIn и город/страна для переноса в Company после Approve.',
    isNullable:true,isUIReadOnly:true,icon:'IconBuilding'}};
  const result = await api('/metadata',{query:'mutation($input:CreateOneFieldMetadataInput!){createOneField(input:$input){id name type}}',variables:{input}});
  writeFileSync(root+'/schema-result.json',JSON.stringify(result,null,2),{mode:0o600});
  if (result.data?.createOneField?.name !== 'companyProfile') throw new Error('Field creation not verified');
  console.log('Company profile field created; existing records unchanged.');
}
