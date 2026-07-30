from __future__ import annotations

import hashlib
import json
import base64
import os
from pathlib import Path
import subprocess
import sys

import yaml
import pytest

from mycevo.core import Paths
from mycevo.migration import classify_legacy_mapping
from mycevo.services import migration_plan


FIXTURE = Path(__file__).parent / "fixtures" / "migration" / "resevo_v1_manifest.json"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _golden() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _seed_legacy_tree(workspace: Path) -> tuple[Path, dict[str, bytes]]:
    legacy = workspace / ".resevo"
    files: dict[str, bytes] = {}
    for entry in _golden()["files"]:
        relative = str(entry["path"])
        content = (
            base64.b64decode(str(entry["content_base64"]))
            if "content_base64" in entry
            else str(entry["content"]).encode("utf-8")
        )
        if "sha256" in entry:
            assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        target = legacy / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        files[relative] = content
    return legacy, files


def test_migration_golden_classes_are_explicit_and_disjoint() -> None:
    classes = _golden()["classifications"]
    expected = {"lossless", "lossy_candidate_only", "ambiguous", "rejected_unrepresentable"}
    assert set(classes) == expected
    values = [item for group in classes.values() for item in group]
    assert len(values) == len(set(values))
    assert "opaque_automatic_execution_authority" in classes["rejected_unrepresentable"]


def test_migration_mapping_cases_match_fail_closed_classifier() -> None:
    for case in _golden()["mapping_cases"]:
        result = classify_legacy_mapping(case)
        assert result["mapping_class"] == case["mapping_class"], case["name"]
        if "reject_code" in case:
            assert result["ok"] is False
            assert result["reject_code"] == case["reject_code"]
        else:
            assert result["promotion_allowed"] is False
            assert result["target_status"] == case["target_status"]


def test_legacy_authority_flags_are_candidate_only() -> None:
    for flag in ("apply_allowed", "promotion_allowed", "promotion_performed", "automatic_promotion", "auto_promote", "approved", "human_approved"):
        result = classify_legacy_mapping(
            {
                "source_object_type": "knowledge",
                "source_id": "k-authority",
                "source_status": "pending validation",
                flag: True,
            }
        )
        assert result["ok"] is True
        assert result["mapping_class"] == "lossy_candidate_only"
        assert result["target_status"] == "candidate"
        assert result["promotion_allowed"] is False
        assert result["legacy_authority_flags"] == {flag: True}

    false_control = classify_legacy_mapping(
        {
            "source_object_type": "knowledge",
            "source_id": "k-control",
            "source_status": "pending validation",
            "promotion_allowed": False,
        }
    )
    assert false_control["mapping_class"] == "lossless"


