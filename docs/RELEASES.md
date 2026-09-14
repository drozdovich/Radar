# Releases

## 0.34.0 — public prototype

- Added `radar demo`: invented messages, real evidence/review/delivery code and an in-memory Inbox. Repeated delivery preserves prior decisions.
- Moved source allowlists and comment pairs to explicit local configuration; a fresh checkout or synthetic registry cannot start live collection.
- Made the review profile and previous-original registry local inputs.
- Removed private review datasets, internal infrastructure identities and installation-specific token minting/repair scripts from the public tree.
- Added portable operator environment settings, origin-bound API access and workspace SQL rendering.
- Added presentation documentation, a synthetic walkthrough and a publication-hygiene check.

The public code starts from a clean publication history. Earlier private development records and operational data are not distributed. Twenty Review remains 0.23.0; its application has not been changed by this packaging release.

Live service deployment, complete bootstrap/restore and UI acceptance remain separate work. CI results are available in the repository's Actions tab.

## Earlier implementation work

Before public preparation, the collector was corrected to advance independently of pending delivery, digest positions gained exact non-overlapping spans, and HTTP redirects were validated before following them. These changes are included in the public version.
