# Feedback

Radar keeps decision history and current Inbox status separately. A historical Reject that was later overturned must not remain an active rejection.

- `THIS_ITEM`: applies only to the reviewed card.
- `SIMILAR`: requires a stated reason and a concrete similarity boundary.
- `ALWAYS`: requires a stated, agreed rule; it is not inferred from one example.

The read-only Twenty exporter creates an atomic snapshot of history and current records. Stale/failed feedback blocks dependent review/delivery work; collecting the authorised source window can remain independent.

The local feedback sandbox imports snapshots, previews possible rules, evaluates held-out examples and preserves earlier approvals. Choosing a sandbox version does not deploy it to the live selector. QA examples are excluded from real user evidence.

```sh
uv run --locked radar feedback-demo
```

This second demo is entirely synthetic and shows feedback semantics, not automatic learning. Real labels belong under `.radar/feedback/` and are ignored by Git. Previously reviewed originals can be recorded in `.radar/reviewed-originals.json` as objects containing `chat_id` and `message_id`.
