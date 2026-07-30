from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


def configured_path(env_names: tuple[str, ...], default: Path | str) -> Path:
    for env_name in env_names:
        raw = os.environ.get(env_name)
        if raw:
            return Path(raw).expanduser().resolve()
    return Path(default).expanduser().resolve()


ROOT = configured_path(
    ("MYCEVO_WORKSPACE_ROOT", "MYCEVO_ROOT", "RESEVO_WORKSPACE_ROOT", "RESEVO_ROOT", "RESEARCHLOOP_ROOT"),
    Path(__file__).absolute().parents[1],
)
ENGINE_ROOT = configured_path(
    ("MYCEVO_ENGINE_ROOT", "RESEVO_ENGINE_ROOT", "RESEARCHLOOP_ENGINE_ROOT"),
    Path(__file__).absolute().parents[1],
)


def load_harness_config(root: Path) -> dict[str, Any]:
    path = root / "harness.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


HARNESS_CONFIG = load_harness_config(ROOT)
PATH_CONFIG = HARNESS_CONFIG.get("paths", {}) if isinstance(HARNESS_CONFIG.get("paths"), dict) else {}
WORKSPACE_ROOT = configured_path(
    ("MYCEVO_DATA_ROOT", "RESEARCHLOOP_WORKSPACE_ROOT"),
    PATH_CONFIG.get("workspace_root") or ROOT.parent,
)
REGISTRY_DIR = ROOT / "registry"
STATE_DIR = ROOT / "state"
RUNS_DIR = ROOT / "runs"
REPORTS_DIR = ROOT / "reports"
SNAPSHOTS_DIR = STATE_DIR / "snapshots"
REUSABLE_KNOWLEDGE_ROOT = configured_path(
    ("MYCEVO_REUSABLE_KNOWLEDGE_ROOT", "RESEARCHLOOP_REUSABLE_KNOWLEDGE_ROOT"),
    PATH_CONFIG.get("reusable_knowledge_root") or WORKSPACE_ROOT / "knowledge" / "reusable_knowledge",
)
REUSABLE_PROMPTS_ROOT = configured_path(
    ("MYCEVO_REUSABLE_PROMPTS_ROOT", "RESEARCHLOOP_REUSABLE_PROMPTS_ROOT"),
    PATH_CONFIG.get("reusable_prompts_root") or WORKSPACE_ROOT / "knowledge" / "reusable_prompts",
)
KNOWLEDGE_ROOT = configured_path(
    ("MYCEVO_KNOWLEDGE_ROOT", "RESEARCHLOOP_KNOWLEDGE_ROOT"),
    REUSABLE_KNOWLEDGE_ROOT.parent,
)
PROJECTS_ROOT = configured_path(
    ("MYCEVO_PROJECTS_ROOT", "RESEARCHLOOP_PROJECTS_ROOT"),
    WORKSPACE_ROOT / "projects",
)

FORBIDDEN_HARNESS_WRITE_ROOTS = [
    KNOWLEDGE_ROOT / "_harness",
    WORKSPACE_ROOT / "knowledge" / "_harness",
]

REGISTRY_FILES = {
    "projects": REGISTRY_DIR / "projects.yaml",
    "knowledge": REGISTRY_DIR / "knowledge.yaml",
    "prompts": REGISTRY_DIR / "prompts.yaml",
    "research_assets": REGISTRY_DIR / "research_assets.yaml",
    "papers": REGISTRY_DIR / "papers.yaml",
    "literature": REGISTRY_DIR / "literature.yaml",
    "figures": REGISTRY_DIR / "figures.yaml",
    "output_objects": REGISTRY_DIR / "output_objects.yaml",
    "asset_evolution": REGISTRY_DIR / "asset_evolution.yaml",
    "workflow_improvement_backlog": REGISTRY_DIR / "workflow_improvement_backlog.yaml",
    "decisions": REGISTRY_DIR / "decisions.yaml",
    "visual_to_editable_skills": REGISTRY_DIR / "visual_to_editable_skills.yaml",
    "ppt_assets": REGISTRY_DIR / "ppt_assets.yaml",
    "model_assets": REGISTRY_DIR / "model_assets.yaml",
    "feedback": REGISTRY_DIR / "feedback.yaml",
}

