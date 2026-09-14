# Roadmap

## Current release: public prototype 0.34.0

- [x] Preserve exact source evidence and explicit review decisions.
- [x] Resume collection independently of pending delivery.
- [x] Split independent digest positions with exact spans and stable IDs.
- [x] Validate allowlisted HTTP redirects before following them.
- [x] Provide an offline example using invented data and real review/delivery code.
- [x] Move source IDs and installation settings out of published code.
- [x] Keep the public documentation separate from personal operating history.
- [x] Separate public code from private operating history and add full-history checks to CI.

## Next milestone: a reproducible complete review flow

1. Verify Company/Opportunity identities and references against the collision class already fixed for Person.
2. Complete live UI acceptance: Approve / Reject / Need info, next NEW card, correct linked records and actual permissions.
3. Document and test installation from an empty environment, including the initial Twenty schema and restore process.
4. Evaluate selection quality on new, separately labelled examples; measure missed opportunities as well as false positives.

The hackathon focus is this single end-to-end path, not a new orchestration layer or a parallel CRM. A bounded scope can be chosen to match the event's time limit.

## Deliberately later

New sources/platforms, a hosted multi-user service, automatic outreach, additional agents, a separate product frontend and collector relocation. They are not prerequisites for the current review workflow.

## Acceptance boundaries

Unit tests do not replace live UI acceptance. Reconciliation proves technical collection consistency, not semantic recall. The offline demo uses scripted decisions. Existing private operation is separate from a GitHub release; deploying an update requires its own rollout and health checks.

## Scope decision

14 September 2026: the owner requested a public version for a hackathon application. This authorises repository preparation, a synthetic demonstration and publication, while preserving the current private working installation and its data. Earlier private development history is not part of the public dataset.
