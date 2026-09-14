# Installation and operation

The public repository is a reproducible source release and offline demo. A complete new live installation is still a roadmap item.

## Required external pieces

| Piece | Configuration |
|---|---|
| Telegram adapter | Existing TelegramSearchMCP checkout; `TGSEARCH_SOURCE_DIR` |
| TDLib/session | Private runtime config, pinned library hash, credentials and exclusive session lock |
| Source allowlist | `.radar/sources.json` or `RADAR_SOURCES_FILE`; see [SOURCES](../SOURCES.md) |
| Private review profile | `RADAR_PROFILE_FILE`; changes affect new manifest fingerprints |
| Run data | `.radar/` or `RADAR_DATA_DIR` |
| Private API | Local service config, authentication and allowed peers; no public endpoint is provided |
| Twenty | An existing workspace with Project Inbox and native objects |
| Operator API access | `RADAR_TWENTY_URL` and a legitimate `RADAR_TWENTY_TOKEN` |
| SQL maintenance | SSH alias, PostgreSQL container and workspace schema from environment |

[.env.example](../.env.example) lists names without credentials. Python/Node do not automatically source this file; supply environment variables through your own shell or service configuration. Set SSH `ProxyJump`/`IdentityFile` in your private SSH config if needed. Published code contains no fixed jump host, user IDs or token-minting helper.

`RADAR_TWENTY_TOKEN` must be obtained through the installation's normal authentication mechanism. Never put it in source code, command output or an issue. The REST helper refuses cross-origin paths and redirects so a bearer token stays on its configured origin.

## Twenty schema order

1. Establish the initial Project Inbox fields and native Company/Person/Opportunity/Note/Task objects. A complete initial bootstrap is not included yet.
2. Apply the 017 decision guard, then the version migrations 018–021 as appropriate to the installed schema.
3. Add company profile metadata through the schema script.
4. Add event metadata, then 022 event guards and views.
5. Add person profile/context metadata, then the 023 person guards/version migration.
6. Plan the Review extension using Twenty SDK, inspect changes, apply and complete live UI acceptance.

SQL files use the example schema name `workspace_radar` and the non-resolving origin `https://twenty.example.invalid`. `scripts/twenty-admin.mjs sql-file <file>` renders these using validated `RADAR_TWENTY_SCHEMA` and `RADAR_TWENTY_URL` before sending SQL over the configured SSH boundary. Running raw template SQL directly is not a complete installation method.

Maintenance/QA scripts can modify the configured instance. They are excluded from offline checks. Some migration/version files describe historical incremental upgrades, not a fresh-install migration runner. Review's application identities are code-defined; deployment-specific workspace/layout identities must be configured or discovered in the target instance.

## Rollout and restore

A release does not restart the operator's service or update Twenty. A rollout must preserve the prior configuration/version, check health, verify independent scope cursors and confirm uninterrupted source windows. The 05:00 boundary must not lose the hours after an earlier midnight cutoff.

A restore needs separate private backups of run data, sessions, decisions and Twenty. Unit tests, API checks and live UI acceptance answer different questions. The remaining acceptance work is explicit in [ROADMAP](../ROADMAP.md).
