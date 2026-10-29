# Changelog

## 1.1.0 (unreleased)
- Match reviewed bot helpers: persona protocol 2, safe model switching, snapshot conflicts, explicit partial-GIF errors and stdin-only bot posting.
- Use bundled portable demo rules without executing files from the local-copy folder.
- Preserve private local edits when server imports reject a stale snapshot.
- Requires paired server helper updates and production verification.

## 1.0.1
- Standalone release: separate from the LatoChan bot repo; demo mode works without it (vendored `latodash/vendor/botsettings.py` and `gifstore.py` are copies of the bot's files, used only by the demo and tests).
- Settings tab: ~30 live bot settings, validated against the schema the server reports.
- GIFs tab: library view, paste/file import, per-link remove.
- Tests: 61.

## 1.0.0
First release: overview, logs, API calls, tools, brain CLI.
