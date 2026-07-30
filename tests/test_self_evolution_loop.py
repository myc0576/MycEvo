from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / "scripts"))

import self_evolution_loop as loop


def seed_registry(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"version: 1\n{name}: []\n", encoding="utf-8")


@pytest.fixture()
def isolated_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ResearchLoop"
    registry = root / "registry"
    runs = root / "runs"
    knowledge = tmp_path / "knowledge" / "reusable_knowledge"
    prompts = tmp_path / "knowledge" / "reusable_prompts"
    projects = tmp_path / "projects"
    for name in [
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
        "ppt_assets",
        "model_assets",
        "feedback",
    ]:
        seed_registry(registry / f"{name}.yaml", name)
    (registry / "projects.yaml").write_text(
        f"version: 1\nprojects:\n- project_id: demo\n  name: demo\n  path: {projects / 'demo'}\n  role: test\n  closeout_required: true\n  notes: test\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(loop, "ROOT", root)
    monkeypatch.setattr(loop, "RUNS_DIR", runs)
    monkeypatch.setattr(loop, "REUSABLE_KNOWLEDGE_ROOT", knowledge)
    monkeypatch.setattr(loop, "REUSABLE_PROMPTS_ROOT", prompts)
    monkeypatch.setattr(loop, "REGISTRY_FILES", {name: registry / f"{name}.yaml" for name in loop.REGISTRY_FILES})
    monkeypatch.setattr(loop, "FORBIDDEN_HARNESS_WRITE_ROOTS", [tmp_path / "knowledge" / "_harness"])
    return tmp_path


def write_intake(path: Path, project_root: Path, status: str = "validated") -> None:
    data = {
        "schema": loop.SCHEMA,
        "trigger": "自进化",
        "project_root": str(project_root),
        "project_id": "demo",
        "task": {
            "title": "Loop capture",
            "summary": "Capture useful loop behavior.",
            "source_refs": [str(project_root / "README.md")],
            "evidence_refs": [str(project_root / "evidence.txt")],
            "verification_commands": ["python -m py_compile scripts/self_evolution_loop.py"],
        },
        "risk_boundary": ["candidate-first"],
        "candidates": {
            "knowledge": [{"title": "Loop Knowledge", "category": "system_engineering", "status": status, "steps": ["write intake"]}],
            "prompts": [{"title": "Loop Prompt", "category": "codex", "prompt_body": "Capture candidates.", "status": status}],
            "research_assets": [{"title": "Loop Asset", "status": status, "materials": ["evidence.txt"]}],
            "decisions": [{"title": "Loop Decision", "decision": "Use one-click loop.", "status": status}],
            "workflow_improvements": [{"pain_point": "Manual closeout is easy to skip.", "proposed_fix": "Run self-evolution loop.", "status": status}],
        },
        "next_bottleneck": "promote only after evidence",
    }
    loop.write_yaml(path, data)


def test_trigger_terms_include_broad_keywords() -> None:
    assert "自进化" in loop.TRIGGER_TERMS
    assert "沉淀到 harness" in loop.TRIGGER_TERMS
    assert "查历史" in loop.TRIGGER_TERMS


def test_forbidden_status_is_downgraded() -> None:
    warnings: list[str] = []
    assert loop.safe_status("knowledge", "validated", warnings) == "pending validation"
    assert loop.safe_status("research_assets", "reusable", warnings) == "candidate"
    assert warnings


def test_dry_run_writes_no_registry_candidates(isolated_harness: Path) -> None:
    project = isolated_harness / "projects" / "demo"
    project.mkdir(parents=True)
    intake = isolated_harness / "intake.yaml"
    write_intake(intake, project)
    result = loop.execute_run(intake, apply=False, run_validators=False)
    assert result["ok"] is True
    assert result["written"] == []
    assert Path(result["contract"]).exists()
    assert Path(result["state"]).exists()
    assert Path(result["trace"]).exists()


def test_apply_candidates_dedupes_and_keeps_candidate_first(isolated_harness: Path) -> None:
    project = isolated_harness / "projects" / "demo"
    project.mkdir(parents=True)
    intake = isolated_harness / "intake.yaml"
    write_intake(intake, project, status="validated")
    result = loop.execute_run(intake, apply=True, run_validators=False)
    assert result["ok"] is True
    assert result["written"]
    knowledge = loop.read_yaml(loop.REGISTRY_FILES["knowledge"])["knowledge"]
    assets = loop.read_yaml(loop.REGISTRY_FILES["research_assets"])["research_assets"]
    decisions = loop.read_yaml(loop.REGISTRY_FILES["decisions"])["decisions"]
    assert knowledge[0]["status"] == "pending validation"
    assert assets[0]["status"] == "candidate"
    assert decisions[0]["status"] == "pending validation"
    assert len({item["id"] for item in knowledge}) == len(knowledge)


def test_resume_completed_run_does_not_duplicate(isolated_harness: Path) -> None:
    project = isolated_harness / "projects" / "demo"
    project.mkdir(parents=True)
    intake = isolated_harness / "intake.yaml"
    write_intake(intake, project, status="pending validation")
    result = loop.execute_run(intake, apply=True, run_validators=False)
    resumed = loop.resume(result["run_id"])
    assert resumed["status"] == "already_completed"
    knowledge = loop.read_yaml(loop.REGISTRY_FILES["knowledge"])["knowledge"]
    assert len(knowledge) == 1


def test_resume_rejects_non_self_evolution_run(isolated_harness: Path) -> None:
    run_id = "20260701_000000_closeout-check_test"
    run_dir = loop.RUNS_DIR / loop.today() / run_id
    run_dir.mkdir(parents=True)
    loop.write_yaml(run_dir / "contract.yaml", {"schema": "research_harness_closeout_contract.v1"})
    loop.write_json(run_dir / "state.json", {"status": "open"})
    result = loop.resume(run_id)
    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "not a self-evolution" in result["error"]
