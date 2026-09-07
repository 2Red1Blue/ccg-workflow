#!/usr/bin/env python3
"""
CCG Workflow Hook for Codex CLI — Adaptive Guardrail
Injects per-turn guidance based on what Codex has/hasn't done.
Not a rigid state machine — adapts to task complexity and progress.

Hook type: UserPromptSubmit
"""

import json
import os
import sys
import glob
import subprocess
from pathlib import Path
from datetime import datetime
import re
import shlex


# Terminal task statuses — matched case-insensitively after trim. Covers common
# synonyms the model may write (done/finished/closed/...) so a finished task is
# never misjudged as still active. Canonical write value is "completed".
_TERMINAL_STATUSES = frozenset({
    "completed", "complete", "done", "finished", "finish",
    "archived", "archive", "cancelled", "canceled", "closed", "resolved", "abandoned",
})

_SOURCE_EXTENSIONS = frozenset({
    ".c", ".cc", ".cpp", ".cs", ".go", ".java", ".js", ".jsx", ".kt", ".kts",
    ".mjs", ".php", ".py", ".rb", ".rs", ".scala", ".sh", ".sql", ".swift",
    ".ts", ".tsx", ".vue",
})
_REVIEWABLE_CONFIG_NAMES = frozenset({
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "openapi.yaml",
    "openapi.yml", "openapi.json", "swagger.yaml", "swagger.yml", "swagger.json",
})
_TEST_PATH = re.compile(r"(^|[\\/])(?:test|tests|__tests__|spec|fixtures?)(?:[\\/]|$)|[._-](?:test|spec)\.[^.]+$", re.I)
_HIGH_RISK_PATH = re.compile(r"(?:^|[\\/_.-])(?:auth|login|password|token|secret|credential|crypto|encrypt|permission|admin|migration|schema|openid|oauth|sso)(?=$|[\\/_.-])", re.I)
_CONTRACT_PATH = re.compile(r"(^|[\\/])(api|apis|contracts?|migrations?|schema|openapi|swagger|proto)([\\/]|$)|\.(?:proto|graphql|gql)$", re.I)
_CLOSING_INTENT = re.compile(r"\b(done|finish(?:ed)?|complete(?:d)?|deliver(?:ed|y)?|submit|ready\s+to\s+(?:ship|merge)|review\s+it)\b|完成|完毕|交付|提交|验收|收尾|可以合并|准备合并|准备发布", re.I)
_REVIEW_INTENT = re.compile(r"\breview\b|\baudit\b|dual[ -]?review|审查|审核|代码检查|交叉验证", re.I)


def _is_terminal_status(status):
    return str(status or "").strip().lower() in _TERMINAL_STATUSES


def find_project_root():
    """Walk up to find .ccg/ or .git/"""
    d = os.environ.get("CODEX_PROJECT_DIR", os.getcwd())
    for _ in range(20):
        if os.path.isdir(os.path.join(d, ".ccg")) or os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def get_active_task(root):
    """Find the most recent in_progress task."""
    tasks_dir = os.path.join(root, ".ccg", "tasks")
    if not os.path.isdir(tasks_dir):
        return None
    for name in sorted(os.listdir(tasks_dir), reverse=True):
        if name == "archive":
            continue
        task_file = os.path.join(tasks_dir, name, "task.json")
        if not os.path.isfile(task_file):
            continue
        try:
            with open(task_file) as f:
                task = json.load(f)
            if not _is_terminal_status(task.get("status")):
                task["_dir"] = os.path.join(tasks_dir, name)
                task["_name"] = name
                return task
        except Exception:
            continue
    return None


