"""Minimal Coding Domain boundary for the Personal AI Ecosystem pilot.

CCG owns Coding target and review decisions.  This module deliberately holds no
database, retry state, or workflow state machine: it only validates and converts
immutable CCG facts at the boundaries owned by Personal Runtime, Agent Fabric,
and Workbench.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Iterable, Mapping, Protocol, Sequence


SCHEMA_VERSION = "ccg.coding-domain.v1"
ADMISSION_SCHEMA_VERSION = "ccg.coding-admission.v1"
OBSERVATION_SCHEMA_VERSION = "ccg.coding-observation.v1"
PROJECTION_SCHEMA_VERSION = "ccg.coding-projection.v1"


class ContractError(ValueError):
    """A boundary payload is malformed or would transfer ownership."""


def _nonblank(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ContractError(f"{label} must be a non-blank trimmed string")
    return value


def _digest(value: str, label: str) -> str:
    value = _nonblank(value, label)
    if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        raise ContractError(f"{label} must be a sha256 digest")
    try:
        int(value[len("sha256:"):], 16)
    except ValueError as error:
        raise ContractError(f"{label} must be a sha256 digest") from error
    return value


def _canonical_digest(parts: Sequence[str]) -> str:
    return "sha256:" + sha256("\0".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkerRef:
    """A stable CCG worker identity plus one concrete execution identity."""

    subject: str
    execution_ref: str

    def __post_init__(self) -> None:
        _nonblank(self.subject, "worker subject")
        _nonblank(self.execution_ref, "worker execution_ref")


@dataclass(frozen=True)
class ReviewerPolicy:
    """CCG-owned policy used to evaluate implementer/reviewer independence."""

    policy_id: str
    revision: str
    digest: str
    independence_class: str

    def __post_init__(self) -> None:
        _nonblank(self.policy_id, "reviewer policy_id")
        _nonblank(self.revision, "reviewer policy revision")
        _digest(self.digest, "reviewer policy digest")
        _nonblank(self.independence_class, "reviewer independence_class")


@dataclass(frozen=True)
class CodingTargetDecision:
    """An immutable CCG-owned decision; it intentionally contains no verdict."""

    target_id: str
    target_revision: str
    target_digest: str
    domain_decision_ref: str
    implementer: WorkerRef
    reviewer: WorkerRef
    reviewer_policy: ReviewerPolicy
    execution_target_ref: str

    def __post_init__(self) -> None:
        _nonblank(self.target_id, "target_id")
        _nonblank(self.target_revision, "target_revision")
        _digest(self.target_digest, "target_digest")
        _nonblank(self.domain_decision_ref, "domain_decision_ref")
        _nonblank(self.execution_target_ref, "execution_target_ref")
        if self.implementer.subject == self.reviewer.subject:
            raise ContractError("implementer and reviewer subjects must be independent")
        if self.implementer.execution_ref == self.reviewer.execution_ref:
            raise ContractError("implementer and reviewer executions must be independent")

    @property
    def authority(self) -> str:
        return "ccg"


@dataclass(frozen=True)
class ReviewIndependenceAttestation:
    """CCG-local evidence that a completed review used the frozen policy."""

    target_id: str
    target_revision: str
    target_digest: str
    reviewer: WorkerRef
    reviewer_policy: ReviewerPolicy

    def __post_init__(self) -> None:
        _nonblank(self.target_id, "attestation target_id")
        _nonblank(self.target_revision, "attestation target_revision")
        _digest(self.target_digest, "attestation target_digest")


def validate_review_independence(
    decision: CodingTargetDecision,
    attestation: ReviewIndependenceAttestation,
) -> None:
    """CCG, and only CCG, rechecks the actual reviewer against a frozen target."""

    if (
        attestation.target_id != decision.target_id
        or attestation.target_revision != decision.target_revision
        or attestation.target_digest != decision.target_digest
    ):
        raise ContractError("review attestation does not match the target decision")
    if attestation.reviewer_policy != decision.reviewer_policy:
        raise ContractError("review attestation does not use the frozen reviewer policy")
    if attestation.reviewer.subject == decision.implementer.subject:
        raise ContractError("actual reviewer subject must be independent from the implementer")
    if attestation.reviewer.execution_ref == decision.implementer.execution_ref:
        raise ContractError("actual reviewer execution must be independent from the implementer")


@dataclass(frozen=True)
class PersonalAdmissionContext:
    """PR-owned context references; CCG may echo them but cannot decide them."""

    delegation_id: str
    expected_delegation_version: int
    policy_revision_ref: str
    profile_revision_ref: str
    constraint_set_ref: str
    recovery_policy_ref: str

    def __post_init__(self) -> None:
        _nonblank(self.delegation_id, "delegation_id")
        if not isinstance(self.expected_delegation_version, int) or self.expected_delegation_version < 0:
            raise ContractError("expected_delegation_version must be a non-negative integer")
        for label, value in (
            ("policy_revision_ref", self.policy_revision_ref),
            ("profile_revision_ref", self.profile_revision_ref),
            ("constraint_set_ref", self.constraint_set_ref),
            ("recovery_policy_ref", self.recovery_policy_ref),
        ):
            _nonblank(value, label)


@dataclass(frozen=True)
class TargetAdmissionRequest:
    """The thin CCG -> Personal Runtime admission payload.

    The payload carries a target identity and personal-context *references* only.
    It has no CCG review report, verdict, or mutable task state.
    """

    request_id: str
    target: CodingTargetDecision
    personal: PersonalAdmissionContext

    def __post_init__(self) -> None:
        _nonblank(self.request_id, "admission request_id")

    def as_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": ADMISSION_SCHEMA_VERSION,
            "requestId": self.request_id,
            "target": {
                "targetId": self.target.target_id,
                "targetRevision": self.target.target_revision,
                "targetDigest": self.target.target_digest,
                "selectionAuthority": "ccg",
                "domainDecisionRef": self.target.domain_decision_ref,
                "executionTargetRef": self.target.execution_target_ref,
                "implementerRef": self.target.implementer.subject,
                "reviewerPolicyId": self.target.reviewer_policy.policy_id,
                "reviewerPolicyRevision": self.target.reviewer_policy.revision,
                "reviewerPolicyDigest": self.target.reviewer_policy.digest,
                "independenceClass": self.target.reviewer_policy.independence_class,
            },
            "personalContext": {
                "delegationId": self.personal.delegation_id,
                "expectedDelegationVersion": self.personal.expected_delegation_version,
                "policyRevisionRef": self.personal.policy_revision_ref,
                "profileRevisionRef": self.personal.profile_revision_ref,
                "constraintSetRef": self.personal.constraint_set_ref,
                "recoveryPolicyRef": self.personal.recovery_policy_ref,
            },
        }


@dataclass(frozen=True)
class AdmissionOutcome:
    """PR admission result; only PR can choose this outcome."""

    status: str
    delegation_ref: str | None = None
    outbox_command_id: str | None = None

    def __post_init__(self) -> None:
        allowed = {
            "ADMITTED",
            "REJECTED_BY_PERSONAL_CONSTRAINT",
            "INVALID_DELEGATION",
            "UNAVAILABLE",
            "OUTCOME_UNKNOWN",
            "IDEMPOTENCY_CONFLICT",
        }
        if self.status not in allowed:
            raise ContractError("admission status is unsupported")
        if self.status == "ADMITTED":
            _nonblank(self.delegation_ref or "", "admitted delegation_ref")
            _nonblank(self.outbox_command_id or "", "admitted outbox_command_id")
        elif self.delegation_ref is not None or self.outbox_command_id is not None:
            raise ContractError("non-admitted outcome must not claim a delegation or outbox command")


class PersonalRuntimeAdmissionPort(Protocol):
    def admit(self, request: TargetAdmissionRequest) -> AdmissionOutcome:
        """Persist or report a PR-owned admission outcome for one idempotency key."""


def admit_target(port: PersonalRuntimeAdmissionPort, request: TargetAdmissionRequest) -> AdmissionOutcome:
    """Call the external PR seam without interpreting an outcome as review truth."""

    outcome = port.admit(request)
    if not isinstance(outcome, AdmissionOutcome):
        raise ContractError("Personal Runtime admission port returned an invalid outcome")
    return outcome


@dataclass(frozen=True)
class FabricExecutionRequest:
    """An execution-only request pinned to exactly one CCG target revision."""

    delegation_ref: str
    outbox_command_id: str
    target_id: str
    target_revision: str
    target_digest: str
    execution_target_ref: str

    def __post_init__(self) -> None:
        for label, value in (
            ("delegation_ref", self.delegation_ref),
            ("outbox_command_id", self.outbox_command_id),
            ("target_id", self.target_id),
            ("target_revision", self.target_revision),
            ("execution_target_ref", self.execution_target_ref),
        ):
            _nonblank(value, label)
        _digest(self.target_digest, "fabric execution target_digest")


def execution_request_for(
    decision: CodingTargetDecision,
    admission: AdmissionOutcome,
) -> FabricExecutionRequest:
    """Bind Fabric to the admitted CCG revision; never choose a replacement revision."""

    if admission.status != "ADMITTED":
        raise ContractError("Fabric execution requires a recorded Personal Runtime admission")
    return FabricExecutionRequest(
        delegation_ref=admission.delegation_ref or "",
        outbox_command_id=admission.outbox_command_id or "",
        target_id=decision.target_id,
        target_revision=decision.target_revision,
        target_digest=decision.target_digest,
        execution_target_ref=decision.execution_target_ref,
    )


@dataclass(frozen=True)
class OwnerActionDescriptor:
    """An action advertised by its owner; Workbench only routes it."""

    action_type: str
    owner_ref: str
    expected_owner_revision: str
    fence_token: str
    payload_digest: str

    def __post_init__(self) -> None:
        for label, value in (
            ("action_type", self.action_type),
            ("owner_ref", self.owner_ref),
            ("expected_owner_revision", self.expected_owner_revision),
            ("fence_token", self.fence_token),
        ):
            _nonblank(value, label)
        _digest(self.payload_digest, "action payload_digest")


@dataclass(frozen=True)
class OwnerReceipt:
    """A Fabric/PR owner receipt, not a copy of CCG review evidence."""

    owner_ref: str
    receipt_id: str
    request_id: str
    target_id: str
    target_revision: str
    target_digest: str
    owner_sequence: int
    outcome: str
    occurred_at: str
    actions: tuple[OwnerActionDescriptor, ...] = ()
    deep_link: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for label, value in (
            ("owner_ref", self.owner_ref),
            ("receipt_id", self.receipt_id),
            ("request_id", self.request_id),
            ("target_id", self.target_id),
            ("target_revision", self.target_revision),
            ("outcome", self.outcome),
            ("occurred_at", self.occurred_at),
        ):
            _nonblank(value, label)
        _digest(self.target_digest, "owner receipt target_digest")
        if not isinstance(self.owner_sequence, int) or self.owner_sequence < 0:
            raise ContractError("owner_sequence must be a non-negative integer")
        if self.deep_link is not None:
            _nonblank(self.deep_link, "deep_link")
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError("owner receipt schema_version is unsupported")

    @property
    def digest(self) -> str:
        return _canonical_digest((
            self.owner_ref, self.receipt_id, self.request_id, self.target_id,
            self.target_revision, self.target_digest, str(self.owner_sequence),
            self.outcome, self.occurred_at, self.deep_link or "",
            *(
                ":".join((
                    action.action_type,
                    action.owner_ref,
                    action.expected_owner_revision,
                    action.fence_token,
                    action.payload_digest,
                ))
                for action in self.actions
            ),
        ))


@dataclass(frozen=True)
class SourceObservation:
    """Workbench's input, derived only from a durable owner receipt."""

    observation_id: str
    owner_receipt: OwnerReceipt
    source_digest: str

    @classmethod
    def from_owner_receipt(cls, receipt: OwnerReceipt) -> "SourceObservation":
        return cls(
            observation_id="ccg-observation:" + sha256(
                f"{receipt.owner_ref}\0{receipt.receipt_id}".encode("utf-8")
            ).hexdigest(),
            owner_receipt=receipt,
            source_digest=receipt.digest,
        )


