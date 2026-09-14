# Radar Review 0.23.0

A React/TypeScript extension inside Twenty, not a separate hosted application. It presents the source, proposed fit, unknowns and human decisions.

- Approve creates/links the intended destination and retains the source.
- Reject requires a reason/scope; Need info keeps unresolved material visible.
- A completed decision advances to the next NEW card without deleting the currently open record.
- Company/Project transfer uses the extension REST path. Person/Event transfer relies on the database guards and verifies the returned links.

## Check locally

From the repository root: `npm test` and `npm --prefix apps/twenty-review run check`. Use Node 24.5+ from Node 24. The pinned SDK targets the existing Twenty integration; the package lock is the dependency reference.

Fake-API tests do not prove the actual user's permissions or a successful live UI workflow. Company/Opportunity identity checks, complete live acceptance and fresh installation remain in [ROADMAP](../../ROADMAP.md).

## Install separately

The extension needs an existing Inbox/native schema and the appropriate guards from `migrations/twenty`. Use the SDK's normal authentication and plan/apply workflow for the target instance. [Operations](../../docs/OPERATIONS.md) lists the boundary; public code does not include credentials, a token-minting helper or fixed workspace/layout identities.

QA/schema/cleanup scripts can write to the configured instance. They are excluded from offline tests. Version 0.34.0 of the Python/publication package does not deploy this extension or change its application version.
