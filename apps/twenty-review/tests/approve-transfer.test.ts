import test from 'node:test';
import assert from 'node:assert/strict';
import { approveTransfer, destination } from '../src/approve-transfer.ts';

const id = 'c6676637-d173-5410-89cf-11b4425f8bf1';
function profile() {
  const today = new Date().toISOString().slice(0,10);
  return { schema_version: 'company-profile-v1', status:'ready', candidate_id:'radar-example',
    company_name:'Example Studio', domain:'example.com', website_url:'https://example.com',
    linkedin_url:'https://www.linkedin.com/company/example-studio', source_url:'https://www.linkedin.com/company/example-studio',
    city:'Barcelona', country:'ES', checked_on:today,
    identity:{status:'confirmed',company_name:'Example Studio',source_url:'https://www.linkedin.com/company/example-studio',checked_on:today,reason:'Same product as the original announcement'} };
}
function fixture(type = 'COMPANY') {
  const item: any = { personProfile: {schema_version:'person-profile-v1',status:'ready',candidate_id:'radar-example',first_name:'Pavel',last_name:'Example',source_url:'https://t.me/example/1'},sourceLink:{primaryLinkUrl:'https://t.me/example/1'}, companyProfile: profile(), id, candidateId: 'radar-example', candidateType: type, name: 'Pavel Example', summary: { markdown: 'Полный оригинал\nВторая строка' }, knownConditions: { markdown: 'Описание' }, fitScore: 50 };
  const store = new Map<string, any>([[`/rest/projectInbox/${id}`, structuredClone(item)]]);
  let posts = 0;
  const api = {
    async get(path: string) { if (path.startsWith('/rest/companies?')) { const url=decodeURIComponent(path.split('[eq]:')[1].split('&')[0]);return {data:{companies:[...store.entries()].filter(([key,c])=>key.startsWith('/rest/companies/')&&c.domainName?.primaryLinkUrl===url).map(([,c])=>structuredClone(c))}}; } if (!store.has(path)) throw Object.assign(new Error('missing'), { status: 404 }); return { data: structuredClone(store.get(path)) }; },
    async post(path: string, body: any) { posts++; store.set(`${path}/${body.id}`, structuredClone(body)); return { data: body }; },
    async patch(path: string, body: any) { store.set(path, { ...store.get(path), ...body }); return {}; },
    async delete(_path: string) { throw new Error('Review must never delete the open record'); },
  };
  return { item, store, api, posts: () => posts };
}
test('all candidate types reach the native object with original and leave NEW without deletion', async () => {
  for (const type of ['COMPANY', 'PROJECT', 'DIGEST_POSITION']) {
    const f = fixture(type); const target = await approveTransfer(f.api, f.item, { reviewStatus: 'APPROVE' });
    assert.ok(f.store.has(`/rest/${destination(type).plural}/${id}`));
    assert.equal(f.store.get(`/rest/projectInbox/${id}`).reviewStatus, 'APPROVE');
    assert.match(f.store.get(`/rest/notes/${id}`).bodyV2.markdown, /Полный оригинал\nВторая строка/);
    assert.equal(f.store.get(`/rest/noteTargets/${id}`)[target.relation], id);
    if(type === 'PERSON') assert.deepEqual(f.store.get(`/rest/people/${id}`).name, {firstName:'Pavel',lastName:'Example'});
  }
});
test('repeated approval reuses destination, original and relation', async () => {
  const f=fixture(); await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});
  await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'}); assert.equal(f.posts(),3);
});
test('Twenty normalizes blank feedback to newline; repeat still preserves the same note',async()=>{
 const f=fixture();await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});
 f.store.get(`/rest/projectInbox/${id}`).feedbackNote={markdown:'\n'};
 await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});assert.equal(f.posts(),3);
});
test('failed note write keeps source and retry reuses the created destination', async () => {
  const f=fixture();const post=f.api.post;
  f.api.post=async(path,body)=>{if(path==='/rest/notes')throw Object.assign(new Error('unavailable'),{status:503});return post(path,body);};
  await assert.rejects(approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'}));
  assert.ok(f.store.has(`/rest/projectInbox/${id}`));assert.ok(f.store.has(`/rest/companies/${id}`));
  f.api.post=post;await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});assert.equal(f.posts(),3);
});
test('lost create response does not duplicate or lose the source text',async()=>{
 const f=fixture();const post=f.api.post;f.api.post=async(path,body)=>{await post(path,body);throw Object.assign(new Error('response lost'),{status:503});};
 await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});assert.equal(f.posts(),3);
});
test('unknown type fails before any mutation',async()=>{
 const f=fixture('UNKNOWN');await assert.rejects(approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'}));assert.equal(f.posts(),0);assert.ok(f.store.has(`/rest/projectInbox/${id}`));
});

test('create responses avoid redundant read requests',async()=>{
 const f=fixture();let reads=0;const get=f.api.get;f.api.get=async path=>{reads++;return get(path);};
 const post=f.api.post;f.api.post=async(path,body)=>{await post(path,body);const key=path==='/rest/companies'?'createCompany':path==='/rest/notes'?'createNote':'createNoteTarget';return {data:{[key]:body}};};
 await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});assert.equal(reads,5);
});

