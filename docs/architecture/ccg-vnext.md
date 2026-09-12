# CCG vNext architecture

Status: current design, 2026-09-13.

CCG owns engineering workflow semantics, not shared Harness transport.

Core responsibilities:
- classify task complexity/risk;
- choose engineering/review strategy;
- own file/worktree policy and acceptance gates;
- coordinate implementation and independent review roles;
- aggregate findings and decide targeted repair;
- preserve Hook-origin and API-origin events without recursive re-entry.

Shared Runtime responsibilities:
- start/status/cancel/result for external Harnesses;
- Operation/Assignment/Attempt identities;
- capability probing and dynamic Worker reassignment;
- process/workspace ownership and handoff.

Native provider/DSH paths may remain direct where they already have a real supported integration. Do not force every model call through ACP.

A CCG work item keeps stable business identity while implementer/reviewer Harnesses may change between Attempts. `completed` by a Worker never bypasses deterministic tests or ReviewService admission.
