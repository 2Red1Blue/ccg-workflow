# ADR 0001: Coding Domain / Personal Ecosystem minimal pilot

Status: accepted for the CCG pilot (2026-09-20).

## Decision

CCG is the sole authority for a Coding target decision and for evaluating
implementer/reviewer independence. A decision has an immutable CCG identity
`(targetId, targetRevision, targetDigest)`, a CCG `domainDecisionRef`, and a
CCG-owned reviewer-policy identity `(id, revision, digest)`. The implementer
and final reviewer must have different stable subjects and execution references.
CCG evaluates the predicate both when it freezes the target and when it accepts
the final reviewer attestation against that same policy revision. The other
systems may retain `domainDecisionRef` but must not rerun the policy or store a
CCG review verdict.

Personal Runtime receives the published `delegation.commit` admission command.
It owns personal constraint admission, the Delegation record, its durable
`personal-runtime.delegation-admission-receipt.v1` receipt, and the Fabric
outbox command. A successful commit returns only `status="RECORDED"` together
with the exact command, context, Delegation revision/version, and outbox
identities. `UNAVAILABLE` and `OUTCOME_UNKNOWN` describe transport evidence when
no durable receipt was obtained; they are separate from the PR receipt and are
not CCG rejection or review results. The command excludes reports, verdicts,
task state, claims, evidence, and repair state.

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
action descriptor, including its owner revision and fence token. Workbench owns
receipt replay idempotency, ordering, conflict handling, the reducer, durable
reads, and projection persistence; CCG neither implements nor selects them.

Workbench's `read_durable_projection` and its owner-specific live inspect are
separate APIs. Live-inspect data is non-durable and cannot enter observations
or projections. A deep-link failure reports only `unavailable`; it does not
alter the target, receipt, observation, action, or projection.

## Consequences

- There is no Bridge database, cross-system FSM, or CCG review-truth replica.
- This module is a pure boundary adapter. It opens no network connection and
  persists no state; Personal Runtime owns admission and Fabric dispatch.
- The CCG mapper emits the published `delegation.commit` envelope and accepts
  only PR's exact recorded receipt or separate transport evidence. Personal
  Runtime still authenticates its CCG caller and validates its own context.
- Personal Runtime does not yet publish a command builder. The short-term CCG
  builder supports the exact integer JSON subset and rejects floats instead of
  guessing at ECMAScript number serialization. Its parity and service path are
  gated against clean exact pin
  `b92494181d63fe39f07afa8e168a951c33f73888`; the builder should move to a
  published Personal Runtime client seam when that seam exists.
- The review-supervisor installer deploys this module as a private support file
  alongside the supervisor so the installed package contains the same contract.

## Verification

`test_ccg_coding_domain.py` covers the independence predicate, absence of
review truth from admission, separate CCG and PR target digests, the exact PR
receipt, transport-result separation, absence of a CCG Fabric request, and the
owner-receipt to stable source-observation DTO (including action fence
identity). `scripts/qualify-personal-runtime-admission.py` requires the clean
exact PR pin, compares both CCG digests with PR canonical JSON, passes the
command through PR's real parser and service commit, validates the recorded
receipt, and asserts that exactly one Fabric outbox command exists. Workbench
owns the cross-repository observation-to-projection and durable/live-inspect
tests.
