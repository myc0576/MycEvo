from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "papers" / "engineering-demo.typ"
PLUGIN = REPO_ROOT / "plugins" / "paperframes-reference" / "paperframes_reference.py"


def _runtime_paths() -> tuple[str | None, str | None]:
    return os.environ.get("PAPERFRAMES_DOCLING_PYTHON"), os.environ.get("PAPERFRAMES_TYPST_BIN") or shutil.which("typst")


def test_real_docling_typst_runner_golden_path(tmp_path: Path) -> None:
    docling_python, typst = _runtime_paths()
    if not docling_python or not typst:
        pytest.skip("requires explicit PaperFrames Docling Python and Typst CLI runtime")
    source_pdf = tmp_path / "engineering-demo.pdf"
    subprocess.run([typst, "compile", str(FIXTURE), str(source_pdf)], check=True, capture_output=True, text=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "input.pdf").write_bytes(source_pdf.read_bytes())
    env = os.environ.copy()
    env["PAPERFRAMES_TYPST_BIN"] = typst
    code = "from pathlib import Path; import json; from mycevo.runner import run_subprocess; r=run_subprocess(workspace=Path(__import__('sys').argv[1]), command=[__import__('sys').executable, __import__('sys').argv[2], '--input', 'input.pdf', '--workspace', '.'], read_paths=['input.pdf'], write_paths=['build/golden-path/**'], read_allowlist=['input.pdf'], write_allowlist=['build/golden-path/**'], allowed_subprocesses=[__import__('sys').executable]); print(json.dumps(r))"
    result = subprocess.run([docling_python, "-c", code, str(workspace), str(PLUGIN)], cwd=REPO_ROOT, env={**env, "PYTHONPATH": str(REPO_ROOT / "src")}, capture_output=True, text=True, check=False, timeout=180)
    assert result.returncode == 0, result.stderr + result.stdout
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "executed_unverified"
    output = workspace / "build" / "golden-path"
    ir = json.loads((output / "paperframes.ir.json").read_text(encoding="utf-8"))
    assert ir["title"] == "Engineering Demo"
    assert len(ir["sections"]) >= 4
    pdf = output / "reversed-paper.pdf"
    assert pdf.read_bytes()[:4] == b"%PDF"
    provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    assert (output / "document-tree.json").exists()
    assert (output / "figures-manifest.json").exists()
    assert (output / "checksums.json").exists()
    assert provenance["docling_version"] == "2.116.0"
