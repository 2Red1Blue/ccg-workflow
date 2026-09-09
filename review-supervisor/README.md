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
`ccg-agent-supervisor web-url` prints the fixed browser URL. Open
`http://127.0.0.1:19876/` directly in any local browser or tab; no access token,
login, or browser storage is required. The address stays the same across service
restarts. No browser is opened automatically.

For a persistent macOS service, `print-webui-launchd-plist` prints a user LaunchAgent
definition using the installed interpreter and command paths. Save it as
`~/Library/LaunchAgents/com.ccg.review-ui.plist` and load it with `launchctl bootstrap`
in your GUI user domain. It runs on login and restarts after failure, with no automatic
browser opening. Unload it with `launchctl bootout` before removing that plist.
Use `web-url` to check the address when using a custom port.

The page lists retained dual-review runs, project paths, execution states,
aggregate verdicts, individual reports/partial reports, retry origins, durations,
and recorded actual model IDs. A request to a model is labeled separately from
an observed response model. Execution success with REQUEST_CHANGES never appears
as approval. A released run lock without a terminal receipt appears interrupted.
The page polls every three seconds while visible. Files created by the existing
CLI are picked up without importing or migrating them.

The hero's TRACE / REVIEW / DECIDE controls filter the history; the four summary
figures are also filter buttons. Click the poster for focus mode, click a report's
button to collapse or expand it, press `/` to focus search, `r` to refresh, and
`Escape` to clear search. These controls change only the local view; they never
start, retry, approve, cancel, or mutate a review.

Only fixed metadata fields and the two reports are exposed, never raw bundles,
stderr/stdout logs, settings or arbitrary files. Reports are rendered as text,
not executable HTML. Requests require an exact loopback Host; foreign Origin and
cross-site/same-site Fetch Metadata requests are rejected, and no CORS is enabled.
There is intentionally no authentication between local users or processes: this
viewer is for a personal computer, not a shared or network-facing service. Common credential
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

## Durable analysis and canonical tasks

`ccg-task` is the packaged task router. The package source is
`ccg_task_router.py`; a workspace may retain a synchronized `task_router.py`
copy for existing Trellis consumers. Routing stays explicit: a selected root
with `.trellis/workflow.md` owns Trellis tasks, otherwise it owns CCG tasks.

```sh
ccg-task ensure --scope project --project-root "$PWD" \
  --title 'Canonical task lifecycle' --slug canonical-task-lifecycle
ccg-task write --task-dir '<returned taskDir>' \
  --document requirements --content-file requirements-input.md
ccg-task update --task-dir '<returned taskDir>' \
  --phase implementation --next-action 'Implement the accepted design'
ccg-task doctor --scope project --project-root "$PWD"
```

Keep the returned task identity and directory unchanged. `ensure` reuses only
the same selected root/provider, exact slug and exact title; a title mismatch
is an error. `--ccg-meta` supplies initial values only and is deliberately ignored
when reusing an existing task, so a resumed call cannot reset its current phase,
risk, branch or other metadata. Mutable orchestration fields are changed explicitly
through `update`. No whitespace/case normalization or fuzzy matching is applied.
`write` and `update`
validate an existing task and never create a guessed directory. `doctor` reports
orphan/incomplete records without deleting them. A timed-out or failed Trellis
create leaves a router-owned pending marker and requires explicit recovery;
retries cannot mislabel partially completed native creation as success. Different slugs may represent
different tasks; fuzzy name matching is deliberately not an identity policy.
Task mutations currently require POSIX flock (macOS/Linux); unsupported hosts
return `TASK_LOCK_UNSUPPORTED` instead of silently omitting concurrency protection.

One task can have multiple analysis/review runs. Use the existing supervisor
instead of shell fan-out and scanning `/tmp` for `claude.txt`:

```sh
ccg-agent-supervisor analyze --workdir "$PWD" \
  --task-dir '<returned taskDir>' \
  --context-file requirements-input.md --context-file src/relevant.py \
  < analysis-request.md
ccg-agent-supervisor status '<returned run_id>'
```

The current Harness selects bounded context and owns decisions. Each run records
its task association, both backend outcomes, actual reported models and report
paths. Reports use `Options`, `Recommendation`, `Risks`, `Validation`; analysis
has no approval verdict. Missing sections, partial output, timeout or a failed
backend cannot count as completed analysis. Raw context is removed at termination;
reports and receipts follow the existing bounded retention policy.

Retry using the identical request, context and task plus `--retry-run <run_id>`.
Only successful, intact reports from the same operation/identity can be reused.
Review and analysis results never substitute for one another. The history UI
labels completed analysis separately from review approval.

This design uses explicit identity rather than inferred similarity, following
[Amazon's idempotent API guidance](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/),
and distinguishes task intent from a run as in
[Temporal's Workflow ID and Run ID model](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/workflow/workflow-execution/workflowid-runid.mdx).
No new orchestration service is required.