@dataclass(frozen=True)
class WorkbenchProjection:
    """A rebuildable projection; it contains no local transition or review state."""

    projection_id: str
    source_observation_id: str
    owner_ref: str
    target_id: str
    target_revision: str
    owner_sequence: int
    owner_outcome: str
    actions: tuple[OwnerActionDescriptor, ...]
    deep_link: str | None


def project_observations(observations: Iterable[SourceObservation]) -> tuple[WorkbenchProjection, ...]:
    """Deterministically reduce owner observations without inventing owner state."""

    seen_receipts: dict[tuple[str, str], str] = {}
    sequences: dict[tuple[str, str], str] = {}
    current: dict[tuple[str, str], SourceObservation] = {}
    for observation in observations:
        receipt = observation.owner_receipt
        receipt_key = (receipt.owner_ref, receipt.receipt_id)
        prior = seen_receipts.get(receipt_key)
        if prior is not None:
            if prior != observation.source_digest:
                raise ContractError("owner receipt replay has a conflicting digest")
            continue
        seen_receipts[receipt_key] = observation.source_digest
        target_key = (receipt.owner_ref, receipt.target_id)
        sequence_key = (target_key[0], target_key[1], str(receipt.owner_sequence))
        sequence_prior = sequences.get(sequence_key)
        if sequence_prior is not None and sequence_prior != observation.source_digest:
            raise ContractError("owner sequence has a conflicting digest")
        sequences[sequence_key] = observation.source_digest
        existing = current.get(target_key)
        if existing is None or receipt.owner_sequence > existing.owner_receipt.owner_sequence:
            current[target_key] = observation

    projections = []
    for (owner_ref, target_id), observation in sorted(current.items()):
        receipt = observation.owner_receipt
        projections.append(WorkbenchProjection(
            projection_id="ccg-projection:" + sha256(
                f"{owner_ref}\0{target_id}".encode("utf-8")
            ).hexdigest(),
            source_observation_id=observation.observation_id,
            owner_ref=owner_ref,
            target_id=target_id,
            target_revision=receipt.target_revision,
            owner_sequence=receipt.owner_sequence,
            owner_outcome=receipt.outcome,
            actions=receipt.actions,
            deep_link=receipt.deep_link,
        ))
    return tuple(projections)


class DurableProjectionStore(Protocol):
    def read_projection(self, projection_id: str) -> WorkbenchProjection | None:
        """Read only a durable Workbench projection."""


class LiveDetailPort(Protocol):
    def inspect(self, deep_link: str) -> Mapping[str, object]:
        """Inspect a live owner surface; it must not persist or project data."""


@dataclass(frozen=True)
class LiveInspection:
    status: str
    detail: Mapping[str, object] | None = None


def read_durable_projection(store: DurableProjectionStore, projection_id: str) -> WorkbenchProjection | None:
    _nonblank(projection_id, "projection_id")
    return store.read_projection(projection_id)


def inspect_live_detail(port: LiveDetailPort, projection: WorkbenchProjection) -> LiveInspection:
    """Keep failed navigation as surface availability, never a projection mutation."""

    if projection.deep_link is None:
        return LiveInspection(status="unavailable")
    try:
        return LiveInspection(status="available", detail=port.inspect(projection.deep_link))
    except Exception:
        return LiveInspection(status="unavailable")
