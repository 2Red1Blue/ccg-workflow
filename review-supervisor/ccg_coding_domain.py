#!/usr/bin/env python3
"""Minimal Coding Domain boundary for the Personal AI Ecosystem pilot.

CCG owns Coding target and review decisions.  This module deliberately holds no
database, retry state, or workflow state machine: it only validates and converts
immutable CCG facts at the Personal Runtime admission and Workbench observation
boundaries.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import socket
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

SCHEMA_VERSION = "ccg.coding-domain.v1"
OBSERVATION_SCHEMA_VERSION = "ccg.coding-observation.v1"
PERSONAL_RUNTIME_ADMISSION_SCHEMA_VERSION = "personal-runtime.delegation-admission.v1"
PERSONAL_RUNTIME_ADMISSION_RECEIPT_SCHEMA_VERSION = (
    "personal-runtime.delegation-admission-receipt.v1"
)
CODING_ADMISSION_REQUEST_SCHEMA_VERSION = "ccg.coding-admission-request.v1"
PERSONAL_RUNTIME_ADMISSION_ENDPOINT = "/v1/delegations:commit"
DEFAULT_ADMISSION_TIMEOUT_MS = 10_000
DEFAULT_ADMISSION_MAX_RESPONSE_BYTES = 1_048_576
_MAX_HTTP_HEADER_BYTES = 16_384
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_RFC3339_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)


class ContractError(ValueError):
    """A boundary payload is malformed or would transfer ownership."""


def _nonblank(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ContractError(f"{label} must be a non-blank trimmed string")
    return value


def _digest(value: str, label: str) -> str:
    value = _nonblank(value, label)
    if _SHA256_DIGEST.fullmatch(value) is None:
        raise ContractError(f"{label} must be a lowercase sha256 digest")
    return value


def _timestamp(value: str, label: str) -> str:
    value = _nonblank(value, label)
    if _RFC3339_TIMESTAMP.fullmatch(value) is None:
        raise ContractError(f"{label} must be an RFC 3339 timestamp with timezone")
    month = int(value[5:7])
    day = int(value[8:10])
    hour = int(value[11:13])
    minute = int(value[14:16])
    second = int(value[17:19])
    timezone_hour = 0 if value.endswith("Z") else int(value[-5:-3])
    timezone_minute = 0 if value.endswith("Z") else int(value[-2:])
    if (
        month < 1
        or month > 12
        or day < 1
        or hour > 23
        or minute > 59
        or second > 59
        or timezone_hour > 23
        or timezone_minute > 59
    ):
        raise ContractError(f"{label} must be a valid timestamp")
    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{label} must be a valid timestamp") from error
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


def coding_target_digest(
    target_id: str,
    target_revision: str,
    implementer: WorkerRef,
    reviewer: WorkerRef,
    reviewer_policy: ReviewerPolicy,
    execution_target_ref: str,
) -> str:
    """Digest every immutable CCG decision field, including reviewer identity."""

    return _digest_json({
        "targetId": _nonblank(target_id, "target_id"),
        "targetRevision": _nonblank(target_revision, "target_revision"),
        "implementer": {
            "subject": implementer.subject,
            "executionRef": implementer.execution_ref,
        },
        "reviewer": {
            "subject": reviewer.subject,
            "executionRef": reviewer.execution_ref,
        },
        "reviewerPolicy": {
            "id": reviewer_policy.policy_id,
            "revision": reviewer_policy.revision,
            "digest": reviewer_policy.digest,
            "independenceClass": reviewer_policy.independence_class,
        },
        "executionTargetRef": _nonblank(execution_target_ref, "execution_target_ref"),
    })


def coding_domain_decision_ref(target_digest: str) -> str:
    """Derive the opaque cross-system reference from the verified CCG digest."""

    digest = _digest(target_digest, "target_digest")
    return "ccg:decision:" + digest.removeprefix("sha256:")


def coding_admission_command_id(target_digest: str) -> str:
    """Derive the immutable admission command identity from the CCG decision.

    The command ID is a pure function of the verified target digest, so a retry
    of the same immutable decision resubmits the identical command ID while any
    changed worker, policy, or execution target yields a fresh one.  Personal
    Runtime still owns command dedup and the durable receipt.
    """

    digest = _digest(target_digest, "target_digest")
    return "ccg:admission:" + digest.removeprefix("sha256:")


def require_frozen_coding_decision(locator: Mapping[str, object], decision: "CodingTargetDecision") -> None:
    """Require every supplied decision fact to match a root-locked CCG allocation."""
    if set(locator) != {"root", "taskDir", "taskId"}:
        raise ContractError("frozenDecisionLocator requires root, taskDir, and taskId")
    root, task_dir, task_id = (locator.get(key) for key in ("root", "taskDir", "taskId"))
    for label, value in (("root", root), ("taskDir", task_dir), ("taskId", task_id)):
        _nonblank(value, f"frozenDecisionLocator.{label}")
    try:
        import importlib.util
        import sys

        router_path = Path(__file__).with_name("ccg_task_router.py")
        spec = importlib.util.spec_from_file_location("ccg_task_router_admission", router_path)
        if spec is None or spec.loader is None:
            raise ContractError("CCG task router is unavailable")
        router = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = router
        spec.loader.exec_module(router)
        result = router.coding_decision(root, task_dir, target_revision=decision.target_revision)
        record = result["decision"]
        expected = {
            "targetId": decision.target_id,
            "targetRevision": decision.target_revision,
            "targetDigest": decision.target_digest,
            "domainDecisionRef": decision.domain_decision_ref,
            "implementer": {"subject": decision.implementer.subject, "executionRef": decision.implementer.execution_ref},
            "reviewer": {"subject": decision.reviewer.subject, "executionRef": decision.reviewer.execution_ref},
            "reviewerPolicy": {"id": decision.reviewer_policy.policy_id, "revision": decision.reviewer_policy.revision,
                               "digest": decision.reviewer_policy.digest,
                               "independenceClass": decision.reviewer_policy.independence_class},
            "executionTargetRef": decision.execution_target_ref,
        }
        canonical_task_dir = str(Path(task_dir).resolve())
        if (result.get("taskDir") != canonical_task_dir or result.get("taskId") != task_id
                or Path(canonical_task_dir).name != task_id):
            raise ContractError("frozen decision locator does not identify its canonical task")
        if any(record.get(key) != value for key, value in expected.items()):
            raise ContractError("coding decision does not match the frozen CCG allocation")
    except ContractError:
        raise
    except Exception as error:
        raise ContractError(f"frozen CCG decision lookup failed: {error}") from error


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
        expected_digest = coding_target_digest(
            self.target_id,
            self.target_revision,
            self.implementer,
            self.reviewer,
            self.reviewer_policy,
            self.execution_target_ref,
        )
        if self.target_digest != expected_digest:
            raise ContractError("target_digest does not bind the immutable CCG decision")
        if self.domain_decision_ref != coding_domain_decision_ref(expected_digest):
            raise ContractError("domain_decision_ref does not bind target_digest")

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
    if attestation.reviewer != decision.reviewer:
        raise ContractError("review attestation does not use the reviewer frozen by the target decision")
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


def build_coding_admission_request(
    *,
    request_id: str,
    target_id: str,
    target_revision: str,
    implementer: WorkerRef,
    reviewer: WorkerRef,
    reviewer_policy: ReviewerPolicy,
    execution_target_ref: str,
    personal: PersonalAdmissionContext,
    material: PersonalRuntimeCommitMaterial,
    frozen_decision_locator: Mapping[str, object],
    deep_link: str | None = None,
) -> dict[str, object]:
    """Build one wire request from CCG facts and independent leaf receipts.

    The caller supplies only the stable target/policy and Personal Runtime
    context.  This function computes the CCG target digest and binds the same
    reviewer identity into the attestation; it never accepts caller-supplied
    target or decision digests and never constructs a Fabric request.
    """

    target_digest = coding_target_digest(
        target_id,
        target_revision,
        implementer,
        reviewer,
        reviewer_policy,
        execution_target_ref,
    )
    request: dict[str, object] = {
        "schemaVersion": CODING_ADMISSION_REQUEST_SCHEMA_VERSION,
        "frozenDecisionLocator": dict(frozen_decision_locator),
        "requestId": _nonblank(request_id, "admission request_id"),
        "target": {
            "targetId": target_id,
            "targetRevision": target_revision,
            "targetDigest": target_digest,
            "domainDecisionRef": coding_domain_decision_ref(target_digest),
            "implementer": {
                "subject": implementer.subject,
                "executionRef": implementer.execution_ref,
            },
            "reviewer": {
                "subject": reviewer.subject,
                "executionRef": reviewer.execution_ref,
            },
            "reviewerPolicy": {
                "id": reviewer_policy.policy_id,
                "revision": reviewer_policy.revision,
                "digest": reviewer_policy.digest,
                "independenceClass": reviewer_policy.independence_class,
            },
            "executionTargetRef": execution_target_ref,
        },
        "reviewAttestation": {
            "targetId": target_id,
            "targetRevision": target_revision,
            "targetDigest": target_digest,
            "reviewer": {
                "subject": reviewer.subject,
                "executionRef": reviewer.execution_ref,
            },
            "reviewerPolicy": {
                "id": reviewer_policy.policy_id,
                "revision": reviewer_policy.revision,
                "digest": reviewer_policy.digest,
                "independenceClass": reviewer_policy.independence_class,
            },
        },
        "personal": {
            "delegationId": personal.delegation_id,
            "expectedDelegationVersion": personal.expected_delegation_version,
            "policyRevisionRef": personal.policy_revision_ref,
            "profileRevisionRef": personal.profile_revision_ref,
            "constraintSetRef": personal.constraint_set_ref,
            "recoveryPolicyRef": personal.recovery_policy_ref,
        },
        "commitMaterial": {
            "callerIdentity": material.caller_identity,
            "issuedAt": material.issued_at,
            "resolvedTarget": dict(material.resolved_target),
            "executionInput": dict(material.execution_input),
            "constraintReceiptRefs": list(material.constraint_receipt_refs),
            "resolutionReason": material.resolution_reason,
        },
    }
    if deep_link is not None:
        request["deepLink"] = _nonblank(deep_link, "deepLink")
    return request


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
        expected_outbox_id = personal_runtime_outbox_command_id(
            self.caller_id,
            self.command_id,
        )
        if self.outbox_command_id != expected_outbox_id:
            raise ContractError("receipt outbox_command_id does not match its command identity")
        _timestamp(self.recorded_at, "receipt recorded_at")
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


def personal_runtime_outbox_command_id(caller_id: str, command_id: str) -> str:
    """Derive the exact Personal Runtime outbox identity for one admission command."""

    digest = _digest_json({
        "callerId": _nonblank(caller_id, "receipt caller_id"),
        "commandId": _nonblank(command_id, "receipt command_id"),
    })
    return "delegation-admission:" + digest.removeprefix("sha256:")


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


class PersonalRuntimeAdmissionSocketPort:
    """One-shot client for Personal Runtime's authenticated admission socket.

    The client never retries.  A failure before any request byte is written is
    ``UNAVAILABLE``; a failure after the first byte is written is
    ``OUTCOME_UNKNOWN`` because Personal Runtime may already have committed the
    immutable command.  A complete HTTP error response is a definite rejection,
    not transport uncertainty.
    """

    def __init__(
        self,
        socket_path: str,
        token: str,
        caller_id: str,
        *,
        timeout_ms: int = DEFAULT_ADMISSION_TIMEOUT_MS,
        max_response_bytes: int = DEFAULT_ADMISSION_MAX_RESPONSE_BYTES,
    ) -> None:
        path = Path(socket_path)
        if not path.is_absolute():
            raise ContractError("Personal Runtime admission socket path must be absolute")
        if not isinstance(token, str) or not token or any(
            ord(character) < 0x21 or ord(character) > 0x7E for character in token
        ):
            raise ContractError("Personal Runtime admission token must be one opaque ASCII token")
        _nonblank(caller_id, "Personal Runtime admission caller_id")
        for value, label in (
            (timeout_ms, "admission timeout_ms"),
            (max_response_bytes, "admission max_response_bytes"),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > _MAX_SAFE_INTEGER
            ):
                raise ContractError(f"{label} must be a positive safe integer")
        self._socket_path = str(path)
        self._token = token
        self._caller_id = caller_id
        self._timeout_seconds = timeout_ms / 1000
        self._max_response_bytes = max_response_bytes

    def commit(
        self,
        command: Mapping[str, object],
    ) -> Mapping[str, object] | AdmissionTransportOutcome:
        if command.get("callerIdentity") != self._caller_id:
            raise ContractError(
                "admission command callerIdentity does not match the configured caller"
            )
        security = _check_private_admission_socket(self._socket_path)
        if security == "missing":
            return AdmissionTransportOutcome("UNAVAILABLE")

        body = _canonical_json(dict(command)).encode("utf-8")
        frame = (
            f"POST {PERSONAL_RUNTIME_ADMISSION_ENDPOINT} HTTP/1.1\r\n"
            "Host: personal-runtime\r\n"
            "Content-Type: application/json\r\n"
            f"Authorization: Bearer {self._token}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body
        wrote_request = False
        response = bytearray()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self._timeout_seconds)
        try:
            try:
                connection.connect(self._socket_path)
            except OSError:
                return AdmissionTransportOutcome("UNAVAILABLE")
            view = memoryview(frame)
            while view:
                try:
                    written = connection.send(view)
                except OSError:
                    return AdmissionTransportOutcome(
                        "OUTCOME_UNKNOWN" if wrote_request else "UNAVAILABLE"
                    )
                if written <= 0:
                    return AdmissionTransportOutcome(
                        "OUTCOME_UNKNOWN" if wrote_request else "UNAVAILABLE"
                    )
                wrote_request = True
                view = view[written:]
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                return AdmissionTransportOutcome("OUTCOME_UNKNOWN")
            try:
                while True:
                    chunk = connection.recv(65_536)
                    if not chunk:
                        break
                    response.extend(chunk)
                    if len(response) > self._max_response_bytes:
                        return AdmissionTransportOutcome("OUTCOME_UNKNOWN")
            except OSError:
                return AdmissionTransportOutcome("OUTCOME_UNKNOWN")
        finally:
            connection.close()

        parsed = _parse_admission_http_response(bytes(response), self._max_response_bytes)
        if parsed is None:
            return AdmissionTransportOutcome("OUTCOME_UNKNOWN")
        status, value = parsed
        if status == 200:
            if not isinstance(value, Mapping):
                return AdmissionTransportOutcome("OUTCOME_UNKNOWN")
            try:
                _parse_admission_receipt(value, command)
            except ContractError:
                return AdmissionTransportOutcome("OUTCOME_UNKNOWN")
            return value
        if status == 401:
            raise ContractError("Personal Runtime admission authentication failed")
        raise ContractError(f"Personal Runtime admission was rejected with HTTP {status}")


def _check_private_admission_socket(socket_path: str) -> str:
    """Return ``present``/``missing`` and reject unsafe endpoint ownership."""

    if not hasattr(os, "getuid"):
        raise ContractError("Personal Runtime admission Unix sockets are unavailable")
    try:
        parent = os.lstat(str(Path(socket_path).parent))
        endpoint = os.lstat(socket_path)
    except FileNotFoundError:
        return "missing"
    uid = os.getuid()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != uid
        or parent.st_mode & 0o077
        or not stat.S_ISSOCK(endpoint.st_mode)
        or endpoint.st_uid != uid
        or endpoint.st_mode & 0o077
    ):
        raise ContractError(
            "Personal Runtime admission socket and parent must be private to the current user"
        )
    return "present"


def _parse_admission_http_response(
    raw: bytes,
    max_response_bytes: int,
) -> tuple[int, object] | None:
    if not raw or len(raw) > max_response_bytes:
        return None
    marker = raw.find(b"\r\n\r\n")
    if marker < 0 or marker > _MAX_HTTP_HEADER_BYTES:
        return None
    try:
        lines = raw[:marker].decode("latin1").split("\r\n")
        match = re.fullmatch(r"HTTP/1\.[01] ([0-9]{3}) .+", lines[0])
        if match is None:
            return None
        status = int(match.group(1))
        headers: dict[str, list[str]] = {}
        for line in lines[1:]:
            if ":" not in line:
                return None
            name, value = line.split(":", 1)
            headers.setdefault(name.strip().lower(), []).append(value.strip())
        if "transfer-encoding" in headers:
            return None
        lengths = headers.get("content-length")
        if lengths is None or len(lengths) != 1 or not lengths[0].isdigit():
            return None
        content_types = headers.get("content-type")
        if content_types is None or len(content_types) != 1:
            return None
        if content_types[0].split(";", 1)[0].strip().lower() != "application/json":
            return None
        body = raw[marker + 4:]
        if len(body) != int(lengths[0]):
            return None
        return status, json.loads(body.decode("utf-8", errors="strict"))
    except (IndexError, UnicodeError, ValueError):
        return None


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
    request_digest: str
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
        _digest(self.request_digest, "owner receipt request_digest")
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
            "requestDigest": self.request_digest,
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

    def __post_init__(self) -> None:
        _nonblank(self.observation_id, "observation_id")
        _digest(self.source_digest, "source_digest")
        if self.source_digest != self.owner_receipt.digest:
            raise ContractError("source_digest does not match the complete owner receipt")

    @classmethod
    def from_owner_receipt(cls, receipt: OwnerReceipt) -> SourceObservation:
        return cls(
            observation_id="ccg-observation:" + sha256(
                f"{receipt.owner_ref}\0{receipt.receipt_id}".encode()
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
                "requestDigest": receipt.request_digest,
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


def execute_coding_admission(
    port: PersonalRuntimeAdmissionPort,
    raw_request: Mapping[str, object],
) -> dict[str, object]:
    """Run the supported CCG decision-to-observation product path."""

    _exact_keys(raw_request, {
        "schemaVersion",
        "requestId",
        "target",
        "reviewAttestation",
        "personal",
        "commitMaterial",
        "frozenDecisionLocator",
    }, "coding admission request", optional={"deepLink"})
    if raw_request.get("schemaVersion") != CODING_ADMISSION_REQUEST_SCHEMA_VERSION:
        raise ContractError("coding admission request schemaVersion is unsupported")

    target_value = _record(raw_request.get("target"), "target")
    _exact_keys(target_value, {
        "targetId", "targetRevision", "targetDigest", "domainDecisionRef",
        "implementer", "reviewer", "reviewerPolicy", "executionTargetRef",
    }, "target")
    decision = CodingTargetDecision(
        target_id=target_value.get("targetId"),
        target_revision=target_value.get("targetRevision"),
        target_digest=target_value.get("targetDigest"),
        domain_decision_ref=target_value.get("domainDecisionRef"),
        implementer=_parse_worker(target_value.get("implementer"), "target implementer"),
        reviewer=_parse_worker(target_value.get("reviewer"), "target reviewer"),
        reviewer_policy=_parse_reviewer_policy(
            target_value.get("reviewerPolicy"), "target reviewerPolicy"
        ),
        execution_target_ref=target_value.get("executionTargetRef"),
    )
    locator = _record(raw_request.get("frozenDecisionLocator"), "frozenDecisionLocator")
    require_frozen_coding_decision(locator, decision)

    attestation_value = _record(raw_request.get("reviewAttestation"), "reviewAttestation")
    _exact_keys(attestation_value, {
        "targetId", "targetRevision", "targetDigest", "reviewer", "reviewerPolicy",
    }, "reviewAttestation")
    attestation = ReviewIndependenceAttestation(
        target_id=attestation_value.get("targetId"),
        target_revision=attestation_value.get("targetRevision"),
        target_digest=attestation_value.get("targetDigest"),
        reviewer=_parse_worker(attestation_value.get("reviewer"), "attestation reviewer"),
        reviewer_policy=_parse_reviewer_policy(
            attestation_value.get("reviewerPolicy"), "attestation reviewerPolicy"
        ),
    )
    validate_review_independence(decision, attestation)

    personal_value = _record(raw_request.get("personal"), "personal")
    _exact_keys(personal_value, {
        "delegationId", "expectedDelegationVersion", "policyRevisionRef",
        "profileRevisionRef", "constraintSetRef", "recoveryPolicyRef",
    }, "personal")
    personal = PersonalAdmissionContext(
        delegation_id=personal_value.get("delegationId"),
        expected_delegation_version=personal_value.get("expectedDelegationVersion"),
        policy_revision_ref=personal_value.get("policyRevisionRef"),
        profile_revision_ref=personal_value.get("profileRevisionRef"),
        constraint_set_ref=personal_value.get("constraintSetRef"),
        recovery_policy_ref=personal_value.get("recoveryPolicyRef"),
    )
    request = TargetAdmissionRequest(raw_request.get("requestId"), decision, personal)

    material_value = _record(raw_request.get("commitMaterial"), "commitMaterial")
    _exact_keys(material_value, {
        "callerIdentity", "issuedAt", "resolvedTarget", "executionInput",
        "constraintReceiptRefs", "resolutionReason",
    }, "commitMaterial")
    refs = material_value.get("constraintReceiptRefs")
    if not isinstance(refs, list):
        raise ContractError("commitMaterial constraintReceiptRefs must be an array")
    material = PersonalRuntimeCommitMaterial(
        caller_identity=material_value.get("callerIdentity"),
        issued_at=material_value.get("issuedAt"),
        resolved_target=_record(material_value.get("resolvedTarget"), "resolvedTarget"),
        execution_input=_record(material_value.get("executionInput"), "executionInput"),
        constraint_receipt_refs=tuple(refs),
        resolution_reason=material_value.get("resolutionReason"),
    )
    command = to_delegation_commit(request, material)
    request_digest = _digest_json(command)
    result = admit_target(port, request, material)
    if isinstance(result, AdmissionTransportOutcome):
        return {
            "schemaVersion": "ccg.coding-admission-result.v1",
            "status": result.status,
            "requestDigest": request_digest,
        }

    deep_link = raw_request.get("deepLink")
    if deep_link is not None:
        _nonblank(deep_link, "deepLink")
    receipt_identity = sha256(
        f"{result.caller_id}\0{result.command_id}".encode()
    ).hexdigest()
    owner_receipt = OwnerReceipt(
        owner_ref=f"personal-runtime:delegation:{result.delegation_id}",
        receipt_id=f"personal-runtime-admission-receipt:{receipt_identity}",
        request_id=request.request_id,
        request_digest=request_digest,
        target_id=decision.target_id,
        target_revision=decision.target_revision,
        target_digest=decision.target_digest,
        owner_sequence=result.delegation_version,
        outcome=result.status,
        occurred_at=result.recorded_at,
        deep_link=deep_link,
    )
    observation = SourceObservation.from_owner_receipt(owner_receipt)
    return {
        "schemaVersion": "ccg.coding-admission-result.v1",
        "status": result.status,
        "requestDigest": request_digest,
        "admissionReceipt": _receipt_as_wire(result),
        "sourceObservation": observation.as_workbench_input(),
    }


def _record(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    return value


def _exact_keys(
    value: Mapping[str, object],
    required: set[str],
    label: str,
    *,
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise ContractError(f"{label} fields are invalid")


def _parse_worker(value: object, label: str) -> WorkerRef:
    record = _record(value, label)
    _exact_keys(record, {"subject", "executionRef"}, label)
    return WorkerRef(record.get("subject"), record.get("executionRef"))


def _parse_reviewer_policy(value: object, label: str) -> ReviewerPolicy:
    record = _record(value, label)
    _exact_keys(record, {"id", "revision", "digest", "independenceClass"}, label)
    return ReviewerPolicy(
        record.get("id"),
        record.get("revision"),
        record.get("digest"),
        record.get("independenceClass"),
    )


def _receipt_as_wire(receipt: PersonalRuntimeAdmissionReceipt) -> dict[str, object]:
    return {
        "schemaVersion": receipt.schema_version,
        "callerId": receipt.caller_id,
        "commandId": receipt.command_id,
        "payloadDigest": receipt.payload_digest,
        "contextDigest": receipt.context_digest,
        "status": receipt.status,
        "delegationId": receipt.delegation_id,
        "delegationRevision": receipt.delegation_revision,
        "delegationVersion": receipt.delegation_version,
        "outboxCommandId": receipt.outbox_command_id,
        "recordedAt": receipt.recorded_at,
    }


def _read_cli_request(path: str) -> Mapping[str, object]:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        source = Path(path)
        if not source.is_file() or source.is_symlink():
            raise ContractError("coding admission input must be a regular non-symlink file")
        value = json.loads(source.read_text(encoding="utf-8"))
    return _record(value, "coding admission request")


def _parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    admit = subparsers.add_parser(
        "admit", help="validate a frozen Coding decision and submit it to Personal Runtime"
    )
    admit.add_argument("--socket", required=True, help="absolute Personal Runtime admission socket")
    admit.add_argument(
        "--token-env", required=True, help="environment variable holding the admission token"
    )
    admit.add_argument("--input", default="-", help="request JSON file, or - for stdin")
    admit.add_argument("--timeout-ms", type=int, default=DEFAULT_ADMISSION_TIMEOUT_MS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_cli_args(argv)
    try:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.token_env):
            raise ContractError("--token-env must name one environment variable")
        token = os.environ.get(args.token_env)
        if token is None:
            raise ContractError(f"admission token environment variable {args.token_env} is missing")
        raw = _read_cli_request(args.input)
        material = _record(raw.get("commitMaterial"), "commitMaterial")
        caller_id = material.get("callerIdentity")
        _nonblank(caller_id, "commitMaterial callerIdentity")
        port = PersonalRuntimeAdmissionSocketPort(
            args.socket,
            token,
            caller_id,
            timeout_ms=args.timeout_ms,
        )
        result = execute_coding_admission(port, raw)
        print(_canonical_json(result))
        return 0
    except (ContractError, OSError, ValueError) as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
