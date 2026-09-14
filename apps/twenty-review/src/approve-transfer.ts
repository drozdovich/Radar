import { transferPerson } from './person-transfer.ts';
import { personName } from './person-profile.ts';
import { originalText } from './review-model.ts';
import { companyFields, companyFieldsMatch, missingCompanyFields, verifiedCompanyProfile } from './company-profile.ts';

type Api = { get: (path: string) => Promise<any>; post: (path: string, body: any) => Promise<any>; patch: (path: string, body: any) => Promise<any>; delete: (path: string) => Promise<any> };
type Candidate = { id: string; name: string; candidateType?: string; candidateId: string; [key: string]: any };

export function destination(type?: string) {
  if (type === 'EVENT') return { plural: 'radarEvents', singular: 'radarEvent', label: 'Ивенты', relation: 'eventId' };
  if (type === 'COMPANY') return { plural: 'companies', singular: 'company', label: 'Companies', relation: 'targetCompanyId' };
  if (type === 'PERSON') return { plural: 'people', singular: 'person', label: 'People', relation: 'targetPersonId' };
  if (type === 'PROJECT' || type === 'DIGEST_POSITION') return { plural: 'opportunities', singular: 'opportunity', label: 'Opportunities', relation: 'targetOpportunityId' };
  throw new Error('У карточки не указан поддерживаемый тип. Перенос не выполнен.');
}

function record(result: any, key: string) {
  const data = result?.data ?? result;
  if (data && key in data) return data[key];
  const createKey = `create${key[0].toUpperCase()}${key.slice(1)}`;
  return data?.[createKey] ?? data;
}
async function find(api: Api, path: string, key: string) {
  try { return record(await api.get(path), key); }
  catch (error) { if ((error as any)?.status === 404) return null; throw error; }
}
async function ensure(api: Api, plural: string, singular: string, body: any, verify = (_record: any) => true, alternative = async (): Promise<any> => null) {
  const path = `/rest/${plural}/${body.id}`;
  const existing = await find(api, path, singular);
  if (existing) return existing;
  const reusable = await alternative();
  if (reusable) return reusable;
  try {
    const created = record(await api.post(`/rest/${plural}`, body), singular);
    if (created?.id === body.id && verify(created)) return created;
  }
  catch (error) {
    // A second click or a lost create response must reuse the same UUID.
    const created = await find(api, path, singular);
    if (created?.id === body.id && verify(created)) return created;
    const reusable = await alternative();
    if (reusable) return reusable;
    throw error;
  }
  const created = await find(api, path, singular);
  if (created?.id !== body.id || !verify(created)) throw new Error('Созданную запись не удалось проверить. Исходная карточка сохранена.');
  return created;
}
function noteBody(item: Candidate) {
  const fields = [['Описание и условия', item.knownConditions], ['Почему подходит', item.whyFit], ['Риски', item.risks], ['Неизвестно', item.unknowns], ['Твоё пояснение', item.feedbackNote]];
  const markdown = [
    `Radar: ${item.candidateId}`, item.sourceLink?.primaryLinkUrl ? `Источник: ${item.sourceLink.primaryLinkUrl}` : '',
    item.publishedAt ? `Дата публикации: ${item.publishedAt}` : '', item.fitScore != null ? `Оценка: ${item.fitScore}` : '',
    ...fields.map(([label, value]) => originalText(value).trim() ? `## ${label}\n${originalText(value)}` : ''),
    ...(verifiedCompanyProfile(item) ? [`## Реквизиты компании\nСайт: ${item.companyProfile.website_url}\nLinkedIn: ${item.companyProfile.linkedin_url}\nГород / страна: ${[item.companyProfile.city, item.companyProfile.country].filter(Boolean).join(', ') || 'Не опубликованы'}\nПроверено: ${item.companyProfile.checked_on}`] : []),
    `## Оригинальный текст\n${originalText(item.summary)}`,
  ].filter(Boolean).join('\n\n');
  return { markdown, blocknote: JSON.stringify([{ id: 'radar-source', type: 'paragraph', props: {}, content: [{ type: 'text', text: markdown, styles: {} }], children: [] }]) };
}

