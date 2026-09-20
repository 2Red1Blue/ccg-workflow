# ADR 0001: Coding Domain / Personal Ecosystem minimal pilot

Status: accepted for the CCG pilot (2026-09-20).

## Decision

CCG is the sole authority for a Coding target decision and for evaluating
implementer/reviewer independence. A decision has an immutable target identity
`(targetId, targetRevision, targetDigest)`, a CCG `domainDecisionRef`, and a
CCG-owned reviewer-policy identity `(id, revision, digest)`. The implementer
and final reviewer must have different stable subjects and execution references.
CCG evaluates the predicate both when it freezes the target and when it accepts
the final reviewer attestation against that same policy revision. The other
systems may retain references but must not rerun the policy or store a CCG
review verdict.

Personal Runtime receives a narrow target-admission request. It owns personal
constraint admission, the Delegation record, its receipt, and any outbox
command. It returns one of `ADMITTED`, `REJECTED_BY_PERSONAL_CONSTRAINT`,
`INVALID_DELEGATION`, `UNAVAILABLE`, `OUTCOME_UNKNOWN`, or
`IDEMPOTENCY_CONFLICT`. `UNAVAILABLE` and `OUTCOME_UNKNOWN` are explicitly not
CCG rejection or review results. The request carries CCG target identity and
PR-owned context references only; it excludes reports, verdicts, task state,
claims, evidence, and repair state.

The CCG boundary maps this request to Personal Runtime's public
`delegation.commit` command: its decision uses
`selectionAuthority="ccg"` and the immutable `domainDecisionRef`. It must not
substitute a private PR store call or invent another admission protocol.

Agent Fabric receives a request pinned to the exact CCG target identity and
executes that request. It may reject an ineligible/stale exact reference, but
it must not pick a newer target revision or decide that a CCG review passed.
Any revision upgrade is a fresh CCG decision and a fresh request ID.

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
  persists no state; real ports remain owned and released by their respective
  repositories.
- The CCG mapper emits the published `delegation.commit` envelope. Personal
  Runtime still authenticates its CCG caller and validates its own context. The
  pilot does not claim that a live PR/Fabric/Workbench deployment exists.
- The review-supervisor installer deploys this module as a private support file
  alongside the supervisor so the installed package contains the same contract.

## Verification

`test_ccg_coding_domain.py` covers the independence predicate, absence of
review truth from admission, exact public PR command mapping, PR outcome
separation, exact Fabric target binding, and the owner-receipt to stable
source-observation DTO (including action fence identity). Workbench owns the
cross-repository observation-to-projection and durable/live-inspect tests.
