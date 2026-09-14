# Legacy orchestration

n8n was used during an earlier implementation and is not required by the current product. The installation-specific n8n operator script is not included in the public version.

The supported source-code boundaries are the private pipeline API and the assistant review commands. Collection, analysis and delivery have independent completion states. Scheduling should invoke only explicitly configured scopes and preserve the human review boundary.

See [architecture](ARCHITECTURE.md) and [operations](docs/OPERATIONS.md). This page is background, not an instruction to install another orchestration service.
