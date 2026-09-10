from __future__ import annotations

import json
import importlib.util
import os
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


SUPERVISOR = Path(__file__).resolve().parents[1] / "ccg-agent-supervisor.py"
sys.path.insert(0, str(SUPERVISOR.parent))
_spec = importlib.util.spec_from_file_location("ccg_agent_supervisor_test_subject", SUPERVISOR)
assert _spec and _spec.loader
supervisor = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = supervisor
_spec.loader.exec_module(supervisor)


FAKE_CODEX = r'''#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if os.environ.get("FAKE_IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

args = sys.argv[1:]
if args == ["--version"]:
    if os.environ.get("FAKE_CODEX_VERSION_BYTES"):
        sys.stdout.write("x" * int(os.environ["FAKE_CODEX_VERSION_BYTES"]))
        raise SystemExit(0)
    print(os.environ.get("FAKE_CODEX_VERSION", "codex-cli 0.150.1"))
    raise SystemExit(0)
if args == ["exec", "--help"]:
    print(os.environ.get("FAKE_CODEX_HELP", "--ignore-user-config --disable --model --sandbox --cd --skip-git-repo-check"))
    raise SystemExit(0)
if os.environ.get("FAKE_CODEX_ARGS_FILE"):
    Path(os.environ["FAKE_CODEX_ARGS_FILE"]).write_text("\n".join(args), encoding="utf-8")
if os.environ.get("FAKE_SINGLE_RUN"):
    sys.stdin.read()
    print("single run ok")
    raise SystemExit(0)

sync = Path(os.environ["TEST_SYNC_DIR"])
(sync / "codex.started").write_text("started")
deadline = time.time() + 2
while not (sync / "claude.started").exists() and time.time() < deadline:
    time.sleep(0.02)
if not (sync / "claude.started").exists():
    print("claude did not start concurrently", file=sys.stderr)
    raise SystemExit(8)
if os.environ.get("CCG_LEAF_REVIEW") != "1" or os.environ.get("CODEAGENT_WRAPPER_DEPTH") != "1":
    print("missing leaf/depth environment", file=sys.stderr)
    raise SystemExit(9)
if not Path("REQUEST.md").is_file() or not Path("CHANGES.patch").is_file():
    print("isolated bundle inputs missing", file=sys.stderr)
    raise SystemExit(10)
if any((path.stat().st_mode & 0o777) != 0o600 for path in (Path("REQUEST.md"), Path("CHANGES.patch"))):
    print("bundle input permissions are not private", file=sys.stderr)
    raise SystemExit(11)
if os.environ.get("FAKE_SLEEP"):
    if os.environ.get("FAKE_CODEX_DESCENDANT"):
        child = subprocess.Popen(
            [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
        )
        (sync / "codex.descendant.pid").write_text(str(child.pid))
    time.sleep(float(os.environ["FAKE_SLEEP"]))
print("## Critical\nNone\n## Warning\nNone\n## Info\nFake Codex review\n## Verdict\nAPPROVE")
'''


