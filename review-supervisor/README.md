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
ccg_review_web.py
ccg_review_web.html
ccg_review_web.js
ccg_review_web.css
```

The wrapper's local Web UI remains available. The [Chrome companion](../browser-companion/README.md)
opens inactive task tabs and closes them on completion. Without the companion,
the printed URL remains usable. `CODEAGENT_WEB_UI_AUTO_OPEN=false` disables
automatic tabs; `true` explicitly selects the legacy system-browser opener.

## Review center

Run `ccg-agent-supervisor web-ui` to serve the read-only review center at
`127.0.0.1:19876` (`--port` overrides it). In another terminal,
`ccg-agent-supervisor web-url` prints the authenticated browser URL. Keep this
URL private. The token is passed in a fragment and stored in that tab's session
storage; API requests use an Authorization header. Restarting the server rotates
the token, so reopen the printed URL afterward. No browser is opened automatically.

For a persistent macOS service, `print-webui-launchd-plist` prints a user LaunchAgent
definition using the installed interpreter and command paths. Save it as
`~/Library/LaunchAgents/com.ccg.review-ui.plist` and load it with `launchctl bootstrap`
in your GUI user domain. It runs on login and restarts after failure, with no automatic
browser opening. Unload it with `launchctl bootout` before removing that plist.
Use `web-url` after each restart to obtain the current access URL.

The page lists retained dual-review runs, project paths, execution states,
aggregate verdicts, individual reports/partial reports, retry origins, durations,
and recorded actual model IDs. A request to a model is labeled separately from
an observed response model. Execution success with REQUEST_CHANGES never appears
as approval. A released run lock without a terminal receipt appears interrupted.
The page polls every three seconds while visible. Files created by the existing
CLI are picked up without importing or migrating them.

Only fixed metadata fields and the two reports are exposed, never raw bundles,
stderr/stdout logs, settings or arbitrary files. Reports are rendered as text,
not executable HTML. Requests require an exact loopback Host, same-origin browser
context, and an API token; no CORS is enabled. The token protects against other
websites, not other processes already running as your OS user. Common credential
shapes are masked as a best effort; reports can still contain sensitive project
content. Do not expose this server through a reverse proxy.

Existing retention remains unchanged: successful executions up to 24 hours,
failed executions up to 7 days, at most 200 terminal runs / 200 MiB. REQUEST_CHANGES
can still be a successful execution and follows the 24-hour policy. Cleanup can
evict earlier; expired files cannot be recovered by the UI. Directory enumeration
is bounded at 2048 entries and shows a warning if the listing is partial.

The Chrome companion still manages short-lived wrapper task tabs. This history
page is independent and stays open until the user closes it; it does not start,
retry, cancel, or delete reviews.

## Tests

```sh
python3 -m unittest discover -s review-supervisor/tests -p 'test_*.py'
```

The installed command is documented by `~/.claude/docs/ccg-review-supervisor.md`.
That local document and review receipts are intentionally not package inputs.
