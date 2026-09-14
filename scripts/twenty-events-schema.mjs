// Additive, resumable metadata migration. Existing decisions and objects are preserved.
import { api, remote, sql } from './twenty-admin.mjs';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
const root='.radar/events-2026-09-14'; mkdirSync(root,{recursive:true,mode:0o700});
const query='{objects(paging:{first:100}){edges{node{id nameSingular namePlural universalIdentifier fields(paging:{first:100}){edges{node{id name type options}}}}}}}';
const objects=(await api('/metadata',{query})).data.objects.edges.map(x=>x.node);
const inbox=objects.find(o=>o.nameSingular==='projectInboxItem');
let event=objects.find(o=>o.nameSingular==='radarEvent');
const fields=[
 ['eventKey','TEXT','Ключ мероприятия'],['startDate','DATE','Дата начала'],['startTime','TEXT','Время начала'],['endDate','DATE','Дата окончания'],['endTime','TEXT','Время окончания'],['timeZone','TEXT','Часовой пояс'],
 ['city','TEXT','Город'],['venue','TEXT','Место / адрес'],['eventFormat','TEXT','Формат'],['language','TEXT','Язык'],['cost','TEXT','Стоимость'],['eventCurrency','TEXT','Валюта'],['priceStatus','TEXT','Платность'],['registrationDeadline','TEXT','Срок регистрации'],['organizer','TEXT','Организатор'],['officialUrl','TEXT','Официальная страница / регистрация'],
 ['whyAttend','TEXT','Зачем мне идти'],['audience','TEXT','Предполагаемая аудитория'],['announcedParticipants','TEXT','Опубликованные компании / спикеры'],['unknowns','TEXT','Что неизвестно'],['checkedAt','DATE_TIME','Последняя проверка'],['userComment','TEXT','Твой комментарий'],['originalText','TEXT','Полный первый исходник'],['sourceUrl','TEXT','Первый источник'],['provenance','RICH_TEXT','Источники и решения'],['sourceRecords','RAW_JSON','История происхождения'],['eventCancellation','SELECT','Отмена мероприятия'],['attendanceStatus','SELECT','Твоё решение'],
];
const opts=values=>values.map(([value,label],position)=>({id:randomUUID(),value,label,position,color:['blue','yellow','green','purple','gray'][position%5]}));
const statuses=opts([['INTERESTED','Интересно'],['PLANNING','Планирую'],['REGISTERED','Зарегистрирован'],['ATTENDED','Посетил'],['NOT_GOING','Не пойду']]);
const cancellations=opts([['UNKNOWN','Не проверена'],['SCHEDULED','Подтверждено'],['CANCELLED','Отменено'],['POSTPONED','Перенесено']]);
console.log(JSON.stringify({eventObject:event?'reuse':'create',inboxFields:['eventData','eventId'],eventFields:fields.length,deletions:0}));
if(!process.argv.includes('--apply'))process.exit(0);
if(!existsSync(root+'/records-before.json')) {
 const before=sql(`BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT jsonb_build_object('inbox',(SELECT jsonb_agg(to_jsonb(t)) FROM workspace_radar."_projectInboxItem" t),'feedback',(SELECT jsonb_agg(to_jsonb(f)) FROM radar_review.feedback f),'companies',(SELECT jsonb_agg(to_jsonb(c)) FROM workspace_radar.company c),'people',(SELECT jsonb_agg(to_jsonb(p)) FROM workspace_radar.person p),'opportunities',(SELECT jsonb_agg(to_jsonb(o)) FROM workspace_radar.opportunity o)); ROLLBACK;`);
 writeFileSync(root+'/records-before.json',before.split('\n').find(x=>x.startsWith('{')),{mode:0o600});
}
if(!event){event=(await api('/metadata',{query:'mutation($input:CreateOneObjectInput!){createOneObject(input:$input){id nameSingular namePlural universalIdentifier fields(paging:{first:100}){edges{node{id name type options}}}}}',variables:{input:{object:{nameSingular:'radarEvent',namePlural:'radarEvents',labelSingular:'Ивент',labelPlural:'Ивенты',icon:'IconCalendarEvent',description:'Мероприятия для знакомств и возможного сотрудничества; Approve не означает регистрацию.'}}}})).data.createOneObject;}
async function field(object,name,type,label,options){
 const found=object.fields.edges.find(x=>x.node.name===name)?.node;
 if(found){if(found.type!==type)throw Error('Incompatible existing field '+name);return found;}
 const f=(await api('/metadata',{query:'mutation($input:CreateOneFieldMetadataInput!){createOneField(input:$input){id name type options}}',variables:{input:{field:{objectMetadataId:object.id,name,type,label,isNullable:true,icon:'IconCalendarEvent',...(options?{options}:{}),...(['eventKey','provenance','sourceRecords','eventId'].includes(name)?{isUIReadOnly:true}:{})}}}})).data.createOneField;
 object.fields.edges.push({node:f}); return f;
}
await field(inbox,'eventData','RAW_JSON','Сведения о мероприятии');await field(inbox,'eventId','UUID','Сохранённый ивент');
for(const [name,type,label] of fields)await field(event,name,type,label,name==='attendanceStatus'?statuses:name==='eventCancellation'?cancellations:null);
for(const [name,extras] of [['candidateType',[['EVENT','Событие']]],['rejectReason',[['EVENT_TOPIC','Тема / аудитория'],['EVENT_DATE','Неудобная дата'],['EVENT_COST','Стоимость'],['EVENT_LANGUAGE','Язык']]]]){
 const f=inbox.fields.edges.find(x=>x.node.name===name).node;
 const missing=extras.filter(([v])=>!f.options.some(o=>o.value===v));
 if(missing.length)await api('/metadata',{query:'mutation($input:UpdateOneFieldMetadataInput!){updateOneField(input:$input){id}}',variables:{input:{id:f.id,update:{options:[...f.options,...opts(missing).map((o,i)=>({...o,position:f.options.length+i}))]}}}});
}
writeFileSync(root+'/schema.json',JSON.stringify({inbox,event},null,2),{mode:0o600});
console.log(JSON.stringify({created:true,eventId:event.id,universalIdentifier:event.universalIdentifier}));
