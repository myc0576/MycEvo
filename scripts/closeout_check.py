from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from _harness_common import (
    FORBIDDEN_HARNESS_WRITE_ROOTS,
    PROJECTS_ROOT,
    REGISTRY_FILES,
    REPORTS_DIR,
    STATE_DIR,
    append_ledger,
    contains_bad_encoding,
    make_run_dir,
    path_exists,
    read_yaml,
    today,
    write_json,
    write_yaml,
)

sys.path.insert(0, str(Path(__file__).absolute().parent))


def parse_date(value: Any) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


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


def registry_health() -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    encoding_issues: list[dict[str, Any]] = []
    pending_items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    cutoff = datetime.now().date() - timedelta(days=7)
    for name, path in REGISTRY_FILES.items():
        data = read_yaml(path)
        items = data.get(name, [])
        if not isinstance(items, list):
            errors.append(f"{path}: {name}_not_list")
            continue
        counts[name] = len(items)
        for item in items:
            item_id = item.get("id") or item.get("project_id") or item.get("output_id") or item.get("asset_id") or item.get("issue_id")
            if contains_bad_encoding(item) or referenced_markdown_has_bad_encoding(item):
                issue = {"registry": name, "id": item_id, "encoding_status": item.get("encoding_status", "")}
                encoding_issues.append(issue)
                if item.get("encoding_status") != "needs_review":
                    errors.append(f"{name}:{item_id}:bad_encoding_unmarked")
            for field in ("path", "asset_dir", "manifest", "reproduction_entry"):
                if field in item and item.get(field) and not path_exists(item[field]):
                    errors.append(f"{name}:{item_id}:missing_{field}:{item[field]}")
            for ref in item.get("evidence_refs", []) or []:
                if ref and not path_exists(ref):
                    warnings.append(f"{name}:{item_id}:missing_evidence_ref:{ref}")
            if item.get("status") == "pending validation":
                updated = parse_date(item.get("updated_at") or item.get("created_at"))
                if updated is None or updated <= cutoff:
                    pending_items.append({"registry": name, "id": item_id, "title": item.get("title"), "updated_at": item.get("updated_at")})
    forbidden = [{"path": str(path), "exists": path.exists()} for path in FORBIDDEN_HARNESS_WRITE_ROOTS]
    if any(row["exists"] and row["path"].lower().endswith("knowledge\\_harness") for row in forbidden):
        errors.append("forbidden_path_exists:knowledge/_harness")
    return {
        "ok": not errors,
        "counts": counts,
        "errors": errors,
        "warnings": warnings,
        "encoding_issues": encoding_issues,
        "stale_pending_validation": pending_items,
        "forbidden_paths": forbidden,
    }


def scan_research_assets() -> dict[str, Any]:
    projects = read_yaml(REGISTRY_FILES["projects"]).get("projects", [])
    missing_manifest: list[str] = []
    missing_reproduction: list[str] = []
    checked: list[str] = []
    for project in projects:
        raw_path = str(project.get("path") or "")
        try:
            Path(raw_path).resolve().relative_to(PROJECTS_ROOT.resolve())
        except (OSError, ValueError):
            continue
        project_path = Path(raw_path)
        asset_root = project_path / "research_assets"
        if not asset_root.exists():
            continue
        for asset_dir in sorted(path for path in asset_root.iterdir() if path.is_dir()):
            checked.append(str(asset_dir))
            if not (asset_dir / "00_asset_manifest.md").exists():
                missing_manifest.append(str(asset_dir))
            if not (asset_dir / "reproduction_entry.md").exists():
                missing_reproduction.append(str(asset_dir))
    return {
        "checked_count": len(checked),
        "checked": checked,
        "missing_manifest": missing_manifest,
        "missing_reproduction": missing_reproduction,
    }


def run_derived_checks() -> dict[str, Any]:
    derived: dict[str, Any] = {}
    try:
        import evaluator

        derived["evaluation"] = evaluator.evaluate("all")
    except Exception as exc:  # pragma: no cover - defensive health reporting
        derived["evaluation"] = {"ok": False, "error": str(exc)}
    try:
        import project_health

        derived["project_health"] = project_health.report()
    except Exception as exc:  # pragma: no cover - defensive health reporting
        derived["project_health"] = {"ok": False, "error": str(exc)}
    try:
        import evolve_proposals

        derived["proposals"] = evolve_proposals.scan()
    except Exception as exc:  # pragma: no cover - defensive health reporting
        derived["proposals"] = {"ok": False, "error": str(exc)}
    return derived


