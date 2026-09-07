#!/usr/bin/env python3
"""Build a clean Git checkout and install its wrapper with rollback and provenance."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("codeagent-wrapper", "codeagent-wrapper.real", "codeagent-wrapper.build.json")


def output(args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def atomic_copy(source, destination, mode):
    fd, name = tempfile.mkstemp(prefix=".wrapper-install-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            with source.open("rb") as content:
                shutil.copyfileobj(content, stream)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=Path.home()/".claude")
    args = parser.parse_args()
    if output(["git", "rev-parse", "--show-toplevel"]) != str(ROOT):
        raise RuntimeError("installer must be inside its Git repository")
    if output(["git", "status", "--porcelain"]):
        raise RuntimeError("commit changes before building an identifiable install")
    commit = output(["git", "rev-parse", "HEAD"])
    install = args.install_dir.expanduser().absolute()
    if install.is_symlink():
        raise RuntimeError("install directory must not be a symlink")
    install.mkdir(parents=True, exist_ok=True)
    bin_dir = install/"bin"
    if bin_dir.is_symlink():
        raise RuntimeError("bin directory must not be a symlink")
    bin_dir.mkdir(exist_ok=True)
    with (bin_dir/".wrapper-install.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name in TARGETS:
            p = bin_dir/name
            if p.is_symlink() or (p.exists() and not p.is_file()):
                raise RuntimeError(f"refusing non-regular target: {name}")
        with tempfile.TemporaryDirectory(prefix="ccg-wrapper-build-") as temporary:
            build = Path(temporary)
            binary = build/"codeagent-wrapper.real"
            subprocess.run(["go", "build", "-trimpath", "-ldflags", f"-s -w -X main.buildCommit={commit}", "-o", str(binary), "."], cwd=ROOT/"codeagent-wrapper", check=True)
            if output(["git", "status", "--porcelain"]) or output(["git", "rev-parse", "HEAD"]) != commit:
                raise RuntimeError("checkout changed during build")
            info = json.loads(output([str(binary), "--build-info"]))
            if info.get("gitCommit") != commit:
                raise RuntimeError("compiled commit identity does not match")
            receipt = {**info, "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                       "guardSha256": hashlib.sha256((ROOT/'review-supervisor/codeagent-wrapper-guard.py').read_bytes()).hexdigest(),
                       "goVersion": output(["go", "version"]), "sourceRoot": str(ROOT),
                       "installedAt": datetime.now(timezone.utc).isoformat()}
            receipt_file = build/"codeagent-wrapper.build.json"
            receipt_file.write_text(json.dumps(receipt, indent=2)+"\n")
            backup_root = install/"backups"
            if backup_root.is_symlink():
                raise RuntimeError("backup directory must not be a symlink")
            backup_root.mkdir(exist_ok=True)
            backup = Path(tempfile.mkdtemp(prefix="wrapper-git-", dir=backup_root))
            for name in TARGETS:
                if (bin_dir/name).exists():
                    shutil.copy2(bin_dir/name, backup/name)
            sources = {"codeagent-wrapper": ROOT/"review-supervisor/codeagent-wrapper-guard.py",
                       "codeagent-wrapper.real": binary, "codeagent-wrapper.build.json": receipt_file}
            try:
                for name in TARGETS:
                    atomic_copy(sources[name], bin_dir/name, 0o600 if name.endswith(".json") else 0o700)
                verified = json.loads(output([str(bin_dir/"codeagent-wrapper"), "--build-info"]))
                if verified != info:
                    raise RuntimeError("installed wrapper identity mismatch")
            except BaseException:
                for name in TARGETS:
                    if (backup/name).exists():
                        atomic_copy(backup/name, bin_dir/name, (backup/name).stat().st_mode & 0o777)
                    else:
                        (bin_dir/name).unlink(missing_ok=True)
                raise
            print(json.dumps({"installed": True, **receipt, "rollbackDirectory": str(backup)}, indent=2))


if __name__ == "__main__":
    main()
