from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from runtime import TypstRuntime


def test_typst_runtime_fails_closed_when_executable_is_missing(tmp_path: Path) -> None:
    runtime = TypstRuntime(executable=tmp_path / "missing-typst.exe")
    result = runtime.detect()
    assert result["status"] == "runtime_missing"
    assert result["ok"] is False


def test_typst_runtime_uses_argv_and_reports_compile_failure(tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip(".cmd fixture requires Windows command execution")
    fake = tmp_path / "fake-typst.cmd"
    fake.write_text("@echo off\necho compile-failed 1>&2\nexit /b 7\n", encoding="utf-8")
    source = tmp_path / "input.typ"
    output = tmp_path / "out.pdf"
    source.write_text("= Demo\n", encoding="utf-8")
    result = TypstRuntime(executable=fake).compile(source, output, tmp_path, timeout=5)
    assert result["status"] == "compile_failed"
    assert result["returncode"] == 7
    assert result["command"][0] == str(fake)
