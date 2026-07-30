from pathlib import Path

from importlib.util import module_from_spec, spec_from_file_location


def load_reference():
    path = Path(__file__).parents[1] / "plugins" / "paperframes-reference" / "paperframes_reference.py"
    spec = spec_from_file_location("paperframes_reference", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reference_plugin_fails_closed_when_runtime_is_missing(tmp_path: Path) -> None:
    result = load_reference().inspect_runtime(tmp_path)
    assert result["status"] == "missing_runtime_dependency"
    assert result["promotion_allowed"] is False
