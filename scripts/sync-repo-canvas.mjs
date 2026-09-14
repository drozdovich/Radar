// Reconcile the reviewed, versioned map. No observer, model, browser or remote calls.
import { readFileSync } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
if (args.length !== 1 || !['--check', '--apply'].includes(args[0])) {
  throw new Error('Use --check for validation or --apply to update the local map');
}
process.env.REPO_CANVAS_ROOT = fileURLToPath(new URL('../', import.meta.url));
const { validateArchitecture } = await import('../node_modules/repo-canvas/repo-canvas/scripts/semantic-model.mjs');
const { createEvent, appendEvents, getSnapshot } = await import('../node_modules/repo-canvas/repo-canvas/scripts/canvas-store.mjs');
const { validateEvent, validateEventSequence } = await import('../node_modules/repo-canvas/repo-canvas/scripts/canvas-schema.mjs');
const model = JSON.parse(readFileSync(new URL('../docs/repo-map.json', import.meta.url), 'utf8'));
validateArchitecture(model, { areas: [], entities: [], relations: [] });
const { areas, entities, relations, removedAreaIds, removedEntityIds, removedRelationIds, ...map } = model;
const actor = 'codex-reviewed-map';
const desired = [createEvent('map.upsert', { actor, payload: map }),
  ...areas.map(payload => createEvent('area.upsert', { actor, payload })),
  ...entities.map(payload => createEvent('entity.upsert', { actor, payload })),
  ...relations.map(payload => createEvent('relation.upsert', { actor, payload }))];
const errors = [...desired.flatMap(validateEvent), ...validateEventSequence(desired.map((event, i) => ({ event, line: i + 1 })))];
if (errors.length) throw new Error(JSON.stringify(errors));
if (args[0] === '--check') {
  console.log(`Map valid: ${entities.length} entities, ${relations.length} relations; no local state changed`);
} else {
  const before = getSnapshot();
  if (before.storeErrors.length) throw new Error('Existing Canvas needs review before synchronization');
  const tables = { map: new Map([['map', before.map]]), area: new Map(before.areas.map(x => [x.id, x])),
    entity: new Map(before.entities.map(x => [x.id, x])), relation: new Map(before.relations.map(x => [x.id, x])) };
  const events = desired.filter(event => {
    const old = tables[event.type.split('.')[0]].get(event.payload.id ?? 'map');
    return !old || Object.entries(event.payload).some(([k, v]) => !isDeepStrictEqual(old[k], v));
  });
  for (const [kind, ids] of [['relation', removedRelationIds], ['entity', removedEntityIds], ['area', removedAreaIds]]) {
    for (const id of ids) if (tables[kind].has(id)) {
      events.push(createEvent(`${kind}.remove`, { actor, payload: { id, reason: 'Reviewed map replaces obsolete structure; event history retained' } }));
    }
  }
  if (events.length) appendEvents(events, { expectedRevision: before.revision });
  const after = getSnapshot();
  if (after.storeErrors.length) throw new Error('Canvas verification failed');
  console.log(`Canvas synchronized: ${events.length} events; revision ${after.revision}; historical work retained`);
}
