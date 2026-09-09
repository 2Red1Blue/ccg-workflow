# Canonical task identity and durable dual analysis

## Scope

Keep current CCG/Trellis routing and supervisor runtime. Do not change Java business code, OpenSpace, model credentials, or model mappings. Existing Java workspace router changes must be preserved.

## Decisions

- Stable explicit task identity, not fuzzy title matching. `ensure` may reuse only matching identities and rejects conflicting requests; documents and phase updates use a returned existing task handle/path with no implicit mkdir.
- Task is the durable intent; analysis/review run is one execution attempt. Store task association in the run receipt, do not maintain another mutable task registry.
- Reuse the supervisor dual-leaf process runner for `analyze` with bounded supplied context and distinct analysis report validation. Reuse timeout, cancellation, partial results, safe retention and retry integrity.
- Keep review verdict semantics separate. Successful analysis is not review approval.
- Diagnose orphan task directories without silently deleting or auto-merging them. Preserve current active catalog task artifacts.

## Ownership

1. Router worker: Java workspace `.trellis/scripts/task_router.py` and `test_task_router.py` only.
2. Supervisor worker: ccg-workflow `review-supervisor/ccg-agent-supervisor.py` and its Python tests only.
3. Parent: package README/templates, installation and integration, task records, scoped review. Canonical router is shipped as ccg_task_router.py and reused by analyze task association validation; workspace task_router.py is a synchronized deployment copy. Host instructions receive an additive routing/analysis rule preserving user creative exemption.

## Verification

Router: concurrent same identity, conflicting ensure, path traversal/symlink/partial dir rejection, handle writes no mkdir, Trellis vs CCG ownership, doctor orphan report.
Supervisor: parallel leaves, analysis format, incomplete/error handling, retry cannot cross mode/task, existing review regression.
Use live dual analysis of this design through installed command and verify two terminal reports/status receipts. Run scoped final dual review.
