export const REASONS = [
  ['NOT_RELEVANT_ROLE', 'Не моя роль / задача'],
  ['WRONG_INDUSTRY_CONTEXT', 'Не та отрасль или контекст'],
  ['FULL_TIME_PERMANENT', 'Full-time / постоянная работа'],
  ['GEOGRAPHY_ONSITE', 'География или офис'],
  ['LEVEL_MISMATCH', 'Не мой уровень'],
  ['TOOL_STACK_MISMATCH', 'Не мой стек / инструменты'],
  ['ADVERTISEMENT_NO_CONCRETE_ASK', 'Реклама / нет конкретного запроса'],
  ['DUPLICATE', 'Повтор'],
  ['OTHER', 'Другое'],
] as const;
export const EVENT_REASONS = [['EVENT_TOPIC', 'Тема / аудитория'], ['EVENT_DATE', 'Неудобная дата'], ['EVENT_COST', 'Стоимость'], ['EVENT_LANGUAGE', 'Язык'], ['DUPLICATE', 'Повтор'], ['OTHER', 'Другое']] as const;
export const SCOPES = [
  ['THIS_ITEM', 'Только эта карточка'],
  ['SIMILAR', 'Предложить правило для похожих'],
  ['ALWAYS', 'Предложить постоянное правило'],
] as const;
export function decisionPatch(status: string, reason = '', scope = '', note = '') {
  if (!['APPROVE', 'REJECT', 'NEED_INFO'].includes(status)) throw new Error('Неизвестное решение');
  if (status === 'REJECT' && (![...REASONS, ...EVENT_REASONS].some(([v]) => v === reason) || !SCOPES.some(([v]) => v === scope))) {
    throw new Error('Укажите причину отказа и к чему её применять.');
  }
  return {
    reviewStatus: status,
    rejectReason: status === 'REJECT' ? reason : null,
    feedbackScope: status === 'REJECT' ? scope : null,
    feedbackNote: { markdown: note, blocknote: JSON.stringify([{ id: 'radar-feedback', type: 'paragraph', props: {}, content: [{ type: 'text', text: note, styles: {} }], children: [] }]) },
  };
}

export function originalText(summary?: { markdown?: string; blocknote?: string }) {
  if (summary?.markdown) return summary.markdown;
  if (!summary?.blocknote) return '';
  // Never silently truncate a source if only BlockNote content is present.
  try {
    const blocks = JSON.parse(summary.blocknote);
    const blockText = (b: any): string => (b.content ?? []).map((c: any) => c.text ?? (c.content ?? []).map((x: any) => x.text ?? '').join('')).join('') + (b.children?.length ? '\n' + b.children.map(blockText).join('\n') : '');
    return blocks.map(blockText).join('\n');
  } catch { return ''; }
}