FAKE_WRAPPER = r'''#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

if os.environ.get("FAKE_IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

args = sys.argv[1:]
if os.environ.get("CODEAGENT_WRAPPER_DEPTH") not in (None, "", "0"):
    if os.environ.get("FAKE_DELETE_ON_GUARD_PROBE"):
        Path(__file__).unlink()
    print("nested fake wrapper blocked", file=sys.stderr)
    raise SystemExit(125)
if args == ["--version"]:
    if os.environ.get("FAKE_DELETE_AFTER_VERSION"):
        Path(__file__).unlink()
    print(os.environ.get("FAKE_WRAPPER_VERSION", "codeagent-wrapper version 5.16.0"))
    raise SystemExit(0)
if os.environ.get("FAKE_CLAUDE_EFFORT_FILE"):
    Path(os.environ["FAKE_CLAUDE_EFFORT_FILE"]).write_text(
        os.environ.get("CLAUDE_CODE_EFFORT_LEVEL", ""), encoding="utf-8"
    )
if os.environ.get("FAKE_PROGRESS"):
    print("progress: claude leaf started", file=sys.stderr, flush=True)
if os.environ.get("FAKE_SINGLE_RUN"):
    sys.stdin.read()
    print("single run ok")
    raise SystemExit(0)
os.environ["CODEAGENT_WRAPPER_DEPTH"] = "1"
sync = Path(os.environ["TEST_SYNC_DIR"])
(sync / "claude.started").write_text("started")
deadline = time.time() + 2
while not (sync / "codex.started").exists() and time.time() < deadline:
    time.sleep(0.02)
if not (sync / "codex.started").exists():
    print("codex did not start concurrently", file=sys.stderr)
    raise SystemExit(8)
if os.environ.get("CCG_LEAF_REVIEW") != "1" or os.environ.get("CODEAGENT_WRAPPER_DEPTH") != "1":
    print("missing leaf/depth environment", file=sys.stderr)
    raise SystemExit(9)
if not Path("REQUEST.md").is_file() or not Path("CHANGES.patch").is_file():
    print("isolated bundle inputs missing", file=sys.stderr)
    raise SystemExit(10)
if any((path.stat().st_mode & 0o777) != 0o600 for path in (Path("REQUEST.md"), Path("CHANGES.patch"))):
    print("bundle input permissions are not private", file=sys.stderr)
    raise SystemExit(11)
if os.environ.get("FAKE_SLEEP"):
    time.sleep(float(os.environ["FAKE_SLEEP"]))
if os.environ.get("FAKE_CLAUDE_FAIL"):
    print("requested fake Claude failure", file=sys.stderr)
    raise SystemExit(7)
if os.environ.get("FAKE_REPORT_BYTES"):
    sys.stdout.write("x" * int(os.environ["FAKE_REPORT_BYTES"]))
    raise SystemExit(0)
if os.environ.get("FAKE_INVALID_REPORT"):
    print("wrapper diagnostic without review sections")
    raise SystemExit(0)
if os.environ.get("FAKE_EXTRA_HEADING"):
    print("## Critical\nNone\n## Warning\nNone\n## Info\nNone\n## Extra\nUnexpected\n## Verdict\nAPPROVE")
    raise SystemExit(0)
if os.environ.get("FAKE_FENCED_HEADINGS"):
    print("## Critical\nNone\n## Warning\nNone\n## Info\n```markdown\n## Extra\n## Verdict\nREQUEST_CHANGES\n```\n## Verdict\nAPPROVE\n~~~~\nREQUEST_CHANGES\n~~~\n~~~~")
    raise SystemExit(0)
if os.environ.get("FAKE_BACKTICK_VERDICT"):
    print("## Critical\nNone\n## Warning\nNone\n## Info\nBackticked verdict\n## Verdict\n`APPROVE`")
    raise SystemExit(0)
if os.environ.get("FAKE_EXPLAINED_VERDICT"):
    print("## Critical\nNone\n## Warning\nNone\n## Info\nExplained verdict\n## Verdict\n**APPROVE** — the patch is correct and complete.")
    raise SystemExit(0)
if os.environ.get("FAKE_EXPLAINED_REQUEST_CHANGES"):
    print("## Critical\nOne issue\n## Warning\nNone\n## Info\nExplained verdict\n## Verdict\n- **REQUEST_CHANGES**: fix Critical 1.")
    raise SystemExit(0)
if os.environ.get("FAKE_CONTRADICTORY_VERDICT"):
    print("## Critical\nNone\n## Warning\nNone\n## Info\nContradictory verdict\n## Verdict\nAPPROVE\nREQUEST_CHANGES")
    raise SystemExit(0)
if os.environ.get("FAKE_SAME_LINE_CONTRADICTION"):
    print("## Critical\nNone\n## Warning\nNone\n## Info\nContradictory verdict\n## Verdict\n**APPROVE** — not REQUEST_CHANGES material")
    raise SystemExit(0)
if os.environ.get("FAKE_INLINE_EARLY_VERDICT_HEADING"):
    print("## Critical\nNone\n## Warning\nNone\n## Info\nInline text ## Verdict\nAPPROVE\n## Verdict\nNo declaration here")
    raise SystemExit(0)
if os.environ.get("FAKE_APPROVED_ONLY"):
    print("## Critical\nNone\n## Warning\nNone\n## Info\nNo exact token\n## Verdict\nAPPROVED")
    raise SystemExit(0)
if os.environ.get("FAKE_CRLF_REPORT"):
    sys.stdout.write("## Critical\r\nNone\r\n## Warning\r\nNone\r\n## Info\r\nCRLF report\r\n## Verdict\r\nAPPROVE\r\n")
    raise SystemExit(0)
print("## Critical\nNone\n## Warning\nNone\n## Info\nFake Claude review\n## Verdict\nAPPROVE")
'''


