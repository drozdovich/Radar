import {completePersonProfile} from '../src/person-profile.ts';
import {originalText} from '../src/review-model.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {personName} from '../src/person-profile.ts';
import {transferPerson,validatePersonContext} from '../src/person-transfer.ts';
const fixture=()=>({id:'inbox',candidateId:'radar-one',candidateType:'PERSON',name:'Editorial description',summary:{markdown:'Создаю CRM для звонков.'},sourceLink:{primaryLinkUrl:'https://t.me/example/1'},personProfile:{schema_version:'person-profile-v1',status:'ready',candidate_id:'radar-one',source_url:'https://t.me/example/1',first_name:'Pavel',last_name:'',next_action:{title:'Уточнить интеграцию CRM',evidence_quote:'Создаю CRM',questions:'Какие звонки?',useful_result:'Установить сценарий',draft:'Привет! Какие звонки есть в CRM?'}}});
test('editorial title never becomes name, unknown surname stays empty',()=>{assert.deepEqual(personName(fixture()),{firstName:'Pavel',lastName:''});});
test('transplanted identity or invented quote fails before mutation',async()=>{
 for(const change of [(x:any)=>delete x.personProfile,(x:any)=>x.personProfile.candidate_id='radar-other',(x:any)=>x.personProfile.source_url='https://t.me/other/2',(x:any)=>x.personProfile.next_action.evidence_quote='invented',(x:any)=>{delete x.personProfile.next_action;x.summary={markdown:''}}]){
  const i:any=fixture();change(i);let writes=0;const api={get:async()=>({data:{projectInboxItem:i}}),patch:async()=>{writes++}};
  await assert.rejects(transferPerson(api,fixture(),{reviewStatus:'APPROVE'}));assert.equal(writes,0);
 }
});
test('client reconciles lost transaction response and verifies both source relations',async()=>{
 const i:any=fixture();let patches=0;
 const api={get:async(p:string)=>p.includes('/noteTargets?')?{data:{noteTargets:[{noteId:'note',targetPersonId:'person'}]}}:p.includes('/taskTargets?')?{data:{taskTargets:[{taskId:'task',targetPersonId:'person'}]}}:{data:{projectInboxItem:i}},patch:async()=>{i.reviewStatus='APPROVE';i.personProfile.transfer_result={person_id:'person',note_id:'note',task_id:'task'};patches++;throw Error('lost response')}};
 assert.equal((await transferPerson(api,fixture(),{reviewStatus:'APPROVE'})).id,'person');assert.equal(patches,1);
});
test('missing Task is never reported as complete',async()=>{
 const i:any=fixture();i.reviewStatus='APPROVE';i.personProfile.transfer_result={person_id:'person',note_id:'note',task_id:'task'};const api={get:async(p:string)=>p.includes('/noteTargets?')?{data:{noteTargets:[{noteId:'note',targetPersonId:'person'}]}}:p.includes('/taskTargets?')?{data:{taskTargets:[]}}:{data:{projectInboxItem:i}},patch:async()=>{}};
 await assert.rejects(transferPerson(api,fixture(),{reviewStatus:'APPROVE'}),/задача/);
});

test('old queued name-only profile gets a concrete source-bound clarification',()=>{const i:any=fixture();delete i.personProfile.next_action;i.unknowns={markdown:'Нужна ли интеграция CRM?'};const p=completePersonProfile(i,originalText);assert.match(p.next_action.title,/интеграция CRM/);assert.equal(p.next_action.evidence_quote,i.summary.markdown);assert.equal(i.personProfile.next_action,undefined)});

test('an old Approve is not proof that a changed comment survived a failed request',async()=>{
 const i:any=fixture();i.reviewStatus='APPROVE';i.feedbackNote={markdown:'old'};const api={get:async()=>({data:{projectInboxItem:i}}),patch:async()=>{throw Error('rejected new comment')}};
 await assert.rejects(transferPerson(api,fixture(),{reviewStatus:'APPROVE',feedbackNote:{markdown:'new'}}),/rejected new comment/);
});
