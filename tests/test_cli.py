from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
import mycevo.mcp_installer as mcp_installer
from mycevo.core import Paths
from mycevo.retrieval import rank_results
from mycevo.evolution import evaluate_guard
from mycevo.provenance import record_run
from mycevo.plugin_boundary import inspect_entry_points


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_NAMES = [
    "projects",
    "knowledge",
    "prompts",
    "research_assets",
    "papers",
    "literature",
    "figures",
    "output_objects",
    "asset_evolution",
    "workflow_improvement_backlog",
    "decisions",
    "upstream_workflows",
    "visual_to_editable_skills",
    "ppt_assets",
    "model_assets",
    "feedback",
]


def seed_instance(root: Path) -> Path:
    registry = root / "registry"
    registry.mkdir(parents=True)
    for name in REGISTRY_NAMES:
        (registry / f"{name}.yaml").write_text(f"version: 1\n{name}: []\n", encoding="utf-8")
    project = root.parent / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    (registry / "projects.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "projects": [
                    {
                        "project_id": "demo",
                        "name": "demo",
                        "path": str(project),
                        "role": "test",
                        "closeout_required": True,
                        "notes": "temporary test project",
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "harness.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "paths": {
                    "workspace_root": str(root.parent),
                    "reusable_knowledge_root": str(root.parent / "knowledge" / "reusable_knowledge"),
                    "reusable_prompts_root": str(root.parent / "knowledge" / "reusable_prompts"),
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return project


def test_core_plugin_inspection_does_not_load_implementation(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeEntryPoint:
        name = "paperframes-reference"
        value = "paperframes_reference:plugin"

        def load(self):  # pragma: no cover - a failure proves the boundary was crossed
            raise AssertionError("Core must not load plugin implementations")

    monkeypatch.setattr("mycevo.plugin_boundary.entry_points", lambda: {"mycevo.plugins": [FakeEntryPoint()]})
    discovered = inspect_entry_points()
    assert discovered == [{
        "name": "paperframes-reference",
        "value": "paperframes_reference:plugin",
        "group": "mycevo.plugins",
        "implementation_loaded": "false",
    }]


def test_plugin_cli_is_plan_only_by_default(tmp_path: Path) -> None:
    result = run_cli(tmp_path / "instance", "plugin", "run", "paperframes-reference", "--input", "paper.pdf")
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "planned"
    assert payload["execute"] is False


def test_plugin_cli_rejects_execute_before_authorization(tmp_path: Path) -> None:
    result = run_cli(tmp_path / "instance", "plugin", "run", "paperframes-reference", "--execute")
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "authorization_required"


def run_cli(instance: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["MYCEVO_USER_ROOT"] = str(instance.parent / "mycevo-user")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mycevo.cli",
            "--engine-root",
            str(REPO_ROOT),
            "--root",
            str(instance),
            *args,
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_registry_validate_uses_instance_root(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    seed_instance(instance)
    result = run_cli(instance, "registry", "validate")
    assert result.returncode == 0, result.stderr + result.stdout
    validation = json.loads((instance / "state" / "registry_validation.json").read_text(encoding="utf-8"))
    assert validation["ok"] is True


def test_product_cli_initializes_portable_workspace(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    seed_instance(instance)
    init = run_cli(instance, "init", "--json")
    assert init.returncode == 0, init.stderr + init.stdout
    doctor = run_cli(instance, "doctor", "--json")
    assert doctor.returncode == 0, doctor.stderr + doctor.stdout
    status = run_cli(instance, "status", "--json")
    assert status.returncode == 0, status.stderr + status.stdout
    assert (instance / ".mycevo" / "config.yaml").exists()
    assert json.loads(doctor.stdout)["product"] == "MycEvo"


def test_fresh_workspace_completes_local_demo(tmp_path: Path) -> None:
    instance = tmp_path / "fresh-workspace"
    instance.mkdir()
    init = run_cli(instance, "init", "--json")
    assert init.returncode == 0, init.stderr + init.stdout
    for relative in (
        "harness.yaml",
        "registry/knowledge.yaml",
        "registry/prompts.yaml",
        "registry/research_assets.yaml",
        "registry/decisions.yaml",
        "registry/feedback.yaml",
        "knowledge/README.md",
        "prompts/README.md",
    ):
        assert (instance / relative).exists(), relative
    demo = run_cli(instance, "demo", "--json")
    assert demo.returncode == 0, demo.stderr + demo.stdout
    payload = json.loads(demo.stdout)
    assert payload["ok"] is True
    assert payload["candidate_status"] == "pending validation"
    assert Path(payload["run_dir"], "run.json").exists()
    doctor = run_cli(instance, "doctor", "--json")
    assert doctor.returncode == 0, doctor.stderr + doctor.stdout


def test_workspace_remove_does_not_delete_workspace_data(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    seed_instance(instance)
    target = tmp_path / "tracked-workspace"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    added = run_cli(instance, "workspace", "add", "demo", str(target))
    removed = run_cli(instance, "workspace", "remove", "demo")
    assert added.returncode == 0, added.stderr + added.stdout
    assert removed.returncode == 0, removed.stderr + removed.stdout
    assert marker.exists()


def test_mcp_install_print_is_side_effect_free(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    seed_instance(instance)
    result = run_cli(instance, "mcp", "install", "codex", "--print")
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "dry_run"
    assert payload["changed"] is False
    assert "--workspace-root" in payload["command"]
    assert str(instance.resolve()) in payload["command"]
    assert "--engine-root" in payload["command"]
    assert str(REPO_ROOT.resolve()) in payload["command"]
    assert not (tmp_path / "mycevo-user").exists()


def test_unknown_mcp_agent_prints_snippet_without_editing(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    seed_instance(instance)
    result = run_cli(instance, "mcp", "install", "unknown-agent", "--print")
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "unsupported_agent"
    assert payload["changed"] is False


def test_mcp_install_is_idempotent_when_official_get_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = Paths(engine=REPO_ROOT, workspace=tmp_path / "workspace", user=tmp_path / "user")
    calls: list[list[str]] = []

    monkeypatch.setattr(mcp_installer.shutil, "which", lambda _: "fake-agent")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="configured", stderr="")

    monkeypatch.setattr(mcp_installer, "_run", fake_run)
    result = mcp_installer.install(paths, "codex")
    assert result["status"] == "already_configured"
    assert ["codex", "mcp", "add", "mycevo", "--", "mycevo", "mcp", "serve"] not in calls
    assert list((paths.user / "mcp-preflight").glob("*.json"))


def test_retrieval_adds_utility_metadata_and_null_reuse(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "knowledge": [
                    {
                        "id": "validated-item",
                        "path": str(tmp_path / "validated.md"),
                        "status": "validated",
                        "evidence_refs": ["run.json"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (registry / "utility_metadata.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "items": [
                    {
                        "id": "validated-item",
                        "domain_fit": 0.9,
                        "successful_reuse_count": 4,
                        "failed_reuse_count": 1,
                        "last_feedback": "useful",
                        "provenance": {"run": "run.json"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    ranked = rank_results(
        [
            {"path": str(tmp_path / "validated.md"), "title": "Validated", "kind": "knowledge"},
            {"path": str(tmp_path / "unknown.md"), "title": "Unknown", "kind": "knowledge"},
        ],
        registry,
    )
    selected = ranked["results"][0]
    assert selected["utility"]["evidence_grade"] == "A"
    assert selected["utility"]["successful_reuse_count"] == 4
    assert selected["utility"]["last_feedback"] == "useful"
    assert ranked["reuse"]["selected_path"] == str(tmp_path / "validated.md")
    assert rank_results([], registry)["reuse"] is None
    assert rank_results(
        [{"path": str(tmp_path / "unknown.md"), "title": "Unknown", "kind": "knowledge"}],
        registry,
    )["reuse"] is None


def test_guarded_evolution_reverts_without_held_out_pass(tmp_path: Path) -> None:
    result = evaluate_guard(tmp_path, "workflow-a", 0.80, 0.95, held_out_pass=False)
    assert result["status"] == "reverted"
    assert result["apply_allowed"] is False
    assert result["rollback"]["champion_preserved"] is True
    decision = json.loads((Path(result["run_dir"]) / "decision.json").read_text(encoding="utf-8"))
    assert decision["promotion_required"] is True


def test_guarded_evolution_allows_experiment_only_after_gates(tmp_path: Path) -> None:
    result = evaluate_guard(tmp_path, "workflow-a", 0.80, 0.90, held_out_pass=True)
    assert result["status"] == "candidate_accepted_for_experiment"
    assert result["apply_allowed"] is True
    assert result["promotion_required"] is True
    assert set(result["rollback"]) == {"performed", "champion_preserved", "reason"}


def test_provenance_records_hashes_without_copying_inputs(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    output = tmp_path / "output.txt"
    source.write_text("private source", encoding="utf-8")
    output.write_text("derived result", encoding="utf-8")
    result = record_run(tmp_path, "hash-test", ["mycevo", "status"], inputs=[source], outputs=[output])
    run_dir = Path(result["run_dir"])
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert run["input_hashes"][str(source)]
    assert run["output_hashes"][str(output)]
    assert "private source" not in (run_dir / "run.json").read_text(encoding="utf-8")
    assert len(manifest["artifacts"]) == 2
    assert (run_dir / "trace.jsonl").read_text(encoding="utf-8").count("run_recorded") == 1


def test_mcp_resolves_engine_and_workspace_separately(tmp_path: Path) -> None:
    workspace = tmp_path / "private-instance"
    workspace.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["MYCEVO_ENGINE_ROOT"] = str(REPO_ROOT)
    env["MYCEVO_ROOT"] = str(workspace)
    code = (
        "import importlib.util; from pathlib import Path; "
        "spec=importlib.util.spec_from_file_location('mycevo_mcp_check', r'"
        + str(REPO_ROOT / "mcp" / "mycevo_mcp.py")
        + "'); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
        "assert mod.ENGINE_ROOT == Path(r'"
        + str(REPO_ROOT)
        + "').resolve(); assert mod.ROOT == Path(r'"
        + str(workspace)
        + "').resolve()"
    )
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout


def test_cli_self_evolution_keeps_candidate_first(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    project = seed_instance(instance)
    intake = tmp_path / "intake.yaml"
    intake.write_text(
        yaml.safe_dump(
            {
                "schema": "research_harness_self_evolution_intake.v1",
                "trigger": "自进化",
                "project_root": str(project),
                "project_id": "demo",
                "task": {
                    "title": "CLI writeback",
                    "summary": "Exercise candidate-first writeback through the packaged CLI.",
                    "source_refs": [str(project / "README.md")],
                    "evidence_refs": [str(project / "README.md")],
                    "verification_commands": ["researchloop self-evolution run"],
                },
                "candidates": {
                    "knowledge": [{"title": "CLI Knowledge", "category": "system_engineering", "status": "validated"}],
                    "research_assets": [{"title": "CLI Asset", "status": "reusable"}],
                    "decisions": [{"title": "CLI Decision", "status": "approved", "decision": "Keep wrappers candidate-first."}],
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = run_cli(instance, "self-evolution", "run", "--intake", str(intake), "--apply-candidates", "--skip-validation", "--json")
    assert result.returncode == 0, result.stderr + result.stdout
    knowledge = yaml.safe_load((instance / "registry" / "knowledge.yaml").read_text(encoding="utf-8"))["knowledge"]
    assets = yaml.safe_load((instance / "registry" / "research_assets.yaml").read_text(encoding="utf-8"))["research_assets"]
    decisions = yaml.safe_load((instance / "registry" / "decisions.yaml").read_text(encoding="utf-8"))["decisions"]
    assert knowledge[0]["status"] == "pending validation"
    assert assets[0]["status"] == "candidate"
    assert decisions[0]["status"] == "pending validation"


@pytest.mark.skipif(importlib.util.find_spec("fastmcp") is None, reason="fastmcp is not installed in this environment")
def test_cli_mcp_self_test_starts(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    seed_instance(instance)
    result = run_cli(instance, "mcp", "self-test")
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True


def test_legacy_researchloop_import_points_to_mycevo() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", "import researchloop, mycevo; assert researchloop.__version__ == mycevo.__version__"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_legacy_resevo_import_points_to_mycevo() -> None:
    import mycevo
    import resevo

    assert resevo.__version__ == mycevo.__version__


def test_mcp_command_injects_mycevo_roots(tmp_path: Path) -> None:
    paths = Paths(engine=tmp_path / "engine", workspace=tmp_path / "workspace", user=tmp_path / "user")
    for agent, flag in (("codex", "--env"), ("claude", "-e")):
        command = mcp_installer.command_for(agent, "add", paths)
        assert flag in command
        assert f"MYCEVO_ENGINE_ROOT={paths.engine}" in command
        assert f"MYCEVO_ROOT={paths.workspace}" in command
        assert command[-2:] == ["mcp", "serve"]


def test_migrate_resevo_previews_then_backs_up_without_deleting(tmp_path: Path) -> None:
    instance = tmp_path / "workspace"
    legacy = instance / ".resevo"
    legacy.mkdir(parents=True)
    (legacy / "config.yaml").write_text("product: Resevo\nversion: 1\n", encoding="utf-8")
    preview = run_cli(instance, "migrate", "resevo")
    assert preview.returncode == 0, preview.stderr + preview.stdout
    assert legacy.exists()
    applied = run_cli(instance, "migrate", "resevo", "--apply")
    assert applied.returncode == 0, applied.stderr + applied.stdout
    assert legacy.exists()
    assert (instance / ".mycevo" / "config.yaml").exists()
    assert (instance / ".mycevo-migration-backup" / "resevo" / "config.yaml").exists()
