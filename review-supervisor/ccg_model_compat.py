"""Private, revisioned storage for exact outbound-model compatibility rules."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


MAX_FILE_BYTES = 64 * 1024
MAX_RULES = 128
_CONTROL = frozenset(chr(value) for value in range(0x20)) | {"\x7f"}


class CompatibilityError(ValueError):
    """Raised when compatibility policy storage is invalid or unsafe."""


class CompatibilityConflict(CompatibilityError):
    """Raised when a write's expected revision is no longer current."""


def default_config_path() -> Path:
    """Return the configured absolute policy path, or CCG's private default."""
    configured = os.environ.get("CCG_MODEL_COMPAT_CONFIG")
    if configured is not None:
        if not configured:
            raise CompatibilityError("CCG_MODEL_COMPAT_CONFIG must not be empty")
        path = Path(configured)
        if not path.is_absolute():
            raise CompatibilityError("CCG_MODEL_COMPAT_CONFIG must be an absolute path")
        return path
    return Path.home() / ".claude" / ".ccg" / "model-compatibility" / "config.json"


def _empty_config() -> dict[str, Any]:
    return {"version": 1, "rules": []}


def _canonical(config: dict[str, Any]) -> bytes:
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _revision(config: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(config)).hexdigest()


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise CompatibilityError(f"{name} must be a string")
    if value != value.strip() or not value or len(value) > 256 or any(char in _CONTROL for char in value):
        raise CompatibilityError(f"{name} must be a trimmed, nonempty, at-most-256-character string without control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CompatibilityError(f"{name} must contain valid Unicode scalar characters") from exc
    return value


def validate_config(value: Any) -> dict[str, Any]:
    """Validate and normalize the frozen version-one compatibility schema."""
    if not isinstance(value, dict) or set(value) != {"version", "rules"}:
        raise CompatibilityError("config must contain exactly version and rules")
    if type(value["version"]) is not int or value["version"] != 1:
        raise CompatibilityError("version must be exactly integer 1")
    rules = value["rules"]
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise CompatibilityError(f"rules must be an array of at most {MAX_RULES} entries")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or set(rule) != {"provider_id", "model", "omit_disabled_thinking"}:
            raise CompatibilityError(f"rule {index} must contain exactly provider_id, model, omit_disabled_thinking")
        provider_id = _string(rule["provider_id"], f"rule {index} provider_id")
        model = _string(rule["model"], f"rule {index} model")
        if type(rule["omit_disabled_thinking"]) is not bool:
            raise CompatibilityError(f"rule {index} omit_disabled_thinking must be a boolean")
        key = (provider_id, model)
        if key in seen:
            raise CompatibilityError(f"rule {index} duplicates provider_id/model")
        seen.add(key)
        normalized.append({"provider_id": provider_id, "model": model,
                           "omit_disabled_thinking": rule["omit_disabled_thinking"]})
    return {"version": 1, "rules": normalized}


def _mode_is_private(mode: int) -> bool:
    return mode & 0o077 == 0


def _check_owner_and_mode(metadata: os.stat_result, label: str) -> None:
    if metadata.st_uid != os.getuid():
        raise CompatibilityError(f"{label} must be owned by the current user")
    if label == "compatibility config" and stat.S_IMODE(metadata.st_mode) != 0o600:
        raise CompatibilityError("compatibility config must have mode 0600")
    if not _mode_is_private(stat.S_IMODE(metadata.st_mode)):
        raise CompatibilityError(f"{label} must not be accessible by group or others")


def _check_directory(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise CompatibilityError(f"cannot inspect config directory {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise CompatibilityError(f"config directory must be a real directory: {path}")
    _check_owner_and_mode(metadata, "config directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise CompatibilityError("config directory must have mode 0700")


class CompatibilityStore:
    """Read and atomically update an owner-private compatibility policy file."""

    def __init__(self, path: Path | str | None = None):
        raw_path = default_config_path() if path is None else Path(path).expanduser()
        if not raw_path.is_absolute():
            raise CompatibilityError("compatibility config path must be absolute")
        self.path = Path(os.path.abspath(raw_path))

    def _ensure_parent(self) -> None:
        parent = self.path.parent
        if parent.exists():
            _check_directory(parent)
            return
        # Only create the final private policy directory; a missing broader path
        # is an operator/configuration error rather than an invitation to create it.
        grandparent = parent.parent
        if not grandparent.exists() or grandparent.is_symlink() or not grandparent.is_dir():
            raise CompatibilityError(f"config parent does not exist: {grandparent}")
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _check_directory(parent)

    def _read_config(self) -> dict[str, Any]:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return _empty_config()
        except OSError as exc:
            raise CompatibilityError(f"cannot open compatibility config: {exc}") from exc
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise CompatibilityError("compatibility config must be a regular file")
            _check_owner_and_mode(before, "compatibility config")
            data = bytearray()
            while len(data) <= MAX_FILE_BYTES:
                chunk = os.read(fd, min(8192, MAX_FILE_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > MAX_FILE_BYTES:
                raise CompatibilityError(f"compatibility config exceeds {MAX_FILE_BYTES} bytes")
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise CompatibilityError("compatibility config changed while being read")
        finally:
            os.close(fd)
        try:
            return validate_config(json.loads(bytes(data).decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise CompatibilityError(f"invalid compatibility config JSON: {exc}") from exc

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._ensure_parent()
        lock_path = self.path.with_name("." + self.path.name + ".lock")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise CompatibilityError(f"cannot open compatibility lock: {exc}") from exc
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise CompatibilityError("compatibility lock must be a regular file")
            _check_owner_and_mode(metadata, "compatibility lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _result(self, config: dict[str, Any]) -> dict[str, Any]:
        return {"config": config, "revision": _revision(config), "path": str(self.path)}

    def read(self) -> dict[str, Any]:
        """Return the validated current config and its canonical SHA-256 revision."""
        self._ensure_parent()
        return self._result(self._read_config())

    def write(self, config: Any, expected_revision: str) -> dict[str, Any]:
        """Save a config only if the caller's revision still matches the file."""
        normalized = validate_config(config)
        content = _canonical(normalized)
        if len(content) > MAX_FILE_BYTES:
            raise CompatibilityError(f"compatibility config exceeds {MAX_FILE_BYTES} bytes")
        if not isinstance(expected_revision, str):
            raise CompatibilityError("expected_revision must be a string")
        with self._lock():
            current = self._read_config()
            if expected_revision != _revision(current):
                raise CompatibilityConflict("compatibility config revision conflict")
            fd, temporary = tempfile.mkstemp(prefix="." + self.path.name + ".", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                current_metadata = None
                try:
                    current_metadata = os.lstat(self.path)
                except FileNotFoundError:
                    pass
                if current_metadata is not None and stat.S_ISLNK(current_metadata.st_mode):
                    raise CompatibilityError("compatibility config must not be a symlink")
                os.replace(temporary, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return self._result(normalized)
