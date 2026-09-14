export const INBOX_VIEW = '8004ad79-94b2-4379-b6c6-95b1e6cd0913';
export async function nextCandidate(api: { get: (path: string) => Promise<any> }) {
  const result = await api.get('/rest/projectInbox?filter=reviewStatus[eq]:NEW&order_by=createdAt[AscNullsLast]&limit=1');
  const rows = result?.data?.projectInbox ?? [];
  const next = rows[0];
  return next?.id && next.reviewStatus === 'NEW' ? next : null;
}