class SupervisorReviewTest(unittest.TestCase):
    def test_review_report_validation_entrypoint_remains_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "review.md"
            path.write_text("## Critical\nNone\n## Warning\nNone\n## Info\nNone\n## Verdict\nAPPROVE\n")
            self.assertEqual(("APPROVE", None), supervisor.validate_review_report(path))
            path.write_text("## Options\nA\n## Recommendation\nA\n## Risks\nNone\n## Validation\nTest\n")
            self.assertIsNotNone(supervisor.validate_review_report(path)[1])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run_root = self.root / "runs"
        self.workdir = self.root / "work"
        self.sync = self.root / "sync"
        self.workdir.mkdir()
        self.sync.mkdir()
        self.patch = self.root / "changes.patch"
        self.patch.write_text("diff --git a/a.py b/a.py\n+print('ok')\n", encoding="utf-8")
        self.codex = self._executable("fake-codex", FAKE_CODEX)
        self.wrapper = self._executable("fake-wrapper", FAKE_WRAPPER)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _executable(self, name: str, body: str) -> Path:
        path = self.root / name
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        path.chmod(0o700)
        return path

    def _review(
        self,
        *,
        environment: dict[str, str] | None = None,
        timeout: int = 10,
        request: bytes = b"Review this test patch.",
        extra_args: list[str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env["TEST_SYNC_DIR"] = str(self.sync)
        env["CCG_SUPERVISOR_TEST_MODE"] = "1"
        if environment:
            env.update(environment)
        return subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "--root",
                str(self.run_root),
                "review",
                "--workdir",
                str(self.workdir),
                "--diff-file",
                str(self.patch),
                "--codex-cli",
                str(self.codex),
                "--wrapper",
                str(self.wrapper),
                "--timeout-seconds",
                str(timeout),
                *(extra_args or []),
            ],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=timeout + 10,
        )

    def _git_review(
        self,
        repository: Path,
        *,
        extra_args: list[str] | None = None,
        timeout: int = 10,
    ) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env["TEST_SYNC_DIR"] = str(self.sync)
        env["CCG_SUPERVISOR_TEST_MODE"] = "1"
        return subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "--root",
                str(self.run_root),
                "review",
                "--workdir",
                str(repository),
                "--codex-cli",
                str(self.codex),
                "--wrapper",
                str(self.wrapper),
                "--timeout-seconds",
                str(timeout),
                *(extra_args or []),
            ],
            input=b"Review this Git patch.",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=timeout + 10,
        )

    def _git_repository(self) -> Path:
        repository = self.root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Review Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "review@example.invalid"], check=True)
        (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "base"], check=True)
        return repository

    def _status(self) -> dict[str, object]:
        statuses = list(self.run_root.glob("*/status.json"))
        self.assertEqual(1, len(statuses))
        return json.loads(statuses[0].read_text(encoding="utf-8"))

    def test_dual_review_is_concurrent_isolated_and_successful(self) -> None:
        completed = self._review()
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        status = self._status()
        self.assertEqual("succeeded", status["state"])
        self.assertEqual("succeeded", status["backends"]["codex"]["state"])
        self.assertEqual("succeeded", status["backends"]["claude"]["state"])
        run_directory = next(self.run_root.glob("*/status.json")).parent
        self.assertFalse((run_directory / "bundle").exists())
        for report in (run_directory / "codex.report.md", run_directory / "claude.report.md"):
            self.assertTrue(report.is_file())
            self.assertEqual(0o600, stat.S_IMODE(report.stat().st_mode))

    def test_codex_review_defaults_to_luna_and_records_model(self) -> None:
        args_file = self.sync / "codex.args"
        completed = self._review(environment={"FAKE_CODEX_ARGS_FILE": str(args_file)})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        args = args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual("gpt-5.6-luna", args[args.index("--model") + 1])
        status = self._status()
        self.assertEqual("gpt-5.6-luna", status["preflight"]["codex_review_model"])

    def test_codex_review_model_can_be_overridden(self) -> None:
        args_file = self.sync / "codex.args"
        completed = self._review(
            environment={"FAKE_CODEX_ARGS_FILE": str(args_file)},
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        default_args = args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual("gpt-5.6-luna", default_args[default_args.index("--model") + 1])

        self.run_root = self.root / "runs-override"
        args_file.unlink()
        env = os.environ.copy()
        env.update({"FAKE_CODEX_ARGS_FILE": str(args_file), "CCG_CODEX_REVIEW_MODEL": "gpt-5.6-sol"})
        env["TEST_SYNC_DIR"] = str(self.sync)
        env["CCG_SUPERVISOR_TEST_MODE"] = "1"
        completed = subprocess.run(
            [
                sys.executable, str(SUPERVISOR), "--root", str(self.run_root), "review",
                "--workdir", str(self.workdir), "--diff-file", str(self.patch),
                "--codex-cli", str(self.codex), "--wrapper", str(self.wrapper), "--timeout-seconds", "10",
            ], input=b"Review this test patch.", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, timeout=20,
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        args = args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual("gpt-5.6-sol", args[args.index("--model") + 1])

    def test_claude_review_defaults_to_low_effort_and_records_it(self) -> None:
        effort_file = self.sync / "claude-effort"
        completed = self._review(environment={"FAKE_CLAUDE_EFFORT_FILE": str(effort_file)})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("low", effort_file.read_text(encoding="utf-8"))
        self.assertEqual("low", self._status()["preflight"]["claude_review_effort"])

    def test_claude_review_effort_can_be_overridden(self) -> None:
        effort_file = self.sync / "claude-effort"
        completed = self._review(
            environment={
                "FAKE_CLAUDE_EFFORT_FILE": str(effort_file),
                "CCG_CLAUDE_REVIEW_EFFORT": "medium",
                "CCG_CLAUDE_ANALYSIS_EFFORT": "high",
            }
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("medium", effort_file.read_text(encoding="utf-8"))
        self.assertEqual("medium", self._status()["preflight"]["claude_review_effort"])

    def test_leaf_stderr_progress_is_forwarded_live(self) -> None:
        completed = self._review(environment={"FAKE_PROGRESS": "1"})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertIn(b"[claude] progress: claude leaf started", completed.stderr)

    def test_mixed_backend_failure_preserves_successful_report(self) -> None:
        completed = self._review(environment={"FAKE_CLAUDE_FAIL": "1"})
        self.assertEqual(1, completed.returncode)
        status = self._status()
        self.assertEqual("failed", status["state"])
        self.assertEqual("succeeded", status["backends"]["codex"]["state"])
        self.assertEqual("failed", status["backends"]["claude"]["state"])
        run_directory = next(self.run_root.glob("*/status.json")).parent
        self.assertIn("Fake Codex review", (run_directory / "codex.report.md").read_text(encoding="utf-8"))

    def test_truncated_report_fails_closed(self) -> None:
        completed = self._review(environment={"FAKE_REPORT_BYTES": str(2 * 1024 * 1024 + 1)})
        self.assertEqual(1, completed.returncode)
        status = self._status()
        self.assertEqual("failed", status["backends"]["claude"]["state"])
        self.assertTrue(status["backends"]["claude"]["report"]["truncated"])

    def test_invalid_report_format_fails_closed(self) -> None:
        completed = self._review(environment={"FAKE_INVALID_REPORT": "1"})
        self.assertEqual(1, completed.returncode)
        status = self._status()
        self.assertEqual("failed", status["backends"]["claude"]["state"])
        self.assertIn("headings exactly once", status["backends"]["claude"]["format_error"])

    def test_extra_report_section_fails_closed(self) -> None:
        completed = self._review(environment={"FAKE_EXTRA_HEADING": "1"})
        self.assertEqual(1, completed.returncode)
        self.assertIn("headings exactly once", self._status()["backends"]["claude"]["format_error"])

    def test_fenced_examples_are_not_report_sections_or_verdicts(self) -> None:
        completed = self._review(environment={"FAKE_FENCED_HEADINGS": "1"})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("APPROVE", self._status()["backends"]["claude"]["verdict"])

    def test_backticked_verdict_is_accepted(self) -> None:
        completed = self._review(environment={"FAKE_BACKTICK_VERDICT": "1"})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("APPROVE", self._status()["backends"]["claude"]["verdict"])

    def test_bold_verdict_with_same_line_rationale_is_accepted(self) -> None:
        completed = self._review(environment={"FAKE_EXPLAINED_VERDICT": "1"})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("APPROVE", self._status()["backends"]["claude"]["verdict"])

    def test_request_changes_with_same_line_rationale_is_accepted(self) -> None:
        completed = self._review(environment={"FAKE_EXPLAINED_REQUEST_CHANGES": "1"})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("REQUEST_CHANGES", self._status()["backends"]["claude"]["verdict"])

    def test_contradictory_verdict_fails_closed(self) -> None:
        completed = self._review(environment={"FAKE_CONTRADICTORY_VERDICT": "1"})
        self.assertEqual(1, completed.returncode)
        self.assertIn("exactly one", self._status()["backends"]["claude"]["format_error"])

    def test_same_line_contradictory_verdict_fails_closed(self) -> None:
        completed = self._review(environment={"FAKE_SAME_LINE_CONTRADICTION": "1"})
        self.assertEqual(1, completed.returncode)
        self.assertIn("exactly one", self._status()["backends"]["claude"]["format_error"])

    def test_inline_earlier_verdict_text_does_not_replace_real_heading(self) -> None:
        completed = self._review(environment={"FAKE_INLINE_EARLY_VERDICT_HEADING": "1"})
        self.assertEqual(1, completed.returncode)
        self.assertIn("exactly one", self._status()["backends"]["claude"]["format_error"])

    def test_verdict_token_inside_larger_word_fails_closed(self) -> None:
        completed = self._review(environment={"FAKE_APPROVED_ONLY": "1"})
        self.assertEqual(1, completed.returncode)
        self.assertIn("exactly one", self._status()["backends"]["claude"]["format_error"])

    def test_crlf_report_is_accepted(self) -> None:
        completed = self._review(environment={"FAKE_CRLF_REPORT": "1"})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("APPROVE", self._status()["backends"]["claude"]["verdict"])

    def test_timeout_is_terminal(self) -> None:
        started = time.monotonic()
        completed = self._review(
            environment={"FAKE_SLEEP": "12", "FAKE_IGNORE_TERM": "1", "FAKE_CODEX_DESCENDANT": "1"},
            timeout=3,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(124, completed.returncode)
        self.assertGreaterEqual(elapsed, 7)
        self.assertLess(elapsed, 20)
        status = self._status()
        self.assertEqual("timed_out", status["state"])
        self.assertEqual({"timed_out"}, {item["state"] for item in status["backends"].values()})
        descendant_pid = int((self.sync / "codex.descendant.pid").read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            alive = subprocess.run(["ps", "-p", str(descendant_pid), "-o", "stat="], stdout=subprocess.PIPE).stdout.strip()
            if not alive or alive.startswith(b"Z"):
                break
            time.sleep(0.05)
        self.assertTrue(not alive or alive.startswith(b"Z"), f"descendant {descendant_pid} survived: {alive!r}")

    def test_signal_cancels_active_backends_and_records_terminal_status(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "TEST_SYNC_DIR": str(self.sync),
                "CCG_SUPERVISOR_TEST_MODE": "1",
                "FAKE_SLEEP": "30",
            }
        )
        process = subprocess.Popen(
            [
                sys.executable,
                str(SUPERVISOR),
                "--root",
                str(self.run_root),
                "review",
                "--workdir",
                str(self.workdir),
                "--diff-file",
                str(self.patch),
                "--codex-cli",
                str(self.codex),
                "--wrapper",
                str(self.wrapper),
                "--timeout-seconds",
                "20",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        assert process.stdin is not None
        process.stdin.write(b"Review before cancellation.")
        process.stdin.close()
        process.stdin = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ((self.sync / "codex.started").exists() and (self.sync / "claude.started").exists()):
            time.sleep(0.02)
        self.assertTrue((self.sync / "codex.started").exists())
        self.assertTrue((self.sync / "claude.started").exists())
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=8)
        self.assertEqual(128 + signal.SIGTERM, process.returncode, (stdout + stderr).decode(errors="replace"))
        status = self._status()
        self.assertEqual("cancelled", status["state"])
        self.assertEqual({"cancelled"}, {item["state"] for item in status["backends"].values()})

    def test_empty_patch_fails_and_records_status(self) -> None:
        self.patch.write_bytes(b"")
        completed = self._review()
        self.assertEqual(1, completed.returncode)
        status = self._status()
        self.assertEqual("failed", status["state"])
        self.assertIn("review patch is empty", status["supervisor_error"])

    def test_oversized_patch_fails_closed(self) -> None:
        self.patch.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
        completed = self._review()
        self.assertEqual(1, completed.returncode)
        self.assertIn("exceeds", self._status()["supervisor_error"])

    def test_oversized_request_fails_closed(self) -> None:
        completed = self._review(request=b"x" * (512 * 1024 + 1))
        self.assertEqual(1, completed.returncode)
        self.assertIn("review request exceeds", self._status()["supervisor_error"])

    def test_future_codex_version_is_accepted_and_recorded(self) -> None:
        completed = self._review(environment={"FAKE_CODEX_VERSION": "codex-cli 9.9.9"})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("codex-cli 9.9.9", self._status()["preflight"]["codex_version"])

    def test_upgraded_codex_version_is_accepted_and_recorded(self) -> None:
        completed = self._review(environment={"FAKE_CODEX_VERSION": "codex-cli 0.153.2"})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("codex-cli 0.153.2", self._status()["preflight"]["codex_version"])

    def test_oversized_probe_output_fails_closed(self) -> None:
        completed = self._review(environment={"FAKE_CODEX_VERSION_BYTES": str(512 * 1024 + 1)})
        self.assertEqual(1, completed.returncode)
        self.assertIn("exceeds", self._status()["supervisor_error"])

    def test_wrapper_version_is_recorded_without_a_release_pin(self) -> None:
        completed = self._review(environment={"FAKE_WRAPPER_VERSION": "codeagent-wrapper version 99.0.0"})
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("codeagent-wrapper version 99.0.0", self._status()["preflight"]["wrapper_version"])

    def test_missing_codex_leaf_flag_fails_closed(self) -> None:
        completed = self._review(environment={"FAKE_CODEX_VERSION": "codex-cli 9.9.9", "FAKE_CODEX_HELP": "--ignore-user-config --disable --sandbox --cd"})
        self.assertEqual(1, completed.returncode)
        self.assertIn("--skip-git-repo-check", self._status()["supervisor_error"])
        self.assertFalse((self.sync / "codex.started").exists())
        self.assertFalse((self.sync / "claude.started").exists())

    def test_partial_backend_launch_failure_is_terminal(self) -> None:
        completed = self._review(environment={"FAKE_DELETE_AFTER_VERSION": "1"})
        self.assertEqual(1, completed.returncode)
        status = self._status()
        self.assertEqual("failed", status["state"])
        self.assertIn("FileNotFoundError", status["supervisor_error"])

    def test_default_git_review_refuses_untracked_files(self) -> None:
        repository = self._git_repository()
        (repository / "untracked.txt").write_text("not represented by git diff\n", encoding="utf-8")
        completed = self._git_review(repository)
        self.assertEqual(1, completed.returncode)
        self.assertIn("default review would omit 1 untracked file", self._status()["supervisor_error"])

    def test_base_git_review_uses_merge_base_and_succeeds(self) -> None:
        repository = self._git_repository()
        (repository / "tracked.txt").write_text("base\nchanged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-qm", "change"], check=True)
        completed = self._git_review(repository, extra_args=["--base", "HEAD~1"])
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        status = self._status()
        self.assertEqual("succeeded", status["state"])
        self.assertEqual("base", status["source"]["mode"])
        self.assertEqual("HEAD~1", status["source"]["base"])

    def test_base_git_review_refuses_dirty_worktree(self) -> None:
        repository = self._git_repository()
        (repository / "tracked.txt").write_text("base\nunstaged\n", encoding="utf-8")
        completed = self._git_review(repository, extra_args=["--base", "HEAD"])
        self.assertEqual(1, completed.returncode)
        self.assertIn("requires a clean worktree", self._status()["supervisor_error"])

    def test_nonterminating_stdin_obeys_global_deadline(self) -> None:
        env = os.environ.copy()
        env["TEST_SYNC_DIR"] = str(self.sync)
        env["CCG_SUPERVISOR_TEST_MODE"] = "1"
        process = subprocess.Popen(
            [
                sys.executable,
                str(SUPERVISOR),
                "--root",
                str(self.run_root),
                "review",
                "--workdir",
                str(self.workdir),
                "--diff-file",
                str(self.patch),
                "--codex-cli",
                str(self.codex),
                "--wrapper",
                str(self.wrapper),
                "--timeout-seconds",
                "1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.assertEqual(124, process.wait(timeout=4))
        if process.stdin:
            process.stdin.close()
        if process.stdout:
            process.stdout.read()
            process.stdout.close()
        if process.stderr:
            process.stderr.read()
            process.stderr.close()
        self.assertEqual("timed_out", self._status()["state"])

    def test_legacy_run_command_still_completes(self) -> None:
        env = os.environ.copy()
        env["FAKE_SINGLE_RUN"] = "1"
        completed = subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "--root",
                str(self.run_root),
                "run",
                "--backend",
                "claude",
                "--workdir",
                str(self.workdir),
                "--wrapper",
                str(self.wrapper),
                "--timeout-seconds",
                "2",
            ],
            input=b"legacy smoke",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertEqual("succeeded", self._status()["state"])

    def test_status_and_health_commands_remain_compatible(self) -> None:
        completed = self._review()
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        status_path = next(self.run_root.glob("*/status.json"))
        run_id = status_path.parent.name
        status = subprocess.run(
            [sys.executable, str(SUPERVISOR), "--root", str(self.run_root), "status", run_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, status.returncode, status.stderr.decode())
        self.assertEqual("succeeded", json.loads(status.stdout)["state"])
        health = subprocess.run(
            [sys.executable, str(SUPERVISOR), "--root", str(self.run_root), "health"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, health.returncode, health.stderr.decode())
        self.assertEqual(1, json.loads(health.stdout)["terminal_runs"])

    def test_custom_wrapper_is_allowed_for_portable_installations(self) -> None:
        env = os.environ.copy()
        env["TEST_SYNC_DIR"] = str(self.sync)
        completed = subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "--root",
                str(self.run_root),
                "review",
                "--workdir",
                str(self.workdir),
                "--diff-file",
                str(self.patch),
                "--codex-cli",
                str(self.codex),
                "--wrapper",
                str(self.wrapper),
            ],
            input=b"review",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode())

    def test_cleanup_tolerates_bad_status_and_removes_stale_incomplete_run(self) -> None:
        malformed = self.run_root / "11111111-1111-1111-1111-111111111111"
        malformed.mkdir(parents=True)
        (malformed / "status.json").write_text('{"state":"failed","finished_at_epoch":null}', encoding="utf-8")
        stale = self.run_root / "22222222-2222-2222-2222-222222222222"
        stale.mkdir()
        (stale / "running.json").write_text('{"state":"running"}', encoding="utf-8")
        (stale / "run.lock").touch()
        old = time.time() - 8 * 24 * 60 * 60
        os.utime(stale, (old, old))
        completed = subprocess.run(
            [sys.executable, str(SUPERVISOR), "--root", str(self.run_root), "cleanup"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode())
        self.assertFalse(stale.exists())

    def test_terminal_candidate_scan_is_bounded(self) -> None:
        for index in range(3):
            directory = self.run_root / f"00000000-0000-0000-0000-{index:012d}"
            directory.mkdir(parents=True)
            (directory / "run.lock").touch()
            (directory / "status.json").write_text(json.dumps({"state": "succeeded", "finished_at_epoch": index + 1}), encoding="utf-8")
        candidates, count, _ = supervisor.terminal_candidates(self.run_root, supervisor.Policy(scan_limit=2))
        self.assertEqual(2, count)
        self.assertEqual(2, len(candidates))

    def test_root_symlink_is_refused_before_any_run_write(self) -> None:
        target = self.root / "target"
        target.mkdir()
        link = self.root / "runs-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "must not be a symlink"):
            supervisor.ensure_root(link)
        self.assertEqual([], list(target.iterdir()))

    def test_nonterminating_run_stdin_obeys_deadline(self) -> None:
        process = subprocess.Popen(
            [sys.executable, str(SUPERVISOR), "--root", str(self.run_root), "run", "--backend", "claude", "--workdir", str(self.workdir), "--wrapper", str(self.wrapper), "--timeout-seconds", "1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(124, process.wait(timeout=4))
        if process.stdin:
            process.stdin.close()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()

    def test_terminal_candidate_is_rechecked_while_locked_before_removal(self) -> None:
        directory = self.run_root / "00000000-0000-0000-0000-000000000001"
        directory.mkdir(parents=True)
        (directory / "run.lock").touch()
        (directory / "status.json").write_text(json.dumps({"state": "succeeded", "finished_at_epoch": 1}), encoding="utf-8")
        candidate = {"path": directory}
        lock = supervisor.lock_file(directory / "run.lock", blocking=True)
        assert lock is not None
        try:
            self.assertFalse(supervisor.remove_terminal_candidate(self.run_root, candidate, time.time(), supervisor.Policy(), False))
        finally:
            supervisor.unlock_file(lock)
        self.assertTrue(directory.exists())

    def test_terminal_candidate_uses_status_mtime_when_timestamp_is_zero(self) -> None:
        directory = self.run_root / "00000000-0000-0000-0000-000000000002"
        directory.mkdir(parents=True)
        (directory / "run.lock").touch()
        (directory / "status.json").write_text(json.dumps({"state": "succeeded", "finished_at_epoch": 0}), encoding="utf-8")
        candidate = {"path": directory}
        self.assertFalse(supervisor.remove_terminal_candidate(self.run_root, candidate, time.time(), supervisor.Policy(), True))
        self.assertTrue(directory.exists())

    def test_global_recursion_guards(self) -> None:
        wrapper_env = os.environ.copy()
        wrapper_env["CODEAGENT_WRAPPER_DEPTH"] = "1"
        wrapper = subprocess.run([str(self.wrapper), "--version"], env=wrapper_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(125, wrapper.returncode)
        leaf_env = os.environ.copy()
        leaf_env["CCG_LEAF_REVIEW"] = "1"
        supervisor = subprocess.run(
            [sys.executable, str(SUPERVISOR), "--root", str(self.run_root), "review", "--workdir", str(self.workdir), "--diff-file", str(self.patch)],
            input=b"review",
            env=leaf_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(126, supervisor.returncode)
        run = subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "--root",
                str(self.run_root),
                "run",
                "--backend",
                "claude",
                "--workdir",
                str(self.workdir),
            ],
            input=b"nested run",
            env=leaf_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(126, run.returncode)


if __name__ == "__main__":
    unittest.main()
