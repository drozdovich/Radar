import test from 'node:test';
import assert from 'node:assert/strict';
import { nextCandidate } from '../src/queue.ts';
test('next card is fetched from NEW after a decision; finished queues return empty', async()=>{
 const calls:string[]=[];
 const api={get:async(path:string)=>{calls.push(path);return {data:{projectInbox:[{id:'next',reviewStatus:'NEW'}]}};}};
 assert.equal((await nextCandidate(api)).id,'next');assert.match(calls[0],/reviewStatus\[eq\]:NEW/);
 assert.equal(await nextCandidate({get:async()=>({data:{projectInbox:[]}})}),null);
 assert.equal(await nextCandidate({get:async()=>({data:{projectInbox:[{id:'done',reviewStatus:'REJECT'}]}})}),null);
});
