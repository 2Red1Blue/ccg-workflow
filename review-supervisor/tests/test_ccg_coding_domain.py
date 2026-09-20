"""Contract tests for the CCG Coding Domain topology pilot."""

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ccg_coding_domain.py"
SPEC = importlib.util.spec_from_file_location("ccg_coding_domain", SCRIPT)
DOMAIN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DOMAIN
SPEC.loader.exec_module(DOMAIN)

DIGEST = "sha256:" + "a" * 64
POLICY_DIGEST = "sha256:" + "b" * 64
ACTION_DIGEST = "sha256:" + "c" * 64
CONTEXT_DIGEST = "sha256:" + "d" * 64
REQUEST_DIGEST = "sha256:" + "e" * 64


def target():
    implementer = DOMAIN.WorkerRef("ccg-implementer", "run:implement-1")
    reviewer = DOMAIN.WorkerRef("ccg-reviewer", "run:review-1")
    reviewer_policy = DOMAIN.ReviewerPolicy(
        "dual-leaf", "v2", POLICY_DIGEST, "separate-subject-and-run"
    )
    target_digest = DOMAIN.coding_target_digest(
        "coding:target-17",
        "r7",
        implementer,
        reviewer,
        reviewer_policy,
        "fabric-target:codebuddy:qualified-v1",
    )
    return DOMAIN.CodingTargetDecision(
        target_id="coding:target-17",
        target_revision="r7",
        target_digest=target_digest,
        domain_decision_ref=DOMAIN.coding_domain_decision_ref(target_digest),
        implementer=implementer,
        reviewer=reviewer,
        reviewer_policy=reviewer_policy,
        execution_target_ref="fabric-target:codebuddy:qualified-v1",
    )


def receipt(sequence=1, receipt_id="receipt-1", outcome="accepted", deep_link="codex://runs/receipt-1"):
    return DOMAIN.OwnerReceipt(
        owner_ref="agent-fabric:operation-17",
        receipt_id=receipt_id,
        request_id="request-17",
        request_digest=REQUEST_DIGEST,
        target_id="coding:target-17",
        target_revision="r7",
        target_digest=DIGEST,
        owner_sequence=sequence,
        outcome=outcome,
        occurred_at="2026-09-20T10:00:00Z",
        actions=(DOMAIN.OwnerActionDescriptor("owner.cancel", "agent-fabric:operation-17", "7", "attempt:7", ACTION_DIGEST),),
        deep_link=deep_link,
    )


def commit_material():
    return DOMAIN.PersonalRuntimeCommitMaterial(
        caller_identity="ccg:local-client",
        issued_at="2026-09-20T10:00:00Z",
        resolved_target={
            "profileRevisionRef": "profile:8",
            "harnessRef": "harness:personal",
            "backendRef": "backend:fabric",
        },
        execution_input={"input": {"artifactRef": "ccg:target-17"}, "workspaceRef": "workspace:17"},
        constraint_receipt_refs=("constraint-receipt:9",),
        resolution_reason="ccg target revision r7",
    )


