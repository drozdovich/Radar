// Explicit operator configuration; no embedded infrastructure or token minting.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

export function required(name) {
  const value = process.env[name];
  if (!value?.trim()) throw new Error(`Set ${name} in your private environment before running maintenance.`);
  return value;
}

export function twentyUrl() {
  const url = new URL(required('RADAR_TWENTY_URL'));
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
      || url.search || url.hash || url.pathname !== '/') {
    throw new Error('RADAR_TWENTY_URL must be an HTTP(S) origin without credentials or a path.');
  }
  return url.origin;
}

export function remote(command, input) {
  const host = required('RADAR_SSH_HOST');
  if (!/^[A-Za-z0-9][A-Za-z0-9._@-]*$/.test(host)) throw new Error('Invalid SSH host alias.');
  // Configure ProxyJump/IdentityFile in ~/.ssh/config when needed.
  return execFileSync('ssh', ['-o', 'BatchMode=yes', host, command], {
    input, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024,
  });
}

export function renderSql(query) {
  const schema = required('RADAR_TWENTY_SCHEMA');
  if (!/^workspace_[a-z0-9_]+$/.test(schema)) throw new Error('Invalid Twenty workspace schema.');
  const origin = twentyUrl().replaceAll("'", "''");
  return query.replaceAll('workspace_radar', schema).replaceAll('https://twenty.example.invalid', origin);
}

export function sql(query) {
  const container = required('RADAR_POSTGRES_CONTAINER');
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(container)) throw new Error('Invalid PostgreSQL container name.');
  return remote(`docker exec -i ${container} sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'`, renderSql(query));
}

export async function api(path, body, method = 'POST') {
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Expected an API path on the configured Twenty origin.');
  const origin = twentyUrl();
  const target = new URL(path, origin);
  if (target.origin !== origin) throw new Error('API path must stay on the configured Twenty origin.');
  const response = await fetch(target, {
    method, redirect: 'error',
    headers: { Authorization: `Bearer ${required('RADAR_TWENTY_TOKEN')}`, 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok || data.errors) throw new Error(`Twenty API request failed (HTTP ${response.status}); response contents omitted.`);
  return data;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [action, arg] = process.argv.slice(2);
  if (action === 'query') console.log(JSON.stringify(await api('/metadata', { query: arg })));
  else if (action === 'sql') console.log(sql(arg));
  else if (action === 'sql-file') console.log(sql(readFileSync(arg, 'utf8')));
  else throw new Error('Expected query, sql, or sql-file. Configure SDK authentication separately.');
}
