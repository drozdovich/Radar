import { useEffect, useState } from 'react';
import { defineFrontComponent } from 'twenty-sdk/define';
import { useSelectedRecordIds, useColorScheme, navigate, AppPath, openSidePanelPage, SidePanelPages, closeSidePanel } from 'twenty-sdk/front-component';
import { RestApiClient } from 'twenty-client-sdk/rest';
import { nextCandidate, INBOX_VIEW } from '../queue';
import { approveTransfer } from '../approve-transfer';
import { verifiedCompanyProfile } from '../company-profile';
import { decisionPatch, originalText, REASONS, EVENT_REASONS, SCOPES } from '../review-model';

type Item = { eventData?: any; eventId?: string; companyProfile?: any; id: string; name: string; candidateId: string; candidateType?: string; reviewStatus: string; rejectReason?: string; feedbackScope?: string; summary?: {markdown?: string; blocknote?: string}; feedbackNote?: {markdown?: string}; sourceLink?: {primaryLinkUrl?: string}; whyFit?: {markdown?: string}; knownConditions?: {markdown?: string}; risks?: {markdown?: string}; unknowns?: {markdown?: string} };
const statuses: Record<string,string> = { NEW: 'На рассмотрении', APPROVE: 'Одобрено', REJECT: 'Архив', NEED_INFO: 'На исследование' };
const destinations: Record<string,string> = { APPROVE: 'Одобрено', REJECT: 'Архив', NEED_INFO: 'На исследование' };

