import assert from 'node:assert/strict';
import {api,remote,sql} from './twenty-admin.mjs';
import {readFileSync,writeFileSync} from 'node:fs';
import {randomUUID,createHash} from 'node:crypto';
import {buildContext} from './radar-feedback-export.mjs';
const root='.radar/events-2026-09-14';
const key='event-'+createHash('sha256').update(randomUUID()).digest('hex');
const qa=(await api('/rest/projectInbox/3221a184-b493-4978-ba1c-9a7f8a658a9a',undefined,'GET')).data.projectInboxItem;
const ids=[];
for(let n=0;n<2;n++){
 const id=randomUUID();ids.push(id);
 await api('/rest/projectInbox',{id,name:'QA concurrent '+n,candidateId:'radar-qa-events-concurrency-'+id,candidateType:'EVENT',reviewStatus:'NEW',summary:qa.summary,sourceLink:qa.sourceLink,eventData:{...qa.eventData,eventKey:key,eventCancellation:'CANCELLED'}});
}
await Promise.all(ids.map(id=>api('/rest/projectInbox/'+id,{reviewStatus:'APPROVE'},'PATCH')));
const rows=await Promise.all(ids.map(async id=>(await api('/rest/projectInbox/'+id,undefined,'GET')).data.projectInboxItem));
assert.equal(rows[0].eventId,rows[1].eventId);
let ev=(await api('/rest/radarEvents/'+rows[0].eventId,undefined,'GET')).data.radarEvent;
assert.equal(ev.sourceRecords.length,2);assert.equal(ev.eventCancellation,'CANCELLED');assert.equal(ev.attendanceStatus,'INTERESTED');
await api('/rest/radarEvents/'+ev.id,{attendanceStatus:'PLANNING',venue:'Manual venue'},'PATCH');
await api('/rest/projectInbox/'+ids[0],{eventData:{...rows[0].eventData,venue:'Published venue',language:'English'}},'PATCH');
ev=(await api('/rest/radarEvents/'+ev.id,undefined,'GET')).data.radarEvent;
assert.equal(ev.venue,'Manual venue');assert.equal(ev.language,'English');assert.equal(ev.attendanceStatus,'PLANNING');
assert.equal(ev.sourceRecords.find(x=>x.inboxId===ids[0]).facts.venue,'Published venue');
const out=sql(`BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT jsonb_build_object('records',(SELECT jsonb_agg(to_jsonb(t)) FROM workspace_radar."_projectInboxItem" t),'events',(SELECT jsonb_agg(to_jsonb(f)) FROM radar_review.feedback f),'companies',(SELECT jsonb_agg(to_jsonb(c)) FROM workspace_radar.company c),'people',(SELECT jsonb_agg(to_jsonb(p)) FROM workspace_radar.person p),'opportunities',(SELECT jsonb_agg(to_jsonb(o)) FROM workspace_radar.opportunity o)); ROLLBACK;`);
const after=JSON.parse(out.split('\n').find(x=>x.startsWith('{'))), before=JSON.parse(readFileSync(root+'/records-before.json'));
const context=buildContext(after);
assert.ok(![...context.active_rejects,...context.current_approvals].some(x=>x.candidate_id.startsWith('radar-qa-')));
const eventsById=new Map(after.events.map(x=>[x.id,x]));for(const old of before.feedback)assert.deepEqual(eventsById.get(old.id),old,'Historical feedback changed');
const afterById=new Map(after.records.map(x=>[x.id,x]));const external=[];
for(const old of before.inbox){const saved=afterById.get(old.id);assert.ok(saved,'Old Inbox deleted');assert.equal(saved.summaryMarkdown,old.summaryMarkdown,'Original changed');if(old.id!=='27cae7bb-968b-52e3-9d38-cdaf22967a6b'&&old.reviewStatus!==saved.reviewStatus)external.push({id:old.id,before:old.reviewStatus,after:saved.reviewStatus});}
for(const kind of ['companies','people','opportunities']){const by=new Map(after[kind].map(x=>[x.id,x]));for(const old of before[kind])assert.ok(by.has(old.id),'Existing record missing');}
writeFileSync(root+'/verification.json',JSON.stringify({passed:true,concurrent_sources:true,fact_update_preserves_manual:true,cancellation_independent:true,qa_excluded:true,historical_feedback_preserved:before.feedback.length,originals_preserved:before.inbox.length,other_decision_changes_during_user_review:external},null,2),{mode:0o600});
writeFileSync(root+'/records-after.json',JSON.stringify(after),{mode:0o600});
console.log(JSON.stringify({passed:true,concurrentSources:true,qaExcluded:true,historicalFeedbackPreserved:before.feedback.length,otherDecisionChanges:external.length}));
