import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {api,remote,sql} from './twenty-admin.mjs';
import {readFileSync,writeFileSync} from 'node:fs';
import {approveTransfer} from '../apps/twenty-review/src/approve-transfer.ts';
import {decisionPatch} from '../apps/twenty-review/src/review-model.ts';
const root='.radar/people-context-2026-09-14/';
const client={get:p=>api(p,undefined,'GET'),post:(p,b)=>api(p,b),patch:(p,b)=>api(p,b,'PATCH')};
const ids=[];const suffix=randomUUID().slice(0,8);const source=(n,username='qa_'+suffix)=>{
 const i={id:randomUUID(),candidateId:'radar-qa-person-'+suffix+'-'+n,candidateType:'PERSON',name:'QA PERSON '+n,reviewStatus:'NEW',source:'QA only',publishedAt:'2026-09-14T10:00:00Z',summary:{markdown:'Я QA. Создаю CRM для звонков. Мой email: qa-'+suffix+'@example.com'},whyFit:{markdown:'Гипотеза: интеграция CRM и робота, спрос не подтверждён.'},sourceLink:{primaryLinkUrl:'https://t.me/qa_person_'+suffix+'/'+n}};
 i.personProfile={schema_version:'person-profile-v1',status:'ready',candidate_id:i.candidateId,source_url:i.sourceLink.primaryLinkUrl,first_name:'QA',last_name:'',telegram_username:username,email:'qa-'+suffix+'@example.com',job_title:'QA role',checked_on:'2026-09-14',next_action:{title:'Уточнить сценарий звонков и интеграцию CRM',evidence_quote:'Создаю CRM для звонков.',questions:'Какие звонки автоматизировать?',useful_result:'Выяснить один сценарий интеграции.',draft:'Привет! Видел CRM для звонков. Какие сценарии стоит автоматизировать?'}};return i;
};
async function create(i){await client.post('/rest/projectInbox',i);ids.push(i.id);writeFileSync(root+'person-qa-ids.json',JSON.stringify(ids));return i;}
const patch=decisionPatch('APPROVE','','','QA comment, not training');const a=await create(source(1000));
const first=await approveTransfer(client,a,patch);assert.notEqual(first.id,a.id);
const get=async(k,id,key)=>(await client.get('/rest/'+k+'/'+id)).data[key];
let person=await get('people',first.id,'person');assert.equal(person.emails.primaryEmail,a.personProfile.email);
const transfer=(await get('projectInbox',a.id,'projectInboxItem')).personProfile.transfer_result;
const links=(await client.get('/rest/noteTargets?filter=noteId[eq]:'+transfer.note_id)).data.noteTargets;
const tasklinks=(await client.get('/rest/taskTargets?filter=taskId[eq]:'+transfer.task_id)).data.taskTargets;
assert.equal(new Set([a.id,first.id,links[0].id,links[0].noteId,tasklinks[0].id,tasklinks[0].taskId]).size,6);
const note=await get('notes',links[0].noteId,'note');assert.ok(note.bodyV2.markdown.endsWith(a.summary.markdown));assert.ok(JSON.parse(note.bodyV2.blocknote).some(b=>b.content.some(c=>c.type==='link')));
await client.patch('/rest/people/'+first.id,{jobTitle:'Manual role'});
await client.patch('/rest/tasks/'+tasklinks[0].taskId,{title:'Manual task',status:'DONE'});
await approveTransfer(client,a,patch);assert.equal((await get('people',first.id,'person')).jobTitle,'Manual role');assert.equal((await get('tasks',tasklinks[0].taskId,'task')).title,'Manual task');
const b=await create(source(1001));assert.equal((await approveTransfer(client,b,patch)).id,first.id);
let lost=true;const c=await create(source(1002));const fault={...client,patch:async(p,v)=>{const r=await client.patch(p,v);if(lost){lost=false;throw Error('lost response')}return r}};assert.equal((await approveTransfer(fault,c,patch)).id,first.id);
const concurrent=await Promise.all([create(source(1003)),create(source(1004))]);const moved=await Promise.all(concurrent.map(i=>approveTransfer(client,i,patch)));assert.ok(moved.every(x=>x.id===first.id));
// Fault after task creation: a temporary scoped trigger raises only for this QA Inbox.
const bad=await create(source(1005));
sql(`CREATE OR REPLACE FUNCTION radar_review.qa_person_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW."taskId"=radar_review.person_uuid('radar-task:${bad.id}') THEN RAISE EXCEPTION 'QA late failure'; END IF; RETURN NEW; END; $$; CREATE TRIGGER qa_person_fail BEFORE INSERT ON workspace_radar."taskTarget" FOR EACH ROW EXECUTE FUNCTION radar_review.qa_person_fail();`);
try {await assert.rejects(approveTransfer(client,bad,patch));assert.equal((await get('projectInbox',bad.id,'projectInboxItem')).reviewStatus,'NEW');assert.equal(sql(`SELECT count(*) FROM radar_review.person_source WHERE inbox_id='${bad.id}'`).trim(),'0');assert.equal(sql(`SELECT count(*) FROM radar_review.feedback WHERE record_id='${bad.id}'`).trim(),'0');}finally{sql('DROP TRIGGER qa_person_fail ON workspace_radar."taskTarget"; DROP FUNCTION radar_review.qa_person_fail();');}
await approveTransfer(client,bad,patch);
assert.equal(sql(`SELECT count(*) FROM radar_review.feedback WHERE record_id='${a.id}' AND decision='APPROVE'`).trim(),'1');
const ui=source(1006,'qa_ui_'+suffix);ui.personProfile.email='';await create(ui);
writeFileSync(root+'person-qa-result.json',JSON.stringify({passed:true,ids,personId:first.id,ui,checks:['atomic Person+Note+Task+Approve','distinct cross-object IDs','email field from original','full original and native link nodes','repeat without duplicate feedback','manual role and DONE task preserved','second source same Person separate Note/Task','lost HTTP response','simultaneous sources one Person','late failure rollback then retry']},null,2));console.log(JSON.stringify({passed:true,count:ids.length,uiId:ui.id}));
