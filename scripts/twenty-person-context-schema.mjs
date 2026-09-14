// Add only the missing native link field. Default read-only; --apply is explicit.
import {api} from './twenty-admin.mjs';
const objects=(await api('/metadata',{query:'{ objects(paging:{first:100}) {edges {node {id nameSingular fields(paging:{first:100}) {edges {node {id name type}}}}}} }'})).data.objects.edges.map(x=>x.node);
const person=objects.find(x=>x.nameSingular==='person');if(!person)throw Error('People metadata unavailable');
const existing=person.fields.edges.find(x=>x.node.name==='telegram')?.node;
if(existing&&existing.type!=='LINKS')throw Error('Incompatible Telegram field');
console.log(JSON.stringify({object:'person',field:'telegram',action:existing?'preserve':'create',deletions:0}));
if(!existing&&process.argv.includes('--apply')) await api('/metadata',{query:'mutation($input:CreateOneFieldMetadataInput!){createOneField(input:$input){id name type}}',variables:{input:{field:{objectMetadataId:person.id,type:'LINKS',name:'telegram',label:'Telegram',description:'Подтверждённый профиль автора; полный источник находится в Notes.',isNullable:true,icon:'IconBrandTelegram'}}}});
