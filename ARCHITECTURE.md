# Architecture

Radar 0.34.0 consists of a Python collector/review pipeline and a TypeScript extension for Twenty (Review 0.23.0). The [data map](docs/DATA_MAP.md) separates source evidence, decisions and derived records.

## Components

| Component | Responsibility | Entry point |
|---|---|---|
| Collector | Approved sources, complete windows, reconciliation, checkpoints | [pipeline.py](src/telegram_project_radar/pipeline.py) |
| Telegram adapter | TDLib runtime, session lock and normalised messages | [tdlib_source.py](src/telegram_project_radar/tdlib_source.py) |
| Private API | Authenticated run/status/collect/analyse/deliver operations | [pipeline_api.py](src/telegram_project_radar/pipeline_api.py) |
| Assistant review | Bounded batches, reading receipts, decisions, evidence and audit | [agent_review.py](src/telegram_project_radar/agent_review.py) |
| Candidate preparation | Company identity/size, author context and event facts | `company_*`, `person_context.py`, `events.py` |
| Delivery | Stable identities, exact text and read-back verification | [semantic_delivery.py](src/telegram_project_radar/semantic_delivery.py) |
| Review interface | Queue and human decisions | [Twenty extension](apps/twenty-review/README.md) |
| Database guards | Atomic Person/Event transfer and decision history | [migrations](migrations/twenty/017-review-guard.sql) |
| Feedback snapshot | Current status and history in one read-only transaction | [exporter](scripts/radar-feedback-export.mjs) |

## Collection

Sources come from a private JSON registry. `pilot` is the first two approved sources, `approved_sources` is the primary group, and `weekly_sources` is a legacy name for an additional group. Both groups can be collected daily. Only explicitly approved IDs are used; the old broad-account route is disabled.

The preset daily boundary is 05:00 Europe/Madrid. Collection resumes an incomplete collection of the same scope, then advances from its latest complete boundary. Waiting for analysis or delivery does not block the next collection interval. Gaps longer than 35 days are split into bounded runs. Date comparisons use actual instants, including timezone offsets.

Each run stores source snapshots, pass fingerprints, checkpoints and reply context in SQLite. Two passes must agree on message IDs and content. Comment audits use configured channel/discussion pairs and account for replies to older posts. Unreadable media and unavailable context remain explicit gaps.

## Review and delivery

`prepare` creates a manifest tied to the profile hash. `show` emits complete bounded parts. `submit` requires an explicit disposition for every primary message and exact evidence for each candidate. `audit-accept` binds the second review to the unchanged input and answer. This is an accountability mechanism, not proof of perfect AI recall or an independent reviewer.

Ordinary cards retain a chat/message identity. Digest cards use chat/message/span identity; spans use Unicode character offsets with an exclusive end. Overlap, quotes from another position and mixed whole-message/position candidates are rejected. Source hashes preserve the original relationship.

Before delivery, candidate identity and content hashes are checked against existing records, including earlier decisions. After creation, the record is read back. A retry preserves the previous status. Company facts require identity evidence; unknown size can hold a personal-project route without suppressing a separate valid telephony route.

Native Person and Event approval uses database guards to create related records atomically. Company/Opportunity transfer currently runs through the extension's REST path; its identity and live UI acceptance remain open work.

## Feedback and alternatives

Feedback export reads current Inbox states and append-only decision history in a repeatable-read transaction. An overturned Reject must not remain active. `THIS_ITEM` does not become a global exclusion. The feedback sandbox can preview rules but does not switch the production selector automatically.

The model-API implementation and deterministic selectors are alternative/legacy paths. n8n is not required. The offline demo uses real review and delivery functions with invented snapshots and an in-memory Inbox; it performs neither model inference nor live collection.

## Installation boundary

TDLib, a Telegram session, a private profile/source registry, Twenty metadata and operator credentials are external to Git. Maintenance uses explicit environment settings; there is no built-in user-token minting or server route. SQL uses a `workspace_radar` placeholder rendered for the configured workspace by the operator helper. [Installation details](docs/OPERATIONS.md).