def markdown_report(health: dict[str, Any], assets: dict[str, Any], derived: dict[str, Any], run_id: str) -> str:
    lines = [
        f"# Closeout Health Report {today()}",
        "",
        f"- run_id: `{run_id}`",
        f"- ok: `{health['ok'] and not assets['missing_manifest'] and not assets['missing_reproduction']}`",
        "",
        "## Registry Counts",
        "",
    ]
    for name, count in sorted(health["counts"].items()):
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Errors", ""])
    if health["errors"]:
        lines.extend(f"- {item}" for item in health["errors"])
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    if health["warnings"]:
        lines.extend(f"- {item}" for item in health["warnings"])
    else:
        lines.append("- none")
    lines.extend(["", "## Encoding Issues", ""])
    if health["encoding_issues"]:
        for item in health["encoding_issues"]:
            lines.append(f"- {item['registry']}:{item['id']} status={item['encoding_status']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Stale Pending Validation", ""])
    if health["stale_pending_validation"]:
        for item in health["stale_pending_validation"]:
            lines.append(f"- {item['registry']}:{item['id']} updated_at={item.get('updated_at')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Research Assets", ""])
    lines.append(f"- checked_count: {assets['checked_count']}")
    if assets["missing_manifest"]:
        lines.append("- missing manifest:")
        lines.extend(f"  - {path}" for path in assets["missing_manifest"])
    if assets["missing_reproduction"]:
        lines.append("- missing reproduction_entry:")
        lines.extend(f"  - {path}" for path in assets["missing_reproduction"])
    if not assets["missing_manifest"] and not assets["missing_reproduction"]:
        lines.append("- all checked asset dirs have required closeout files")
    lines.extend(["", "## Derived V2 Checks", ""])
    evaluation = derived.get("evaluation", {})
    lines.append(
        f"- evaluator: ok={evaluation.get('ok')} average_score={evaluation.get('average_score')} "
        f"hard_failures={evaluation.get('hard_failure_count')}"
    )
    project_health = derived.get("project_health", {})
    lines.append(
        f"- project_health: ok={project_health.get('ok')} project_count={project_health.get('project_count')}"
    )
    proposals = derived.get("proposals", {})
    lines.append(
        f"- proposals: count={proposals.get('proposal_count')} auto_safe={proposals.get('auto_safe_count')}"
    )
    lines.extend(["", "## Forbidden Paths", ""])
    for row in health["forbidden_paths"]:
        lines.append(f"- {row['path']}: exists={row['exists']}")
    lines.append("")
    return "\n".join(lines)


def run() -> dict[str, Any]:
    run_id, run_dir = make_run_dir("closeout-check")
    health = registry_health()
    assets = scan_research_assets()
    derived = run_derived_checks()
    derived_ok = all(section.get("ok", True) for section in derived.values() if isinstance(section, dict))
    ok = health["ok"] and not assets["missing_manifest"] and not assets["missing_reproduction"] and derived_ok
    result = {"ok": ok, "run_id": run_id, "health": health, "research_assets": assets, "derived": derived}
    report = markdown_report(health, assets, derived, run_id)
    report_path = REPORTS_DIR / "health" / f"{today()}_closeout_health.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    state_path = STATE_DIR / "closeout_health.json"
    write_json(state_path, result)
    write_yaml(
        run_dir / "manifest.yaml",
        {
            "schema": "research_harness_closeout_check.v1",
            "run_id": run_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "outputs": [str(report_path), str(state_path)],
        },
    )
    (run_dir / "closeout_report.md").write_text(report, encoding="utf-8")
    write_json(run_dir / "artifacts" / "closeout_health.json", result)
    ledger = append_ledger(
        "closeout_check",
        {
            "run_id": run_id,
            "ok": ok,
            "report": str(report_path),
            "run_dir": str(run_dir),
            "error_count": len(health["errors"]),
            "warning_count": len(health["warnings"]),
            "derived_ok": derived_ok,
        },
    )
    result["report"] = str(report_path)
    result["run_dir"] = str(run_dir)
    result["ledger_path"] = ledger["ledger_path"]
    return result


def print_json(data: dict[str, Any]) -> None:
    import json

    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run research harness closeout health checks")
    parser.parse_args()
    result = run()
    print_json(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