def test_registry_identity_keys_are_preserved_and_missing_id_blocks(tmp_path: Path) -> None:
    cases = {
        "projects": ("project_id", "project-stable-001"),
        "output_objects": ("output_id", "output-stable-001"),
        "asset_evolution": ("asset_id", "asset-stable-001"),
        "workflow_improvement_backlog": ("issue_id", "issue-stable-001"),
    }
    for collection, (key, value) in cases.items():
        workspace = tmp_path / collection
        registry = workspace / ".resevo" / "registry" / f"{collection}.yaml"
        registry.parent.mkdir(parents=True)
        registry.write_text(
            yaml.safe_dump({"version": 1, collection: [{key: value, "status": "candidate"}]}, sort_keys=False),
            encoding="utf-8",
        )
        result = migration_plan(Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user"), apply=False)
        semantic = next(row for row in result["mapping_manifest"]["mappings"] if row.get("source_id"))
        assert semantic["source_id_key"] == key
        assert semantic["source_id"] == value
        assert semantic["mapping_class"] == "lossless"

    workspace = tmp_path / "missing-id"
    registry = workspace / ".resevo" / "registry" / "knowledge.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("version: 1\nknowledge:\n- status: candidate\n", encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")
    blocked = migration_plan(paths, apply=True)
    assert blocked["ok"] is False
    assert blocked["reject_code"] == "MIGRATION_ID_UNSTABLE"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_migration_preview_is_side_effect_free_and_stable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    legacy, _ = _seed_legacy_tree(workspace)
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")
    before = _tree_hashes(legacy)

    first = migration_plan(paths, apply=False)
    second = migration_plan(paths, apply=False)

    assert first == second
    assert first["apply"] is False
    assert first["legacy_exists"] is True
    assert _tree_hashes(legacy) == before
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_migration_apply_preserves_bytes_ids_status_and_history(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    legacy, original_files = _seed_legacy_tree(workspace)
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is True
    assert result["changed"] is True
    assert legacy.exists()
    backup = workspace / ".mycevo-migration-backup" / "resevo"
    for relative, original in original_files.items():
        assert (legacy / relative).read_bytes() == original
        assert (backup / relative).read_bytes() == original
        assert (paths.workspace_meta / relative).read_bytes() == original

    registry = yaml.safe_load((paths.workspace_meta / "registry" / "knowledge.yaml").read_text(encoding="utf-8"))
    record = registry["knowledge"][0]
    assert record["id"] == _golden()["expected"]["persistent_id"]
    assert record["status"] == _golden()["expected"]["status"]
    assert record["provenance"]["run_id"] == "run-stable-001"


def test_migration_repeated_apply_fails_closed_without_rewrite(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    legacy, _ = _seed_legacy_tree(workspace)
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")
    first = migration_plan(paths, apply=True)
    before = {
        "legacy": _tree_hashes(legacy),
        "backup": _tree_hashes(workspace / ".mycevo-migration-backup" / "resevo"),
        "target": _tree_hashes(paths.workspace_meta),
    }

    second = migration_plan(paths, apply=True)

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["status"] == _golden()["expected"]["second_apply_status"]
    assert second["changed"] is False
    assert _tree_hashes(legacy) == before["legacy"]
    assert _tree_hashes(workspace / ".mycevo-migration-backup" / "resevo") == before["backup"]
    assert _tree_hashes(paths.workspace_meta) == before["target"]


def test_unknown_lifecycle_status_rejects_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    registry = workspace / ".resevo" / "registry" / "knowledge.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("version: 1\nknowledge:\n- id: k-1\n  status: magically_done\n", encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_STATUS_UNKNOWN"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_future_registry_version_rejects_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    registry = workspace / ".resevo" / "registry" / "knowledge.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("version: 99\nknowledge: []\n", encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_SCHEMA_UNKNOWN"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_future_config_version_rejects_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config = workspace / ".resevo" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("product: Resevo\nversion: 2\n", encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_SCHEMA_UNKNOWN"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


@pytest.mark.parametrize("document", [
    {"product": "Resevo"},
    {"product": "Resevo", "version": 0},
    {"product": "Resevo", "version": -1},
    {"product": "Resevo", "version": True},
    {"product": "Resevo", "version": None},
    {"product": "Resevo", "version": "1"},
    {"product": "Resevo", "version": 1.5},
])
def test_unsupported_config_version_forms_reject_without_writes(tmp_path: Path, document: dict[str, object]) -> None:
    workspace = tmp_path / "workspace"
    config = workspace / ".resevo" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump(document), encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_SCHEMA_UNKNOWN"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_nonnumeric_registry_version_rejects_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    registry = workspace / ".resevo" / "registry" / "knowledge.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("version: future\nknowledge: []\n", encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_SCHEMA_UNKNOWN"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_registry_list_root_rejects_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    registry = workspace / ".resevo" / "registry" / "knowledge.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("- id: k-1\n  status: candidate\n", encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_SCHEMA_UNKNOWN"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


@pytest.mark.parametrize("version", [0, -1, True, None, "1", 1.5])
def test_unsupported_registry_version_forms_reject_without_writes(tmp_path: Path, version: object) -> None:
    workspace = tmp_path / "workspace"
    registry = workspace / ".resevo" / "registry" / "knowledge.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text(yaml.safe_dump({"version": version, "knowledge": []}), encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_SCHEMA_UNKNOWN"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_malformed_registry_yaml_rejects_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    registry = workspace / ".resevo" / "registry" / "knowledge.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("version: 1\nknowledge: [unterminated\n", encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_SCHEMA_UNKNOWN"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_opaque_executable_hook_rejects_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    hook = workspace / ".resevo" / "hooks" / "before_apply.yaml"
    hook.parent.mkdir(parents=True)
    hook.write_text("shell: powershell -Command do-something\n", encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_AUTHORITY_UNSUPPORTED"
    assert not paths.workspace_meta.exists()
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_existing_target_conflict_is_explicit_and_non_mutating(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    legacy, _ = _seed_legacy_tree(workspace)
    target = workspace / ".mycevo" / "config.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("product: different\n", encoding="utf-8")
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")
    before = {"legacy": _tree_hashes(legacy), "target": _tree_hashes(paths.workspace_meta)}

    result = migration_plan(paths, apply=True)

    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_TARGET_CONFLICT"
    assert result["changed"] is False
    assert _tree_hashes(legacy) == before["legacy"]
    assert _tree_hashes(paths.workspace_meta) == before["target"]
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_nonregular_migration_entries_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    legacy = workspace / ".resevo"
    source = legacy / "config.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("product: Resevo\nversion: 1\n", encoding="utf-8")
    target_directory = workspace / ".mycevo" / "config.yaml"
    target_directory.mkdir(parents=True)
    paths = Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user")

    directory_conflict = migration_plan(paths, apply=True)

    assert directory_conflict["ok"] is False
    assert directory_conflict["reject_code"] == "MIGRATION_TARGET_CONFLICT"
    assert not (workspace / ".mycevo-migration-backup").exists()

    target_directory.rmdir()
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")
    link = legacy / "external-link.txt"
    try:
        link.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    symlink_result = migration_plan(paths, apply=True)

    assert symlink_result["ok"] is False
    assert symlink_result["reject_code"] == "MIGRATION_AUTHORITY_UNSUPPORTED"
    assert not (workspace / ".mycevo-migration-backup").exists()


def test_target_symlink_paths_fail_closed_without_external_writes(tmp_path: Path) -> None:
    for variant in ("same-byte-file", "broken-file", "parent", "target-root", "legacy-root"):
        case = tmp_path / variant
        workspace = case / "workspace"
        external = case / "external"
        external.mkdir(parents=True)
        legacy = workspace / ".resevo"
        config = legacy / "config.yaml"
        config.parent.mkdir(parents=True)
        config_bytes = b"product: Resevo\nversion: 1\n"
        config.write_bytes(config_bytes)
        target_root = workspace / ".mycevo"
        try:
            if variant == "same-byte-file":
                external_file = external / "config.yaml"
                external_file.write_bytes(config_bytes)
                target_root.mkdir(parents=True)
                (target_root / "config.yaml").symlink_to(external_file)
            elif variant == "broken-file":
                target_root.mkdir(parents=True)
                (target_root / "config.yaml").symlink_to(external / "missing.yaml")
            elif variant == "parent":
                (external / "registry").mkdir()
                target_root.mkdir(parents=True)
                (target_root / "registry").symlink_to(external / "registry", target_is_directory=True)
                registry = legacy / "registry" / "knowledge.yaml"
                registry.parent.mkdir(parents=True)
                registry.write_text("version: 1\nknowledge: []\n", encoding="utf-8")
            elif variant == "target-root":
                target_root.symlink_to(external, target_is_directory=True)
            else:
                real_legacy = case / "real-legacy"
                legacy.rename(real_legacy)
                legacy.symlink_to(real_legacy, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink creation is unavailable: {exc}")

        before = {path.relative_to(external).as_posix(): path.read_bytes() for path in external.rglob("*") if path.is_file()}
        result = migration_plan(Paths(engine=case / "engine", workspace=workspace, user=case / "user"), apply=True)
        after = {path.relative_to(external).as_posix(): path.read_bytes() for path in external.rglob("*") if path.is_file()}

        assert result["ok"] is False, variant
        assert result["reject_code"] == "MIGRATION_AUTHORITY_UNSUPPORTED", variant
        assert before == after, variant
        assert not (workspace / ".mycevo-migration-backup").exists(), variant


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction regression is Windows-specific")
def test_backup_junction_fails_closed_without_external_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    config = workspace / ".resevo" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("product: Resevo\nversion: 1\n", encoding="utf-8")
    external = tmp_path / "external-backup"
    external.mkdir()
    redirect = workspace / ".mycevo-migration-backup"
    redirect.parent.mkdir(parents=True, exist_ok=True)
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(redirect), str(external)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {created.stderr or created.stdout}")
    before = sorted(path.relative_to(external).as_posix() for path in external.rglob("*"))

    result = migration_plan(Paths(engine=tmp_path / "engine", workspace=workspace, user=tmp_path / "user"), apply=True)

    after = sorted(path.relative_to(external).as_posix() for path in external.rglob("*"))
    assert result["ok"] is False
    assert result["reject_code"] == "MIGRATION_AUTHORITY_UNSUPPORTED"
    assert result["changed"] is False
    assert before == after
    assert not (external / "resevo").exists()
    assert not (workspace / ".mycevo").exists()

def test_cli_migration_block_returns_nonzero_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    registry = workspace / ".resevo" / "registry" / "knowledge.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("version: 1\nknowledge:\n- id: k-1\n  status: magically_done\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-m", "mycevo.cli", "--workspace-root", str(workspace), "migrate", "resevo", "--apply"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["reject_code"] == "MIGRATION_STATUS_UNKNOWN"
    assert not (workspace / ".mycevo").exists()
    assert not (workspace / ".mycevo-migration-backup").exists()
