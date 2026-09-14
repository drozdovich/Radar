# Source configuration

No real Telegram source list is included in the public repository. A fresh checkout has no live collection permissions.

Start with [sources.example.json](examples/sources.example.json), copy it to the ignored `.radar/sources.json`, and configure sources you are authorised to read. The example is marked `synthetic: true`; live collection rejects it. Replace the invented IDs/names and deliberately set `synthetic: false` only for your own installation.

`approved` contains the primary group, `additional` the additional group, and `comment_pairs` binds an additional channel to its included discussion group. IDs must be negative integers; duplicate IDs/names and discussion pairs outside the allowlist are rejected. A private file can also be selected with `RADAR_SOURCES_FILE`.

`pilot` uses the first two primary entries. The historical scope name `weekly_sources` means the additional group; scheduling it daily is allowed. Source order affects review order, not whether a message is retained.

Direct messages, bots, Saved Messages and secret chats are outside the collection scope. Expanding the source list is an explicit operator decision. A successful technical pass does not prove that every useful meaning was recognised.
