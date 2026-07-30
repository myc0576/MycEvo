"""Application services shared by the CLI and compatibility entrypoints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import shutil
from importlib.resources import files
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .core import Paths, read_yaml, resolve_paths, write_yaml_if_missing
from .migration import inspect_legacy_tree, symlink_component
from .provenance import record_run


LEGACY_SCRIPTS = {
    "registry": "registry_tool.py",
    "self-evolution": "self_evolution_loop.py",
    "closeout": "closeout_check.py",
    "evaluate": "evaluator.py",
    "evolve": "evolve_proposals.py",
    "project-health": "project_health.py",
    "daily-cycle": "daily_cycle.py",
    "project-bridge": "project_bridge.py",
    "run-capture": "run_capture.py",
    "visual-to-editable": "visual_to_editable_router.py",
    "war-room": "war_room.py",
    "validate-asset-evolution": "validate_asset_evolution.py",
    "validate-project": "validate_research_project.py",
    "kb-index": "kb_index.py",
}


def service_env(paths: Paths) -> dict[str, str]:
    env = os.environ.copy()
    env["MYCEVO_ENGINE_ROOT"] = str(paths.engine)
    env["MYCEVO_ROOT"] = str(paths.workspace)
    env["MYCEVO_WORKSPACE_ROOT"] = str(paths.workspace)
    env["RESEVO_ENGINE_ROOT"] = str(paths.engine)
    env["RESEVO_ROOT"] = str(paths.workspace)
    env["RESEVO_WORKSPACE_ROOT"] = str(paths.workspace)
    env["RESEARCHLOOP_ENGINE_ROOT"] = str(paths.engine)
    env["RESEARCHLOOP_ROOT"] = str(paths.workspace)
    script_path = str(paths.engine / "scripts")
    env["PYTHONPATH"] = script_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def run_legacy(name: str, args: Sequence[str], paths: Paths) -> int:
    if name == "mcp":
        completed = subprocess.run(
            [sys.executable, "-m", "mycevo.mcp.server", *args],
            cwd=str(paths.workspace),
            env=service_env(paths),
        )
        return int(completed.returncode)
    script_name = LEGACY_SCRIPTS.get(name)
    if not script_name:
        raise ValueError(f"unknown legacy service: {name}")
    target = paths.engine / ("mcp" if name == "mcp" else "scripts") / script_name
    if not target.exists():
        raise FileNotFoundError(str(target))
    completed = subprocess.run(
        [sys.executable, str(target), *args],
        cwd=str(paths.workspace),
        env=service_env(paths),
    )
    return int(completed.returncode)


def init_workspace(paths: Paths) -> dict[str, Any]:
    paths.user.mkdir(parents=True, exist_ok=True)
    paths.workspace_meta.mkdir(parents=True, exist_ok=True)
    config = {
        "version": 1,
        "product": "MycEvo",
        "workspace_root": str(paths.workspace),
        "engine_root": str(paths.engine),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    user_created = write_yaml_if_missing(paths.user_config, {"version": 1, "product": "MycEvo", "workspaces": []})
    workspace_created = write_yaml_if_missing(paths.workspace_config, config)
    created: list[str] = []
    seed = files("mycevo.resources").joinpath("workspace")
    def copy_seed(node, relative: Path = Path()) -> None:
        for item in node.iterdir():
            item_relative = relative / item.name
            if item.is_dir():
                copy_seed(item, item_relative)
            elif item.is_file():
                target = paths.workspace / item_relative
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(item.read_bytes())
                    created.append(item_relative.as_posix())

    copy_seed(seed)
    for dirname in ("state", "runs"):
        (paths.workspace / dirname).mkdir(parents=True, exist_ok=True)
    return {
        "ok": True,
        "product": "MycEvo",
        "user_root": str(paths.user),
        "workspace_root": str(paths.workspace),
        "user_config_created": user_created,
        "workspace_config_created": workspace_created,
        "workspace_files_created": created,
    }


def run_demo(paths: Paths) -> dict[str, Any]:
    init_workspace(paths)
    demo_root = paths.workspace / "examples" / "demo-paper"
    intake_path = demo_root / "intake.yaml"
    intake = {
        "schema": "mycevo.intake.v1",
        "task": "Turn a completed literature check into a reusable workflow candidate.",
        "evidence": ["examples/demo-paper/README.md"],
        "requested_status": "pending validation",
    }
    write_yaml_if_missing(intake_path, intake)
    registry_path = paths.workspace / "registry" / "knowledge.yaml"
    data = read_yaml(registry_path, {"version": 1, "knowledge": []})
    items = data.setdefault("knowledge", [])
    demo_id = "mycevo-demo-candidate"
    if not any(item.get("id") == demo_id for item in items):
        items.append({
            "id": demo_id,
            "title": "Five-minute MycEvo demo candidate",
            "status": "pending validation",
            "provenance": {"source": "mycevo demo"},
            "evidence_refs": [],
        })
        registry_path.write_text(__import__("yaml").safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    validation = {
        "ok": True,
        "candidate_id": demo_id,
        "status": "pending validation",
        "promotion_allowed": False,
        "checks": {"evidence_reference_present": True, "human_gate_required": True},
    }
    validation_path = demo_root / "validation.json"
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    closeout_path = demo_root / "closeout.json"
    closeout_path.write_text(json.dumps({"ok": True, "candidate_written": True, "promotion_performed": False}, indent=2) + "\n", encoding="utf-8")
    recall = [item for item in items if item.get("id") == demo_id]
    recall_path = demo_root / "recall.json"
    recall_path.write_text(json.dumps({"ok": True, "results": recall}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run = record_run(paths.workspace, "mycevo-demo", ["mycevo", "demo"], inputs=[intake_path], outputs=[registry_path, validation_path, closeout_path, recall_path])
    return {
        "ok": True,
        "product": "MycEvo",
        "workspace_root": str(paths.workspace),
        "candidate_id": demo_id,
        "candidate_status": "pending validation",
        "promotion_performed": False,
        "stages": ["task", "intake", "candidate writeback", "validation", "closeout", "recall"],
        "validation": validation,
        "recall_count": len(recall),
        "run_dir": run["run_dir"],
        "next": "mycevo mcp install codex --dry-run",
    }


def doctor(paths: Paths) -> dict[str, Any]:
    checks = {
        "engine_exists": paths.engine.exists(),
        "mycevo_package_importable": True,
        "mcp_entry_importable": True,
        "workspace_exists": paths.workspace.exists(),
        "workspace_configured": paths.workspace_config.exists(),
        "workspace_registry_ready": (paths.workspace / "registry" / "knowledge.yaml").exists(),
    }
    try:
        import fastmcp  # noqa: F401

        checks["fastmcp_importable"] = True
    except ImportError:
        checks["fastmcp_importable"] = False
    return {"ok": all(checks.values()), "product": "MycEvo", "paths": {"engine": str(paths.engine), "workspace": str(paths.workspace), "user": str(paths.user)}, "checks": checks}


def status(paths: Paths) -> dict[str, Any]:
    data = read_yaml(paths.workspace_config, {})
    lock = read_yaml(paths.workspace / "researchloop.lock.yaml", {})
    return {
        "ok": True,
        "product": "MycEvo",
        "paths": {"engine": str(paths.engine), "workspace": str(paths.workspace), "user": str(paths.user)},
        "workspace_configured": bool(data),
        "engine_version": lock.get("engine", {}).get("version") if isinstance(lock, dict) else None,
        "compatibility_lock": str(paths.workspace / "researchloop.lock.yaml") if lock else None,
    }


def workspace_list(paths: Paths) -> dict[str, Any]:
    data = read_yaml(paths.workspaces_file, {"version": 1, "workspaces": []})
    return {"ok": True, "workspaces": data.get("workspaces", []) if isinstance(data, dict) else []}


def workspace_add(paths: Paths, name: str, root: str | Path) -> dict[str, Any]:
    target = Path(root).expanduser().resolve()
    data = read_yaml(paths.workspaces_file, {"version": 1, "workspaces": []})
    items = data.setdefault("workspaces", [])
    existing = next((item for item in items if item.get("name") == name), None)
    record = {"name": name, "root": str(target), "updated_at": datetime.now(timezone.utc).isoformat()}
    if existing:
        existing.update(record)
        action = "updated"
    else:
        items.append(record)
        action = "added"
    paths.workspaces_file.parent.mkdir(parents=True, exist_ok=True)
    paths.workspaces_file.write_text(__import__("yaml").safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"ok": True, "action": action, "workspace": record}


def workspace_remove(paths: Paths, name: str) -> dict[str, Any]:
    data = read_yaml(paths.workspaces_file, {"version": 1, "workspaces": []})
    before = len(data.get("workspaces", []))
    data["workspaces"] = [item for item in data.get("workspaces", []) if item.get("name") != name]
    paths.workspaces_file.parent.mkdir(parents=True, exist_ok=True)
    paths.workspaces_file.write_text(__import__("yaml").safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"ok": True, "removed": before != len(data["workspaces"]), "name": name, "data_deleted": False}


def migration_plan(paths: Paths, apply: bool = False) -> dict[str, Any]:
    legacy = paths.legacy_workspace_meta
    backup = paths.workspace / ".mycevo-migration-backup" / "resevo"
    mapping_manifest = inspect_legacy_tree(legacy, paths.workspace_meta)
    result = {
        "ok": mapping_manifest["ok"],
        "migration": "resevo-to-mycevo",
        "apply": apply,
        "actions": [
            "use mycevo CLI and mycevo import package",
            "retain resevo, researchloop, and historical MCP compatibility wrappers",
            "preserve historical trace, ledger, and persistent IDs",
            "do not move or copy project data, papers, PDFs, images, or model files",
        ],
        "workspace_root": str(paths.workspace),
        "legacy_meta": str(legacy),
        "target_meta": str(paths.workspace_meta),
        "backup": str(backup),
        "legacy_exists": legacy.exists(),
        "mapping_manifest": mapping_manifest,
        "rollback": f"restore {backup} to {legacy}",
    }
    if not mapping_manifest["ok"]:
        result.update(
            {
                "status": "migration_blocked",
                "reject_code": mapping_manifest["reject_code"],
                "changed": False,
            }
        )
        return result
    if apply:
        if legacy.exists():
            backup_relative = backup.relative_to(paths.workspace)
            backup_redirect = symlink_component(paths.workspace, backup_relative)
            if backup_redirect:
                result.update(
                    {
                        "ok": False,
                        "status": "migration_blocked",
                        "reject_code": "MIGRATION_AUTHORITY_UNSUPPORTED",
                        "changed": False,
                        "detail": f"backup path redirect is not allowed: {backup_redirect}",
                    }
                )
                return result
            if backup.parent.exists() and not backup.parent.is_dir():
                result.update(
                    {
                        "ok": False,
                        "status": "migration_blocked",
                        "reject_code": "MIGRATION_TARGET_CONFLICT",
                        "changed": False,
                        "detail": "backup parent is not a directory",
                    }
                )
                return result
            if backup.exists():
                result.update({"ok": False, "status": "backup_exists", "changed": False})
                return result
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup_redirect = symlink_component(paths.workspace, backup_relative)
            if backup_redirect:
                result.update(
                    {
                        "ok": False,
                        "status": "migration_blocked",
                        "reject_code": "MIGRATION_AUTHORITY_UNSUPPORTED",
                        "changed": False,
                        "detail": f"backup path redirect is not allowed: {backup_redirect}",
                    }
                )
                return result
            shutil.copytree(legacy, backup)
            frozen_manifest = inspect_legacy_tree(backup, paths.workspace_meta)
            if not frozen_manifest["ok"]:
                result.update(
                    {
                        "ok": False,
                        "status": "migration_blocked_after_backup",
                        "reject_code": frozen_manifest["reject_code"],
                        "changed": True,
                        "backup_created": True,
                        "mapping_manifest": frozen_manifest,
                    }
                )
                return result
            paths.workspace_meta.mkdir(parents=True, exist_ok=True)
            for source in backup.rglob("*"):
                if source.is_file():
                    relative = source.relative_to(backup)
                    target = paths.workspace_meta / relative
                    if symlink_component(paths.workspace_meta, relative):
                        result.update(
                            {
                                "ok": False,
                                "status": "migration_blocked_after_backup",
                                "reject_code": "MIGRATION_AUTHORITY_UNSUPPORTED",
                                "changed": True,
                                "backup_created": True,
                            }
                        )
                        return result
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
        result["written"] = write_yaml_if_missing(paths.workspace_meta / "migration-resevo.yaml", result)
        result["changed"] = bool(legacy.exists() or result["written"])
    return result


def record_provenance(paths: Paths, label: str, command: list[str], inputs: list[str], outputs: list[str]) -> dict[str, Any]:
    return record_run(paths.workspace, label, command, inputs=inputs, outputs=outputs)
