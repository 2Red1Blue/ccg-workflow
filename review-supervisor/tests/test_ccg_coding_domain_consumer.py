"""Product-flow and transport tests for the CCG Coding Domain consumer."""

import importlib.util
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ccg_coding_domain.py"
SPEC = importlib.util.spec_from_file_location("ccg_coding_domain_consumer", SCRIPT)
DOMAIN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DOMAIN
SPEC.loader.exec_module(DOMAIN)

DIGEST = "sha256:" + "a" * 64
POLICY_DIGEST = "sha256:" + "b" * 64
CONTEXT_DIGEST = "sha256:" + "c" * 64
_DECISION_TEMP = tempfile.TemporaryDirectory(prefix="ccg-consumer-decision-")
_DECISION_ROOT = Path(_DECISION_TEMP.name)
sys.path.insert(0, str(SCRIPT.parent))
import ccg_task_router as ROUTER
_DECISION_RESOLUTION, _DECISION_TASK = ROUTER.create_task(
    str(_DECISION_ROOT), "Consumer test", "consumer-test", {},
)
for _index in range(1, 7):
    ROUTER.coding_decision(
        str(_DECISION_ROOT), str(_DECISION_TASK), allocation_id=f"prior-{_index}",
        target_id="coding:target-17", implementer_subject="ccg-implementer",
        implementer_execution_ref=f"run:prior-{_index}", reviewer_subject="ccg-reviewer",
        reviewer_execution_ref="run:review-1", policy_id="dual-leaf", policy_revision="v2",
        policy_digest=POLICY_DIGEST, independence_class="separate-subject-and-run",
        execution_target_ref="personal-runtime:resolved-target",
    )
ROUTER.coding_decision(
    str(_DECISION_ROOT), str(_DECISION_TASK), allocation_id="selected-r7",
    target_id="coding:target-17", implementer_subject="ccg-implementer",
    implementer_execution_ref="run:implement-1", reviewer_subject="ccg-reviewer",
    reviewer_execution_ref="run:review-1", policy_id="dual-leaf", policy_revision="v2",
    policy_digest=POLICY_DIGEST, independence_class="separate-subject-and-run",
    execution_target_ref="personal-runtime:resolved-target",
)
_FROZEN_LOCATOR = {"root": str(_DECISION_ROOT), "taskDir": str(_DECISION_TASK), "taskId": "consumer-test"}


def request_payload(*, locator=None, target_revision="r7"):
    reviewer = {"subject": "ccg-reviewer", "executionRef": "run:review-1"}
    policy = {
        "id": "dual-leaf",
        "revision": "v2",
        "digest": POLICY_DIGEST,
        "independenceClass": "separate-subject-and-run",
    }
    implementer = {
        "subject": "ccg-implementer",
        "executionRef": "run:implement-1",
    }
    target_digest = DOMAIN.coding_target_digest(
        "coding:target-17",
        target_revision,
        DOMAIN.WorkerRef(implementer["subject"], implementer["executionRef"]),
        DOMAIN.WorkerRef(reviewer["subject"], reviewer["executionRef"]),
        DOMAIN.ReviewerPolicy(
            policy["id"],
            policy["revision"],
            policy["digest"],
            policy["independenceClass"],
        ),
        "personal-runtime:resolved-target",
    )
    return {
        "schemaVersion": "ccg.coding-admission-request.v1",
        "frozenDecisionLocator": dict(locator or _FROZEN_LOCATOR),
        "requestId": "request-17",
        "target": {
            "targetId": "coding:target-17",
            "targetRevision": target_revision,
            "targetDigest": target_digest,
            "domainDecisionRef": DOMAIN.coding_domain_decision_ref(target_digest),
            "implementer": implementer,
            "reviewer": reviewer,
            "reviewerPolicy": policy,
            "executionTargetRef": "personal-runtime:resolved-target",
        },
        "reviewAttestation": {
            "targetId": "coding:target-17",
            "targetRevision": target_revision,
            "targetDigest": target_digest,
            "reviewer": reviewer,
            "reviewerPolicy": policy,
        },
        "personal": {
            "delegationId": "delegation-17",
            "expectedDelegationVersion": 0,
            "policyRevisionRef": "policy-main@1",
            "profileRevisionRef": "profile-main@1",
            "constraintSetRef": "constraints-main@1",
            "recoveryPolicyRef": "recovery-default@1",
        },
        "commitMaterial": {
            "callerIdentity": "ccg-caller",
            "issuedAt": "2026-09-21T00:00:00.000Z",
            "resolvedTarget": {
                "profileRevisionRef": "profile-main@1",
                "harnessRef": "codex",
                "backendRef": "local",
            },
            "executionInput": {
                "input": {"prompt": "implement target 17"},
                "workspaceRef": "workspace:17",
            },
            "constraintReceiptRefs": [],
            "resolutionReason": "frozen CCG target r7",
        },
        "deepLink": "codex://ccg/target-17",
    }


