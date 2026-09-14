# Product

## Purpose

Find a small number of useful opportunities in explicitly selected Telegram sources and preserve enough evidence for the owner to make a decision. Quality matters more than queue size. A possible fit is a hypothesis, not confirmed demand.

The current preset covers personal projects, telephony buyers and voice-automation opportunities. Their internal keys remain `projects`, `infinity` and `avans` for compatibility. Events and professional contacts are also supported. These tracks are examples from the original use case, not a claim of universal matching.

## Review contract

1. Collect accessible text within an approved scope and reconcile its contents. Record gaps.
2. Review every primary message with its context. Keep exact quotes and explicit unknowns.
3. Let the person approve a batch before delivering it to Project Inbox.
4. Review the Inbox card: Approve, Reject with reason/scope, or Need info.
5. Preserve the original evidence, current status, decision history and links to destination objects.

A chat approval authorises delivery of a batch. A native Approve inside Twenty creates or links the destination record; these are distinct actions. No messages, applications or registrations are sent automatically.

An ordinary message produces one card with one or more tracks. An independent digest position has its own non-overlapping Unicode `source_span`, exact text block and stable ID.

## Candidate types

| Type | Result after native approval |
|---|---|
| PROJECT / DIGEST_POSITION | Opportunity and source Note |
| COMPANY | Verified Company identity and source Note |
| PERSON | Confirmed author, Person, Note, Task and links |
| EVENT | Event with explicit date/time uncertainty, source and decision |

Missing information is not invented. A vendor is not automatically a customer. Identical person names do not prove identical people. Unknown event times are not converted to midnight.

## Personalisation boundary

[PROFESSIONAL_PROFILE.md](PROFESSIONAL_PROFILE.md) is a public example. A private profile can be supplied with `RADAR_PROFILE_FILE`; changing it changes the review-manifest fingerprint and requires an explicit migration for existing batches. Deterministic legacy filters and delivery guards remain a concrete preset; editing prose alone does not reconfigure all of them.

## Public release decision — 14 September 2026

The owner requested a presentable public version for a hackathon application. Version 0.34.0 removes installation identifiers and private review data, adds explicit local configuration and a synthetic offline demonstration. This packaging work does not deploy the service, change Twenty or restart collection. A clean publication history is required; earlier private development records stay private.

The live-product acceptance gaps remain in [ROADMAP](ROADMAP.md). Publicity does not turn the prototype into a production-ready hosted service.
