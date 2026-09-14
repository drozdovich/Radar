import {personName,completePersonProfile} from './person-profile.ts';
import {originalText} from './review-model.ts';
export function validatePersonContext(item: any) {
 personName(item);
 const a=item.personProfile.next_action;
 if(!a || ['title','evidence_quote','questions','useful_result','draft'].some(k=>typeof a[k]!=='string'||!a[k].trim())) throw new Error('Следующий шаг для контакта ещё не подготовлен. Карточка сохранена.');
 if(!originalText(item.summary).includes(a.evidence_quote)) throw new Error('Цитата следующего шага не совпадает с оригиналом.');
}
export async function transferPerson(api: any, input: any, decision: any) {
 const response=await api.get(`/rest/projectInbox/${input.id}`);
 const item=response?.data?.projectInboxItem ?? response?.data ?? response;
 if(item.candidateId!==input.candidateId || item.candidateType!=='PERSON' || !item.candidateId?.startsWith('radar-')) throw new Error('Карточка изменилась. Открой её заново.');
 const profile=completePersonProfile(item,originalText);
 validatePersonContext({...item,personProfile:profile});
 let failure;
 try {await api.patch(`/rest/projectInbox/${input.id}`,{...decision,personProfile:profile});} catch(e) {failure=e;}
 // The database transaction owns creation. Reconcile a lost HTTP response.
 const sourceResponse=await api.get(`/rest/projectInbox/${input.id}`);
 const saved=sourceResponse?.data?.projectInboxItem ?? sourceResponse?.data ?? sourceResponse;
 if(saved.reviewStatus!=='APPROVE' || (decision.feedbackNote && originalText(saved.feedbackNote).trim()!==originalText(decision.feedbackNote).trim())) throw failure ?? new Error('Approve не сохранён. Повтор продолжит перенос.');
 const result=saved.personProfile?.transfer_result;
 if(!result?.person_id||!result?.note_id||!result?.task_id) throw new Error('Следующая задача и полный исходник не подтверждены.');
 const noteResult=await api.get(`/rest/noteTargets?filter=noteId[eq]:${result.note_id}&limit=20`);
 if(!(noteResult?.data?.noteTargets??[]).some((l:any)=>l.targetPersonId===result.person_id)) throw new Error('Связь исходника с контактом не подтверждена.');
 const taskResult=await api.get(`/rest/taskTargets?filter=taskId[eq]:${result.task_id}&limit=20`);
 if(!(taskResult?.data?.taskTargets??[]).some((l:any)=>l.targetPersonId===result.person_id)) throw new Error('Следующая задача контакта не подтверждена.');
 return {plural:'people',singular:'person',label:'People',relation:'targetPersonId',id:result.person_id};
}
