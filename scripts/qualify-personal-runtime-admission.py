#!/usr/bin/env python3
"""Qualify CCG's real CLI against one exact clean Personal Runtime owner."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PERSONAL_RUNTIME_COMMIT = "1ad2df9deeb3f9cd89fc5d0adab92c90bf592cde"
ROOT = Path(__file__).resolve().parents[1]
DOMAIN_PATH = ROOT / "review-supervisor" / "ccg_coding_domain.py"
OWNER_PROBE_PATH = (
    ROOT
    / "review-supervisor"
    / "tests"
    / "fixtures"
    / "personal_runtime_admission_owner_probe.mjs"
)


def _git(personal_runtime: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(personal_runtime), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _load_domain():
    spec = importlib.util.spec_from_file_location("ccg_coding_domain", DOMAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load CCG coding domain")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _request(domain) -> dict[str, object]:
    policy_digest = "sha256:" + "b" * 64
    implementer = {"subject": "ccg-implementer", "executionRef": "run:implement-exact-pin"}
    reviewer = {"subject": "ccg-reviewer", "executionRef": "run:review-exact-pin"}
    policy = {
        "id": "dual-leaf",
        "revision": "v1",
        "digest": policy_digest,
        "independenceClass": "separate-subject-and-run",
    }
    target_digest = domain.coding_target_digest(
        "coding:exact-pin-probe",
        "r1",
        domain.WorkerRef(implementer["subject"], implementer["executionRef"]),
        domain.WorkerRef(reviewer["subject"], reviewer["executionRef"]),
        domain.ReviewerPolicy(
            policy["id"],
            policy["revision"],
            policy["digest"],
            policy["independenceClass"],
        ),
        "personal-runtime:resolved-target",
    )
    return {
        "schemaVersion": "ccg.coding-admission-request.v1",
        "requestId": "ccg-exact-pin-admission",
        "target": {
            "targetId": "coding:exact-pin-probe",
            "targetRevision": "r1",
            "targetDigest": target_digest,
            "domainDecisionRef": domain.coding_domain_decision_ref(target_digest),
            "implementer": implementer,
            "reviewer": reviewer,
            "reviewerPolicy": policy,
            "executionTargetRef": "personal-runtime:resolved-target",
        },
        "reviewAttestation": {
            "targetId": "coding:exact-pin-probe",
            "targetRevision": "r1",
            "targetDigest": target_digest,
            "reviewer": reviewer,
            "reviewerPolicy": policy,
        },
        "personal": {
            "delegationId": "delegation-ccg-exact-pin",
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
                "input": {"prompt": "qualify CCG through PR admission"},
                "workspaceRef": "workspace:ccg-exact-pin",
                "watchdogAfterMs": 30_000,
            },
            "constraintReceiptRefs": [],
            "resolutionReason": "CCG exact-pin qualification target",
        },
        "deepLink": "codex://ccg/exact-pin-probe",
    }


def _run_cli(input_path: Path, socket_path: Path, token: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["CCG_PR_ADMISSION_TOKEN"] = token
    return subprocess.run(
        [
            sys.executable,
            str(DOMAIN_PATH),
            "admit",
            "--socket",
            str(socket_path),
            "--token-env",
            "CCG_PR_ADMISSION_TOKEN",
            "--input",
            str(input_path),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--personal-runtime", required=True, type=Path)
    args = parser.parse_args()
    personal_runtime = args.personal_runtime.resolve()

    actual_commit = _git(personal_runtime, "rev-parse", "HEAD")
    if actual_commit != PERSONAL_RUNTIME_COMMIT:
        raise RuntimeError(
            f"Personal Runtime pin mismatch: expected {PERSONAL_RUNTIME_COMMIT}, got {actual_commit}"
        )
    dirty = _git(personal_runtime, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise RuntimeError("Personal Runtime exact-pin worktree must be clean")

    build = subprocess.run(
        ["pnpm", "run", "build"],
        cwd=personal_runtime,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:
        raise RuntimeError("Personal Runtime exact-pin build failed:\n" + build.stdout + build.stderr)

    domain = _load_domain()
    request = _request(domain)
    with tempfile.TemporaryDirectory(prefix="ccg-pr-public-admission-") as directory_name:
        directory = Path(directory_name)
        directory.chmod(0o700)
        input_path = directory / "request.json"
        config_path = directory / "owner.json"
        database_path = directory / "runtime.sqlite"
        socket_path = directory / "admission.sock"
        token_path = directory / "token"
        input_path.write_text(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        config_path.write_text(
            json.dumps(
                {
                    "databasePath": str(database_path),
                    "socketPath": str(socket_path),
                    "tokenPath": str(token_path),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        owner = subprocess.Popen(
            ["node", str(OWNER_PROBE_PATH), str(personal_runtime), str(config_path)],
            cwd=personal_runtime,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready_line = owner.stdout.readline()
            if not ready_line:
                raise RuntimeError("Personal Runtime owner failed before readiness: " + owner.stderr.read())
            ready = json.loads(ready_line)
            if ready != {"ready": True, "socketPath": str(socket_path)}:
                raise AssertionError(f"unexpected Personal Runtime owner readiness: {ready!r}")
            token = token_path.read_text(encoding="utf-8")

            rejected = _run_cli(input_path, socket_path, "wrong-token")
            if rejected.returncode == 0 or "authentication failed" not in rejected.stderr:
                raise AssertionError(
                    "public admission wire did not reject the wrong credential:\n"
                    + rejected.stdout
                    + rejected.stderr
                )

            admitted = _run_cli(input_path, socket_path, token)
            if admitted.returncode != 0:
                raise RuntimeError(
                    "CCG product CLI admission failed:\n" + admitted.stdout + admitted.stderr
                )
            result = json.loads(admitted.stdout)
            receipt = result["admissionReceipt"]
            owner.stdin.write(json.dumps({"outboxCommandId": receipt["outboxCommandId"]}) + "\n")
            owner.stdin.flush()
            remaining_stdout, remaining_stderr = owner.communicate(timeout=20)
            if owner.returncode != 0:
                raise RuntimeError(
                    "Personal Runtime owner evidence failed:\n"
                    + remaining_stdout
                    + remaining_stderr
                )
            evidence = json.loads(remaining_stdout.strip())
        finally:
            if owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=5)

    target_value = request["target"]
    personal_value = request["personal"]
    material_value = request["commitMaterial"]
    target = domain.CodingTargetDecision(
        target_id=target_value["targetId"],
        target_revision=target_value["targetRevision"],
        target_digest=target_value["targetDigest"],
        domain_decision_ref=target_value["domainDecisionRef"],
        implementer=domain.WorkerRef(
            target_value["implementer"]["subject"],
            target_value["implementer"]["executionRef"],
        ),
        reviewer=domain.WorkerRef(
            target_value["reviewer"]["subject"],
            target_value["reviewer"]["executionRef"],
        ),
        reviewer_policy=domain.ReviewerPolicy(
            target_value["reviewerPolicy"]["id"],
            target_value["reviewerPolicy"]["revision"],
            target_value["reviewerPolicy"]["digest"],
            target_value["reviewerPolicy"]["independenceClass"],
        ),
        execution_target_ref=target_value["executionTargetRef"],
    )
    admission_request = domain.TargetAdmissionRequest(
        request["requestId"],
        target,
        domain.PersonalAdmissionContext(
            personal_value["delegationId"],
            personal_value["expectedDelegationVersion"],
            personal_value["policyRevisionRef"],
            personal_value["profileRevisionRef"],
            personal_value["constraintSetRef"],
            personal_value["recoveryPolicyRef"],
        ),
    )
    material = domain.PersonalRuntimeCommitMaterial(
        caller_identity=material_value["callerIdentity"],
        issued_at=material_value["issuedAt"],
        resolved_target=material_value["resolvedTarget"],
        execution_input=material_value["executionInput"],
        constraint_receipt_refs=tuple(material_value["constraintReceiptRefs"]),
        resolution_reason=material_value["resolutionReason"],
    )
    command = domain.to_delegation_commit(admission_request, material)
    delegation = evidence["delegation"]
    outbox = evidence["outbox"]
    observation = result["sourceObservation"]
    owner_receipt = observation["ownerReceipt"]

    if receipt["payloadDigest"] != command["payloadDigest"]:
        raise AssertionError("durable receipt payloadDigest differs from the submitted command")
    if result["requestDigest"] != domain._digest_json(command):
        raise AssertionError("CLI requestDigest does not cover the complete admission command")
    if receipt["delegationRevision"] != 1 or receipt["delegationVersion"] != 1:
        raise AssertionError("wrong-auth attempt mutated the aggregate or recorded revision is wrong")
    aggregate = delegation["delegation"]
    if (
        aggregate["lifecycle"] != "open"
        or aggregate["version"] != receipt["delegationVersion"]
        or aggregate["latestDecisionRevision"] != receipt["delegationRevision"]
        or aggregate["pendingDispatchRevision"] != receipt["delegationRevision"]
    ):
        raise AssertionError("Personal Runtime aggregate state does not match the admission receipt")
    latest = delegation["latestDecision"]
    if (
        latest["domainDecisionRef"] != target.domain_decision_ref
        or latest["resolvedTarget"] != material_value["resolvedTarget"]
        or latest["targetDigest"] != command["payload"]["decision"]["targetDigest"]
    ):
        raise AssertionError("recorded target differs from the frozen CCG admission command")
    if (
        outbox["commandId"] != receipt["outboxCommandId"]
        or outbox["state"] != "pending"
        or outbox["payload"]["delegationRevision"] != receipt["delegationRevision"]
        or outbox["payload"]["execution"] != material_value["executionInput"]
        or outbox["payloadDigest"] != domain._digest_json(outbox["payload"])
    ):
        raise AssertionError("Fabric outbox state or immutable execution input is wrong")
    if (
        owner_receipt["targetId"] != target.target_id
        or owner_receipt["targetRevision"] != target.target_revision
        or owner_receipt["targetDigest"] != target.target_digest
        or owner_receipt["requestDigest"] != result["requestDigest"]
        or owner_receipt["ownerSequence"] != receipt["delegationVersion"]
    ):
        raise AssertionError("SourceObservation lost CCG target or PR aggregate identity")
    expected_source_digest = domain.OwnerReceipt(
        owner_ref=owner_receipt["ownerRef"],
        receipt_id=owner_receipt["receiptId"],
        request_id=owner_receipt["requestId"],
        request_digest=owner_receipt["requestDigest"],
        target_id=owner_receipt["targetId"],
        target_revision=owner_receipt["targetRevision"],
        target_digest=owner_receipt["targetDigest"],
        owner_sequence=owner_receipt["ownerSequence"],
        outcome=owner_receipt["outcome"],
        occurred_at=owner_receipt["occurredAt"],
        deep_link=owner_receipt["deepLinkMetadata"]["href"],
    ).digest
    if observation["sourceDigest"] != expected_source_digest:
        raise AssertionError("SourceObservation sourceDigest does not cover its owner receipt")

    print(
        json.dumps(
            {
                "personalRuntimeCommit": actual_commit,
                "receiptSchemaVersion": receipt["schemaVersion"],
                "receiptStatus": receipt["status"],
                "delegationRevision": receipt["delegationRevision"],
                "delegationVersion": receipt["delegationVersion"],
                "aggregateLifecycle": aggregate["lifecycle"],
                "outboxState": outbox["state"],
                "payloadDigest": receipt["payloadDigest"],
                "requestDigest": result["requestDigest"],
                "sourceDigest": observation["sourceDigest"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
