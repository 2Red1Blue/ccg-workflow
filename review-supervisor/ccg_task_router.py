#!/usr/bin/env python3
"""Resolve, ensure, document and inspect single-owner CCG or Trellis tasks."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows lacks POSIX advisory locks.
    fcntl = None


MAX_FILE_BYTES = 2 * 1024 * 1024
TRELLIS_CREATE_TIMEOUT_SECONDS = 30
PHASES = ("analysis", "planning", "implementation", "review", "completed")
DOCUMENTS = {
    "ccg": {"requirements": "requirements.md", "plan": "plan.md", "review": "review.md"},
    "trellis": {"requirements": "prd.md", "plan": "implement.md", "review": "review.md"},
}

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class TaskRouterError(Exception):
    """A user-actionable task routing error."""


def _canonical_directory(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_dir():
        raise TaskRouterError("TASK_ROOT_NOT_FOUND: %s" % path)
    return path


def _validate_slug(slug: str) -> str:
    if not SLUG_PATTERN.fullmatch(slug or ""):
        raise TaskRouterError("INVALID_TASK_SLUG: use lowercase kebab-case, 1-64 characters")
    return slug


def _inside(root: Path, candidate: Path) -> Path:
    """Reject symlinks as well as lexical escapes within the selected root."""
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise TaskRouterError("TASK_PATH_ESCAPE: %s" % candidate)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise TaskRouterError("TASK_PATH_SYMLINK: %s" % current)
    return candidate


def resolve_task_root(root_path: str) -> Dict[str, str]:
    """Return the single task owner for an explicit project/workspace root."""
    root = _canonical_directory(root_path)
    workflow = _inside(root, root / ".trellis" / "workflow.md")
    provider = "trellis" if workflow.is_file() else "ccg"
    tasks_dir = _inside(root, root / (".trellis/tasks" if provider == "trellis" else ".ccg/tasks"))
    return {"provider": provider, "root": str(root), "tasksDir": str(tasks_dir)}


@contextmanager
def _root_lock(root: Path):
    """Lock the existing root inode; writes never need a lock/task mkdir."""
    if fcntl is None:
        raise TaskRouterError("TASK_LOCK_UNSUPPORTED: POSIX flock is required")
    descriptor = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _read_bounded(path: Path) -> bytes:
    if not path.is_file():
        raise TaskRouterError("TASK_FILE_NOT_FOUND: %s" % path)
    with path.open("rb") as handle:
        content = handle.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise TaskRouterError("TASK_FILE_TOO_LARGE: %s" % path)
    return content


def _read_metadata(task_dir: Path, *, validate_title: bool = True) -> Dict[str, Any]:
    path = _inside(task_dir, task_dir / "task.json")
    try:
        data = json.loads(_read_bounded(path))
    except (ValueError, UnicodeError) as exc:
        raise TaskRouterError("INVALID_TASK_JSON: %s: %s" % (path, exc))
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise TaskRouterError("INVALID_TASK_JSON: expected object with string id: %s" % path)
    _validate_slug(data["id"])
    if validate_title:
        _validate_title(data, path)
    return data


def _validate_title(data: Dict[str, Any], path: Path) -> None:
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        raise TaskRouterError("INVALID_TASK_JSON: missing title: %s" % path)


def _validate_owner(resolution: Dict[str, str], task_dir: Path, data: Dict[str, Any]) -> None:
    tasks_dir = Path(resolution["tasksDir"])
    if task_dir.parent != tasks_dir or task_dir.name == "archive":
        raise TaskRouterError("TASK_OWNER_MISMATCH: %s" % task_dir)
    slug = data["id"]
    expected = task_dir.name == slug if resolution["provider"] == "ccg" else bool(
        re.fullmatch(r"[0-9]{2}-[0-9]{2}-" + re.escape(slug), task_dir.name)
    )
    if not expected:
        raise TaskRouterError("TASK_ID_PATH_MISMATCH: %s" % task_dir)


def _task_entries(tasks_dir: Path):
    if not tasks_dir.exists():
        return []
    return sorted(path for path in tasks_dir.iterdir() if path.name != "archive" and not path.name.startswith("."))


def _matching_tasks(resolution: Dict[str, str], slug: str):
    tasks_dir = Path(resolution["tasksDir"])
    matches = []
    for entry in _task_entries(tasks_dir):
        name_matches = entry.name == slug or bool(re.fullmatch(r"[0-9]{2}-[0-9]{2}-" + re.escape(slug), entry.name))
        try:
            _inside(tasks_dir, entry)
            data = _read_metadata(entry, validate_title=False)
        except (TaskRouterError, OSError):
            if name_matches:
                raise TaskRouterError("INCOMPLETE_TASK: %s; inspect with doctor" % entry)
            continue
        if data["id"] == slug:
            matches.append((entry, data))
        elif name_matches:
            raise TaskRouterError("TASK_ID_PATH_MISMATCH: %s" % entry)
    if len(matches) > 1:
        raise TaskRouterError("DUPLICATE_TASK_ID: %s" % slug)
    for entry, data in matches:
        _validate_title(data, entry / "task.json")
        _validate_owner(resolution, entry, data)
    return matches


def _reject_legacy(root: Path, provider: str, slug: str) -> None:
    other = "ccg" if provider == "trellis" else "trellis"
    tasks_dir = _inside(root, root / ("." + other) / "tasks")
    resolution = {"provider": other, "tasksDir": str(tasks_dir)}
    if _matching_tasks(resolution, slug):
        raise TaskRouterError("LEGACY_TASK_EXISTS: explicit migration required for %s" % slug)


def _parse_ccg_meta(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TaskRouterError("INVALID_CCG_META_JSON: %s" % exc.msg)
    if not isinstance(value, dict):
        raise TaskRouterError("INVALID_CCG_META_JSON: expected an object")
    return value


def _atomic_write(path: Path, content: bytes) -> None:
    if len(content) > MAX_FILE_BYTES:
        raise TaskRouterError("TASK_FILE_TOO_LARGE: %s" % path)
    _inside(path.parent, path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    _atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _ensure_ccg_tasks_ignored(root: Path) -> None:
    """Keep local CCG fallback state out of an application repository's index."""
    ignore_file = _inside(root, root / ".gitignore")
    ignored_paths = {
        ".ccg", ".ccg/", "/.ccg", "/.ccg/", "**/.ccg", "**/.ccg/",
        ".ccg/tasks", ".ccg/tasks/", "/.ccg/tasks", "/.ccg/tasks/",
        "**/.ccg/tasks", "**/.ccg/tasks/",
    }
    try:
        with ignore_file.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                existing = handle.read()
                patterns = {
                    line.strip() for line in existing.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                }
                if patterns.intersection(ignored_paths):
                    return
                suffix = "\n" if existing and not existing.endswith("\n") else ""
                separator = "\n" if existing else ""
                handle.write(suffix + separator + "# Local CCG task state\n.ccg/tasks/\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise TaskRouterError("CCG_IGNORE_WRITE_FAILED: %s" % exc)


def _creation_marker(tasks_dir: Path, slug: str) -> Path:
    return _inside(tasks_dir, tasks_dir / (".router-create-" + slug + ".json"))


def _check_trellis_creation_state(tasks_dir: Path, slug: str) -> None:
    marker = _creation_marker(tasks_dir, slug)
    if marker.exists():
        raise TaskRouterError(
            "INCOMPLETE_TASK_CREATION: %s; run doctor, inspect native task/session state "
            "and recover explicitly before removing this marker" % marker
        )


def _create_trellis_task(root: Path, title: str, slug: str, ccg_meta: Dict[str, Any]) -> Path:
    tasks_dir = root / ".trellis" / "tasks"
    task_script = _inside(root, root / ".trellis" / "scripts" / "task.py")
    if not task_script.is_file():
        raise TaskRouterError("TRELLIS_TASK_SCRIPT_MISSING: %s" % task_script)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    marker = _creation_marker(tasks_dir, slug)
    _atomic_write_json(marker, {"id": slug, "title": title, "state": "creating"})
    try:
        completed = subprocess.run(
            [sys.executable, str(task_script), "create", title, "--slug", slug],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=TRELLIS_CREATE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise TaskRouterError(
            "TRELLIS_TASK_CREATE_TIMEOUT: exceeded %ss; native artifacts were preserved; "
            "run doctor and inspect task/session state for explicit recovery: %s"
            % (TRELLIS_CREATE_TIMEOUT_SECONDS, marker)
        )
    if completed.returncode != 0:
        raise TaskRouterError("TRELLIS_TASK_CREATE_FAILED: %s" % completed.stderr.strip())

    matches = _matching_tasks({"provider": "trellis", "tasksDir": str(tasks_dir)}, slug)
    if not matches:
        raise TaskRouterError("TRELLIS_TASK_CREATE_FAILED: task directory was not created")
    created, data = matches[0]
    if data["title"] != title:
        raise TaskRouterError("TASK_TITLE_CONFLICT: %s" % created)
    task_json = created / "task.json"
    meta = data.setdefault("meta", {})
    if not isinstance(meta, dict):
        raise TaskRouterError("INVALID_CCG_META_JSON: task meta must be an object")
    if "ccg" in meta:
        raise TaskRouterError("CCG_META_CONFLICT: %s" % task_json)
    meta["ccg"] = ccg_meta
    data["meta"] = meta
    try:
        _atomic_write_json(task_json, data)
    except (OSError, TaskRouterError) as exc:
        raise TaskRouterError(
            "TRELLIS_TASK_METADATA_WRITE_FAILED: %s; native artifacts preserved; "
            "run doctor and recover explicitly: %s" % (exc, marker)
        )
    marker.unlink()
    return created


def _create_ccg_task(root: Path, title: str, slug: str, ccg_meta: Dict[str, Any]) -> Path:
    tasks_dir = root / ".ccg" / "tasks"
    task_dir = tasks_dir / slug
    tasks_dir.mkdir(parents=True, exist_ok=True)
    try:
        task_dir.mkdir()
    except FileExistsError:
        raise TaskRouterError("TASK_EXISTS: %s" % task_dir)
    try:
        _ensure_ccg_tasks_ignored(root)
        task = {
            "id": slug,
            "title": title,
            "status": "planning",
            "complexity": ccg_meta.get("complexity", "M"),
            "risk": ccg_meta.get("risk", "medium"),
            "domain": ccg_meta.get("domain", "general"),
            "currentPhase": "analysis",
            "nextAction": ccg_meta.get("nextAction", "analyze requirements"),
            "createdAt": date.today().isoformat(),
            "branch": ccg_meta.get("branch", "pending"),
        }
        _atomic_write_json(task_dir / "task.json", task)
        (task_dir / "requirements.md").write_text("# %s\n\nTBD.\n" % title, encoding="utf-8")
    except Exception:
        for child in task_dir.iterdir():
            child.unlink()
        task_dir.rmdir()
        raise
    return task_dir


def create_task(root_path: str, title: str, slug: str, ccg_meta: Dict[str, Any],
                ensure: bool = False) -> Tuple[Dict[str, Any], Path]:
    # Identity is root/provider + slug with an exact title conflict check.
    # ccg_meta is initialization-only; ensure never resets mutable task state.
    slug = _validate_slug(slug)
    if not title.strip():
        raise TaskRouterError("INVALID_TASK_TITLE")
    root = _canonical_directory(root_path)
    with _root_lock(root):
        resolution = resolve_task_root(str(root))
        if resolution["provider"] == "trellis":
            _check_trellis_creation_state(Path(resolution["tasksDir"]), slug)
        _reject_legacy(root, resolution["provider"], slug)
        matches = _matching_tasks(resolution, slug)
        if matches:
            task_dir, data = matches[0]
            if not ensure:
                raise TaskRouterError("TASK_EXISTS: %s" % task_dir)
            if data["title"] != title:
                raise TaskRouterError("TASK_TITLE_CONFLICT: %s" % task_dir)
        elif resolution["provider"] == "trellis":
            task_dir = _create_trellis_task(root, title, slug, ccg_meta)
        else:
            task_dir = _create_ccg_task(root, title, slug, ccg_meta)
        resolution["taskDir"] = str(_inside(Path(resolution["tasksDir"]), task_dir))
        resolution["taskId"] = slug
        resolution["created"] = not bool(matches)
        return resolution, task_dir


def _task_path_under_root(root: Path, raw_path: str) -> Path:
    """Normalize aliases above the root without resolving task-chain symlinks."""
    task_path = Path(os.path.abspath(Path(raw_path).expanduser()))
    try:
        task_path.relative_to(root)
        return task_path
    except ValueError:
        pass
    # /var and /private/var may name the same selected root on macOS.
    # Prefer the outermost matching ancestor so an inner symlink cannot
    # masquerade as a second root and bypass _inside's component checks.
    for ancestor in reversed(task_path.parents):
        if ancestor.resolve() == root:
            return root / task_path.relative_to(ancestor)
    return task_path


def _existing_task(resolution: Dict[str, str], raw_path: str):
    task_path = _task_path_under_root(Path(resolution["root"]), raw_path)
    task_dir = _inside(Path(resolution["tasksDir"]), task_path)
    if not task_dir.is_dir():
        raise TaskRouterError("TASK_NOT_FOUND: %s" % task_dir)
    data = _read_metadata(task_dir)
    _validate_owner(resolution, task_dir, data)
    if resolution["provider"] == "trellis":
        _check_trellis_creation_state(Path(resolution["tasksDir"]), data["id"])
    _reject_legacy(Path(resolution["root"]), resolution["provider"], data["id"])
    _matching_tasks(resolution, data["id"])
    return task_dir, data


def _root_from_task_path(task_path: str) -> str:
    task_dir = Path(os.path.abspath(Path(task_path).expanduser()))
    if task_dir.parent.name != "tasks" or task_dir.parent.parent.name not in (".ccg", ".trellis"):
        raise TaskRouterError("TASK_OWNER_MISMATCH: expected a direct task directory: %s" % task_dir)
    return str(task_dir.parent.parent.parent)


def validate_task(task_path: str, root_path: Optional[str] = None) -> Dict[str, str]:
    """Read-only canonical task validation shared with orchestration callers."""
    resolution = resolve_task_root(root_path or _root_from_task_path(task_path))
    task_dir, data = _existing_task(resolution, task_path)
    receipt = dict(resolution, taskDir=str(task_dir), taskId=data["id"])
    if isinstance(data.get("createdAt"), str):
        receipt["createdAt"] = data["createdAt"]
    return receipt


def modify_task(root_path: str, task_path: str, *, document: Optional[str] = None,
                content_file: Optional[str] = None, phase: Optional[str] = None,
                next_action: Optional[str] = None) -> Dict[str, str]:
    root = _canonical_directory(root_path)
    with _root_lock(root):
        resolution = resolve_task_root(str(root))
        task_dir, data = _existing_task(resolution, task_path)
        if document is not None:
            if document not in DOCUMENTS[resolution["provider"]] or not content_file:
                raise TaskRouterError("INVALID_TASK_DOCUMENT")
            content = _read_bounded(Path(content_file).expanduser())
            try:
                content.decode("utf-8")
            except UnicodeError:
                raise TaskRouterError("INVALID_DOCUMENT_ENCODING: expected UTF-8")
            destination = _inside(task_dir, task_dir / DOCUMENTS[resolution["provider"]][document])
            _atomic_write(destination, content)
            resolution["documentPath"] = str(destination)
        else:
            if phase not in PHASES or not isinstance(next_action, str) or not next_action.strip():
                raise TaskRouterError("INVALID_TASK_UPDATE")
            target = data
            if resolution["provider"] == "trellis":
                meta = data.setdefault("meta", {})
                if not isinstance(meta, dict):
                    raise TaskRouterError("INVALID_CCG_META_JSON: task meta must be an object")
                target = meta.setdefault("ccg", {})
                if not isinstance(target, dict):
                    raise TaskRouterError("INVALID_CCG_META_JSON: meta.ccg must be an object")
            target.update(currentPhase=phase, nextAction=next_action)
            if resolution["provider"] == "ccg":
                data["status"] = "completed" if phase == "completed" else "in_progress"
            _atomic_write_json(task_dir / "task.json", data)
        resolution["taskDir"] = str(task_dir)
        resolution["taskId"] = data["id"]
        return resolution


def doctor(root_path: str) -> Dict[str, Any]:
    resolution = resolve_task_root(root_path)
    root = Path(resolution["root"])
    issues = []
    identities = {}
    for provider in (resolution["provider"], "ccg" if resolution["provider"] == "trellis" else "trellis"):
        try:
            tasks_dir = _inside(root, root / ("." + provider) / "tasks")
            entries = _task_entries(tasks_dir)
            if tasks_dir.is_dir():
                for marker in sorted(tasks_dir.glob(".router-create-*.json")):
                    issues.append({
                        "code": "INCOMPLETE_TASK_CREATION", "path": str(marker),
                        "detail": "Inspect native task/session state and recover explicitly before removing marker",
                    })
        except (TaskRouterError, OSError) as exc:
            issues.append({"code": "INVALID_TASK_ROOT", "detail": str(exc)})
            continue
        for entry in entries:
            if not entry.is_dir() and not entry.is_symlink():
                continue
            try:
                _inside(tasks_dir, entry)
                data = _read_metadata(entry, validate_title=False)
                identities.setdefault(data["id"], []).append(str(entry))
                _validate_title(data, entry / "task.json")
                _validate_owner({"provider": provider, "tasksDir": str(tasks_dir)}, entry, data)
                if provider != resolution["provider"]:
                    issues.append({"code": "ORPHAN_TASK", "path": str(entry)})
            except (TaskRouterError, OSError) as exc:
                issues.append({"code": "INCOMPLETE_TASK", "path": str(entry), "detail": str(exc)})
    for identity, paths in identities.items():
        if len(paths) > 1:
            issues.append({"code": "DUPLICATE_TASK_ID", "id": identity, "paths": paths})
    return dict(resolution, ok=not issues, issues=issues)


def _emit(value: Dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _selected_root(args: argparse.Namespace) -> str:
    if args.scope == "workspace":
        if not args.workspace_root:
            raise TaskRouterError("WORKSPACE_ROOT_REQUIRED")
        return args.workspace_root
    if not args.project_root:
        if args.command in ("write", "update"):
            return _root_from_task_path(args.task_dir)
        raise TaskRouterError("PROJECT_ROOT_REQUIRED")
    return args.project_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("resolve", "create", "ensure", "write", "update", "doctor"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--project-root")
        command_parser.add_argument("--workspace-root")
        command_parser.add_argument("--scope", choices=("project", "workspace"), default="project")
        if command in ("create", "ensure"):
            command_parser.add_argument("--title", required=True)
            command_parser.add_argument("--slug", required=True)
            command_parser.add_argument("--ccg-meta")
        if command in ("write", "update"):
            command_parser.add_argument("--task-dir", required=True)
        if command == "write":
            command_parser.add_argument("--document", choices=tuple(DOCUMENTS["ccg"]), required=True)
            command_parser.add_argument("--content-file", required=True)
        if command == "update":
            command_parser.add_argument("--phase", choices=PHASES, required=True)
            command_parser.add_argument("--next-action", required=True)

    args = parser.parse_args()
    try:
        if args.command == "resolve":
            _emit(resolve_task_root(_selected_root(args)))
        elif args.command in ("create", "ensure"):
            resolution, _ = create_task(_selected_root(args), args.title, args.slug,
                                        _parse_ccg_meta(args.ccg_meta), ensure=args.command == "ensure")
            _emit(resolution)
        elif args.command == "doctor":
            report = doctor(_selected_root(args))
            _emit(report)
            return 0 if report["ok"] else 2
        elif args.command == "write":
            _emit(modify_task(_selected_root(args), args.task_dir, document=args.document,
                              content_file=args.content_file))
        else:
            _emit(modify_task(_selected_root(args), args.task_dir, phase=args.phase,
                              next_action=args.next_action))
        return 0
    except (TaskRouterError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
