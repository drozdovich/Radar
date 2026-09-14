// Editorial card titles must never become a human name.
export function personName(item: any) {
  const p = item.personProfile;
  if (p?.schema_version !== 'person-profile-v1' || p.status !== 'ready'
      || p.candidate_id !== item.candidateId || !p.source_url
      || p.source_url !== item.sourceLink?.primaryLinkUrl
      || typeof p.first_name !== 'string' || !p.first_name.trim()
      || typeof p.last_name !== 'string') {
    throw new Error('Имя человека ещё не проверено по исходнику. Карточка сохранена; описание не будет записано вместо имени.');
  }
  return { firstName: p.first_name.trim(), lastName: p.last_name.trim() };
}

// Existing queued PERSON cards get a source-bound clarification on their next approval.
// No queue rewrite or Telegram call is needed to migrate the old name-only contract.
export function completePersonProfile(item: any, text: (value: any) => string) {
 personName(item);
 const p=structuredClone(item.personProfile);
 const original=text(item.summary);
 if(!p.next_action){
  const quote=original.split('\n').find(line=>line.trim()&&!line.startsWith('#'))||'';
  const question=text(item.unknowns).split('\n').find(line=>line.trim())||'Какую совместную задачу ты готов обсуждать сейчас';
  p.next_action={title:`Уточнить у ${p.first_name}: ${question.replace(/[.?]$/, '')}`,evidence_quote:quote,questions:text(item.unknowns)||question,useful_result:`Проверить гипотезу: ${text(item.whyFit)||'есть ли конкретная совместная задача'}. Спрос пока не подтверждён.`,draft:`${p.first_name}, привет! Увидел твоё сообщение: «${quote}». Хотел уточнить: ${question.replace(/[.?]$/, '')}?`};
 }
 if(/(?:\bЯ\b|Я |меня зовут|мой линкдин|my linkedin)/i.test(original)&&!/(?:его|её|коллег|подруг|знакомого)/i.test(original)){
  const urls=original.match(/(?:https?:\/\/)?(?:www\.)?linkedin\.com\/in\/[A-Za-z0-9_-]+\/?/g)||[];
  if(!p.linkedin_url&&urls.length===1)p.linkedin_url=(urls[0].startsWith('http')?urls[0]:'https://'+urls[0]).replace(/\/$/,'');
  const emails=[...original.matchAll(/^(?:мой\s+)?(?:e-?mail|почта)\s*:\s*([^\s<>@]+@[^\s<>@]+\.[A-Za-z]{2,})\s*$/gim)];
  if(!p.email&&emails.length===1)p.email=emails[0][1];
 }
 p.checked_on||=new Date().toISOString().slice(0,10);p.enrichment_source||=p.source_url;
 return p;
}
