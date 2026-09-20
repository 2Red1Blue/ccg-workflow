"""Minimal Coding Domain boundary for the Personal AI Ecosystem pilot.

CCG owns Coding target and review decisions.  This module deliberately holds no
database, retry state, or workflow state machine: it only validates and converts
immutable CCG facts at the Personal Runtime admission and Workbench observation
boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Protocol


SCHEMA_VERSION = "ccg.coding-domain.v1"
OBSERVATION_SCHEMA_VERSION = "ccg.coding-observation.v1"
PERSONAL_RUNTIME_ADMISSION_SCHEMA_VERSION = "personal-runtime.delegation-admission.v1"
PERSONAL_RUNTIME_ADMISSION_RECEIPT_SCHEMA_VERSION = (
    "personal-runtime.delegation-admission-receipt.v1"
)
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


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


def _canonical_json(value: object) -> str:
    """Match Personal Runtime canonical JSON for CCG's supported JSON subset.

    Personal Runtime currently publishes the digest algorithm but not a command
    builder.  CCG therefore accepts only integers that round-trip through a
    JavaScript ``number`` exactly and rejects floats instead of guessing at
    ECMAScript number formatting.  The exact-pin cross-repository qualification
    compares both command digests against Personal Runtime's implementation.
    """

    return _serialize_canonical(value, set())


def _serialize_canonical(value: object, ancestors: set[int]) -> str:
    if value is None or isinstance(value, bool):
        return json.dumps(value, separators=(",", ":"))
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ContractError("canonical JSON does not support surrogate code points")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ContractError("canonical JSON integer exceeds JavaScript safe range")
        return str(value)
    if isinstance(value, float):
        raise ContractError("canonical JSON floats require the Personal Runtime builder")
    if not isinstance(value, (dict, list, tuple)):
        raise ContractError(f"canonical JSON does not support {type(value).__name__}")

    identity = id(value)
    if identity in ancestors:
        raise ContractError("canonical JSON does not support cyclic data")
    ancestors.add(identity)
    try:
        if isinstance(value, (list, tuple)):
            return "[" + ",".join(_serialize_canonical(item, ancestors) for item in value) + "]"

        if any(not isinstance(key, str) for key in value):
            raise ContractError("canonical JSON object keys must be strings")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(
            _serialize_canonical(key, ancestors)
            + ":"
            + _serialize_canonical(value[key], ancestors)
            for key in keys
        ) + "}"
    finally:
        ancestors.remove(identity)


def _digest_json(value: object) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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
        if (
            not isinstance(self.expected_delegation_version, int)
            or isinstance(self.expected_delegation_version, bool)
            or self.expected_delegation_version < 0
            or self.expected_delegation_version > _MAX_SAFE_INTEGER
        ):
            raise ContractError("expected_delegation_version must be a non-negative safe integer")
        for label, value in (
            ("policy_revision_ref", self.policy_revision_ref),
            ("profile_revision_ref", self.profile_revision_ref),
            ("constraint_set_ref", self.constraint_set_ref),
            ("recovery_policy_ref", self.recovery_policy_ref),
        ):
            _nonblank(value, label)


@dataclass(frozen=True)
class TargetAdmissionRequest:
    """Inputs for one submission to Personal Runtime's admission command."""

    request_id: str
    target: CodingTargetDecision
    personal: PersonalAdmissionContext

    def __post_init__(self) -> None:
        _nonblank(self.request_id, "admission request_id")


@dataclass(frozen=True)
class PersonalRuntimeCommitMaterial:
    """Inputs required by PR's public command but not owned by a CCG review."""

    caller_identity: str
    issued_at: str
    resolved_target: Mapping[str, object]
    execution_input: Mapping[str, object]
    constraint_receipt_refs: tuple[str, ...]
    resolution_reason: str

    def __post_init__(self) -> None:
        for label, value in (
            ("caller_identity", self.caller_identity),
            ("issued_at", self.issued_at),
            ("resolution_reason", self.resolution_reason),
        ):
            _nonblank(value, label)
        if not self.resolved_target or not self.execution_input:
            raise ContractError("Personal Runtime commit material requires target and execution input")
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.constraint_receipt_refs):
            raise ContractError("constraint_receipt_refs must contain non-blank strings")


