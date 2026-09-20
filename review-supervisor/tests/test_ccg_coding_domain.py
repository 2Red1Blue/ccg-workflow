#!/usr/bin/env python3
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


def target():
    return DOMAIN.CodingTargetDecision(
        target_id="coding:target-17",
        target_revision="r7",
        target_digest=DIGEST,
        domain_decision_ref="ccg:decision:target-17:r7",
        implementer=DOMAIN.WorkerRef("ccg-implementer", "run:implement-1"),
        reviewer=DOMAIN.WorkerRef("ccg-reviewer", "run:review-1"),
        reviewer_policy=DOMAIN.ReviewerPolicy("dual-leaf", "v2", POLICY_DIGEST, "separate-subject-and-run"),
        execution_target_ref="fabric-target:codebuddy:qualified-v1",
    )


def receipt(sequence=1, receipt_id="receipt-1", outcome="accepted", deep_link="codex://runs/receipt-1"):
    return DOMAIN.OwnerReceipt(
        owner_ref="agent-fabric:operation-17",
        receipt_id=receipt_id,
        request_id="request-17",
        target_id="coding:target-17",
        target_revision="r7",
        target_digest=DIGEST,
        owner_sequence=sequence,
        outcome=outcome,
        occurred_at="2026-09-20T10:00:00Z",
        actions=(DOMAIN.OwnerActionDescriptor("owner.cancel", "agent-fabric:operation-17", "7", "attempt:7", ACTION_DIGEST),),
        deep_link=deep_link,
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

    def test_admission_contains_target_identity_but_never_review_truth(self):
        request = DOMAIN.TargetAdmissionRequest(
            "request-17",
            target(),
            DOMAIN.PersonalAdmissionContext("delegation-17", 3, "policy:4", "profile:8", "constraint:9", "recovery:2"),
        )
        payload = request.as_payload()
        self.assertEqual("ccg", payload["target"]["selectionAuthority"])
        self.assertEqual("r7", payload["target"]["targetRevision"])
        self.assertEqual(DIGEST, payload["target"]["targetDigest"])
        self.assertNotIn("reviewerRef", payload["target"])
        self.assertNotIn("verdict", payload["target"])
        self.assertNotIn("review", payload["target"])

    def test_ccg_rechecks_completed_reviewer_under_the_frozen_policy(self):
        decision = target()
        DOMAIN.validate_review_independence(
            decision,
            DOMAIN.ReviewIndependenceAttestation(
                decision.target_id, decision.target_revision, decision.target_digest,
                DOMAIN.WorkerRef("ccg-reviewer", "run:review-final"), decision.reviewer_policy,
            ),
        )
        with self.assertRaisesRegex(DOMAIN.ContractError, "actual reviewer subject"):
            DOMAIN.validate_review_independence(
                decision,
                DOMAIN.ReviewIndependenceAttestation(
                    decision.target_id, decision.target_revision, decision.target_digest,
                    DOMAIN.WorkerRef("ccg-implementer", "run:review-final"), decision.reviewer_policy,
                ),
            )

    def test_admission_outcomes_keep_personal_decision_separate_from_ccg_review(self):
        admitted = DOMAIN.AdmissionOutcome("ADMITTED", "delegation-17", "outbox-17")
        self.assertEqual("ADMITTED", admitted.status)
        self.assertEqual("OUTCOME_UNKNOWN", DOMAIN.AdmissionOutcome("OUTCOME_UNKNOWN").status)
        with self.assertRaisesRegex(DOMAIN.ContractError, "must not claim"):
            DOMAIN.AdmissionOutcome("UNAVAILABLE", "delegation-17")

    def test_fabric_execution_is_pinned_to_the_admitted_ccg_target(self):
        request = DOMAIN.execution_request_for(target(), DOMAIN.AdmissionOutcome("ADMITTED", "delegation-17", "outbox-17"))
        self.assertEqual(("coding:target-17", "r7", DIGEST), (request.target_id, request.target_revision, request.target_digest))
        with self.assertRaisesRegex(DOMAIN.ContractError, "requires a recorded"):
            DOMAIN.execution_request_for(target(), DOMAIN.AdmissionOutcome("OUTCOME_UNKNOWN"))

    def test_owner_receipt_flows_through_observation_to_latest_projection_idempotently(self):
        first = DOMAIN.SourceObservation.from_owner_receipt(receipt(1, "receipt-1", "accepted"))
        later = DOMAIN.SourceObservation.from_owner_receipt(receipt(2, "receipt-2", "completed"))
        projection = DOMAIN.project_observations([later, first, first])
        self.assertEqual(1, len(projection))
        self.assertEqual("completed", projection[0].owner_outcome)
        self.assertEqual(2, projection[0].owner_sequence)
        self.assertEqual(later.observation_id, projection[0].source_observation_id)
        self.assertEqual("owner.cancel", projection[0].actions[0].action_type)

    def test_conflicting_owner_sequence_is_rejected_instead_of_creating_a_projection_fsm(self):
        first = DOMAIN.SourceObservation.from_owner_receipt(receipt(1, "receipt-1", "accepted"))
        conflict = DOMAIN.SourceObservation.from_owner_receipt(receipt(1, "receipt-2", "failed"))
        with self.assertRaisesRegex(DOMAIN.ContractError, "owner sequence has a conflicting digest"):
            DOMAIN.project_observations([first, conflict])

    def test_durable_read_and_live_inspection_are_separate_and_link_failure_is_only_unavailable(self):
        projection = DOMAIN.project_observations([DOMAIN.SourceObservation.from_owner_receipt(receipt())])[0]

        class Store:
            def __init__(self):
                self.calls = 0
            def read_projection(self, projection_id):
                self.calls += 1
                return projection if projection_id == projection.projection_id else None

        class BrokenLivePort:
            def inspect(self, deep_link):
                raise RuntimeError("owner surface is offline")

        store = Store()
        self.assertEqual(projection, DOMAIN.read_durable_projection(store, projection.projection_id))
        self.assertEqual(1, store.calls)
        inspection = DOMAIN.inspect_live_detail(BrokenLivePort(), projection)
        self.assertEqual("unavailable", inspection.status)
        self.assertEqual("accepted", projection.owner_outcome)
        self.assertEqual("codex://runs/receipt-1", projection.deep_link)


if __name__ == "__main__":
    unittest.main()
