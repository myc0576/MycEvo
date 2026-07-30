"""External runtime adapters for the PaperFrames reference plugin."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


class RuntimePolicyError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class TypstRuntime:
    """Resolve and invoke the official Typst CLI without shell evaluation."""

    def __init__(self, executable: str | Path | None = None) -> None:
        configured = executable or os.environ.get("PAPERFRAMES_TYPST_BIN")
        self.executable = Path(configured) if configured else None

    def _resolve(self) -> Path | None:
        if self.executable:
            return self.executable if self.executable.is_file() else None
        found = shutil.which("typst")
        return Path(found) if found else None

    def detect(self) -> dict[str, Any]:
        executable = self._resolve()
        if executable is None:
            return {"ok": False, "status": "runtime_missing", "runtime": "typst-cli"}
        version = self.version(executable)
        if version["returncode"] != 0:
            return {"ok": False, "status": "runtime_unhealthy", "runtime": "typst-cli", **version}
        return {"ok": True, "status": "runtime_available", "runtime": "typst-cli", "executable": str(executable.resolve()), "executable_digest": _sha256(executable), **version}

    def version(self, executable: Path | None = None) -> dict[str, Any]:
        target = executable or self._resolve()
        if target is None:
            return {"returncode": None, "stdout": "", "stderr": "", "version": None}
        completed = subprocess.run([str(target), "--version"], capture_output=True, text=True, timeout=15, check=False)
        return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr, "version": (completed.stdout or completed.stderr).strip()}

    def compile(self, source: Path, output: Path, root: Path, timeout: int = 300) -> dict[str, Any]:
        executable = self._resolve()
        if executable is None:
            return {"ok": False, "status": "runtime_missing", "command": [], "returncode": None}
        source = source.resolve()
        output = output.resolve()
        root = root.resolve()
        if not source.is_file() or not _inside(source, root) or not _inside(output, root):
            raise RuntimePolicyError("Typst source/output must stay inside workspace")
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [str(executable), "compile", str(source), str(output)]
        started = time.time()
        try:
            completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            return {"ok": False, "status": "compile_timeout", "command": command, "returncode": None, "stdout": exc.stdout or "", "stderr": exc.stderr or "", "duration_seconds": round(time.time() - started, 6)}
        status = "compiled" if completed.returncode == 0 and output.is_file() and output.stat().st_size > 0 and output.read_bytes()[:4] == b"%PDF" else "compile_failed"
        result = {"ok": status == "compiled", "status": status, "command": command, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr, "duration_seconds": round(time.time() - started, 6), "runtime": self.detect()}
        if status == "compiled":
            result["output_digest"] = _sha256(output)
        return result


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