class CodingDomainTest(unittest.TestCase):
    def test_target_requires_distinct_implementer_and_reviewer(self):
        with self.assertRaisesRegex(DOMAIN.ContractError, "subjects must be independent"):
            DOMAIN.CodingTargetDecision(
                target_id="coding:target-17", target_revision="r7", target_digest=DIGEST,
                domain_decision_ref="ccg:decision:target-17:r7",
                implementer=DOMAIN.WorkerRef("same", "run:implement-1"),
                reviewer=DOMAIN.WorkerRef("same", "run:review-1"),
                reviewer_policy=DOMAIN.ReviewerPolicy("dual-leaf", "v2", POLICY_DIGEST, "separate"),
                execution_target_ref="fabric-target:qualified-v1",
            )

    def test_ccg_submits_only_the_personal_runtime_admission_command(self):
        request = DOMAIN.TargetAdmissionRequest(
            "request-17",
            target(),
            DOMAIN.PersonalAdmissionContext("delegation-17", 3, "policy:4", "profile:8", "constraint:9", "recovery:2"),
        )
        command = DOMAIN.to_delegation_commit(request, commit_material())
        decision = command["payload"]["decision"]
        self.assertEqual("personal-runtime.delegation-admission.v1", command["schemaVersion"])
        self.assertEqual("ccg", decision["selectionAuthority"])
        self.assertEqual(target().domain_decision_ref, decision["domainDecisionRef"])
        self.assertNotEqual(DIGEST, decision["targetDigest"])
        self.assertFalse(hasattr(request, "as_target_reference"))
        self.assertNotIn("reviewerRef", decision)
        self.assertNotIn("verdict", decision)
        self.assertNotIn("review", decision)

    def test_admission_maps_to_the_public_personal_runtime_command(self):
        request = DOMAIN.TargetAdmissionRequest(
            "request-17", target(),
            DOMAIN.PersonalAdmissionContext("delegation-17", 3, "policy:4", "profile:8", "constraint:9", "recovery:2"),
        )
        command = DOMAIN.to_delegation_commit(request, commit_material())
        self.assertEqual("personal-runtime.delegation-admission.v1", command["schemaVersion"])
        self.assertEqual("delegation.commit", command["commandType"])
        self.assertEqual("ccg", command["payload"]["decision"]["selectionAuthority"])
        self.assertEqual(
            target().domain_decision_ref,
            command["payload"]["decision"]["domainDecisionRef"],
        )
        self.assertNotIn("reviewerPolicyId", command["payload"]["decision"])
        self.assertEqual(
            "sha256:0e1757ac0dc663d5f2fc40e2a4c3a956e35a7cd82c04b08797a2ef46ac2338fa",
            command["payloadDigest"],
        )
        self.assertEqual(
            "sha256:b8e1730f83df3a35d88e59d6860b2498dd806171ebeb5d0a8ebaf23bdc02f7dd",
            command["payload"]["decision"]["targetDigest"],
        )

    def test_ccg_rechecks_completed_reviewer_under_the_frozen_policy(self):
        decision = target()
        DOMAIN.validate_review_independence(
            decision,
            DOMAIN.ReviewIndependenceAttestation(
                decision.target_id, decision.target_revision, decision.target_digest,
                decision.reviewer, decision.reviewer_policy,
            ),
        )
        with self.assertRaisesRegex(DOMAIN.ContractError, "reviewer frozen"):
            DOMAIN.validate_review_independence(
                decision,
                DOMAIN.ReviewIndependenceAttestation(
                    decision.target_id, decision.target_revision, decision.target_digest,
                    DOMAIN.WorkerRef("another-reviewer", "run:review-final"), decision.reviewer_policy,
                ),
            )

    def test_recorded_receipt_uses_the_exact_personal_runtime_contract(self):
        request = DOMAIN.TargetAdmissionRequest(
            "request-17", target(),
            DOMAIN.PersonalAdmissionContext("delegation-17", 3, "policy:4", "profile:8", "constraint:9", "recovery:2"),
        )
        command = DOMAIN.to_delegation_commit(request, commit_material())

        class Port:
            def commit(self, submitted):
                self.submitted = submitted
                return {
                    "schemaVersion": "personal-runtime.delegation-admission-receipt.v1",
                    "callerId": "ccg:local-client",
                    "commandId": "request-17",
                    "payloadDigest": submitted["payloadDigest"],
                    "contextDigest": CONTEXT_DIGEST,
                    "status": "RECORDED",
                    "delegationId": "delegation-17",
                    "delegationRevision": 4,
                    "delegationVersion": 5,
                    "outboxCommandId": DOMAIN.personal_runtime_outbox_command_id(
                        "ccg:local-client", "request-17"
                    ),
                    "recordedAt": "2026-09-20T10:00:01Z",
                }

        port = Port()
        receipt = DOMAIN.admit_target(port, request, commit_material())
        self.assertEqual(command, port.submitted)
        self.assertEqual("RECORDED", receipt.status)
        self.assertEqual("personal-runtime.delegation-admission-receipt.v1", receipt.schema_version)
        self.assertEqual(
            DOMAIN.personal_runtime_outbox_command_id("ccg:local-client", "request-17"),
            receipt.outbox_command_id,
        )

    def test_recorded_receipt_rejects_impossible_outbox_id_and_timestamp(self):
        request = DOMAIN.TargetAdmissionRequest(
            "request-17", target(),
            DOMAIN.PersonalAdmissionContext(
                "delegation-17", 3, "policy:4", "profile:8", "constraint:9", "recovery:2"
            ),
        )

        class Port:
            def __init__(self, outbox_command_id, recorded_at):
                self.outbox_command_id = outbox_command_id
                self.recorded_at = recorded_at

            def commit(self, submitted):
                return {
                    "schemaVersion": "personal-runtime.delegation-admission-receipt.v1",
                    "callerId": "ccg:local-client",
                    "commandId": "request-17",
                    "payloadDigest": submitted["payloadDigest"],
                    "contextDigest": CONTEXT_DIGEST,
                    "status": "RECORDED",
                    "delegationId": "delegation-17",
                    "delegationRevision": 4,
                    "delegationVersion": 5,
                    "outboxCommandId": self.outbox_command_id,
                    "recordedAt": self.recorded_at,
                }

        with self.assertRaisesRegex(DOMAIN.ContractError, "outbox_command_id"):
            DOMAIN.admit_target(
                Port("delegation-admission:17", "2026-09-20T10:00:01Z"),
                request,
                commit_material(),
            )
        for recorded_at in (
            "2026-02-30T10:00:01Z",
            "2026-01-01T24:00:00Z",
            "2026-01-01T00:00:00+01:60",
        ):
            with (
                self.subTest(recorded_at=recorded_at),
                self.assertRaisesRegex(DOMAIN.ContractError, "timestamp"),
            ):
                DOMAIN.admit_target(
                    Port(
                        DOMAIN.personal_runtime_outbox_command_id(
                            "ccg:local-client", "request-17"
                        ),
                        recorded_at,
                    ),
                    request,
                    commit_material(),
                )

    def test_transport_outcomes_are_not_personal_runtime_receipts(self):
        request = DOMAIN.TargetAdmissionRequest(
            "request-17", target(),
            DOMAIN.PersonalAdmissionContext("delegation-17", 3, "policy:4", "profile:8", "constraint:9", "recovery:2"),
        )

        class Port:
            def commit(self, _submitted):
                return DOMAIN.AdmissionTransportOutcome("OUTCOME_UNKNOWN")

        result = DOMAIN.admit_target(Port(), request, commit_material())
        self.assertIsInstance(result, DOMAIN.AdmissionTransportOutcome)
        self.assertEqual("OUTCOME_UNKNOWN", result.status)
        with self.assertRaisesRegex(DOMAIN.ContractError, "transport status"):
            DOMAIN.AdmissionTransportOutcome("RECORDED")

    def test_ccg_does_not_construct_a_fabric_execution_request(self):
        self.assertFalse(hasattr(DOMAIN, "FabricExecutionRequest"))
        self.assertFalse(hasattr(DOMAIN, "execution_request_for"))

    def test_canonical_digest_rejects_numbers_without_exact_runtime_parity(self):
        material = commit_material()
        unsupported = DOMAIN.PersonalRuntimeCommitMaterial(
            caller_identity=material.caller_identity,
            issued_at=material.issued_at,
            resolved_target=material.resolved_target,
            execution_input={"input": {"temperature": 0.5}},
            constraint_receipt_refs=material.constraint_receipt_refs,
            resolution_reason=material.resolution_reason,
        )
        request = DOMAIN.TargetAdmissionRequest(
            "request-17", target(),
            DOMAIN.PersonalAdmissionContext("delegation-17", 3, "policy:4", "profile:8", "constraint:9", "recovery:2"),
        )
        with self.assertRaisesRegex(DOMAIN.ContractError, "Personal Runtime builder"):
            DOMAIN.to_delegation_commit(request, unsupported)

    def test_owner_receipt_becomes_a_stable_workbench_observation_input(self):
        observation = DOMAIN.SourceObservation.from_owner_receipt(receipt())
        payload = observation.as_workbench_input()
        self.assertEqual("ccg.coding-observation.v1", payload["schemaVersion"])
        self.assertEqual(observation.observation_id, payload["observationId"])
        self.assertEqual(observation.source_digest, payload["sourceDigest"])
        self.assertEqual(REQUEST_DIGEST, payload["ownerReceipt"]["requestDigest"])
        self.assertEqual("attempt:7", payload["ownerReceipt"]["actions"][0]["fenceToken"])
        self.assertEqual("codex://runs/receipt-1", payload["ownerReceipt"]["deepLinkMetadata"]["href"])

    def test_owner_action_fence_is_part_of_the_observation_identity(self):
        original = receipt()
        changed = DOMAIN.OwnerReceipt(
            owner_ref=original.owner_ref, receipt_id="receipt-2", request_id=original.request_id,
            request_digest=original.request_digest,
            target_id=original.target_id, target_revision=original.target_revision,
            target_digest=original.target_digest, owner_sequence=original.owner_sequence,
            outcome=original.outcome, occurred_at=original.occurred_at,
            actions=(DOMAIN.OwnerActionDescriptor("owner.cancel", original.owner_ref, "7", "attempt:8", ACTION_DIGEST),),
            deep_link=original.deep_link,
        )
        self.assertNotEqual(original.digest, changed.digest)
        self.assertNotEqual(
            DOMAIN.SourceObservation.from_owner_receipt(original).source_digest,
            DOMAIN.SourceObservation.from_owner_receipt(changed).source_digest,
        )

    def test_owner_action_digest_has_no_cross_field_delimiter_collision(self):
        original = receipt()
        left = DOMAIN.OwnerReceipt(
            owner_ref=original.owner_ref, receipt_id=original.receipt_id, request_id=original.request_id,
            request_digest=original.request_digest,
            target_id=original.target_id, target_revision=original.target_revision,
            target_digest=original.target_digest, owner_sequence=original.owner_sequence,
            outcome=original.outcome, occurred_at=original.occurred_at,
            actions=(DOMAIN.OwnerActionDescriptor("owner.cancel", original.owner_ref, "7:x", "fence", ACTION_DIGEST),),
            deep_link=original.deep_link,
        )
        right = DOMAIN.OwnerReceipt(
            owner_ref=original.owner_ref, receipt_id=original.receipt_id, request_id=original.request_id,
            request_digest=original.request_digest,
            target_id=original.target_id, target_revision=original.target_revision,
            target_digest=original.target_digest, owner_sequence=original.owner_sequence,
            outcome=original.outcome, occurred_at=original.occurred_at,
            actions=(DOMAIN.OwnerActionDescriptor("owner.cancel", original.owner_ref, "7", "x:fence", ACTION_DIGEST),),
            deep_link=original.deep_link,
        )
        self.assertNotEqual(left.digest, right.digest)

    def test_ccg_does_not_implement_workbench_projection_or_live_inspection(self):
        self.assertFalse(hasattr(DOMAIN, "WorkbenchProjection"))
        self.assertFalse(hasattr(DOMAIN, "project_observations"))
        self.assertFalse(hasattr(DOMAIN, "read_durable_projection"))
        self.assertFalse(hasattr(DOMAIN, "inspect_live_detail"))


if __name__ == "__main__":
    unittest.main()