class FlowTest(unittest.TestCase):
    def test_never_allocated_self_consistent_revision_is_rejected_before_port(self):
        value = request_payload()
        target = value["target"]
        target["targetRevision"] = "r999"
        target["targetDigest"] = DOMAIN.coding_target_digest(
            target["targetId"], target["targetRevision"],
            DOMAIN.WorkerRef(target["implementer"]["subject"], target["implementer"]["executionRef"]),
            DOMAIN.WorkerRef(target["reviewer"]["subject"], target["reviewer"]["executionRef"]),
            DOMAIN.ReviewerPolicy(**{
                "policy_id": target["reviewerPolicy"]["id"],
                "revision": target["reviewerPolicy"]["revision"],
                "digest": target["reviewerPolicy"]["digest"],
                "independence_class": target["reviewerPolicy"]["independenceClass"],
            }), target["executionTargetRef"],
        )
        target["domainDecisionRef"] = DOMAIN.coding_domain_decision_ref(target["targetDigest"])
        value["reviewAttestation"].update({
            "targetRevision": target["targetRevision"],
            "targetDigest": target["targetDigest"],
        })
        class Port:
            touched = False
            def commit(self, _command):
                self.touched = True
        port = Port()
        with self.assertRaisesRegex(DOMAIN.ContractError, "CODING_DECISION_NOT_FOUND"):
            DOMAIN.execute_coding_admission(port, value)
        self.assertFalse(port.touched)

    def test_wrong_task_identity_is_rejected_before_port(self):
        value = request_payload()
        value["frozenDecisionLocator"]["taskId"] = "different-task"
        class Port:
            touched = False
            def commit(self, _command):
                self.touched = True
        port = Port()
        with self.assertRaisesRegex(DOMAIN.ContractError, "canonical task"):
            DOMAIN.execute_coding_admission(port, value)
        self.assertFalse(port.touched)

    def test_trellis_date_prefixed_task_accepts_frozen_admission(self):
        with tempfile.TemporaryDirectory(prefix="ccg-frozen-trellis-") as temporary:
            root = Path(temporary)
            trellis = root / ".trellis"
            (trellis / "scripts").mkdir(parents=True)
            (trellis / "workflow.md").write_text("workflow\n", encoding="utf-8")
            (trellis / "scripts" / "task.py").write_text(
                "import argparse, json\nfrom pathlib import Path\n"
                "p=argparse.ArgumentParser(); p.add_argument('command'); p.add_argument('title'); p.add_argument('--slug'); a=p.parse_args()\n"
                "d=Path('.trellis/tasks') / ('08-28-' + a.slug); d.mkdir(parents=True)\n"
                "(d/'task.json').write_text(json.dumps({'id': a.slug, 'title': a.title, 'meta': {}}))\n",
                encoding="utf-8",
            )
            resolution, task_dir = ROUTER.create_task(
                str(root), "Trellis consumer", "trellis-consumer", {},
            )
            record = ROUTER.coding_decision(
                str(root), str(task_dir), allocation_id="trellis-admission",
                target_id="coding:target-17", implementer_subject="ccg-implementer",
                implementer_execution_ref="run:implement-1", reviewer_subject="ccg-reviewer",
                reviewer_execution_ref="run:review-1", policy_id="dual-leaf", policy_revision="v2",
                policy_digest=POLICY_DIGEST, independence_class="separate-subject-and-run",
                execution_target_ref="personal-runtime:resolved-target",
            )["decision"]
            locator = {
                "root": resolution["root"],
                "taskDir": str(task_dir),
                "taskId": resolution["taskId"],
            }

            class Port:
                submitted = None

                def commit(self, command):
                    self.submitted = command
                    return {
                        "schemaVersion": "personal-runtime.delegation-admission-receipt.v1",
                        "callerId": "ccg-caller",
                        "commandId": "request-17",
                        "payloadDigest": command["payloadDigest"],
                        "contextDigest": CONTEXT_DIGEST,
                        "status": "RECORDED",
                        "delegationId": "delegation-17",
                        "delegationRevision": 1,
                        "delegationVersion": 2,
                        "outboxCommandId": DOMAIN.personal_runtime_outbox_command_id(
                            "ccg-caller", "request-17"
                        ),
                        "recordedAt": "2026-09-21T00:00:01.000Z",
                    }

            port = Port()
            result = DOMAIN.execute_coding_admission(
                port, request_payload(locator=locator, target_revision=record["targetRevision"]),
            )
        self.assertEqual("RECORDED", result["status"])
        self.assertEqual("08-28-trellis-consumer", Path(locator["taskDir"]).name)

    def test_supported_flow_constructs_decision_commits_and_emits_observation(self):
        class Port:
            submitted = None

            def commit(self, command):
                self.submitted = command
                return {
                    "schemaVersion": "personal-runtime.delegation-admission-receipt.v1",
                    "callerId": "ccg-caller",
                    "commandId": "request-17",
                    "payloadDigest": command["payloadDigest"],
                    "contextDigest": CONTEXT_DIGEST,
                    "status": "RECORDED",
                    "delegationId": "delegation-17",
                    "delegationRevision": 1,
                    "delegationVersion": 2,
                    "outboxCommandId": DOMAIN.personal_runtime_outbox_command_id(
                        "ccg-caller", "request-17"
                    ),
                    "recordedAt": "2026-09-21T00:00:01.000Z",
                }

        port = Port()
        result = DOMAIN.execute_coding_admission(port, request_payload())
        self.assertEqual("RECORDED", result["status"])
        self.assertEqual(
            request_payload()["commitMaterial"]["executionInput"],
            port.submitted["payload"]["execution"]["value"],
        )
        owner = result["sourceObservation"]["ownerReceipt"]
        self.assertEqual("coding:target-17", owner["targetId"])
        self.assertEqual("r7", owner["targetRevision"])
        self.assertEqual(result["requestDigest"], owner["requestDigest"])
        self.assertEqual(2, owner["ownerSequence"])
        self.assertEqual("RECORDED", owner["outcome"])
        self.assertEqual(
            DOMAIN.OwnerReceipt(
                owner_ref=owner["ownerRef"],
                receipt_id=owner["receiptId"],
                request_id=owner["requestId"],
                request_digest=owner["requestDigest"],
                target_id=owner["targetId"],
                target_revision=owner["targetRevision"],
                target_digest=owner["targetDigest"],
                owner_sequence=owner["ownerSequence"],
                outcome=owner["outcome"],
                occurred_at=owner["occurredAt"],
                deep_link=owner["deepLinkMetadata"]["href"],
            ).digest,
            result["sourceObservation"]["sourceDigest"],
        )

    def test_reviewer_replacement_requires_a_new_frozen_decision(self):
        value = request_payload()
        value["reviewAttestation"] = {
            **value["reviewAttestation"],
            "reviewer": {"subject": "replacement", "executionRef": "run:replacement"},
        }

        class Port:
            def commit(self, _command):
                raise AssertionError("admission must not be called")

        with self.assertRaisesRegex(DOMAIN.ContractError, "reviewer frozen"):
            DOMAIN.execute_coding_admission(Port(), value)

    def test_reviewer_and_attestation_cannot_replace_the_frozen_decision(self):
        value = request_payload()
        replacement = {"subject": "replacement", "executionRef": "run:replacement"}
        value["target"]["reviewer"] = replacement
        value["reviewAttestation"]["reviewer"] = replacement

        class Port:
            def commit(self, _command):
                raise AssertionError("admission must not be called")

        with self.assertRaisesRegex(DOMAIN.ContractError, "immutable CCG decision"):
            DOMAIN.execute_coding_admission(Port(), value)

    def test_recomputed_digest_requires_a_new_domain_decision_ref(self):
        value = request_payload()
        replacement = {"subject": "replacement", "executionRef": "run:replacement"}
        value["target"]["reviewer"] = replacement
        value["reviewAttestation"]["reviewer"] = replacement
        policy = value["target"]["reviewerPolicy"]
        value["target"]["targetDigest"] = DOMAIN.coding_target_digest(
            value["target"]["targetId"],
            value["target"]["targetRevision"],
            DOMAIN.WorkerRef(
                value["target"]["implementer"]["subject"],
                value["target"]["implementer"]["executionRef"],
            ),
            DOMAIN.WorkerRef(replacement["subject"], replacement["executionRef"]),
            DOMAIN.ReviewerPolicy(
                policy["id"],
                policy["revision"],
                policy["digest"],
                policy["independenceClass"],
            ),
            value["target"]["executionTargetRef"],
        )
        value["reviewAttestation"]["targetDigest"] = value["target"]["targetDigest"]

        class Port:
            def commit(self, _command):
                raise AssertionError("admission must not be called")

        with self.assertRaisesRegex(DOMAIN.ContractError, "domain_decision_ref"):
            DOMAIN.execute_coding_admission(Port(), value)

    def test_deep_link_is_optional_and_transport_evidence_emits_no_observation(self):
        value = request_payload()
        value.pop("deepLink")

        class Port:
            def commit(self, _command):
                return DOMAIN.AdmissionTransportOutcome("UNAVAILABLE")

        result = DOMAIN.execute_coding_admission(Port(), value)
        self.assertEqual("UNAVAILABLE", result["status"])
        self.assertNotIn("sourceObservation", result)


