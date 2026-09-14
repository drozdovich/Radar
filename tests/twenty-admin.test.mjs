import { test } from 'node:test';
import assert from 'node:assert/strict';
import { api, renderSql, required } from '../scripts/twenty-admin.mjs';

test('maintenance requires explicit private configuration', () => {
  delete process.env.RADAR_TEST_MISSING;
  assert.throws(() => required('RADAR_TEST_MISSING'), /Set RADAR_TEST_MISSING/);
  process.env.RADAR_TWENTY_URL = 'https://twenty.example.invalid';
  process.env.RADAR_TWENTY_SCHEMA = 'workspace_example';
  assert.equal(renderSql('SELECT * FROM workspace_radar.person; -- https://twenty.example.invalid'),
    'SELECT * FROM workspace_example.person; -- https://twenty.example.invalid');
  process.env.RADAR_TWENTY_SCHEMA = 'workspace_x; DROP SCHEMA public';
  assert.throws(() => renderSql('SELECT 1'), /Invalid Twenty workspace schema/);
});

test('API credentials stay on the configured origin and are not forwarded on redirects', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  process.env.RADAR_TWENTY_URL = 'https://twenty.example.invalid';
  process.env.RADAR_TWENTY_TOKEN = 'synthetic-test-credential';
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return { ok: true, status: 200, json: async () => ({ data: {} }) };
  };
  try {
    await assert.rejects(api('//outside.example/path'), /Expected an API path/);
    await assert.rejects(api('/\\outside.example/path'), /configured Twenty origin/);
    assert.equal(calls.length, 0);
    await api('/rest/projectInbox', undefined, 'GET');
    assert.equal(calls[0].url, 'https://twenty.example.invalid/rest/projectInbox');
    assert.equal(calls[0].options.redirect, 'error');
    assert.equal(calls[0].options.headers.Authorization, 'Bearer synthetic-test-credential');
  } finally {
    globalThis.fetch = originalFetch;
    delete process.env.RADAR_TWENTY_TOKEN;
  }
});
