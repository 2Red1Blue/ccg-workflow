from __future__ import annotations

import json
import multiprocessing
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ccg_model_compat import (CompatibilityConflict, CompatibilityError,
                              CompatibilityStore, default_config_path, validate_config)


def _concurrent_writer(path, revision, provider, ready, result):
    """Wait at a barrier so independent processes contend on the same revision."""
    ready.wait(5)
    try:
        CompatibilityStore(path).write({"version": 1, "rules": [{
            "provider_id": provider, "model": "m", "omit_disabled_thinking": True,
        }]}, revision)
        result.put("saved")
    except CompatibilityConflict:
        result.put("conflict")


class CompatibilityStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.path = self.root / "policy.json"
        self.store = CompatibilityStore(self.path)
        self.fixture = json.loads((Path(__file__).parent / "fixtures/model-compatibility.json").read_text())

    def test_fixture_schema_and_missing_config_have_stable_revision(self):
        self.assertEqual(self.fixture, validate_config(self.fixture))
        first = self.store.read()
        self.assertEqual({"version": 1, "rules": []}, first["config"])
        self.assertEqual(64, len(first["revision"]))
        self.assertEqual(str(self.path), first["path"])
        saved = self.store.write(self.fixture, first["revision"])
        self.assertEqual(self.fixture, saved["config"])
        self.assertEqual("88d71177b78c0b62f2b96a89683fd8254bf182e40edcfd267b97b737caeab035", saved["revision"])
        self.assertEqual(saved, self.store.read())
        self.assertEqual(0o600, stat.S_IMODE(self.path.stat().st_mode))

    def test_strict_schema_rejects_corruption_and_duplicates(self):
        invalid = [None, {}, {"version": True, "rules": []}, {"version": 2, "rules": []},
                   {"version": 1, "rules": {}, "extra": 1},
                   {"version": 1, "rules": [{"provider_id":"x", "model":"m", "omit_disabled_thinking":1}]},
                   {"version": 1, "rules": [{"provider_id":"x\nm", "model":"m", "omit_disabled_thinking":True}]},
                   {"version": 1, "rules": [{"provider_id":"x", "model":"m", "omit_disabled_thinking":True}, {"provider_id":"x", "model":"m", "omit_disabled_thinking":False}]}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(CompatibilityError):
                validate_config(value)
        self.path.write_text("{bad")
        os.chmod(self.path, 0o600)
        with self.assertRaises(CompatibilityError):
            self.store.read()

    def test_deep_malformed_json_is_a_compatibility_error(self):
        self.path.write_text("[" * 2_000 + "]" * 2_000)
        os.chmod(self.path, 0o600)
        with self.assertRaises(CompatibilityError):
            self.store.read()

    def test_stale_and_concurrent_writers_conflict(self):
        first = self.store.read()
        if os.name == "nt":
            self.skipTest("the store requires POSIX flock")
        context = multiprocessing.get_context("fork")
        ready, result = context.Event(), context.Queue()
        processes = [context.Process(target=_concurrent_writer,
                                     args=(str(self.path), first["revision"], provider, ready, result))
                     for provider in ("one", "two")]
        for process in processes:
            process.start()
        ready.set()
        for process in processes:
            process.join(5)
            self.assertEqual(0, process.exitcode)
        results = [result.get(timeout=1) for _ in processes]
        self.assertCountEqual(["saved", "conflict"], results)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_rejects_symlink_fifo_and_unsafe_permissions(self):
        target = self.root / "target.json"
        target.write_text('{"version":1,"rules":[]}')
        os.chmod(target, 0o600)
        self.path.symlink_to(target)
        with self.assertRaises(CompatibilityError): self.store.read()
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(CompatibilityError): self.store.read()
        self.path.unlink()
        self.path.write_text('{"version":1,"rules":[]}')
        os.chmod(self.path, 0o400)
        with self.assertRaises(CompatibilityError): self.store.read()

    def test_oversized_canonical_config_never_replaces_saved_config(self):
        first = self.store.read()
        saved = self.store.write(self.fixture, first["revision"])
        rules = [{"provider_id": ("p" * 252) + f"{index:04d}", "model": "m" * 256,
                  "omit_disabled_thinking": True} for index in range(128)]
        with self.assertRaises(CompatibilityError):
            self.store.write({"version": 1, "rules": rules}, saved["revision"])
        self.assertEqual(self.fixture, self.store.read()["config"])

    def test_default_override_must_be_absolute(self):
        original = os.environ.get("CCG_MODEL_COMPAT_CONFIG")
        self.addCleanup(lambda: os.environ.__setitem__("CCG_MODEL_COMPAT_CONFIG", original) if original is not None else os.environ.pop("CCG_MODEL_COMPAT_CONFIG", None))
        for value in ("", "relative.json"):
            os.environ["CCG_MODEL_COMPAT_CONFIG"] = value
            with self.subTest(value=value), self.assertRaises(CompatibilityError):
                default_config_path()
        os.environ["CCG_MODEL_COMPAT_CONFIG"] = str(self.path)
        self.assertEqual(self.path, default_config_path())

    def test_final_config_directory_requires_exact_mode_0700(self):
        os.chmod(self.root, 0o500)
        try:
            with self.assertRaises(CompatibilityError):
                self.store.read()
        finally:
            os.chmod(self.root, 0o700)
