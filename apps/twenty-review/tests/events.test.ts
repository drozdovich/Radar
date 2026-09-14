import test from 'node:test';
import assert from 'node:assert/strict';
import {approveTransfer} from '../src/approve-transfer.ts';
import {decisionPatch} from '../src/review-model.ts';

test('event approve reconciles a lost response to the atomic server transaction',async()=>{
 const item={id:'source',candidateId:'radar-qa-event',candidateType:'EVENT',name:'Event',reviewStatus:'NEW',eventId:null as string|null};
 let patches=0;
 const api={get:async(path:string)=>path.includes('projectInbox')?{data:{projectInboxItem:item}}:{data:{radarEvent:{id:'target'}}},patch:async()=>{patches++;item.reviewStatus='APPROVE';item.eventId='target';throw Error('Connection lost')},post:async()=>{throw Error('Client must not create a partial event')},delete:async()=>{throw Error('Source must stay')}};
 const result=await approveTransfer(api,item,decisionPatch('APPROVE'));assert.equal(result.id,'target');assert.equal(result.label,'Ивенты');assert.equal(patches,1);
});
test('changed event identity fails before writing a decision',async()=>{
 const item={id:'source',candidateId:'radar-qa-event',candidateType:'EVENT',name:'Event'};let writes=0;
 const api={get:async()=>({data:{projectInboxItem:{...item,candidateType:'PERSON'}}}),patch:async()=>{writes++},post:async()=>{},delete:async()=>{}};
 await assert.rejects(approveTransfer(api,item,decisionPatch('APPROVE')));assert.equal(writes,0);
});
test('event rejection preserves specific reason and scope',()=>{
 const p=decisionPatch('REJECT','EVENT_DATE','THIS_ITEM','Другой день');assert.equal(p.rejectReason,'EVENT_DATE');assert.equal(p.feedbackScope,'THIS_ITEM');assert.equal(p.feedbackNote.markdown,'Другой день');
});
