#!/usr/bin/env python3
"""Qualify CCG admission against one exact clean Personal Runtime commit."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile


PERSONAL_RUNTIME_COMMIT = "b92494181d63fe39f07afa8e168a951c33f73888"
ROOT = Path(__file__).resolve().parents[1]
DOMAIN_PATH = ROOT / "review-supervisor" / "ccg_coding_domain.py"
PROBE_PATH = (
    ROOT
    / "review-supervisor"
    / "tests"
    / "fixtures"
    / "personal_runtime_admission_probe.mjs"
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
    )
    if build.returncode != 0:
        raise RuntimeError("Personal Runtime exact-pin build failed:\n" + build.stdout + build.stderr)

    domain = _load_domain()
    coding_target_digest = "sha256:" + "a" * 64
    target = domain.CodingTargetDecision(
        target_id="coding:exact-pin-probe",
        target_revision="r1",
        target_digest=coding_target_digest,
        domain_decision_ref="ccg:decision:exact-pin-probe:r1",
        implementer=domain.WorkerRef("ccg-implementer", "run:implement-exact-pin"),
        reviewer=domain.WorkerRef("ccg-reviewer", "run:review-exact-pin"),
        reviewer_policy=domain.ReviewerPolicy(
            "dual-leaf",
            "v1",
            "sha256:" + "b" * 64,
            "separate-subject-and-run",
        ),
        execution_target_ref="personal-runtime:resolved-target",
    )
    request = domain.TargetAdmissionRequest(
        "ccg-exact-pin-admission",
        target,
        domain.PersonalAdmissionContext(
            "delegation-ccg-exact-pin",
            0,
            "policy-main@1",
            "profile-main@1",
            "constraints-main@1",
            "recovery-default@1",
        ),
    )
    material = domain.PersonalRuntimeCommitMaterial(
        caller_identity="ccg-caller",
        issued_at="2026-09-21T00:00:00.000Z",
        resolved_target={
            "profileRevisionRef": "profile-main@1",
            "harnessRef": "codex",
            "backendRef": "local",
        },
        execution_input={
            "input": {"prompt": "qualify CCG through PR admission"},
            "workspaceRef": "workspace:ccg-exact-pin",
            "watchdogAfterMs": 30_000,
        },
        constraint_receipt_refs=(),
        resolution_reason="CCG exact-pin qualification target",
    )
    command = domain.to_delegation_commit(request, material)

    with tempfile.TemporaryDirectory(prefix="ccg-pr-admission-") as temporary_directory:
        input_path = Path(temporary_directory) / "probe.json"
        input_path.write_text(
            json.dumps(
                {"command": command, "codingTargetDigest": coding_target_digest},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                "node",
                str(PROBE_PATH),
                str(personal_runtime),
                str(input_path),
            ],
            cwd=personal_runtime,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Personal Runtime admission probe failed:\n"
                + completed.stdout
                + completed.stderr
            )
    probe_result = json.loads(completed.stdout)

    class RecordedPort:
        def commit(self, submitted):
            if submitted != command:
                raise AssertionError("admit_target rebuilt a different command")
            return probe_result["receipt"]

    receipt = domain.admit_target(RecordedPort(), request, material)
    if receipt.status != "RECORDED" or probe_result["fabricOutboxCount"] != 1:
        raise AssertionError("exact-pin admission did not record exactly one Fabric outbox command")
    print(
        json.dumps(
            {
                "personalRuntimeCommit": actual_commit,
                "receiptSchemaVersion": receipt.schema_version,
                "receiptStatus": receipt.status,
                "fabricOutboxCount": probe_result["fabricOutboxCount"],
                "outboxCommandId": receipt.outbox_command_id,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