class SocketPortTest(unittest.TestCase):
    def test_missing_endpoint_is_pre_write_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            port = DOMAIN.PersonalRuntimeAdmissionSocketPort(
                str(Path(directory) / "missing.sock"), "token", "ccg-caller"
            )
            self.assertEqual("UNAVAILABLE", port.commit({"callerIdentity": "ccg-caller"}).status)

    def test_lost_acknowledgement_after_write_is_outcome_unknown(self):
        with self._server(lambda connection, _request: connection.close()) as endpoint:
            port = DOMAIN.PersonalRuntimeAdmissionSocketPort(endpoint, "token", "ccg-caller")
            self.assertEqual(
                "OUTCOME_UNKNOWN",
                port.commit({"callerIdentity": "ccg-caller", "commandId": "request-17"}).status,
            )

    def test_complete_authentication_failure_is_not_transport_uncertainty(self):
        def reject(connection, _request):
            body = b'{"error":{"code":"AUTHENTICATION_FAILED"}}'
            connection.sendall(
                b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
                + body
            )

        with self._server(reject) as endpoint:
            port = DOMAIN.PersonalRuntimeAdmissionSocketPort(endpoint, "wrong", "ccg-caller")
            with self.assertRaisesRegex(DOMAIN.ContractError, "authentication failed"):
                port.commit({"callerIdentity": "ccg-caller", "commandId": "request-17"})

    class _server:
        def __init__(self, handler):
            self.handler = handler
            self.temporary = None
            self.listener = None
            self.thread = None

        def __enter__(self):
            self.temporary = tempfile.TemporaryDirectory()
            os.chmod(self.temporary.name, 0o700)
            self.path = str(Path(self.temporary.name) / "admission.sock")
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.listener.bind(self.path)
            os.chmod(self.path, 0o600)
            self.listener.listen(1)

            def serve():
                connection, _ = self.listener.accept()
                request = bytearray()
                while True:
                    chunk = connection.recv(65_536)
                    if not chunk:
                        break
                    request.extend(chunk)
                try:
                    self.handler(connection, bytes(request))
                finally:
                    connection.close()

            self.thread = threading.Thread(target=serve, daemon=True)
            self.thread.start()
            return self.path

        def __exit__(self, _kind, _error, _traceback):
            self.listener.close()
            self.thread.join(timeout=2)
            self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
