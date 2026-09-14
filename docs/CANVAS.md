# Repo Canvas

The portable model is [repo-map.json](repo-map.json). It describes code responsibilities, data boundaries and open acceptance work. It contains no live records or credentials. A readable flow is in [DATA_MAP](DATA_MAP.md).

```sh
npm run canvas:check
npm run canvas:sync
npm run repo-canvas:check
```

`canvas:check` validates the model. `canvas:sync` updates only the local `.repo-canvas/events.jsonl` journal, preserving unknown additions and prior history. Neither command invokes an architect/observer model or reads Telegram/CRM data. Repeating a sync with unchanged inputs adds no events.

The pinned Repo Canvas package provides the local board. `repo-canvas:start` can also activate an observer depending on local settings, so it is not required to validate this model. Board credentials, settings and event history remain local.

“Operational” in the model means implemented code, not a claim that the current release was deployed or that all live acceptance checks passed.
