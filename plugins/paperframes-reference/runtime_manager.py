"""Persistent, user-scoped Docling runtime manager for PaperFrames."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class PaperFramesRuntimeManager:
    def __init__(self, home: str | Path | None = None) -> None:
        default = Path(os.environ.get("PAPERFRAMES_HOME", Path.home() / ".paperframes"))
        self.home = Path(home or default).expanduser().resolve()
        self.runtime_root = self.home / "runtimes" / "docling"
        self.lock_path = self.home / "docling-runtime.lock.json"

    def resolve_runtime(self) -> dict[str, Any]:
        configured = os.environ.get("PAPERFRAMES_DOCLING_PYTHON")
        candidates = [Path(configured)] if configured else []
        candidates.extend([Path(sys.executable), Path(shutil.which("python3.12") or ""), Path(shutil.which("python3.11") or "")])
        interpreter = next((p.resolve() for p in candidates if str(p) and p.is_file()), None)
        return {"runtime": "docling", "home": str(self.home), "runtime_root": str(self.runtime_root), "python": str(interpreter) if interpreter else None, "lock": str(self.lock_path), "status": "resolved" if interpreter else "python_missing"}

    def create_runtime(self) -> dict[str, Any]:
        resolved = self.resolve_runtime()
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        return resolved

    def install_locked_dependencies(self, requirements: str | Path) -> dict[str, Any]:
        resolved = self.create_runtime()
        if not resolved.get("python"):
            return {**resolved, "status": "python_missing", "ok": False}
        requirements = Path(requirements).resolve()
        if not requirements.is_file():
            return {**resolved, "status": "lock_missing", "ok": False}
        command = [resolved["python"], "-m", "pip", "install", "--require-hashes", "-r", str(requirements)]
        completed = subprocess.run(command, cwd=self.home, capture_output=True, text=True, timeout=1800, check=False)
        result = {**resolved, "status": "installed" if completed.returncode == 0 else "install_failed", "ok": completed.returncode == 0, "command": command, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
        if completed.returncode == 0:
            self.lock_path.write_text(json.dumps({"schema": "paperframes.docling_runtime_lock.v1", "requirements": str(requirements), "python": resolved["python"]}, indent=2) + "\n", encoding="utf-8")
        return result

    def verify_runtime(self, resolved: dict[str, Any] | None = None) -> dict[str, Any]:
        current = resolved or self.resolve_runtime()
        python = current.get("python")
        if not python:
            return {**current, "ok": False, "status": "python_missing"}
        completed = subprocess.run([python, "-c", "import docling; print(getattr(docling, '__version__', 'unknown'))"], capture_output=True, text=True, timeout=30, check=False)
        return {**current, "ok": completed.returncode == 0, "status": "runtime_available" if completed.returncode == 0 else "runtime_missing", "docling_version": completed.stdout.strip(), "stderr": completed.stderr}

    def prefetch_models(self) -> dict[str, Any]:
        return {"ok": False, "status": "not_requested", "runtime": "docling", "models": []}

    def runtime_provenance(self, result: dict[str, Any]) -> dict[str, Any]:
        return {"runtime": "docling", "python": sys.executable, "python_version": sys.version, "home": str(self.home), **result}
