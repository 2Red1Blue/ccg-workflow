#!/usr/bin/env python3
"""Guarded public entry point for the CCG codeagent wrapper.

The original binary lives beside this file as ``codeagent-wrapper.real``.
Every first-level invocation stamps a depth marker into the backend process;
an instruction-induced nested invocation is rejected before a backend starts.
This is a recursion guardrail, not a same-user hostile-process sandbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path


REAL_WRAPPER = Path(__file__).with_name("codeagent-wrapper.real")
RECEIPT = Path(__file__).with_name("codeagent-wrapper.build.json")
DEPTH_ENV = "CODEAGENT_WRAPPER_DEPTH"
RECURSION_EXIT = 125
INSTALL_EXIT = 126


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_real_wrapper() -> str | None:
    try:
        metadata = REAL_WRAPPER.lstat()
    except FileNotFoundError:
        return f"missing preserved wrapper binary: {REAL_WRAPPER}"
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return f"preserved wrapper must be a regular non-symlink file: {REAL_WRAPPER}"
    if metadata.st_uid != os.getuid():
        return f"preserved wrapper has unexpected owner: {REAL_WRAPPER}"
    if not os.access(REAL_WRAPPER, os.X_OK):
        return f"preserved wrapper is not executable: {REAL_WRAPPER}"
    actual = sha256_file(REAL_WRAPPER)
    try:
        expected = json.loads(RECEIPT.read_text(encoding="utf-8"))["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return "missing or invalid Git build receipt; run scripts/install-local-wrapper.py"
    if actual != expected:
        return "wrapper hash differs from the Git build receipt; reinstall from committed source"
    return None


def main() -> int:
    depth = os.environ.get(DEPTH_ENV, "")
    if depth not in ("", "0"):
        print(
            f"[CCG recursion guard] nested codeagent-wrapper invocation blocked ({DEPTH_ENV}={depth})",
            file=sys.stderr,
        )
        return RECURSION_EXIT

    validation_error = validate_real_wrapper()
    if validation_error:
        print(f"[CCG wrapper guard] {validation_error}", file=sys.stderr)
        return INSTALL_EXIT

    environment = os.environ.copy()
    environment[DEPTH_ENV] = "1"
    os.execve(REAL_WRAPPER, [str(REAL_WRAPPER), *sys.argv[1:]], environment)
    raise AssertionError("os.execve returned unexpectedly")


if __name__ == "__main__":
    raise SystemExit(main())