def detect_progress(root):
    """Measure reviewable source changes, not every changed file."""
    signals = {
        "has_dirty_files": False,
        "dirty_count": 0,
        "changed_lines": 0,
        "source_files": 0,
        "has_test_output": False,
        "high_risk_files": False,
        "contract_change": False,
    }
    try:
        status = subprocess.run(
            ["git", "-c", "core.quotepath=off", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root, capture_output=True, text=True, timeout=5
        )
        lines = [l for l in status.stdout.strip().split("\n") if l.strip()]
        signals["dirty_count"] = len(lines)
        signals["has_dirty_files"] = len(lines) > 0

        for line in lines:
            fname = line[3:].strip().split(" -> ")[-1].strip().strip('"').lower()
            if not _is_reviewable_source(fname):
                continue
            signals["source_files"] += 1
            signals["high_risk_files"] = signals["high_risk_files"] or bool(_HIGH_RISK_PATH.search(fname))
            signals["contract_change"] = signals["contract_change"] or bool(_CONTRACT_PATH.search(fname))

        if signals["has_dirty_files"]:
            diff = subprocess.run(
                ["git", "diff", "HEAD", "--numstat"],
                cwd=root, capture_output=True, text=True, timeout=5
            )
            for dline in diff.stdout.strip().split("\n"):
                parts = dline.split("\t", 2)
                if len(parts) == 3 and parts[0] != "-" and parts[1] != "-" and _is_reviewable_source(parts[2]):
                    signals["changed_lines"] += int(parts[0]) + int(parts[1])
            for line in lines:
                if not line.startswith("?? "):
                    continue
                filename = line[3:].strip()
                if not _is_reviewable_source(filename):
                    continue
                try:
                    with open(os.path.join(root, filename), encoding="utf-8") as source_file:
                        file_lines = 0
                        for _ in source_file:
                            file_lines += 1
                            if file_lines >= 80:
                                break
                        signals["changed_lines"] += file_lines
                except OSError:
                    pass
    except Exception:
        pass
    return signals


def assess_complexity(task):
    """Use an explicit level, otherwise infer it from the selected strategy."""
    configured = str((task or {}).get("complexity", "")).upper()
    if configured in ("S", "M", "L", "XL"):
        return configured
    strategy = str((task or {}).get("strategy", "")).lower()
    if "full-collaborate" in strategy:
        return "L"
    if any(name in strategy for name in ("guided", "debug", "refactor")):
        return "M"
    if any(name in strategy for name in ("direct", "quick")):
        return "S"
    return "M"


def _is_reviewable_source(filename):
    normalized = filename.replace("\\", "/")
    name = Path(normalized).name.lower()
    if _TEST_PATH.search(normalized):
        return False
    return Path(name).suffix.lower() in _SOURCE_EXTENSIONS or name in _REVIEWABLE_CONFIG_NAMES or bool(_CONTRACT_PATH.search(normalized))


def _review_due(task, progress, message):
    complexity = assess_complexity(task)
    phase = str(task.get("currentPhase", "")).lower()
    high_risk = str(task.get("risk", "low")).lower() == "high" or progress["high_risk_files"]
    explicit = bool(_REVIEW_INTENT.search(message))
    boundary = phase == "review" or bool(_CLOSING_INTENT.search(message))

    if progress["source_files"] == 0:
        return explicit, "用户明确请求审查" if explicit else ""
    if explicit:
        return True, "用户明确请求审查"
    if not boundary:
        return False, ""
    if high_risk:
        return True, "高风险认证、安全、迁移或权限变更"
    if complexity in ("L", "XL"):
        return True, f"{complexity} 复杂度任务到达交付/审查节点"
    if complexity == "M" and (progress["source_files"] >= 3 or progress["changed_lines"] >= 80 or progress["contract_change"]):
        if progress["contract_change"]:
            return True, "接口契约或数据结构变更"
        if progress["source_files"] >= 3:
            return True, "跨 3 个以上源码文件"
        return True, f"源码变更达到 {progress['changed_lines']} 行"
    return False, ""


def build_guidance(task, progress, root, message):
    """Emit only a meaningful review gate; ordinary edits stay quiet."""
    parts = []
    complexity = assess_complexity(task)
    due, reason = _review_due(task, progress, message)
    if due:
        supervisor = shlex.quote(str(Path.home() / ".claude" / "bin" / "ccg-agent-supervisor"))
        parts.extend([
            "双模型审查已到达有效触发点；不要把这个要求提前到每次编辑。",
            f"原因：{reason}",
            f"审查范围：{progress['source_files']} 个源码/契约文件，约 {progress['changed_lines']} 行。",
            "先完成相关测试，再在当前工作目录执行一次持久化审查：",
            f"  printf '%s\\n' 'Review the current change for correctness, security, regression risk, and maintainability. Return Critical/Warning/Info findings with file:line evidence.' | {supervisor} review --workdir \"$(pwd)\" --snapshot-base HEAD --include-untracked",
            "等待 Codex 与 Claude leaf 都返回；任一超时、传输失败或模型不匹配都不是审查通过。",
        ])

    return parts


SUB_AGENT_NOTICE = """<ccg-sub-agent-notice>
SUB-AGENT NOTICE — READ FIRST IF SPAWNED VIA spawn_agent

If your parent session spawned you via spawn_agent with an explicit task
message, that message is your ONLY job.
- Execute the parent message exactly as written, then mark yourself complete.
- Ignore all CCG workflow guidance below this notice.
- Do NOT call spawn_agent, wait, or close_agent.
- Do NOT modify .ccg/tasks/* or any workflow state files.
- Do NOT run external model calls (codeagent-wrapper).
- Only modify files explicitly listed in your dispatch message.
</ccg-sub-agent-notice>"""


def is_sub_agent():
    """Detect if running inside a Codex sub-agent session.
    Codex sub-agents spawned with fork_turns='none' get a clean
    context but inherit the env. The parent sets CODEX_AGENT_TYPE
    or the agent_type is visible in the process env."""
    if os.environ.get("CODEX_AGENT_TYPE", ""):
        return True
    if os.environ.get("CODEX_FORK_TURNS", "") == "none":
        return True
    return False


def main():
    try:
        root = find_project_root()
        if not root:
            return
        if not os.path.isdir(os.path.join(root, ".ccg")):
            return

        # Sub-agent: inject notice and skip workflow guidance
        if is_sub_agent():
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": SUB_AGENT_NOTICE
                }
            }))
            return

        task = get_active_task(root)
        if not task:
            return
        progress = detect_progress(root)
        message = sys.stdin.read() if not sys.stdin.isatty() else ""
        try:
            parsed = json.loads(message)
            message = parsed.get("message") or parsed.get("content") or parsed.get("prompt") or ""
            if not isinstance(message, str):
                message = json.dumps(message)
        except Exception:
            pass
        lines = build_guidance(task, progress, root, message)

        if not lines:
            return

        context = "<ccg-state>\n" + "\n".join(lines) + "\n</ccg-state>"

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context
            }
        }))
    except Exception:
        pass


if __name__ == "__main__":
    main()
