# Working on Radar

Read PRODUCT.md, ARCHITECTURE.md and ROADMAP.md before changing behaviour. Keep the original task scope active; examples and side questions do not authorise unrelated implementation.

- Check Git status and preserve other people's changes.
- Make small, reviewable changes and add meaningful tests for changed behaviour.
- Bump the relevant package version when changing application behaviour.
- Run `npm run check`; use the synthetic demo for a local walkthrough.
- Never commit source messages, real review datasets, profiles, credentials, sessions or installation settings.
- No live Telegram collection, CRM writes, production rollout or messages to others unless the operator explicitly requests them.
- Maintenance and QA scripts may have real effects. They are not part of the offline test suite.
- Keep evidence, hypotheses and unknowns distinct. Never claim AI quality or live UI acceptance from unit tests.

Public examples must be invented. The operational runtime and private development history are separate from this repository.