test('Approve transfers verified details to native Company fields and preserves provenance', async()=>{
 const f=fixture();await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});
 const saved=f.store.get(`/rest/companies/${id}`);
 assert.equal(saved.domainName.primaryLinkUrl,'https://example.com');
 assert.equal(saved.linkedinLink.primaryLinkUrl,'https://www.linkedin.com/company/example-studio');
 assert.deepEqual(saved.address,{addressCity:'Barcelona',addressCountry:'ES'});
 assert.match(f.store.get(`/rest/notes/${id}`).bodyV2.markdown,/Проверено:/);
});

test('unverified, stale or transplanted profile cannot create an empty Company',async()=>{
 for(const mutate of [
   (p:any)=>{p.website_url='';}, (p:any)=>{p.candidate_id='radar-other';},
   (p:any)=>{p.checked_on='2000-01-01';}, (p:any)=>{p.identity.company_name='Other';},
   (p:any)=>{p.linkedin_url='https://www.linkedin.com/in/person';},
   (p:any)=>{p.website_url='javascript:alert(1)';},
 ]) {
   const f=fixture();const source=f.store.get(`/rest/projectInbox/${id}`);mutate(source.companyProfile);
   await assert.rejects(approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'}),/Реквизиты/);
   assert.equal(f.posts(),0);assert.notEqual(source.reviewStatus,'APPROVE');
 }
});

test('retry fills only empty fields and never overwrites a user address or link label',async()=>{
 const f=fixture();
 f.store.set(`/rest/companies/${id}`,{id,name:'My edited name',domainName:{primaryLinkUrl:'https://example.com',primaryLinkLabel:'My site',secondaryLinks:[]},address:{addressCity:'Madrid',addressStreet1:'My address'}});
 await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});
 const c=f.store.get(`/rest/companies/${id}`);
 assert.equal(c.name,'My edited name');assert.equal(c.domainName.primaryLinkLabel,'My site');
 assert.deepEqual(c.address,{addressCity:'Madrid',addressStreet1:'My address',addressCountry:'ES'});
 assert.equal(c.linkedinLink.primaryLinkUrl,profile().linkedin_url);
 await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});assert.equal(f.posts(),2);
});

test('a different manually entered domain prevents mixing company identities',async()=>{
 const f=fixture();const original={id,name:'Manual',domainName:{primaryLinkUrl:'https://another.example'}};
 f.store.set(`/rest/companies/${id}`,structuredClone(original));
 await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});
 assert.deepEqual(f.store.get(`/rest/companies/${id}`),original);
});

test('a failed detail write keeps Inbox unfinished and retry repairs the partial transfer',async()=>{
 const f=fixture();f.store.set(`/rest/companies/${id}`,{id,name:f.item.name});
 const patch=f.api.patch;f.api.patch=async(path,body)=>{if(path.includes('/companies/'))throw new Error('offline');return patch(path,body);};
 await assert.rejects(approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'}));
 assert.notEqual(f.store.get(`/rest/projectInbox/${id}`).reviewStatus,'APPROVE');
 f.api.patch=patch;await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});
 assert.equal(f.store.get(`/rest/companies/${id}`).domainName.primaryLinkUrl,'https://example.com');
});

test('a server silently dropping requested fields is not accepted as a completed creation',async()=>{
 const f=fixture();const post=f.api.post;
 f.api.post=async(path,body)=>post(path,path==='/rest/companies'?{id:body.id,name:body.name}:body);
 await assert.rejects(approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'}),/проверить/);
 assert.notEqual(f.store.get(`/rest/projectInbox/${id}`).reviewStatus,'APPROVE');
});

test('two approved source cards with the same verified domain reuse one Company',async()=>{
 const f=fixture();await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'});
 const second={...structuredClone(f.item),id:'449a1cec-89d4-42be-a74a-8470e6f6c77a',candidateId:'radar-second'};
 second.companyProfile.candidate_id=second.candidateId;
 f.store.set(`/rest/projectInbox/${second.id}`,structuredClone(second));
 const moved=await approveTransfer(f.api,second,{reviewStatus:'APPROVE'});
 assert.equal(moved.id,id);assert.equal(f.posts(),5);
 assert.equal(f.store.get(`/rest/noteTargets/${second.id}`).targetCompanyId,id);
 f.store.delete(`/rest/projectInbox/${second.id}`);
 assert.equal((await approveTransfer(f.api,second,{reviewStatus:'APPROVE'})).id,id);
});

test('a shared domain with conflicting LinkedIn is held instead of merging identities',async()=>{
 const f=fixture();f.store.set('/rest/companies/other',{id:'other',name:'Other',domainName:{primaryLinkUrl:'https://example.com'},linkedinLink:{primaryLinkUrl:'https://www.linkedin.com/company/other'}});
 await assert.rejects(approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'}),/другой компании/);
 assert.ok(!f.store.has(`/rest/companies/${id}`));
 assert.notEqual(f.store.get(`/rest/projectInbox/${id}`).reviewStatus,'APPROVE');
});

test('a concurrent creation is reconciled against the domain uniqueness constraint',async()=>{
 const f=fixture();const post=f.api.post;
 f.api.post=async(path,body)=>{
   if(path==='/rest/companies'){
     f.store.set('/rest/companies/other',{...structuredClone(body),id:'other'});
     throw Object.assign(new Error('duplicate entry'),{status:400});
   }
   return post(path,body);
 };
 assert.equal((await approveTransfer(f.api,f.item,{reviewStatus:'APPROVE'})).id,'other');
 assert.equal(f.store.get(`/rest/noteTargets/${id}`).targetCompanyId,'other');
});
