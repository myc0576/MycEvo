from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from _harness_common import (
    REGISTRY_FILES,
    REUSABLE_KNOWLEDGE_ROOT,
    REUSABLE_PROMPTS_ROOT,
    PROJECTS_ROOT,
    STATE_DIR,
    append_ledger,
    contains_bad_encoding,
    file_date,
    first_heading,
    parse_status_from_markdown,
    path_exists,
    read_yaml,
    slugify_project_id,
    today,
    confidence_for_status,
    clamp_confidence,
    write_json,
    write_yaml,
)


REQUIRED_FIELDS = {
    "projects": ["project_id", "name", "path", "role", "closeout_required", "notes"],
    "knowledge": ["id", "title", "category", "path", "source_project", "status", "verification", "evidence_refs", "updated_at"],
    "prompts": ["id", "title", "category", "path", "task", "model", "status", "updated_at"],
    "research_assets": ["id", "project_id", "task_name", "asset_dir", "intended_uses", "manifest", "reproduction_entry", "status"],
    "papers": [
        "id",
        "title",
        "source_project",
        "status",
        "target_journal",
        "path",
        "manuscript_root",
        "output_write_policy",
        "core_claim",
        "figures",
        "existing_evidence",
        "missing_evidence",
        "overclaim_boundaries",
        "updated_at",
    ],
    "literature": [
        "id",
        "title",
        "source_project",
        "status",
        "path",
        "citation_key",
        "pdf_policy",
        "evidence_use",
        "citation_locations",
        "updated_at",
    ],
    "figures": [
        "id",
        "title",
        "source_project",
        "status",
        "path",
        "visual_ref_dir",
        "figure_type",
        "claim_id",
        "source",
        "evidence_use",
        "updated_at",
    ],
    "output_objects": ["output_id", "output_type", "title", "source_project", "status", "target_venue", "linked_project_root", "updated_at"],
    "asset_evolution": [
        "asset_id",
        "asset_type",
        "title",
        "source_project",
        "source_output_objects",
        "current_version",
        "status",
        "quality_score",
        "reuse_count",
        "applicable_contexts",
        "non_applicable_contexts",
        "reproduction_entry",
        "linked_files",
        "updated_at",
    ],
    "workflow_improvement_backlog": [
        "issue_id",
        "source_retro",
        "source_project",
        "pain_point",
        "affected_workflow",
        "affected_assets",
        "proposed_fix",
        "priority",
        "status",
        "validation_method",
        "updated_at",
    ],
    "decisions": ["id", "date", "project_id", "decision", "changed_route_or_assumption", "path", "status"],
    "visual_to_editable_skills": [
        "id",
        "title",
        "upstream_url",
        "route_tags",
        "output_types",
        "status",
        "validation_method",
        "safety_notes",
        "updated_at",
    ],
    "ppt_assets": ["id", "project_id", "title", "path", "version", "status", "evidence_refs", "updated_at"],
    "model_assets": ["id", "project_id", "title", "asset_dir", "version", "status", "evidence_refs", "updated_at"],
    "feedback": ["id", "target_id", "feedback_type", "note", "created_at"],
}


KNOWN_REPAIRS = {
    "knowledge": {
        "20260623_historical_session_ingestion_workflow": {
            "title": "SC-NMT 历史 Codex 会话批量沉淀工作流",
            "verification": "扫描 551 个 session 文件，识别 72 个 SC-NMT 会话，并生成 inventory、registry 与报告。",
        },
        "20260623_sc_nmt_calibration_projection_history": {
            "title": "SC-NMT 标定与投影几何历史会话沉淀",
            "verification": "历史会话归纳，需结合 calibration artifacts 后续验证。",
        },
        "20260623_sc_nmt_scatter_alignment_history": {
            "title": "SC-NMT 预处理与散射对齐历史会话沉淀",
            "verification": "历史会话归纳，需结合 QC/approval artifacts 后续验证。",
        },
        "20260623_sc_nmt_mie_art_reconstruction_history": {
            "title": "SC-NMT ART、Geo-SART、Mie-SART 与 Mie-Pk-TV-S 历史沉淀",
            "verification": "历史会话归纳，需结合 metrics/volume exports 后续验证。",
        },
        "20260623_sc_nmt_historical_visual_materials": {
            "title": "SC-NMT 历史 PPT 与科学可视化素材沉淀",
            "verification": "历史 PPT 任务归纳，并引用 visual closeout asset。",
        },
        "20260623_sc_nmt_paper_claim_boundary_history": {
            "title": "SC-NMT 论文 claim boundary 历史沉淀",
            "verification": "结合历史 memory 与 claim boundary 归纳，需后续论文证据确认。",
        },
    },
    "prompts": {
        "20260623_batch_sc_nmt_session_ingestion_prompt": {
            "title": "批量 SC-NMT 历史 Codex 会话沉淀 Prompt",
            "task": "将 SC-NMT 历史会话批量沉淀到 harness、knowledge、research_assets 和 decision records。",
        }
    },
    "decisions": {
        "20260623_grouped_historical_session_assetization": {
            "decision": "SC-NMT 历史会话采用 inventory、主题聚类和按类别知识卡沉淀的方式处理。"
        }
    },
}


