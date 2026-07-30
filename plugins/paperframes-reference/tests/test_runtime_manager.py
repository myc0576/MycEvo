from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))
from runtime_manager import PaperFramesRuntimeManager


def test_manager_uses_persistent_home_and_fail_closed_verification(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PAPERFRAMES_DOCLING_PYTHON", str(tmp_path / "missing-python.exe"))
    manager = PaperFramesRuntimeManager(home=tmp_path / "paperframes")
    resolved = manager.resolve_runtime()
    assert resolved["home"] == str((tmp_path / "paperframes").resolve())
    result = manager.verify_runtime(resolved)
    assert result["ok"] is False
    assert result["status"] in {"runtime_missing", "python_missing"}


def test_manager_provenance_is_structured(tmp_path: Path) -> None:
    manager = PaperFramesRuntimeManager(home=tmp_path / "paperframes")
    provenance = manager.runtime_provenance({"status": "runtime_missing"})
    assert provenance["runtime"] == "docling"
    assert provenance["status"] == "runtime_missing"
