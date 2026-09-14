import test from 'node:test';
import assert from 'node:assert/strict';
import {buildContext,fresh} from '../scripts/radar-feedback-export.mjs';
test('current state cancels historic Reject, while archived current Reject stays visible',()=>{
 const records=[{id:'a',candidateId:'radar-a',reviewStatus:'APPROVE'},{id:'b',candidateId:'radar-b',reviewStatus:'NEW'},
 {id:'c',candidateId:'radar-c',reviewStatus:'REJECT',rejectReason:'OTHER',feedbackScope:'THIS_ITEM',deletedAt:'y',feedbackNoteMarkdown:'exact note'},
 {id:'qa',candidateId:'radar-qa-example',reviewStatus:'REJECT'}];
 const events=['a','b','c','missing'].map(id=>({id,record_id:id,decision:'REJECT'}));
 const c=buildContext({records,events});assert.deepEqual(c.active_rejects.map(x=>x.record_id),['c']);
 assert.equal(c.active_rejects[0].note,'exact note');assert.equal(c.current_approvals.length,1);assert.equal(c.all_events.length,4);
 assert.equal(c.incomplete[0].issue,'current_record_missing_do_not_assume_active');assert.deepEqual(c.excluded_qa_record_ids,['qa']);
});
test('missing reason is surfaced; duplicate events cannot silently pass',()=>{
 const c=buildContext({records:[{id:'a',candidateId:'radar-a',reviewStatus:'REJECT'}],events:[]});assert.equal(c.incomplete.length,1);
 assert.throws(()=>buildContext({records:[],events:[{id:'x'},{id:'x'}]}));
});
test('freshness follows Madrid date, rejects yesterday and future timestamps',()=>{
 const clock=new Date('2026-09-14T07:00:00Z');
 assert.ok(fresh({status:'ready',exported_at:'2026-09-14T06:00:00Z'},clock));
 assert.ok(!fresh({status:'ready',exported_at:'2026-09-13T06:00:00Z'},clock));
 assert.ok(!fresh({status:'ready',exported_at:'2026-09-14T08:00:00Z'},clock));
 assert.ok(!fresh({status:'failed',exported_at:'2026-09-14T06:00:00Z'},clock));
});
test('removed QA events stay in raw history but do not become missing user feedback',()=>{
 const events=[{id:'qa',candidate_id:'radar-qa-old',record_id:'gone',decision:'REJECT'}];
 const c=buildContext({events,records:[]});assert.equal(c.all_events.length,1);assert.equal(c.incomplete.length,0);
});