def registry_list_name(name: str) -> str:
    return name


def referenced_markdown_has_bad_encoding(item: dict[str, Any]) -> bool:
    for field in ("path", "manifest", "reproduction_entry"):
        raw = item.get(field)
        if not raw:
            continue
        path = Path(str(raw))
        if path.suffix.lower() != ".md" or not path.exists():
            continue
        if contains_bad_encoding(path.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def load_registry(name: str) -> dict[str, Any]:
    path = REGISTRY_FILES[name]
    data = read_yaml(path)
    if not data:
        data = {"version": 1, registry_list_name(name): []}
    data.setdefault("version", 1)
    data.setdefault(registry_list_name(name), [])
    return data


def save_registry(name: str, data: dict[str, Any]) -> None:
    write_yaml(REGISTRY_FILES[name], data)


def normalize_metadata(item: dict[str, Any]) -> None:
    updated = str(item.get("updated_at") or item.get("date") or datetime.now().date().isoformat())
    item.setdefault("created_at", updated)
    if item.get("status") == "validated":
        item.setdefault("last_verified_at", updated)
        item.setdefault("verification_due_days", 90)
    else:
        item.setdefault("last_verified_at", "")
        item.setdefault("verification_due_days", 7)
    if contains_bad_encoding(item) or referenced_markdown_has_bad_encoding(item):
        item["encoding_status"] = "needs_review"
    else:
        item["encoding_status"] = item.get("encoding_status", "ok")
    status = str(item.get("status") or "").strip()
    base_confidence = confidence_for_status(status)
    reference_count = int(item.get("reference_count") or 0)
    if "confidence" not in item:
        item["confidence"] = clamp_confidence(base_confidence + min(reference_count, 4) * 0.03)
    else:
        item["confidence"] = clamp_confidence(item["confidence"])
    item.setdefault("confidence_reason", f"default_from_status:{status or 'unknown'}")
    item.setdefault("reference_count", reference_count)
    item.setdefault("last_used_at", "")
    item.setdefault("last_reviewed_at", item.get("last_verified_at", ""))
    item.setdefault("quality_level", "unrated")
    item.setdefault("superseded_by", "")


def repair_known_encoding() -> dict[str, Any]:
    changed: list[str] = []
    for name, repairs in KNOWN_REPAIRS.items():
        data = load_registry(name)
        items = data.get(name, [])
        for item in items:
            item_id = item.get("id")
            if item_id in repairs:
                for key, value in repairs[item_id].items():
                    if item.get(key) != value:
                        item[key] = value
                        changed.append(f"{name}:{item_id}:{key}")
            normalize_metadata(item)
        save_registry(name, data)
    for name in REGISTRY_FILES:
        data = load_registry(name)
        for item in data.get(name, []):
            normalize_metadata(item)
        save_registry(name, data)
    result = {"changed": changed, "changed_count": len(changed)}
    write_json(STATE_DIR / "registry_repair_report.json", result)
    append_ledger("registry_repair_known_encoding", {"result": result})
    return result


def validate_registry() -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    for name in REGISTRY_FILES:
        path = REGISTRY_FILES[name]
        data = load_registry(name)
        items = data.get(name, [])
        counts[name] = len(items)
        if not isinstance(items, list):
            errors.append(f"{path}: {name}_not_list")
            continue
        ids: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{path}: item_{index}_not_dict")
                continue
            item_id = str(
                item.get("id")
                or item.get("project_id")
                or item.get("output_id")
                or item.get("asset_id")
                or item.get("issue_id")
                or f"item_{index}"
            )
            if item_id in ids:
                errors.append(f"{path}: duplicate_id:{item_id}")
            ids.add(item_id)
            for field in REQUIRED_FIELDS.get(name, []):
                if field not in item or item.get(field) in (None, ""):
                    errors.append(f"{path}: missing_{field}:{item_id}")
            if (contains_bad_encoding(item) or referenced_markdown_has_bad_encoding(item)) and item.get("encoding_status") != "needs_review":
                errors.append(f"{path}: bad_encoding_unmarked:{item_id}")
            for field in ("path", "asset_dir", "manifest", "reproduction_entry"):
                if field in item and item[field] and not path_exists(item[field]):
                    errors.append(f"{path}: missing_path:{item_id}:{field}:{item[field]}")
            for ref in item.get("evidence_refs", []) or []:
                if ref and not path_exists(ref):
                    warnings.append(f"{path}: missing_evidence_ref:{item_id}:{ref}")
    result = {"ok": not errors, "errors": errors, "warnings": warnings, "counts": counts}
    write_json(STATE_DIR / "registry_validation.json", result)
    return result


def scan_knowledge() -> dict[str, Any]:
    data = load_registry("knowledge")
    registered = {str(item.get("path")) for item in data.get("knowledge", [])}
    files = sorted(REUSABLE_KNOWLEDGE_ROOT.rglob("*.md")) if REUSABLE_KNOWLEDGE_ROOT.exists() else []
    unregistered = [str(path) for path in files if str(path) not in registered]
    missing = [str(item.get("path")) for item in data.get("knowledge", []) if item.get("path") and not path_exists(item["path"])]
    return {"file_count": len(files), "unregistered": unregistered, "missing": missing}


def scan_prompts() -> dict[str, Any]:
    data = load_registry("prompts")
    registered = {str(item.get("path")) for item in data.get("prompts", [])}
    files = sorted(REUSABLE_PROMPTS_ROOT.rglob("*.md")) if REUSABLE_PROMPTS_ROOT.exists() else []
    unregistered = [str(path) for path in files if str(path) not in registered]
    missing = [str(item.get("path")) for item in data.get("prompts", []) if item.get("path") and not path_exists(item["path"])]
    return {"file_count": len(files), "unregistered": unregistered, "missing": missing}


def scan_assets() -> dict[str, Any]:
    data = load_registry("research_assets")
    registered = {str(item.get("asset_dir")) for item in data.get("research_assets", [])}
    project_data = load_registry("projects")
    project_paths: list[Path] = []
    for item in project_data.get("projects", []):
        if not item.get("path"):
            continue
        candidate = Path(str(item["path"]))
        try:
            candidate.resolve().relative_to(PROJECTS_ROOT.resolve())
        except (OSError, ValueError):
            continue
        project_paths.append(candidate)
    asset_dirs: list[Path] = []
    for project_path in project_paths:
        root = project_path / "research_assets"
        if root.exists():
            asset_dirs.extend([p for p in root.iterdir() if p.is_dir()])
    unregistered = [str(path) for path in sorted(asset_dirs) if str(path) not in registered]
    missing_manifest = [str(path) for path in sorted(asset_dirs) if not (path / "00_asset_manifest.md").exists()]
    missing_reproduction = [str(path) for path in sorted(asset_dirs) if not (path / "reproduction_entry.md").exists()]
    return {
        "asset_dir_count": len(asset_dirs),
        "unregistered": unregistered,
        "missing_manifest": missing_manifest,
        "missing_reproduction": missing_reproduction,
    }


def sync_registry() -> dict[str, Any]:
    knowledge = load_registry("knowledge")
    known_paths = {str(item.get("path")) for item in knowledge.get("knowledge", [])}
    for path in sorted(REUSABLE_KNOWLEDGE_ROOT.rglob("*.md")) if REUSABLE_KNOWLEDGE_ROOT.exists() else []:
        if str(path) in known_paths:
            continue
        item = {
            "id": path.stem,
            "title": first_heading(path),
            "category": path.parent.name,
            "path": str(path),
            "source_project": "g-workspace",
            "status": parse_status_from_markdown(path),
            "verification": "",
            "evidence_refs": [],
            "updated_at": file_date(path),
        }
        normalize_metadata(item)
        knowledge["knowledge"].append(item)
    save_registry("knowledge", knowledge)

    prompts = load_registry("prompts")
    known_prompt_paths = {str(item.get("path")) for item in prompts.get("prompts", [])}
    for path in sorted(REUSABLE_PROMPTS_ROOT.rglob("*.md")) if REUSABLE_PROMPTS_ROOT.exists() else []:
        if str(path) in known_prompt_paths:
            continue
        item = {
            "id": path.stem,
            "title": first_heading(path),
            "category": path.parent.name,
            "path": str(path),
            "task": "",
            "model": "Codex Desktop / GPT-5 family",
            "status": parse_status_from_markdown(path),
            "updated_at": file_date(path),
        }
        normalize_metadata(item)
        prompts["prompts"].append(item)
    save_registry("prompts", prompts)
    append_ledger("registry_sync", {"knowledge_count": len(knowledge["knowledge"]), "prompt_count": len(prompts["prompts"])})
    return {"knowledge_count": len(knowledge["knowledge"]), "prompt_count": len(prompts["prompts"])}


def print_result(result: dict[str, Any]) -> None:
    import json

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="ResearchLoop registry utility")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("scan-knowledge")
    sub.add_parser("scan-prompts")
    sub.add_parser("scan-assets")
    sub.add_parser("repair-known-encoding")
    sub.add_parser("sync")
    args = parser.parse_args()

    if args.command == "validate":
        result = validate_registry()
        print_result(result)
        return 0 if result["ok"] else 1
    if args.command == "scan-knowledge":
        print_result(scan_knowledge())
        return 0
    if args.command == "scan-prompts":
        print_result(scan_prompts())
        return 0
    if args.command == "scan-assets":
        print_result(scan_assets())
        return 0
    if args.command == "repair-known-encoding":
        print_result(repair_known_encoding())
        return 0
    if args.command == "sync":
        print_result(sync_registry())
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
