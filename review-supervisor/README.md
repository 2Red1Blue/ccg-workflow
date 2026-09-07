# CCG review supervisor

`ccg-agent-supervisor.py` is the durable, user-local dual-review coordinator
for CCG. It invokes independent Codex and Claude Code leaf reviewers, keeps
bounded receipts below `~/.claude/.ccg/agent-runs/`, and never stores raw input
after a terminal result.

`ccg_review_runtime.py` parses direct Claude Code `stream-json` events. It
records only final reports, partial answer text, progress metadata, and actual
response model IDs; it deliberately excludes reasoning and tool payloads.

The installer deploys these files to `~/.claude/bin/` as:

```text
ccg-agent-supervisor
ccg_review_runtime.py
```

The wrapper's local Web UI remains available. The [Chrome companion](../browser-companion/README.md)
opens inactive task tabs and closes them on completion. Without the companion,
the printed URL remains usable. `CODEAGENT_WEB_UI_AUTO_OPEN=false` disables
automatic tabs; `true` explicitly selects the legacy system-browser opener.

Development verification:

```sh
python3 -m unittest discover -s review-supervisor/tests -p 'test_*.py'
```

The installed command is documented by `~/.claude/docs/ccg-review-supervisor.md`.
That local document and review receipts are intentionally not package inputs.
