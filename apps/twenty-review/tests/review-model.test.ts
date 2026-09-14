import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decisionPatch, originalText, REASONS, SCOPES } from '../src/review-model.ts';

test('rejection needs a real reason AND explicit scope', () => {
  for (const args of [['', ''], ['OTHER', ''], ['', 'THIS_ITEM'], ['made_up', 'THIS_ITEM']]) {
    assert.throws(() => decisionPatch('REJECT', ...args), /Укажите/);
  }
  for (const [reason] of REASONS) for (const [scope] of SCOPES) {
    const patch = decisionPatch('REJECT', reason, scope, 'Подробно\nсвоими словами');
    assert.equal(patch.rejectReason, reason);
    assert.equal(patch.feedbackScope, scope);
    assert.equal(patch.feedbackNote.markdown, 'Подробно\nсвоими словами');
  }
});
test('approve and need info clear stale rejection metadata', () => {
  for (const status of ['APPROVE','NEED_INFO']) {
    const p = decisionPatch(status, 'OTHER', 'ALWAYS');
    assert.equal(p.rejectReason, null); assert.equal(p.feedbackScope, null);
  }
});
test('full original, including line breaks and long text, is not shortened', () => {
  const text = 'Текст\n'.repeat(5000);
  assert.equal(originalText({ markdown: text }), text);
  assert.equal(originalText({ blocknote: JSON.stringify([{content:[{text:'Один'}]},{content:[{text:'Два'}]}]) }), 'Один\nДва');
});
test('corrupt source is not fabricated', () => {
  assert.equal(originalText({blocknote:'not json'}), '');
  assert.throws(() => decisionPatch('unknown'));
});
