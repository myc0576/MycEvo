from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _harness_common import (
    REGISTRY_FILES,
    REUSABLE_KNOWLEDGE_ROOT,
    REUSABLE_PROMPTS_ROOT,
    ROOT,
    REPORTS_DIR,
    STATE_DIR,
    append_ledger,
    contains_bad_encoding,
    path_exists,
    read_yaml,
    registry_item_id,
    safe_read_text,
    today,
    write_json,
)


REQUIRED_HEADINGS = {
    "knowledge": ["结论", "适用条件", "步骤", "关键参数", "风险", "验证依据"],
    "prompts": ["task", "input", "output", "prompt body", "model", "notes", "revision history"],
    "research_assets": ["素材清单", "来源路径", "用途", "复现入口", "证据"],
}


def list_items(name: str) -> list[dict[str, Any]]:
    data = read_yaml(REGISTRY_FILES[name])
    items = data.get(name, [])
    return items if isinstance(items, list) else []


def item_path(item: dict[str, Any]) -> Path | None:
    for field in ("path", "asset_dir", "manifest", "reproduction_entry"):
        raw = item.get(field)
        if raw:
            return Path(str(raw))
    return None


def read_item_markdown(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("path", "manifest", "reproduction_entry"):
        raw = item.get(field)
        if raw:
            path = Path(str(raw))
            if path.exists() and path.is_file():
                parts.append(safe_read_text(path))
    return "\n\n".join(parts)


def heading_hits(text: str, headings: list[str]) -> dict[str, bool]:
    lower = text.lower()
    return {heading: heading.lower() in lower for heading in headings}


def required_fields_for(name: str) -> list[str]:
    return {
        "knowledge": ["id", "title", "category", "path", "status", "evidence_refs"],
        "prompts": ["id", "title", "category", "path", "task", "model", "status"],
        "research_assets": ["id", "project_id", "asset_dir", "manifest", "reproduction_entry", "status"],
        "papers": ["id", "title", "source_project", "status", "target_journal", "path", "manuscript_root", "output_write_policy", "core_claim", "figures"],
        "literature": ["id", "title", "source_project", "status", "path", "citation_key", "pdf_policy", "evidence_use"],
        "figures": ["id", "title", "source_project", "status", "path", "visual_ref_dir", "figure_type", "claim_id"],
        "output_objects": ["output_id", "output_type", "title", "source_project", "status", "updated_at"],
        "asset_evolution": ["asset_id", "asset_type", "title", "source_output_objects", "status", "reproduction_entry", "linked_files"],
        "workflow_improvement_backlog": ["issue_id", "source_project", "pain_point", "affected_workflow", "proposed_fix", "priority", "status"],
        "decisions": ["id", "date", "project_id", "decision", "path", "status"],
        "visual_to_editable_skills": ["id", "title", "upstream_url", "route_tags", "output_types", "status", "validation_method", "safety_notes"],
        "projects": ["project_id", "name", "path", "closeout_required"],
        "ppt_assets": ["id", "project_id", "path", "version", "status"],
        "model_assets": ["id", "project_id", "asset_dir", "version", "status"],
        "feedback": ["id", "target_id", "feedback_type", "created_at"],
    }.get(name, [])


def form_score(name: str, item: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    warnings: list[str] = []
    hard_failures: list[str] = []
    fields = required_fields_for(name)
    if not fields:
        return 30, warnings, hard_failures
    present = sum(1 for field in fields if item.get(field) not in (None, "", []))
    score = round(30 * present / len(fields))
    for field in fields:
        if item.get(field) in (None, "", []):
            warnings.append(f"missing_field:{field}")
    for field in ("path", "asset_dir", "manifest", "reproduction_entry"):
        if item.get(field) and not path_exists(item[field]):
            hard_failures.append(f"missing_path:{field}:{item[field]}")
    return score, warnings, hard_failures


def content_score(name: str, item: dict[str, Any], text: str) -> tuple[int, list[str]]:
    warnings: list[str] = []
    headings = REQUIRED_HEADINGS.get(name, [])
    if not headings:
        if name == "decisions":
            has_reason = bool(item.get("decision")) and ("原因" in text or "rationale" in text.lower() or "verification" in text.lower() or "验证" in text)
            return (30 if has_reason else 18, [] if has_reason else ["decision_lacks_reason_or_verification"])
        if name == "projects":
            return (30 if item.get("closeout_required") is True else 18, [] if item.get("closeout_required") is True else ["closeout_not_required"])
        return (24 if text or item else 12, [] if text or item else ["empty_item"])
    hits = heading_hits(text, headings)
    found = sum(1 for ok in hits.values() if ok)
    if found < len(headings):
        missing = [heading for heading, ok in hits.items() if not ok]
        warnings.append("missing_content_sections:" + ",".join(missing))
    return round(30 * found / len(headings)), warnings


def reproducibility_score(name: str, item: dict[str, Any], text: str) -> tuple[int, list[str], list[str]]:
    warnings: list[str] = []
    hard_failures: list[str] = []
    status = str(item.get("status") or "")
    evidence_refs = item.get("evidence_refs") or []
    existing_refs = [ref for ref in evidence_refs if path_exists(ref)]
    lower = text.lower()
    if name == "research_assets":
        manifest_ok = path_exists(item.get("manifest"))
        reproduction_ok = path_exists(item.get("reproduction_entry"))
        if not manifest_ok:
            hard_failures.append("research_asset_missing_manifest")
        if not reproduction_ok:
            hard_failures.append("research_asset_missing_reproduction_entry")
        score = 30 if manifest_ok and reproduction_ok else 10 if manifest_ok or reproduction_ok else 0
        return score, warnings, hard_failures
    if name == "prompts":
        if existing_refs:
            return 30, warnings, hard_failures
        if "revision history" in lower or "task" in lower and "output" in lower:
            return 22, warnings, hard_failures
        warnings.append("prompt_lacks_revision_or_usage_evidence")
        return 10, warnings, hard_failures
    if name == "decisions":
        if existing_refs:
            return 30, warnings, hard_failures
        if any(token in lower for token in ["rationale", "consequences", "verification", "status"]) or "验证" in text:
            return 22, warnings, hard_failures
        if status == "validated":
            hard_failures.append("validated_without_evidence")
        warnings.append("weak_reproducibility_evidence")
        return 10, warnings, hard_failures
    if status == "validated" and not existing_refs and "验证" not in text and "evidence" not in lower:
        hard_failures.append("validated_without_evidence")
    if existing_refs:
        return 30, warnings, hard_failures
    if "复现" in text or "命令" in text or "evidence" in lower or "验证" in text:
        return 22, warnings, hard_failures
    warnings.append("weak_reproducibility_evidence")
    return 10, warnings, hard_failures


def reference_score(item: dict[str, Any], all_text: str) -> tuple[int, int]:
    item_id = str(item.get("id") or item.get("project_id") or "")
    path = str(item.get("path") or item.get("asset_dir") or "")
    reference_count = int(item.get("reference_count") or 0)
    if item_id:
        reference_count += max(0, all_text.count(item_id) - 1)
    if path:
        reference_count += max(0, all_text.count(path) - 1)
    if reference_count >= 2:
        return 10, reference_count
    if reference_count == 1:
        return 6, reference_count
    return 0, reference_count


def level_for(score: int, hard_failures: list[str], reference_count: int) -> str:
    if hard_failures or score < 60:
        return "needs_attention"
    if score >= 90 and reference_count > 0:
        return "gold"
    if score >= 75:
        return "silver"
    return "bronze"


def evaluate_one(name: str, item: dict[str, Any], index: int, all_text: str) -> dict[str, Any]:
    item_id = registry_item_id(item, index)
    text = read_item_markdown(item)
    hard_failures: list[str] = []
    warnings: list[str] = []
    if contains_bad_encoding(item) or contains_bad_encoding(text):
        hard_failures.append("bad_encoding")
    form, form_warnings, form_failures = form_score(name, item)
    content, content_warnings = content_score(name, item, text)
    repro, repro_warnings, repro_failures = reproducibility_score(name, item, text)
    reference, reference_count = reference_score(item, all_text)
    warnings.extend(form_warnings + content_warnings + repro_warnings)
    hard_failures.extend(form_failures + repro_failures)
    score = min(100, form + content + repro + reference)
    quality_level = level_for(score, hard_failures, reference_count)
    return {
        "registry": name,
        "id": item_id,
        "title": item.get("title") or item.get("name") or item.get("decision") or item_id,
        "path": str(item_path(item) or ""),
        "score": score,
        "quality_level": quality_level,
        "score_breakdown": {
            "form": form,
            "content": content,
            "reproducibility": repro,
            "reference_reuse": reference,
        },
        "reference_count": reference_count,
        "hard_failures": sorted(set(hard_failures)),
        "warnings": sorted(set(warnings)),
    }


def gather_reference_text() -> str:
    chunks: list[str] = []
    for path in REGISTRY_FILES.values():
        if path.exists():
            chunks.append(safe_read_text(path))
    for root in [REUSABLE_KNOWLEDGE_ROOT, REUSABLE_PROMPTS_ROOT, ROOT / "decisions"]:
        if root.exists():
            for path in root.rglob("*.md"):
                chunks.append(safe_read_text(path))
    return "\n".join(chunks)


def evaluate(target: str = "all") -> dict[str, Any]:
    registries = list(REGISTRY_FILES) if target == "all" else [target]
    all_text = gather_reference_text()
    items: list[dict[str, Any]] = []
    for name in registries:
        if name not in REGISTRY_FILES:
            continue
        for index, item in enumerate(list_items(name)):
            items.append(evaluate_one(name, item, index, all_text))
    hard_failures = [item for item in items if item["hard_failures"]]
    score_values = [item["score"] for item in items]
    result = {
        "ok": not hard_failures,
        "target": target,
        "evaluated_count": len(items),
        "average_score": round(sum(score_values) / len(score_values), 2) if score_values else 0.0,
        "hard_failure_count": len(hard_failures),
        "quality_counts": {
            level: sum(1 for item in items if item["quality_level"] == level)
            for level in ["gold", "silver", "bronze", "needs_attention"]
        },
        "items": items,
    }
    write_json(STATE_DIR / "evaluation.json", result)
    report_path = REPORTS_DIR / "evaluation" / f"{today()}_evaluation.md"
    report_path.write_text(markdown_report(result), encoding="utf-8")
    append_ledger(
        "quality_evaluation",
        {
            "target": target,
            "ok": result["ok"],
            "evaluated_count": result["evaluated_count"],
            "average_score": result["average_score"],
            "hard_failure_count": result["hard_failure_count"],
            "report": str(report_path),
        },
    )
    result["report"] = str(report_path)
    return result


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        f"# ResearchLoop Quality Evaluation {today()}",
        "",
        f"- ok: `{result['ok']}`",
        f"- evaluated_count: `{result['evaluated_count']}`",
        f"- average_score: `{result['average_score']}`",
        f"- hard_failure_count: `{result['hard_failure_count']}`",
        "",
        "## Quality Counts",
        "",
    ]
    for level, count in result["quality_counts"].items():
        lines.append(f"- {level}: {count}")
    lines.extend(["", "## Items", ""])
    for item in result["items"]:
        failures = ", ".join(item["hard_failures"]) or "none"
        warnings = ", ".join(item["warnings"][:4]) or "none"
        lines.append(f"- {item['registry']}:{item['id']} score={item['score']} level={item['quality_level']} hard_failures={failures} warnings={warnings}")
    lines.append("")
    return "\n".join(lines)


def print_json(data: dict[str, Any]) -> None:
    import json

    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate ResearchLoop content quality")
    sub = parser.add_subparsers(dest="command", required=True)
    eval_parser = sub.add_parser("evaluate")
    eval_parser.add_argument("--target", default="all")
    eval_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "evaluate":
        result = evaluate(args.target)
        print_json(result)
        return 0 if result["ok"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