QUALITY_LEVELS = {"unrated", "bronze", "silver", "gold", "needs_attention"}
STATUS_CONFIDENCE_DEFAULTS = {
    "validated": 0.80,
    "pending validation": 0.45,
    "hypothesis": 0.30,
}
MOJIBAKE_MARKERS = (
    "??",
    "\ufffd",
    "锟",
    "閿",
    "鐭",
    "绋",
    "搴",
    "娴",
    "穩",
    "浼",
    "嗗",
    "氳",
    "熷",
    "€",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y%m%d")


def new_run_id(label: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", label.strip()).strip("-") or "run"
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe}_{uuid.uuid4().hex[:8]}"


def ensure_base_dirs() -> None:
    for path in [
        STATE_DIR,
        STATE_DIR / "kb_index",
        STATE_DIR / "proposals",
        SNAPSHOTS_DIR,
        RUNS_DIR,
        REPORTS_DIR,
        REPORTS_DIR / "health",
        REPORTS_DIR / "evaluation",
        REPORTS_DIR / "project_health",
        REPORTS_DIR / "proposals",
        REPORTS_DIR / "daily",
        REPORTS_DIR / "war_room",
        REGISTRY_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(text, encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_ledger(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_base_dirs()
    path = STATE_DIR / "evolution_ledger.jsonl"
    entry = {
        "schema": "research_harness_evolution_ledger.v1",
        "ledger_id": uuid.uuid4().hex,
        "event": event,
        "created_at": now_iso(),
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return {**entry, "ledger_path": str(path)}


def make_run_dir(label: str) -> tuple[str, Path]:
    ensure_base_dirs()
    run_id = new_run_id(label)
    run_dir = RUNS_DIR / today() / run_id
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def contains_bad_encoding(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return any(marker in text for marker in MOJIBAKE_MARKERS)


def confidence_for_status(status: Any) -> float:
    return STATUS_CONFIDENCE_DEFAULTS.get(str(status or "").strip(), 0.40)


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, round(number, 2)))


def path_exists(raw: Any) -> bool:
    if not raw:
        return False
    try:
        return Path(str(raw)).exists()
    except OSError:
        return False


def first_heading(path: Path) -> str:
    if not path.exists():
        return path.stem
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem


def parse_status_from_markdown(path: Path) -> str:
    if not path.exists():
        return "pending validation"
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"(?im)^\s*[-\"]?\s*status\s*:\s*['\"]?([^'\"\n]+)", text)
    if match:
        status = match.group(1).strip()
        if status in {"validated", "hypothesis", "pending validation"}:
            return status
    return "pending validation"


def file_date(path: Path) -> str:
    if not path.exists():
        return datetime.now().date().isoformat()
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def slugify_project_id(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip().lower()).strip("-._")
    if cleaned:
        return cleaned
    return f"project-{uuid.uuid5(uuid.NAMESPACE_URL, name).hex[:8]}"


def is_subpath(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def normalize_path_text(path: Path | str) -> str:
    return str(path)


def registry_item_id(item: dict[str, Any], index: int = 0) -> str:
    return str(
        item.get("id")
        or item.get("project_id")
        or item.get("output_id")
        or item.get("asset_id")
        or item.get("issue_id")
        or item.get("target_id")
        or f"item_{index}"
    )


def safe_read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def snapshot_files(files: Iterable[Path | str], run_id: str, reason: str) -> dict[str, Any]:
    ensure_base_dirs()
    snapshot_dir = SNAPSHOTS_DIR / run_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for raw in files:
        source = Path(str(raw))
        if not source.exists() or not source.is_file():
            continue
        digest = uuid.uuid5(uuid.NAMESPACE_URL, str(source.resolve())).hex[:10]
        target = snapshot_dir / f"{digest}_{source.name}"
        shutil.copy2(source, target)
        entries.append({"source": str(source), "snapshot": str(target)})
    manifest = {
        "schema": "research_harness_rollback_manifest.v1",
        "run_id": run_id,
        "reason": reason,
        "created_at": now_iso(),
        "entries": entries,
    }
    rollback_manifest = snapshot_dir / "rollback_manifest.json"
    write_json(rollback_manifest, manifest)
    return {"snapshot_dir": str(snapshot_dir), "rollback_manifest": str(rollback_manifest), "entry_count": len(entries)}


def restore_snapshot(rollback_manifest: Path | str) -> dict[str, Any]:
    manifest_path = Path(str(rollback_manifest))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[str] = []
    for entry in manifest.get("entries", []):
        source = Path(str(entry["source"]))
        snapshot = Path(str(entry["snapshot"]))
        if snapshot.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot, source)
            restored.append(str(source))
    append_ledger("snapshot_restore", {"rollback_manifest": str(manifest_path), "restored": restored})
    return {"restored_count": len(restored), "restored": restored}