async function ensureCompany(api: Api, item: Candidate, name: string) {
  const fields = companyFields(item);
  const alternative = async () => {
    const response = await api.get(`/rest/companies?filter=domainName.primaryLinkUrl[eq]:${encodeURIComponent(fields.domainName.primaryLinkUrl)}&limit=2`);
    const matches = response?.data?.companies ?? [];
    if (!matches.length) return null;
    const match = matches[0];
    const sameLinkedIn = match.linkedinLink?.primaryLinkUrl?.replace(/\/$/, '') === fields.linkedinLink.primaryLinkUrl;
    const sameName = match.name?.split(' — ')[0]?.trim().toLowerCase() === item.companyProfile.company_name.toLowerCase();
    if (matches.length !== 1 || !match.id || (!sameLinkedIn && (match.linkedinLink?.primaryLinkUrl || !sameName))) {
      throw new Error('Этот домен уже указан у другой компании. Нужна проверка принадлежности; дубликат не создан.');
    }
    return match;
  };
  const saved = await ensure(api, 'companies', 'company', { id: item.id, name, ...fields }, record => companyFieldsMatch(record, fields), alternative);
  const path = `/rest/companies/${saved.id}`;
  const patch = missingCompanyFields(saved, fields);
  if (Object.keys(patch).length) {
    await api.patch(path, patch);
    const verified = await find(api, path, 'company');
    if (!companyFieldsMatch(verified, patch)) throw new Error('Реквизиты компании не удалось проверить. Исходная карточка сохранена; повтор продолжит перенос.');
  }
  return saved;
}

export async function approveTransfer(api: Api, input: Candidate, decision: any) {
  const target = destination(input.candidateType);
  if (input.candidateType === 'EVENT') {
    // The database trigger commits event, source link and feedback in one transaction.
    // An uncertain network response is reconciled by reading the source again.
    const current = await find(api, `/rest/projectInbox/${input.id}`, 'projectInboxItem');
    if (current?.candidateType !== 'EVENT' || current.candidateId !== input.candidateId || !current.candidateId.startsWith('radar-')) throw new Error('Карточка изменилась. Открой её заново.');
    let failure;
    try { await api.patch(`/rest/projectInbox/${input.id}`, decision); } catch (e) { failure = e; }
    const saved = await find(api, `/rest/projectInbox/${input.id}`, 'projectInboxItem');
    if (saved?.reviewStatus !== 'APPROVE' || !saved.eventId) throw failure ?? new Error('Перенос события не подтверждён. Карточка сохранена.');
    const event = await find(api, `/rest/radarEvents/${saved.eventId}`, 'radarEvent');
    if (!event?.id) throw new Error('Событие сохранено, но чтение результата недоступно. Обнови карточку.');
    return { ...target, id: event.id };
  }
  if (input.candidateType === 'PERSON') return transferPerson(api, input, decision);
  const sourcePath = `/rest/projectInbox/${input.id}`;
  const item = await find(api, sourcePath, 'projectInboxItem') as Candidate | null;
  if (!item) {
    const link = await find(api, `/rest/noteTargets/${input.id}`, 'noteTarget');
    const targetId = link?.[target.relation] ?? input.id;
    const existing = await find(api, `/rest/${target.plural}/${targetId}`, target.singular);
    const note = await find(api, `/rest/notes/${input.id}`, 'note');
    if (existing && note && link?.noteId === input.id && link?.[target.relation] === existing.id) return { ...target, id: existing.id };
    throw new Error('Исходная карточка недоступна; завершённый перенос не найден.');
  }
  if (item.candidateId !== input.candidateId || item.candidateType !== input.candidateType || !item.candidateId.startsWith('radar-')) throw new Error('Карточка изменилась. Открой её заново.');
  const name = target.singular === 'person' ? personName(item) : item.name;
  const body = noteBody(item);
  if (target.singular === 'company') companyFields(item);
  const [saved, note] = await Promise.all([
    target.singular === 'company' ? ensureCompany(api, item, name as string)
      : ensure(api, target.plural, target.singular, { id: item.id, name, ...(target.singular === 'opportunity' ? { stage: 'NEW' } : {}) }),
    ensure(api, 'notes', 'note', { id: item.id, title: `Radar — ${item.name}`, bodyV2: body }),
  ]);
  if (originalText(note.bodyV2) !== body.markdown) throw new Error('Заметка отличается от исходника. Карточка оставлена во входящих.');
  const link = await ensure(api, 'noteTargets', 'noteTarget', { id: item.id, noteId: item.id, [target.relation]: saved.id });
  if (link.noteId !== item.id || link[target.relation] !== saved.id) throw new Error('Не удалось подтвердить связь заметки. Карточка сохранена.');
  // Complete the review without deleting the open native record.
  await api.patch(sourcePath, decision);
  return { ...target, id: saved.id };
}