def to_delegation_commit(
    request: TargetAdmissionRequest,
    material: PersonalRuntimeCommitMaterial,
) -> dict[str, object]:
    """Produce PR's published ``delegation.commit`` envelope without private calls."""

    resolved_target = dict(material.resolved_target)
    payload = {
        "expectedDelegationVersion": request.personal.expected_delegation_version,
        "decision": {
            "delegationId": request.personal.delegation_id,
            "selectionAuthority": "ccg",
            "policyRevision": request.personal.policy_revision_ref,
            "profileRevisionRef": request.personal.profile_revision_ref,
            "constraintSetRef": request.personal.constraint_set_ref,
            "resolvedTarget": resolved_target,
            "targetDigest": _digest_json(resolved_target),
            "constraintReceiptRefs": list(material.constraint_receipt_refs),
            "resolutionReason": material.resolution_reason,
            "recoveryPolicyRef": request.personal.recovery_policy_ref,
            "domainDecisionRef": request.target.domain_decision_ref,
        },
        "execution": {"kind": "ensure", "value": dict(material.execution_input)},
    }
    return {
        "schemaVersion": PERSONAL_RUNTIME_ADMISSION_SCHEMA_VERSION,
        "commandId": request.request_id,
        "commandType": "delegation.commit",
        "payloadDigest": _digest_json(payload),
        "payload": payload,
        "callerIdentity": material.caller_identity,
        "expectedAggregateVersion": request.personal.expected_delegation_version,
        "issuedAt": material.issued_at,
    }


@dataclass(frozen=True)
class PersonalRuntimeAdmissionReceipt:
    """The exact durable receipt returned by Personal Runtime admission."""

    caller_id: str
    command_id: str
    payload_digest: str
    context_digest: str
    delegation_id: str
    delegation_revision: int
    delegation_version: int
    outbox_command_id: str
    recorded_at: str
    status: str = "RECORDED"
    schema_version: str = PERSONAL_RUNTIME_ADMISSION_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PERSONAL_RUNTIME_ADMISSION_RECEIPT_SCHEMA_VERSION:
            raise ContractError("admission receipt schema_version is unsupported")
        if self.status != "RECORDED":
            raise ContractError("admission receipt status must be RECORDED")
        for label, value in (
            ("receipt caller_id", self.caller_id),
            ("receipt command_id", self.command_id),
            ("receipt delegation_id", self.delegation_id),
            ("receipt outbox_command_id", self.outbox_command_id),
            ("receipt recorded_at", self.recorded_at),
        ):
            _nonblank(value, label)
        _digest(self.payload_digest, "receipt payload_digest")
        _digest(self.context_digest, "receipt context_digest")
        for label, value in (
            ("delegation_revision", self.delegation_revision),
            ("delegation_version", self.delegation_version),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > _MAX_SAFE_INTEGER
            ):
                raise ContractError(f"receipt {label} must be a positive safe integer")


@dataclass(frozen=True)
class AdmissionTransportOutcome:
    """Transport evidence when no durable PR receipt was obtained."""

    status: str

    def __post_init__(self) -> None:
        if self.status not in {"UNAVAILABLE", "OUTCOME_UNKNOWN"}:
            raise ContractError("admission transport status is unsupported")


