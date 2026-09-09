#!/usr/bin/env python3
"""Isolated tests for task_router.py."""

import json
import importlib.util
from unittest import mock
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "ccg_task_router.py"
SPEC = importlib.util.spec_from_file_location("task_router", SCRIPT)
ROUTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROUTER)


class TaskRouterTest(unittest.TestCase):
    def run_router(self, root, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--project-root", str(root)],
            text=True,
            capture_output=True,
        )

    def make_trellis_root(self, root):
        (root / ".trellis" / "scripts").mkdir(parents=True)
        (root / ".trellis" / "workflow.md").write_text("workflow", encoding="utf-8")
        (root / ".trellis" / "scripts" / "task.py").write_text(
            "import argparse, json\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('command'); p.add_argument('title'); p.add_argument('--slug'); a=p.parse_args()\n"
            "d=Path('.trellis/tasks') / ('08-28-' + a.slug); d.mkdir(parents=True)\n"
            "(d/'task.json').write_text(json.dumps({'id': a.slug, 'title': a.title, 'meta': {}}))\n",
            encoding="utf-8",
        )

    def test_resolve_trellis_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_trellis_root(root)
            result = self.run_router(root, "resolve")
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual("trellis", payload["provider"])
            self.assertEqual(str((root / ".trellis" / "tasks").resolve()), payload["tasksDir"])

    def test_create_trellis_task_embeds_ccg_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_trellis_root(root)
            result = self.run_router(root, "create", "--title", "Test", "--slug", "test-task", "--ccg-meta", '{"complexity":"M"}')
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            task = json.loads(Path(payload["taskDir"], "task.json").read_text(encoding="utf-8"))
            self.assertEqual("M", task["meta"]["ccg"]["complexity"])

    def test_create_fallback_ccg_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.run_router(root, "create", "--title", "Fallback", "--slug", "fallback-task")
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual("ccg", payload["provider"])
            self.assertTrue(Path(payload["taskDir"], "requirements.md").is_file())
            self.assertIn(".ccg/tasks/", (root / ".gitignore").read_text(encoding="utf-8"))

    def test_fallback_preserves_existing_ccg_ignore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".gitignore").write_text(".ccg/tasks/\n", encoding="utf-8")
            result = self.run_router(root, "create", "--title", "Fallback", "--slug", "fallback-task")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(".ccg/tasks/\n", (root / ".gitignore").read_text(encoding="utf-8"))

    def test_fallback_appends_without_rewriting_existing_ignore_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = "dist/\nnode_modules/\nbuild/\n*.log"
            (root / ".gitignore").write_text(original, encoding="utf-8")
            result = self.run_router(root, "create", "--title", "Fallback", "--slug", "fallback-task")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                original + "\n\n# Local CCG task state\n.ccg/tasks/\n",
                (root / ".gitignore").read_text(encoding="utf-8"),
            )

    def test_fallback_preserves_equivalent_ignore_and_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".gitignore").write_text("# local state\n/.ccg/tasks", encoding="utf-8")
            result = self.run_router(root, "create", "--title", "Fallback", "--slug", "fallback-task")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("# local state\n/.ccg/tasks", (root / ".gitignore").read_text(encoding="utf-8"))

    def test_duplicate_fallback_does_not_change_ignore_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.run_router(root, "create", "--title", "One", "--slug", "same-task")
            before_duplicate = (root / ".gitignore").read_text(encoding="utf-8")
            duplicate = self.run_router(root, "create", "--title", "Two", "--slug", "same-task")
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(2, duplicate.returncode)
            self.assertEqual(before_duplicate, (root / ".gitignore").read_text(encoding="utf-8"))

    def test_concurrent_fallback_creation_preserves_one_ignore_rule(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".gitignore").write_text("# preserve this\n", encoding="utf-8")
            commands = [
                [sys.executable, str(SCRIPT), "create", "--project-root", str(root),
                 "--title", slug, "--slug", slug]
                for slug in ("first-task", "second-task")
            ]
            processes = [subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for command in commands]
            results = [process.communicate() for process in processes]

            self.assertTrue(all(process.returncode == 0 for process in processes), results)
            self.assertTrue((root / ".ccg" / "tasks" / "first-task" / "task.json").is_file())
            self.assertTrue((root / ".ccg" / "tasks" / "second-task" / "task.json").is_file())
            ignore = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("# preserve this", ignore)
            self.assertEqual(1, ignore.count(".ccg/tasks/"))

    def test_workspace_scope_uses_workspace_root(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            project = workspace / "project"
            project.mkdir(parents=True)
            self.make_trellis_root(workspace)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "resolve", "--scope", "workspace",
                 "--workspace-root", str(workspace), "--project-root", str(project)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(str(workspace.resolve()), json.loads(result.stdout)["root"])

    def test_rejects_duplicate_and_unsafe_slug(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.run_router(root, "create", "--title", "One", "--slug", "same-task")
            duplicate = self.run_router(root, "create", "--title", "Two", "--slug", "same-task")
            unsafe = self.run_router(root, "create", "--title", "Unsafe", "--slug", "../escape")
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(2, duplicate.returncode)
            self.assertIn("TASK_EXISTS", duplicate.stderr)
            self.assertEqual(2, unsafe.returncode)
            self.assertIn("INVALID_TASK_SLUG", unsafe.stderr)

    def create_fixture(self, root, provider="ccg", slug="same-task", title="Same"):
        if provider == "trellis":
            self.make_trellis_root(root)
        result = self.run_router(root, "create", "--title", title, "--slug", slug)
        self.assertEqual(0, result.returncode, result.stderr)
        return Path(json.loads(result.stdout)["taskDir"])

    def test_ensure_reuses_exact_identity_and_title_without_writes(self):
        for provider in ("ccg", "trellis"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                task_dir = self.create_fixture(root, provider)
                before = (task_dir / "task.json").read_bytes()
                result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task", "--ccg-meta", '{"risk":"high"}')
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(str(task_dir), json.loads(result.stdout)["taskDir"])
                self.assertEqual("same-task", json.loads(result.stdout)["taskId"])
                self.assertFalse(json.loads(result.stdout)["created"])
                self.assertEqual(before, (task_dir / "task.json").read_bytes())
                conflict = self.run_router(root, "ensure", "--title", "Changed", "--slug", "same-task")
                self.assertEqual(2, conflict.returncode)
                self.assertIn("TASK_TITLE_CONFLICT", conflict.stderr)
                self.assertEqual(before, (task_dir / "task.json").read_bytes())

    def test_ensure_creates_distinct_explicit_ids_even_with_same_title(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.create_fixture(root, "trellis", "not-foo")
            result = self.run_router(root, "ensure", "--title", "Same", "--slug", "foo")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("foo", json.loads(Path(json.loads(result.stdout)["taskDir"], "task.json").read_text())["id"])
            self.assertEqual(2, len(list((root / ".trellis/tasks").glob("*/task.json"))))

    def test_resumed_ensure_does_not_reopen_completed_task_or_reset_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root)
            done = self.run_router(root, "update", "--task-dir", str(task_dir),
                                   "--phase", "completed", "--next-action", "Delivered")
            self.assertEqual(0, done.returncode, done.stderr)
            before = (task_dir / "task.json").read_bytes()
            replay = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task",
                                     "--ccg-meta", '{"risk":"high","branch":"other","currentPhase":"analysis"}')
            self.assertEqual(0, replay.returncode, replay.stderr)
            self.assertFalse(json.loads(replay.stdout)["created"])
            self.assertEqual(before, (task_dir / "task.json").read_bytes())

    def test_concurrent_ensure_returns_one_task_for_both_providers(self):
        for provider in ("ccg", "trellis"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                if provider == "trellis":
                    self.make_trellis_root(root)
                command = [sys.executable, str(SCRIPT), "ensure", "--project-root", str(root), "--title", "Same", "--slug", "same-task"]
                processes = [subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(8)]
                results = [process.communicate(timeout=30) for process in processes]
                self.assertTrue(all(process.returncode == 0 for process in processes), results)
                self.assertEqual(1, len({json.loads(stdout)["taskDir"] for stdout, _ in results}))
                self.assertEqual(1, sum(json.loads(stdout)["created"] for stdout, _ in results))
                self.assertEqual(1, len(list((root / ("." + provider) / "tasks").glob("*/task.json"))))

    def test_concurrent_create_has_one_winner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_trellis_root(root)
            command = [sys.executable, str(SCRIPT), "create", "--project-root", str(root), "--title", "Same", "--slug", "same-task"]
            processes = [subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
            results = [process.communicate(timeout=30) for process in processes]
            self.assertEqual([0, 2, 2, 2], sorted(process.returncode for process in processes), results)

    def test_partial_task_is_never_repaired(self):
        for provider in ("ccg", "trellis"):
            for invalid in (None, "{", '{"id":"different","title":"Same"}'):
                with self.subTest(provider=provider, invalid=invalid), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    if provider == "trellis":
                        self.make_trellis_root(root)
                    task_dir = root / ("." + provider) / "tasks" / ("08-28-same-task" if provider == "trellis" else "same-task")
                    task_dir.mkdir(parents=True)
                    if invalid is not None:
                        (task_dir / "task.json").write_text(invalid)
                    before = {path.name: path.read_bytes() for path in task_dir.iterdir()}
                    result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
                    self.assertEqual(2, result.returncode)
                    self.assertEqual(before, {path.name: path.read_bytes() for path in task_dir.iterdir()})

    def test_duplicate_ids_rejected_and_doctor_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root, "trellis")
            duplicate = task_dir.parent / "08-29-same-task"
            duplicate.mkdir()
            (duplicate / "task.json").write_bytes((task_dir / "task.json").read_bytes())
            result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
            self.assertEqual(2, result.returncode)
            self.assertIn("DUPLICATE_TASK_ID", result.stderr)
            report = self.run_router(root, "doctor")
            self.assertEqual(2, report.returncode)
            self.assertIn("DUPLICATE_TASK_ID", {issue["code"] for issue in json.loads(report.stdout)["issues"]})

    def test_write_maps_provider_documents_and_does_not_change_metadata(self):
        for provider in ("ccg", "trellis"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                task_dir = self.create_fixture(root, provider)
                before = (task_dir / "task.json").read_bytes()
                source = root / "source.md"
                source.write_text("# 测试\n\nContent\n")
                for document, filename in ROUTER.DOCUMENTS[provider].items():
                    result = subprocess.run([sys.executable, str(SCRIPT), "write", "--task-dir", str(task_dir), "--document", document, "--content-file", str(source)], text=True, capture_output=True)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(source.read_bytes(), (task_dir / filename).read_bytes())
                self.assertEqual(before, (task_dir / "task.json").read_bytes())
                self.assertFalse(any(path.name.startswith(".") for path in task_dir.iterdir()))

    def test_update_preserves_native_state_and_unrelated_metadata(self):
        for provider in ("ccg", "trellis"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                task_dir = self.create_fixture(root, provider)
                path = task_dir / "task.json"
                data = json.loads(path.read_text())
                data.update(status="in_progress", extra={"keep": True})
                if provider == "trellis":
                    data["currentPhase"] = "native"
                    data["meta"]["other"] = {"preserve": 1}
                path.write_text(json.dumps(data))
                result = self.run_router(root, "update", "--task-dir", str(task_dir), "--phase", "completed", "--next-action", "Wait for explicit archive")
                self.assertEqual(0, result.returncode, result.stderr)
                actual = json.loads(path.read_text())
                target = actual["meta"]["ccg"] if provider == "trellis" else actual
                self.assertEqual("completed", target["currentPhase"])
                self.assertEqual("Wait for explicit archive", target["nextAction"])
                expected = data["meta"]["ccg"] if provider == "trellis" else data
                expected.update(currentPhase="completed", nextAction="Wait for explicit archive")
                if provider == "ccg":
                    data["status"] = "completed"
                self.assertEqual(data, actual)
                self.assertEqual("completed" if provider == "ccg" else "in_progress", actual["status"])

    def test_write_and_update_missing_task_do_not_create_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.md"
            source.write_text("contents")
            task_dir = root / ".ccg/tasks/missing"
            for args in (("write", "--document", "plan", "--content-file", str(source)), ("update", "--phase", "analysis", "--next-action", "Read")):
                result = self.run_router(root, *args, "--task-dir", str(task_dir))
                self.assertEqual(2, result.returncode)
                self.assertEqual([source], list(root.iterdir()))

    def test_write_rejects_wrong_owner_id_archive_and_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root, "trellis")
            source = root / "input.md"
            source.write_text("contents")
            for invalid_dir in (root / ".ccg/tasks/same-task", task_dir.parent / "archive/same-task", root / "other"):
                invalid_dir.mkdir(parents=True)
                (invalid_dir / "task.json").write_text('{"id":"same-task","title":"Same"}')
                result = self.run_router(root, "write", "--task-dir", str(invalid_dir), "--document", "plan", "--content-file", str(source))
                self.assertEqual(2, result.returncode)
                self.assertFalse((invalid_dir / "implement.md").exists())
            (root / ".ccg/tasks/same-task/task.json").unlink()
            (root / ".ccg/tasks/same-task").rmdir()
            (task_dir / "task.json").write_text('{"id":"different","title":"Same"}')
            result = self.run_router(root, "update", "--task-dir", str(task_dir), "--phase", "review", "--next-action", "Review")
            self.assertEqual(2, result.returncode)
            self.assertIn("TASK_ID_PATH_MISMATCH", result.stderr)

    def test_symlink_task_root_task_and_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root, external = Path(temp), Path(outside)
            (root / ".ccg").mkdir()
            (root / ".ccg/tasks").symlink_to(external, target_is_directory=True)
            result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
            self.assertEqual(2, result.returncode)
            self.assertEqual([], list(external.iterdir()))
            (root / ".ccg/tasks").unlink()
            task_dir = self.create_fixture(root)
            external_file = external / "untouched.md"
            external_file.write_text("original")
            source = root / "source.md"
            source.write_text("replacement")
            (task_dir / "plan.md").symlink_to(external_file)
            result = self.run_router(root, "write", "--task-dir", str(task_dir), "--document", "plan", "--content-file", str(source))
            self.assertEqual(2, result.returncode)
            self.assertEqual("original", external_file.read_text())
            alias = task_dir.parent / "alias"
            alias.symlink_to(task_dir, target_is_directory=True)
            result = self.run_router(root, "update", "--task-dir", str(alias), "--phase", "review", "--next-action", "Read")
            self.assertEqual(2, result.returncode)

    def test_symlink_metadata_and_ignore_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "existing.txt"
            target.write_text("original")
            (root / ".gitignore").symlink_to(target)
            result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
            self.assertEqual(2, result.returncode)
            self.assertEqual("original", target.read_text())
            (root / ".gitignore").unlink()
            task_dir = self.create_fixture(root)
            metadata = task_dir / "task.json"
            target.write_bytes(metadata.read_bytes())
            metadata.unlink()
            metadata.symlink_to(target)
            result = self.run_router(root, "update", "--task-dir", str(task_dir), "--phase", "review", "--next-action", "Read")
            self.assertEqual(2, result.returncode)

    def test_bounded_and_utf8_writes_preserve_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root)
            destination = task_dir / "plan.md"
            destination.write_text("original")
            source = root / "source.md"
            for content in (b"x" * (ROUTER.MAX_FILE_BYTES + 1), b"\xff"):
                source.write_bytes(content)
                result = self.run_router(root, "write", "--task-dir", str(task_dir), "--document", "plan", "--content-file", str(source))
                self.assertEqual(2, result.returncode)
                self.assertEqual("original", destination.read_text())

    def test_failed_atomic_replace_preserves_destination_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root)
            destination = task_dir / "plan.md"
            destination.write_text("original")
            source = root / "source.md"
            source.write_text("replacement")
            with mock.patch.object(ROUTER.os, "replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    ROUTER.modify_task(str(root), str(task_dir), document="plan", content_file=str(source))
            self.assertEqual("original", destination.read_text())
            self.assertFalse(any(path.name.startswith(".") for path in task_dir.iterdir()))

    def test_doctor_is_read_only_reports_orphans_and_ignores_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root, "trellis")
            (task_dir.parent / "incomplete").mkdir()
            legacy = root / ".ccg/tasks/same-task"
            legacy.mkdir(parents=True)
            (legacy / "task.json").write_bytes((task_dir / "task.json").read_bytes())
            (task_dir.parent / "archive/broken").mkdir(parents=True)
            before = {str(path): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None) for path in root.rglob("*")}
            result = self.run_router(root, "doctor")
            self.assertEqual(2, result.returncode)
            issues = json.loads(result.stdout)["issues"]
            self.assertEqual({"INCOMPLETE_TASK", "ORPHAN_TASK", "DUPLICATE_TASK_ID"}, {issue["code"] for issue in issues})
            self.assertFalse(any("archive" in issue.get("path", "") for issue in issues))
            after = {str(path): (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None) for path in root.rglob("*")}
            self.assertEqual(before, after)
            conflict = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
            self.assertEqual(2, conflict.returncode)
            self.assertIn("LEGACY_TASK_EXISTS", conflict.stderr)

    def test_doctor_clean_empty_root_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.run_router(root, "doctor")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])
            self.assertEqual([], list(root.iterdir()))

    def test_public_validation_is_read_only_and_rejects_wrong_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root)
            before = (task_dir / "task.json").read_bytes()
            receipt = ROUTER.validate_task(str(task_dir))
            self.assertEqual("same-task", receipt["taskId"])
            self.assertEqual("ccg", receipt["provider"])
            self.assertEqual(before, (task_dir / "task.json").read_bytes())
            self.make_trellis_root(root)
            with self.assertRaises(ROUTER.TaskRouterError):
                ROUTER.validate_task(str(task_dir))

    def test_update_invalid_meta_and_phase_leave_json_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root, "trellis")
            path = task_dir / "task.json"
            for meta in (None, [], {"ccg": []}, {"ccg": None}):
                data = json.loads(path.read_text())
                data["meta"] = meta
                path.write_text(json.dumps(data))
                before = path.read_bytes()
                result = self.run_router(root, "update", "--task-dir", str(task_dir), "--phase", "review", "--next-action", "Read")
                self.assertEqual(2, result.returncode)
                self.assertEqual(before, path.read_bytes())
            result = self.run_router(root, "update", "--task-dir", str(task_dir), "--phase", "bogus", "--next-action", "Read")
            self.assertEqual(2, result.returncode)
            self.assertEqual(before, path.read_bytes())

    def test_trellis_metadata_conflict_never_deletes_native_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_trellis_root(root)
            script = root / ".trellis/scripts/task.py"
            script.write_text(script.read_text().replace("'meta': {}", "'meta': {'ccg': {'keep': 1}}"))
            result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
            self.assertEqual(2, result.returncode)
            self.assertIn("CCG_META_CONFLICT", result.stderr)
            path = root / ".trellis/tasks/08-28-same-task/task.json"
            self.assertEqual({"keep": 1}, json.loads(path.read_text())["meta"]["ccg"])

    def test_root_alias_is_normalized_without_accepting_nested_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            container = Path(temp)
            root = container / "project"
            root.mkdir()
            task_dir = self.create_fixture(root)
            alias = container / "project-alias"
            alias.symlink_to(root, target_is_directory=True)
            raw_task = alias / ".ccg/tasks/same-task"
            receipt = ROUTER.validate_task(str(raw_task), str(root.resolve()))
            self.assertEqual(str(task_dir), receipt["taskDir"])
            self.assertEqual(receipt, ROUTER.validate_task(str(raw_task)))
            result = self.run_router(root.resolve(), "update", "--task-dir", str(raw_task), "--phase", "review", "--next-action", "Read")
            self.assertEqual(0, result.returncode, result.stderr)
            nested = root / "nested-root-alias"
            nested.symlink_to(root, target_is_directory=True)
            for invalid_path in (nested / ".ccg/tasks/same-task", alias / "nested-root-alias/.ccg/tasks/same-task"):
                with self.assertRaises(ROUTER.TaskRouterError):
                    ROUTER.validate_task(str(invalid_path), str(root.resolve()))
            (task_dir.parent / "task-alias").symlink_to(task_dir, target_is_directory=True)
            with self.assertRaises(ROUTER.TaskRouterError):
                ROUTER.validate_task(str(alias / ".ccg/tasks/task-alias"), str(root.resolve()))

    def test_raw_temporary_root_path_matches_canonical_workdir(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root)
            raw_task = root / ".ccg/tasks/same-task"
            receipt = ROUTER.validate_task(str(raw_task), str(root.resolve()))
            self.assertEqual(str(task_dir), receipt["taskDir"])

    def test_ccg_update_keeps_status_consistent_with_every_phase(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root)
            for phase in ROUTER.PHASES:
                result = self.run_router(root, "update", "--task-dir", str(task_dir), "--phase", phase, "--next-action", "Continue")
                self.assertEqual(0, result.returncode, result.stderr)
                data = json.loads((task_dir / "task.json").read_text())
                self.assertEqual(phase, data["currentPhase"])
                self.assertEqual("completed" if phase == "completed" else "in_progress", data["status"])

    def test_trellis_create_timeout_is_bounded_and_releases_root_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_trellis_root(root)
            native_script = root / ".trellis/scripts/task.py"
            native_script.write_text("import time\ntime.sleep(2)\n" + native_script.read_text())
            with mock.patch.object(ROUTER, "TRELLIS_CREATE_TIMEOUT_SECONDS", 0.05):
                with self.assertRaisesRegex(ROUTER.TaskRouterError, "TRELLIS_TASK_CREATE_TIMEOUT.*explicit recovery"):
                    ROUTER.create_task(str(root), "Same", "same-task", {}, ensure=True)
            marker = root / ".trellis/tasks/.router-create-same-task.json"
            self.assertTrue(marker.is_file())
            result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
            self.assertEqual(2, result.returncode)
            self.assertIn("INCOMPLETE_TASK_CREATION", result.stderr)
            self.assertTrue(marker.is_file())
            report = self.run_router(root, "doctor")
            self.assertEqual(2, report.returncode)
            self.assertIn("INCOMPLETE_TASK_CREATION", {issue["code"] for issue in json.loads(report.stdout)["issues"]})
            # A fresh process completes another mutation after the timeout.
            native_script.write_text(native_script.read_text().replace("import time\ntime.sleep(2)\n", ""))
            result = subprocess.run([sys.executable, str(SCRIPT), "ensure", "--project-root", str(root), "--title", "Other", "--slug", "other"], text=True, capture_output=True, timeout=5)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_failed_trellis_metadata_embedding_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_trellis_root(root)
            original_write = ROUTER._atomic_write_json

            def fail_metadata(path, data):
                if path.name == "task.json":
                    raise OSError("simulated disk failure")
                return original_write(path, data)

            with mock.patch.object(ROUTER, "_atomic_write_json", side_effect=fail_metadata):
                with self.assertRaisesRegex(ROUTER.TaskRouterError, "TRELLIS_TASK_METADATA_WRITE_FAILED"):
                    ROUTER.create_task(str(root), "Same", "same-task", {}, ensure=True)
            task_dir = root / ".trellis/tasks/08-28-same-task"
            before = (task_dir / "task.json").read_bytes()
            self.assertEqual({}, json.loads(before)["meta"])
            result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
            self.assertEqual(2, result.returncode)
            self.assertIn("INCOMPLETE_TASK_CREATION", result.stderr)
            self.assertEqual(before, (task_dir / "task.json").read_bytes())
            report = self.run_router(root, "doctor")
            self.assertEqual(2, report.returncode)
            self.assertIn("INCOMPLETE_TASK_CREATION", {issue["code"] for issue in json.loads(report.stdout)["issues"]})

    def test_native_tasks_without_ccg_meta_reuse_unchanged_and_are_doctor_clean(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self.create_fixture(root, "trellis")
            metadata = task_dir / "task.json"
            for meta in ({}, {"ccg": None}, {"ccg": []}):
                data = json.loads(metadata.read_text())
                data["meta"] = meta
                metadata.write_text(json.dumps(data))
                result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertFalse(json.loads(result.stdout)["created"])
                self.assertEqual(str(task_dir.resolve()), json.loads(result.stdout)["taskDir"])
                self.assertEqual(data, json.loads(metadata.read_text()))
            data["meta"] = {}
            metadata.write_text(json.dumps(data))
            report = self.run_router(root, "doctor")
            self.assertEqual(0, report.returncode, report.stdout)

    def test_new_native_task_wrong_title_remains_marked_for_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_trellis_root(root)
            script = root / ".trellis/scripts/task.py"
            script.write_text(script.read_text().replace("'title': a.title", "'title': 'Wrong title'"))
            result = self.run_router(root, "ensure", "--title", "Same", "--slug", "same-task")
            self.assertEqual(2, result.returncode)
            self.assertIn("TASK_TITLE_CONFLICT", result.stderr)
            self.assertTrue((root / ".trellis/tasks/08-28-same-task/task.json").is_file())
            self.assertTrue((root / ".trellis/tasks/.router-create-same-task.json").is_file())


if __name__ == "__main__":
    unittest.main()