export function Review() {
  const [id] = useSelectedRecordIds();
  const dark = useColorScheme() === 'dark';
  const [item, setItem] = useState<Item | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [scope, setScope] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [savedTo, setSavedTo] = useState('');
  const [transferredUrl, setTransferredUrl] = useState('');
  useEffect(() => {
    let active = true;
    setItem(null); setError(''); setSavedTo(''); setTransferredUrl(''); setRejecting(false);
    if (!id) return;
    new RestApiClient().get(`/rest/projectInbox/${id}`).then((result: any) => {
      if (!active) return;
      const record = result.data?.projectInboxItem ?? result.data?.projectInbox ?? result.data ?? result;
      if (!record?.id || !record.candidateId?.startsWith('radar-')) throw new Error('Не найден исходный проект Radar.');
      setItem(record); setReason(record.rejectReason ?? ''); setScope(record.feedbackScope ?? ''); setNote(record.feedbackNote?.markdown ?? '');
      if (record.reviewStatus === 'REJECT' && (!record.rejectReason || !record.feedbackScope)) setRejecting(true);
    }).catch(() => { if (active) setError('Не удалось загрузить исходник. Закройте карточку и откройте снова.'); });
    return () => { active = false; };
  }, [id]);
  async function save(status: string) {
    if (busy || !item) return;
    setError(''); setSavedTo(''); setBusy(true);
    try {
      const patch = decisionPatch(status, reason, scope, note);
      let savedDestination = destinations[status];
      if (status === 'APPROVE') {
        const moved = await approveTransfer(new RestApiClient(), item, patch);
        savedDestination = moved.label;
        setTransferredUrl(`/object/${moved.singular}/${moved.id}`);
      } else {
        await new RestApiClient().patch(`/rest/projectInbox/${item.id}`, patch);
      }
      setItem({ ...item, ...patch } as Item); setRejecting(false);
      setSavedTo(savedDestination);
      // Find the next item only after the decision is stored, so the current item cannot reopen.
      try {
        const next = await nextCandidate(new RestApiClient());
        void navigate(AppPath.RecordIndexPage, { objectNamePlural: 'projectInbox' }, { viewId: INBOX_VIEW }).catch(() => setError('Решение сохранено. Открой «Входящие» в меню.'));
        if (next) {
          void openSidePanelPage({ page: SidePanelPages.ViewRecord, recordId: next.id,
            objectNameSingular: 'projectInboxItem', resetNavigationStack: true }).catch(() => setError('Решение сохранено. Открой следующую карточку во входящих.'));
        } else {
          void closeSidePanel().catch(() => setError('Входящие разобраны. Можно закрыть панель.'));
        }
      } catch { setError('Решение сохранено. Следующая карточка пока не загрузилась — открой «Входящие».'); }
    } catch (e) { setError(e instanceof Error && !e.message.includes('http') && !(e as any).status ? e.message : 'Действие не завершено. Карточка сохранена; попробуй ещё раз.'); }
    finally { setBusy(false); }
  }
  const profile = item ? verifiedCompanyProfile(item) : null;
  const missingProfile = item?.candidateType === 'COMPANY' && !profile;
  const button = { padding: '9px 13px', borderRadius: 7, border: '1px solid #737373', cursor: busy ? 'wait' : 'pointer', color: dark ? '#eee' : '#202020', background: dark ? '#303030' : '#fff' };
  const field = { padding: '10px', width: '100%', boxSizing: 'border-box' as const, borderRadius: 6, border: '1px solid #888', color: dark ? '#eee' : '#202020', background: dark ? '#252525' : '#fff' };
  return <section style={{ padding: 16, fontFamily: 'system-ui, sans-serif', fontSize: 15, lineHeight: 1.6, color: dark ? '#eee' : '#222' }}>
    <div style={{ fontSize: 12, opacity: .7 }}>RADAR · v0.23.0 · {item ? statuses[item.reviewStatus] : 'Загрузка…'}</div>
    {error && <p role="alert" style={{ color: dark ? '#ffa7a7' : '#a92323' }}>{error}</p>}
    {savedTo && <p role="status">Решение сохранено. Карточка в списке «{savedTo}». {transferredUrl ? <a href={transferredUrl}>Открыть запись →</a> : 'Открываю следующую карточку…'}</p>}
    {!id && <p>Откройте одну карточку Project Inbox.</p>}
    {item && <>
      <h2 style={{ fontSize: 19, margin: '8px 0 12px' }}>{item.name}</h2>
      {!transferredUrl && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
        <button style={{ ...button, borderColor: '#41886b' }} disabled={busy || missingProfile} onClick={() => save('APPROVE')}>{busy ? 'Сохраняю…' : '✓ Approve'}</button>
        <button style={{ ...button, borderColor: '#b76666' }} disabled={busy} onClick={() => { setRejecting(true); setError(''); }}>✕ Reject…</button>
        <button style={button} disabled={busy} onClick={() => save('NEED_INFO')}>? Нужны данные</button>
      </div>}
      {item.candidateType === 'COMPANY' && <div style={{ marginBottom: 18 }}>
        <strong>Реквизиты компании</strong>
        {profile ? <>
          <div><a href={profile.website_url} target="_blank" rel="noopener noreferrer">{profile.domain}</a> · <a href={profile.linkedin_url} target="_blank" rel="noopener noreferrer">LinkedIn ↗</a></div>
          <div>{[profile.city, profile.country].filter(Boolean).join(', ') || 'Город и страна не опубликованы в LinkedIn'}</div>
          {profile.missing?.some((v: string) => v === 'city' || v === 'country') && <div>Адрес заполнится только опубликованными данными.</div>}
          <div style={{ fontSize: 12, opacity: .7 }}>Проверено {profile.checked_on}. Перенесётся в Company после Approve.</div>
        </> : <div>Домен и LinkedIn этой компании ещё не подтверждены или требуют обновления. Карточка остаётся «На исследование» до проверки реквизитов.</div>}
      </div>}
      {rejecting && <div style={{ border: '1px solid #b76666', borderRadius: 8, padding: 14, marginBottom: 20 }}>
        <strong>Почему не подходит?</strong>
        <p style={{ margin: '5px 0 12px', fontSize: 13 }}>{item.candidateType === 'EVENT' ? 'Дата, стоимость и язык — отдельные ограничения. Отказ из-за даты не запрещает тему.' : 'Отказ относится к этой возможности. Существующую компанию он не удаляет.'}</p>
        <label>Причина *<select style={field} value={reason} disabled={busy} onChange={e => setReason(e.target.value)}><option value="">Выберите причину</option>{(item.candidateType === 'EVENT' ? EVENT_REASONS : REASONS).map(([v,l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label>К чему применять *<select style={{ ...field, marginBottom: 10 }} value={scope} disabled={busy} onChange={e => setScope(e.target.value)}><option value="">Выберите область</option>{SCOPES.map(([v,l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label>Ваше пояснение (необязательно)<textarea style={field} rows={4} value={note} disabled={busy} onChange={e => setNote(e.target.value)} placeholder="Можно подробно и своими словами" /></label>
        <p style={{ fontSize: 12, opacity: .8 }}>Пояснение сохранится как обратная связь. Правила не меняются автоматически: сначала проверим их на примерах.</p>
        <div style={{ display: 'flex', gap: 8 }}><button style={button} disabled={busy || !reason || !scope} onClick={() => save('REJECT')}>{busy ? 'Сохраняю…' : 'Сохранить отказ'}</button><button style={button} disabled={busy} onClick={() => { setRejecting(false); setError(''); }}>Отмена</button></div>
      </div>}
      {item.candidateType === 'EVENT' && <div style={{ marginBottom: 18 }}>
        <strong>Событие · Зачем мне идти</strong>
        <p style={{ whiteSpace: 'pre-wrap' }}>{item.eventData?.whyAttend || item.whyFit?.markdown || 'Польза пока не описана'}</p>
        <p style={{fontSize:13}}>Approve сохраняет в «Ивенты» как «Интересно». Это не регистрация и не обещание посещения.</p>
        <dl>{[['Дата', item.eventData?.startDate], ['Время начала / окончания', [item.eventData?.startTime,item.eventData?.endTime].filter(Boolean).join(' — ')],
          ['Дата окончания',item.eventData?.endDate],['Часовой пояс',item.eventData?.timeZone],['Город',item.eventData?.city],['Место',item.eventData?.venue],
          ['Формат',item.eventData?.eventFormat],['Язык',item.eventData?.language],['Стоимость',[item.eventData?.priceStatus,item.eventData?.cost,item.eventData?.currency].filter(Boolean).join(' · ')],
          ['Срок регистрации',item.eventData?.registrationDeadline],['Организатор',item.eventData?.organizer],['Аудитория',item.eventData?.audience],['Опубликованные компании / спикеры',item.eventData?.announcedParticipants],['Проверено',item.eventData?.checkedAt]
        ].map(([label,value])=><div key={label}><dt style={{display:'inline',fontWeight:600}}>{label}: </dt><dd style={{display:'inline',margin:0}}>{value || 'Неизвестно'}</dd></div>)}</dl>
        {item.eventData?.officialUrl && /^https?:\/\//.test(item.eventData.officialUrl) && <a href={item.eventData.officialUrl} target="_blank" rel="noopener noreferrer">Официальная страница / регистрация ↗</a>}
        <p><strong>Что нужно выяснить:</strong> {item.eventData?.unknowns || item.unknowns?.markdown || 'Дополнительные сведения пока не перечислены'}</p>
        <label>Твой комментарий / каких данных не хватает<textarea style={field} rows={3} value={note} disabled={busy} onChange={e=>setNote(e.target.value)} /></label>
      </div>}
      {item.knownConditions?.markdown && <div style={{ marginBottom: 18 }}><strong>{item.candidateType === 'PERSON' ? 'О человеке' : item.candidateType === 'COMPANY' ? 'О компании' : 'О проекте и условиях'}</strong><div style={{ whiteSpace: 'pre-wrap', marginTop: 6 }}>{item.knownConditions.markdown}</div></div>}
      <div style={{ borderTop: '1px solid #8885', paddingTop: 14 }}><strong>Оригинальный текст</strong><div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', marginTop: 10 }}>{originalText(item.summary) || 'Исходный текст отсутствует — решение лучше отложить.'}</div></div>
      {item.sourceLink?.primaryLinkUrl && /^https:\/\/t\.me\//.test(item.sourceLink.primaryLinkUrl) && <p><a href={item.sourceLink.primaryLinkUrl} target="_blank" rel="noopener noreferrer" style={{ color: dark ? '#8ab9ff' : '#245ca3' }}>Открыть оригинал в Telegram ↗</a></p>}
      <details style={{ marginTop: 18 }}><summary>Почему попало в подборку, риски и неизвестные условия</summary>{[['Почему подходит',item.whyFit?.markdown],['Риски',item.risks?.markdown],['Неизвестно',item.unknowns?.markdown]].map(([label,text]) => <div key={label}><strong>{label}</strong><p style={{ whiteSpace: 'pre-wrap' }}>{text || 'Не указано'}</p></div>)}</details>
    </>}
  </section>;
}
export default defineFrontComponent({ universalIdentifier: '9db0f1a5-7288-4ceb-a80e-783026298170', name: 'radar-project-review', description: 'Оригинал и решение v0.23.0', component: Review });
