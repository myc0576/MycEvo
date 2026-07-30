from pathlib import Path

import pytest

from mycevo.runner import RunnerPolicyError, run_subprocess


def test_runner_executes_declared_command_and_returns_unverified_receipt(tmp_path: Path) -> None:
    result = run_subprocess(
        workspace=tmp_path,
        command=["python", "-c", "from pathlib import Path; Path('build.txt').write_text('ok')"],
        write_paths=["build.txt"],
        write_allowlist=["build.txt"],
        allowed_subprocesses=["python"],
    )
    assert result["status"] == "executed_unverified"
    assert result["returncode"] == 0
    assert result["outputs"][0]["sha256"].startswith("sha256:")


def test_runner_rejects_undeclared_subprocess(tmp_path: Path) -> None:
    with pytest.raises(RunnerPolicyError, match="not declared"):
        run_subprocess(workspace=tmp_path, command=["cmd"], allowed_subprocesses=[])


def test_runner_rejects_workspace_escape(tmp_path: Path) -> None:
    with pytest.raises(RunnerPolicyError, match="outside"):
        run_subprocess(
            workspace=tmp_path,
            command=["python", "-c", "pass"],
            write_paths=["../escape.txt"],
            write_allowlist=["**"],
            allowed_subprocesses=["python"],
        )