def _parse_admission_receipt(
    value: Mapping[str, object],
    command: Mapping[str, object],
) -> PersonalRuntimeAdmissionReceipt:
    expected_keys = {
        "schemaVersion",
        "callerId",
        "commandId",
        "payloadDigest",
        "contextDigest",
        "status",
        "delegationId",
        "delegationRevision",
        "delegationVersion",
        "outboxCommandId",
        "recordedAt",
    }
    if set(value) != expected_keys:
        raise ContractError("Personal Runtime admission receipt fields are invalid")
    payload = command.get("payload")
    if not isinstance(payload, Mapping):
        raise ContractError("submitted admission command payload is invalid")
    decision = payload.get("decision")
    if not isinstance(decision, Mapping):
        raise ContractError("submitted admission decision is invalid")
    receipt = PersonalRuntimeAdmissionReceipt(
        schema_version=value.get("schemaVersion"),
        caller_id=value.get("callerId"),
        command_id=value.get("commandId"),
        payload_digest=value.get("payloadDigest"),
        context_digest=value.get("contextDigest"),
        status=value.get("status"),
        delegation_id=value.get("delegationId"),
        delegation_revision=value.get("delegationRevision"),
        delegation_version=value.get("delegationVersion"),
        outbox_command_id=value.get("outboxCommandId"),
        recorded_at=value.get("recordedAt"),
    )
    if (
        receipt.caller_id != command.get("callerIdentity")
        or receipt.command_id != command.get("commandId")
        or receipt.payload_digest != command.get("payloadDigest")
        or receipt.delegation_id != decision.get("delegationId")
    ):
        raise ContractError("Personal Runtime admission receipt does not match its command")
    return receipt


class PersonalRuntimeAdmissionPort(Protocol):
    def commit(
        self,
        command: Mapping[str, object],
    ) -> Mapping[str, object] | AdmissionTransportOutcome:
        """Consume only PR's published ``delegation.commit`` envelope."""


def admit_target(
    port: PersonalRuntimeAdmissionPort,
    request: TargetAdmissionRequest,
    material: PersonalRuntimeCommitMaterial,
) -> PersonalRuntimeAdmissionReceipt | AdmissionTransportOutcome:
    """Submit to PR and keep durable receipts distinct from transport evidence."""

    command = to_delegation_commit(request, material)
    result = port.commit(command)
    if isinstance(result, AdmissionTransportOutcome):
        return result
    if not isinstance(result, Mapping):
        raise ContractError("Personal Runtime admission port returned an invalid result")
    return _parse_admission_receipt(result, command)


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
        payload = {
            "schemaVersion": self.schema_version,
            "ownerRef": self.owner_ref,
            "receiptId": self.receipt_id,
            "requestId": self.request_id,
            "targetId": self.target_id,
            "targetRevision": self.target_revision,
            "targetDigest": self.target_digest,
            "ownerSequence": self.owner_sequence,
            "outcome": self.outcome,
            "occurredAt": self.occurred_at,
            "actions": [
                {
                    "actionType": action.action_type,
                    "ownerRef": action.owner_ref,
                    "expectedOwnerRevision": action.expected_owner_revision,
                    "fenceToken": action.fence_token,
                    "payloadDigest": action.payload_digest,
                }
                for action in self.actions
            ],
            **({"deepLink": self.deep_link} if self.deep_link is not None else {}),
        }
        return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceObservation:
    """A stable input DTO for the Workbench-owned observation/projection path."""

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

    def as_workbench_input(self) -> dict[str, object]:
        receipt = self.owner_receipt
        return {
            "schemaVersion": OBSERVATION_SCHEMA_VERSION,
            "observationId": self.observation_id,
            "sourceDigest": self.source_digest,
            "ownerReceipt": {
                "schemaVersion": receipt.schema_version,
                "ownerRef": receipt.owner_ref,
                "receiptId": receipt.receipt_id,
                "requestId": receipt.request_id,
                "targetId": receipt.target_id,
                "targetRevision": receipt.target_revision,
                "targetDigest": receipt.target_digest,
                "ownerSequence": receipt.owner_sequence,
                "outcome": receipt.outcome,
                "occurredAt": receipt.occurred_at,
                "actions": [
                    {
                        "actionType": action.action_type,
                        "ownerRef": action.owner_ref,
                        "expectedOwnerRevision": action.expected_owner_revision,
                        "fenceToken": action.fence_token,
                        "payloadDigest": action.payload_digest,
                    }
                    for action in receipt.actions
                ],
                **({"deepLinkMetadata": {"ownerRef": receipt.owner_ref, "href": receipt.deep_link}}
                   if receipt.deep_link is not None else {}),
            },
        }
