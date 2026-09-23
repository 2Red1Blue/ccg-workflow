# ADR 0001: Coding Domain / Personal Ecosystem minimal pilot

Status: accepted for the CCG pilot (2026-09-20).

## Decision

CCG is the sole authority for a Coding target decision and for evaluating
implementer/reviewer independence. A decision has an immutable CCG identity
`(targetId, targetRevision, targetDigest)`, a CCG `domainDecisionRef`, and a
CCG-owned reviewer-policy identity `(id, revision, digest)`. The implementer
and final reviewer must have different stable subjects and execution references.
CCG evaluates the predicate both when it freezes the target and when it accepts
the final reviewer attestation against that same policy revision. An attestation
must name the exact reviewer frozen in the decision; selecting a replacement
requires a new immutable decision identity rather than silently weakening the
old one. The CCG owner workflow allocates and persists monotonic target
revisions; this stateless boundary validates their contents but does not create
a second revision ledger.
The CCG `targetDigest` covers target ID/revision, implementer, reviewer, reviewer
policy, and execution target. `domainDecisionRef` is derived from that verified
digest. Replacing either worker, the policy, or the execution target therefore
requires a new digest and reference; changing both the decision and attestation
cannot preserve the old immutable identity. Reusing a revision label with a new
digest/reference is rejected while the CCG task's persisted history and
anti-reuse fence remain intact. `ccg-task allocate` serializes allocation under the
existing project-root flock, assigns the next `rN` within that canonical task,
requires one stable `targetId` throughout the task history, and accepts an
allocation ID only for exact replay. Trellis stores the records
under `meta.ccg.codingTargetDecisions`; fallback CCG tasks use
`ccg.codingTargetDecisions`. A last-revision/digest/reference head is kept in
task.json and a task-local anti-reuse sidecar. Allocation durably publishes the
sidecar before atomically replacing task.json; readers require both heads and
the full history to agree, so a failed second write or partial history edit
stays fail-closed. The sidecar stores no decision history and is not a second
revision ledger or a cross-task sequence. External deletion or rollback of
both task.json and its sidecar remains outside what this file authority can detect.
The other systems may retain `domainDecisionRef` but must not rerun the policy
or store a CCG review verdict.

Personal Runtime receives the published `delegation.commit` admission command.
It owns personal constraint admission, the Delegation record, its durable
`personal-runtime.delegation-admission-receipt.v1` receipt, and the Fabric
outbox command. A successful commit returns only `status="RECORDED"` together
with the exact command, context, Delegation revision/version, and outbox
identities. `UNAVAILABLE` and `OUTCOME_UNKNOWN` describe transport evidence when
no durable receipt was obtained; they are separate from the PR receipt and are
not CCG rejection or review results. The command excludes reports, verdicts,
task state, claims, evidence, and repair state.
CCG validates the recorded timestamp and recomputes the exact PR outbox command
ID from `(callerId, commandId)` before accepting a receipt; a merely non-empty
or syntactically plausible value is not durable admission evidence.

The CCG boundary submits only Personal Runtime's public `delegation.commit`
command. Its decision uses `selectionAuthority="ccg"` and the immutable
`domainDecisionRef`. CCG's Coding `targetDigest` and PR's
`decision.targetDigest` have different subjects: the former identifies the CCG
decision, while the latter is derived inside the CCG command builder from the
exact `resolvedTarget` using PR canonical JSON. Callers cannot supply or
override the PR digest. The boundary must not substitute a private PR store
call, invent another admission protocol, or create a Fabric execution request.

Personal Runtime parses and commits the command, then atomically creates exactly
one Fabric outbox command for the recorded Delegation revision. CCG does not
call Fabric. PR's outbox payload carries the exact resolved execution target and
its digest. Fabric may reject an ineligible or stale PR request, but it must not
pick a newer CCG target revision or decide that a CCG review passed. Any Coding
revision upgrade is a fresh CCG decision and a fresh admission command ID.

Workbench consumes the CCG-emitted source-observation input through this fixed
direction:

```text
owner receipt -> SourceObservation -> rebuildable Workbench projection
```

An owner receipt includes owner and receipt identities, request ID, target
identity, owner sequence, outcome, request digest, occurrence time, and any
owner-advertised action descriptors. The CCG source digest covers the complete
owner receipt, including the request digest and every action descriptor's owner
revision and fence token. For admission, `requestDigest` is the canonical digest
of the complete PR command envelope, while PR's `payloadDigest` continues to
identify only its payload. Workbench owns
receipt replay idempotency, ordering, conflict handling, the reducer, durable
reads, and projection persistence; CCG neither implements nor selects them.

Workbench's `read_durable_projection` and its owner-specific live inspect are
separate APIs. Live-inspect data is non-durable and cannot enter observations
or projections. A deep-link failure reports only `unavailable`; it does not
alter the target, receipt, observation, action, or projection.

## Consequences

- There is no Bridge database, cross-system FSM, or CCG review-truth replica.
- The DTO and mapper functions persist no state. The supported
  `ccg-coding-domain admit` consumer performs one authenticated request over
  Personal Runtime's private Unix admission socket; it owns no retry loop,
  database, scheduler, or command ledger. Personal Runtime owns admission and
  Fabric dispatch.
- The CCG mapper emits the published `delegation.commit` envelope and accepts
  only PR's exact recorded receipt or separate transport evidence. Personal
  Runtime still authenticates its CCG caller and validates its own context.
- Personal Runtime does not yet publish a command builder. The short-term CCG
  builder supports the exact integer JSON subset and rejects floats instead of
  guessing at ECMAScript number serialization. Its parity and service path are
  gated against clean exact pin
  `ce73b4fcb0d7f9b7f23284f44f58d06e1cee11f4`; the builder should move to a
  published Personal Runtime client seam when that seam exists.
- The review-supervisor installer deploys this module as a private support file
  and as the executable `ccg-coding-domain` consumer. The npm package exposes
  the same executable directly.

## Verification

`test_ccg_coding_domain.py` covers the independence predicate, absence of
review truth from admission, separate CCG and PR target digests, the exact PR
receipt, transport-result separation, absence of a CCG Fabric request, and the
owner-receipt to stable source-observation DTO (including action fence
identity). `test_ccg_task_router.py` covers task-local revision allocation,
concurrent uniqueness, exact replay, target identity stability and tamper refusal.
`scripts/qualify-personal-runtime-admission.py` requires the clean
exact PR pin, starts PR's public single-writer owner process, rejects a bad
credential, submits through CCG's packaged CLI and PR's authenticated socket,
then validates target/execution input, Delegation revision and aggregate state,
pending Fabric outbox state, payload/request/source digests, and the recorded
receipt. Coding Domain, task-router and review-supervisor tests plus the PR
qualification are CI and publish gates. Workbench
owns the cross-repository observation-to-projection and durable/live-inspect
tests.
