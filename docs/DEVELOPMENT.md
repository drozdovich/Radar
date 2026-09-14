# Development

Use Python 3.12–3.14, uv and Node 24.5+ from Node 24. Dependencies are locked; Python runtime code uses the standard library.

```sh
uv sync --locked
npm ci --ignore-scripts
npm --prefix apps/twenty-review ci --ignore-scripts
npm run check
uv build
```

`check` validates versions, documentation links, publication hygiene and the Canvas model, then runs Python/Node tests and TypeScript. GitHub Actions uses a clean checkout, installs dependencies and builds the package. The public history is scanned separately in CI.

## Narrow checks

```sh
uv run --locked radar demo
uv run --locked python -m pytest tests/test_public_demo.py tests/test_agent_digest.py
npm test
npm --prefix apps/twenty-review run check
npm run canvas:check
```

Tests use synthetic source IDs from `tests/fixtures/sources.json`; the registry is rejected by live collection. Fake boundaries are used for Telegram and Twenty. The integration and QA scripts are not unit tests and may write to a real configured database.

Version Radar together in pyproject.toml, __init__.py, uv.lock and root npm manifests. Version the Review extension separately when its application changes. Publishing source code does not deploy a running service.

The public profile is illustrative. Keep real labels, local profiles, `.env`, `.radar`, sessions and tokens out of Git. Add only invented examples to tests; `check-public.py` is a guardrail and does not replace human review of a publication.
