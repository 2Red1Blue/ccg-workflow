# CCG vNext documentation index

Status: current refactor baseline, 2026-09-13.

Read in order:
1. `architecture/ccg-vnext.md`
2. `architecture/review-service.md`
3. `architecture/agent-fabric-integration.md`
4. `architecture/dynamic-worker-policy.md`
5. `architecture/hook-api-dedup.md`
6. root README/CLAUDE only for current implementation details that do not conflict with vNext.

CCG remains the engineering workflow/review domain. It owns strategy, file ownership, quality gates and independent review semantics. Shared external Harness transport belongs to Agent Fabric/Harness Runtime.

Old external CLI wrappers and provider-specific launch logic are supporting/historical once the Runtime path is qualified; they must not grow into a second common Harness layer.
