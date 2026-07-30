"""Constrained subprocess runner boundary for external MycEvo plugins."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence


class RunnerPolicyError(ValueError):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _allowed(path: Path, root: Path, patterns: Sequence[str]) -> bool:
    if not _inside(path, root):
        return False
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return any(Path(relative).match(pattern) or Path(relative).match(pattern.removesuffix("/**")) for pattern in patterns)


def validate_paths(root: Path, paths: Sequence[str], patterns: Sequence[str]) -> list[Path]:
    checked: list[Path] = []
    for value in paths:
        candidate = (root / value).resolve()
        if not _allowed(candidate, root, patterns):
            raise RunnerPolicyError(f"path is outside declared capability: {value}")
        checked.append(candidate)
    return checked


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def run_subprocess(
    *,
    workspace: Path,
    command: Sequence[str],
    read_paths: Sequence[str] = (),
    write_paths: Sequence[str] = (),
    read_allowlist: Sequence[str] = (),
    write_allowlist: Sequence[str] = (),
    allowed_subprocesses: Sequence[str] = (),
    network: bool = False,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run one explicitly declared command and return an unverified receipt."""

    if not command or command[0] not in set(allowed_subprocesses):
        raise RunnerPolicyError("subprocess is not declared by plugin capabilities")
    if network:
        raise RunnerPolicyError("network execution is not enabled by the local runner")
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    validate_paths(workspace, read_paths, read_allowlist)
    validate_paths(workspace, write_paths, write_allowlist)
    started = time.time()
    try:
        completed = subprocess.run(
            list(command), cwd=workspace, capture_output=True, text=True,
            timeout=timeout_seconds, check=False, env={**os.environ, "MYCEVO_RUNNER_WORKSPACE": str(workspace)},
        )
        status = "executed_unverified"
        error = None
    except subprocess.TimeoutExpired as exc:
        completed = None
        status = "failed_timeout"
        error = str(exc)
    outputs = []
    for path in write_paths:
        candidate = (workspace / path).resolve()
        if candidate.is_file() and _inside(candidate, workspace):
            outputs.append({"path": candidate.relative_to(workspace).as_posix(), "sha256": _hash_file(candidate)})
    return {
        "status": status,
        "command": list(command),
        "workspace": str(workspace),
        "returncode": completed.returncode if completed else None,
        "stdout": completed.stdout if completed else "",
        "stderr": completed.stderr if completed else "",
        "error": error,
        "outputs": outputs,
        "duration_seconds": round(time.time() - started, 6),
    }
