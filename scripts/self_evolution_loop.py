from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from _harness_common import (
    FORBIDDEN_HARNESS_WRITE_ROOTS,
    REGISTRY_FILES,
    REUSABLE_KNOWLEDGE_ROOT,
    REUSABLE_PROMPTS_ROOT,
    ROOT,
    RUNS_DIR,
    append_ledger,
    now_iso,
    read_yaml,
    slugify_project_id,
    snapshot_files,
    today,
    write_json,
    write_yaml,
)


SCHEMA = "research_harness_self_evolution_intake.v1"
CONTRACT_SCHEMA = "research_harness_self_evolution_contract.v1"
TRACE_SCHEMA = "research_harness_self_evolution_trace.v1"
DEFAULT_MODEL = "Codex or Claude Code via self-evolution intake"

TRIGGER_TERMS = [
    "自进化",
    "沉淀",
    "更新",
    "更新 harness",
    "写回",
    "写入 harness",
    "知识库",
    "复用",
    "资产化",
    "候选资产",
    "经验沉淀",
    "closeout",
    "harness closeout",
    "调用历史",
    "查历史",
    "参考之前",
    "loop",
    "一键 loop",
    "retro",
    "写入知识库",
    "更新知识库",
    "同步 harness",
    "沉淀到 harness",
]

FORBIDDEN_AUTO_STATUSES = {"validated", "reusable", "approved", "pass", "paper_ready"}
KNOWLEDGE_STATUSES = {"pending validation", "hypothesis"}
PROMPT_STATUSES = {"pending validation", "hypothesis"}
ASSET_STATUSES = {"candidate", "pending validation"}
DECISION_STATUSES = {"pending validation", "hypothesis"}
BACKLOG_STATUSES = {"pending validation", "proposed", "candidate"}


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def make_self_run_dir(label: str) -> tuple[str, Path]:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", label.strip()).strip("-") or "run"
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe}_{datetime.now().strftime('%f')[:6]}"
    run_dir = RUNS_DIR / today() / run_id
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def slugify(value: str, fallback: str = "item") -> str:
    text = value.strip().lower()
    replacements = {
        "自进化": "self-evolution",
        "沉淀": "capture",
        "更新": "update",
        "知识库": "knowledge-base",
        "一键": "one-click",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"[^a-z0-9_.-]+", "-", text).strip("-._")
    return text or fallback


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def is_subpath(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def assert_not_forbidden(path: Path) -> None:
    resolved = path.resolve()
    for forbidden in FORBIDDEN_HARNESS_WRITE_ROOTS:
        try:
            if resolved == forbidden.resolve() or is_subpath(resolved, forbidden):
                raise ValueError(f"forbidden write path: {path}")
        except OSError:
            continue


def assert_under(path: Path, root: Path, label: str) -> None:
    assert_not_forbidden(path)
    if not is_subpath(path, root):
        raise ValueError(f"{label} must stay under {root}: {path}")


def load_registry(name: str) -> dict[str, Any]:
    data = read_yaml(REGISTRY_FILES[name])
    data.setdefault("version", 1)
    data.setdefault(name, [])
    if not isinstance(data[name], list):
        data[name] = []
    return data


def save_registry(name: str, data: dict[str, Any]) -> None:
    write_yaml(REGISTRY_FILES[name], data)


def registry_ids(name: str) -> set[str]:
    data = load_registry(name)
    keys = ("id", "project_id", "output_id", "asset_id", "issue_id", "target_id")
    ids: set[str] = set()
    for item in data.get(name, []):
        for key in keys:
            if item.get(key):
                ids.add(str(item[key]))
                break
    return ids


def unique_id(base: str, existing: set[str]) -> str:
    candidate = base
    counter = 2
    while candidate in existing:
        candidate = f"{base}_{counter}"
        counter += 1
    existing.add(candidate)
    return candidate


def safe_status(kind: str, requested: Any, warnings: list[str]) -> str:
    requested_text = str(requested or "").strip()
    lowered = requested_text.lower()
    allowed = {
        "knowledge": KNOWLEDGE_STATUSES,
        "prompts": PROMPT_STATUSES,
        "research_assets": ASSET_STATUSES,
        "decisions": DECISION_STATUSES,
        "workflow_improvements": BACKLOG_STATUSES,
    }[kind]
    default = "candidate" if kind == "research_assets" else "pending validation"
    if lowered in FORBIDDEN_AUTO_STATUSES:
        warnings.append(f"{kind}:requested_forbidden_status:{requested_text}:downgraded_to:{default}")
        return default
    if requested_text in allowed:
        return requested_text
    if lowered in allowed:
        return lowered
    if requested_text:
        warnings.append(f"{kind}:unsupported_status:{requested_text}:downgraded_to:{default}")
    return default


def detect_project_id(project_root: Path) -> str:
    data = load_registry("projects")
    matches: list[tuple[int, str]] = []
    for item in data.get("projects", []):
        raw = item.get("path")
        project_id = item.get("project_id")
        if not raw or not project_id:
            continue
        candidate = Path(str(raw))
        if project_root == candidate or is_subpath(project_root, candidate):
            matches.append((len(str(candidate)), str(project_id)))
    if matches:
        return sorted(matches, reverse=True)[0][1]
    if str(project_root).replace("/", "\\").rstrip("\\").lower() == "g:":
        return "g-workspace"
    return slugify_project_id(project_root.name or "g-workspace")


def trace(run_dir: Path, event: str, payload: dict[str, Any] | None = None) -> None:
    entry = {
        "schema": TRACE_SCHEMA,
        "created_at": now_iso(),
        "event": event,
        "payload": payload or {},
    }
    with (run_dir / "trace.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def state_update(run_dir: Path, **updates: Any) -> dict[str, Any]:
    path = run_dir / "state.json"
    data = load_json(path)
    data.update(updates)
    data["updated_at"] = now_iso()
    write_json(path, data)
    return data


def tokenize_query(query: str) -> list[str]:
    terms = {query.strip()}
    terms.update(token for token in re.split(r"\s+", query) if token)
    terms.update(match.group(0).lower() for match in re.finditer(r"[A-Za-z0-9_.-]+", query))
    terms.update(term for term in TRIGGER_TERMS if term.lower() in query.lower())
    return sorted(term for term in terms if term)


def recall(query: str, project_root: Path, limit: int = 10, write_ledger: bool = True) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    try:
        import kb_index

        index_result = kb_index.search(query, limit)
        for row in index_result.get("results", []):
            results.append({**row, "source": "kb_index"})
    except Exception as exc:  # pragma: no cover - defensive recall fallback
        results.append({"source": "kb_index", "error": str(exc), "score": 0})

    terms = [term.lower() for term in tokenize_query(query)]
    for name, path in REGISTRY_FILES.items():
        data = read_yaml(path)
        for item in data.get(name, []) or []:
            haystack = json.dumps(item, ensure_ascii=False, default=str).lower()
            score = sum(1 for term in terms if term and term in haystack)
            if score <= 0:
                continue
            results.append(
                {
                    "source": "registry",
                    "kind": name,
                    "id": item.get("id") or item.get("project_id") or item.get("asset_id") or item.get("issue_id"),
                    "title": item.get("title") or item.get("name") or item.get("decision") or "",
                    "path": item.get("path") or item.get("asset_dir") or str(path),
                    "score": score,
                    "status": item.get("status", ""),
                }
            )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sorted(results, key=lambda item: float(item.get("score") or 0), reverse=True):
        key = str(row.get("path") or row.get("id") or row.get("title") or row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= limit:
            break

    result = {
        "schema": "research_harness_self_evolution_recall.v1",
        "query": query,
        "project_root": str(project_root),
        "trigger_terms": [term for term in TRIGGER_TERMS if term.lower() in query.lower()],
        "result_count": len(deduped),
        "results": deduped,
    }
    if write_ledger:
        append_ledger("self_evolution_recall", {"query": query, "project_root": str(project_root), "result_count": len(deduped)})
    return result


def init_intake(project_root: Path, trigger_text: str, out: Path) -> dict[str, Any]:
    project_id = detect_project_id(project_root)
    data = {
        "schema": SCHEMA,
        "trigger": trigger_text,
        "project_root": str(project_root),
        "project_id": project_id,
        "task": {
            "title": "",
            "summary": "",
            "source_refs": [],
            "evidence_refs": [],
            "verification_commands": [],
        },
        "risk_boundary": [
            "Do not write to deprecated or ambiguous harness directories.",
            "Do not auto-promote candidate material to validated, reusable, approved, pass, or paper_ready.",
            "Do not copy raw data, model weights, CAD binaries, or temporary files into reusable knowledge.",
        ],
        "candidates": {
            "knowledge": [],
            "prompts": [],
            "research_assets": [],
            "decisions": [],
            "workflow_improvements": [],
        },
        "next_bottleneck": "",
        "notes": "",
    }
    write_yaml(out, data)
    return {"ok": True, "out": str(out), "project_id": project_id, "trigger": trigger_text}


def validate_intake(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if data.get("schema") != SCHEMA:
        errors.append(f"schema_mismatch:{data.get('schema')}")
    project_root = Path(str(data.get("project_root") or ""))
    if not str(project_root):
        errors.append("missing_project_root")
    project_id = str(data.get("project_id") or "") or detect_project_id(project_root)
    task = ensure_dict(data.get("task"))
    if not task.get("title") and not task.get("summary"):
        warnings.append("task_lacks_title_or_summary")
    candidates = ensure_dict(data.get("candidates"))
    for key in ["knowledge", "prompts", "research_assets", "decisions", "workflow_improvements"]:
        if not isinstance(candidates.get(key, []), list):
            errors.append(f"candidates_{key}_not_list")
    normalized = {
        **data,
        "project_root": str(project_root),
        "project_id": project_id,
        "task": task,
        "candidates": {key: ensure_list(candidates.get(key)) for key in ["knowledge", "prompts", "research_assets", "decisions", "workflow_improvements"]},
    }
    return normalized, errors, warnings


def markdown_list(values: list[Any]) -> str:
    if not values:
        return "- none"
    return "\n".join(f"- {value}" for value in values)


def evidence_refs(candidate: dict[str, Any], intake: dict[str, Any]) -> list[str]:
    refs = []
    refs.extend(str(ref) for ref in ensure_list(ensure_dict(intake.get("task")).get("evidence_refs")))
    refs.extend(str(ref) for ref in ensure_list(candidate.get("evidence_refs")))
    return sorted(dict.fromkeys(ref for ref in refs if ref))


def source_refs(candidate: dict[str, Any], intake: dict[str, Any]) -> list[str]:
    refs = []
    refs.extend(str(ref) for ref in ensure_list(ensure_dict(intake.get("task")).get("source_refs")))
    refs.extend(str(ref) for ref in ensure_list(candidate.get("source_refs")))
    return sorted(dict.fromkeys(ref for ref in refs if ref))


def write_text_if_needed(path: Path, text: str, overwrite: bool = False) -> bool:
    assert_not_forbidden(path)
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return True


def add_registry_item(name: str, item: dict[str, Any]) -> bool:
    data = load_registry(name)
    key = "id"
    if name == "workflow_improvement_backlog":
        key = "issue_id"
    existing = {str(row.get(key)) for row in data.get(name, []) if row.get(key)}
    if item.get(key) in existing:
        return False
    data[name].append(item)
    save_registry(name, data)
    return True


def knowledge_card(candidate_id: str, candidate: dict[str, Any], intake: dict[str, Any], status: str) -> str:
    title = candidate.get("title") or candidate_id
    applicable = ensure_list(candidate.get("applicable_contexts") or candidate.get("applicability") or "G drive related tasks")
    steps = ensure_list(candidate.get("steps") or candidate.get("procedure") or [])
    params = ensure_list(candidate.get("key_parameters") or candidate.get("parameters") or [])
    risks = ensure_list(candidate.get("risks") or candidate.get("risk_boundary") or ensure_list(intake.get("risk_boundary")))
    verification = ensure_list(candidate.get("verification") or candidate.get("verification_refs") or evidence_refs(candidate, intake))
    conclusion = candidate.get("conclusion") or candidate.get("summary") or ensure_dict(intake.get("task")).get("summary") or "Pending validation candidate captured by self-evolution loop."
    return f"""# {title}

status: {status}
source_project: {intake.get('project_id')}

## 结论

{conclusion}

## 适用条件

{markdown_list(applicable)}

## 步骤

{markdown_list(steps)}

## 关键参数

{markdown_list(params)}

## 风险

{markdown_list(risks)}

## 验证依据

{markdown_list(verification)}

## Source Refs

{markdown_list(source_refs(candidate, intake))}
"""


def prompt_card(candidate_id: str, candidate: dict[str, Any], intake: dict[str, Any], status: str) -> str:
    title = candidate.get("title") or candidate_id
    task = candidate.get("task") or ensure_dict(intake.get("task")).get("title") or "Self-evolution prompt candidate"
    input_spec = candidate.get("input") or candidate.get("input_spec") or "Structured self-evolution intake and task evidence."
    output_spec = candidate.get("output") or candidate.get("output_spec") or "Candidate knowledge, prompt, asset, decision, or backlog writeback."
    body = candidate.get("prompt_body") or candidate.get("body") or "Review the task evidence, preserve claim boundaries, and write only candidate-first reusable material."
    notes = candidate.get("notes") or ensure_dict(intake.get("task")).get("summary") or ""
    return f"""# Prompt Card: {title}

status: {status}

## task

{task}

## input

{input_spec}

## output

{output_spec}

## prompt body

```text
{body}
```

## model

{candidate.get('model') or DEFAULT_MODEL}

## notes

{notes or 'none'}

## revision history

| Date | Version | Notes |
|---|---|---|
| {datetime.now().date().isoformat()} | v1 | Captured by self-evolution loop as pending validation. |
"""


def asset_manifest(candidate_id: str, candidate: dict[str, Any], intake: dict[str, Any], status: str) -> str:
    title = candidate.get("title") or candidate_id
    intended = ensure_list(candidate.get("intended_uses") or ["Codex retrieval", "future G drive task reuse"])
    source = source_refs(candidate, intake)
    evidence = evidence_refs(candidate, intake)
    return f"""# Asset Manifest: {title}

status: {status}
project_id: {intake.get('project_id')}

## 素材清单

{markdown_list(ensure_list(candidate.get('materials') or candidate.get('files') or []))}

## 来源路径

{markdown_list(source)}

## 用途

{markdown_list(intended)}

## 复现入口

- See `reproduction_entry.md`.

## 证据

{markdown_list(evidence)}

## Notes

{candidate.get('notes') or ensure_dict(intake.get('task')).get('summary') or 'Candidate asset captured by self-evolution loop.'}
"""


def asset_reproduction(candidate: dict[str, Any], intake: dict[str, Any]) -> str:
    commands = ensure_list(candidate.get("reproduction_commands") or ensure_dict(intake.get("task")).get("verification_commands"))
    return f"""# Reproduction Entry

## Task

{ensure_dict(intake.get('task')).get('title') or 'self-evolution candidate asset'}

## Commands

{markdown_list(commands)}

## Evidence

{markdown_list(evidence_refs(candidate, intake))}

## Boundary

This entry reproduces the candidate packaging path. It does not validate any
scientific claim unless the evidence above explicitly does so.
"""


def decision_record(candidate_id: str, candidate: dict[str, Any], intake: dict[str, Any], status: str) -> str:
    title = candidate.get("title") or candidate_id
    decision = candidate.get("decision") or candidate.get("summary") or ensure_dict(intake.get("task")).get("summary") or title
    alternatives = ensure_list(candidate.get("alternatives") or ["Keep ad hoc closeout without a one-click contract"])
    evidence = evidence_refs(candidate, intake)
    return f"""# Decision: {title}

```yaml
id: {candidate_id}
project_id: {intake.get('project_id')}
date: {datetime.now().date().isoformat()}
status: {status}
changed_route_or_assumption: {str(bool(candidate.get('changed_route_or_assumption', True))).lower()}
```

## Decision

{decision}

## Context

{candidate.get('context') or ensure_dict(intake.get('task')).get('summary') or 'Captured by self-evolution loop.'}

## Alternatives Considered

{markdown_list(alternatives)}

## Evidence

{markdown_list(evidence)}

## Impact

- 对 research goal 的影响：{candidate.get('research_goal_impact') or 'pending validation'}
- 对 project route 的影响：{candidate.get('project_route_impact') or 'self-evolution loop captures reusable material through candidate-first writeback'}
- 对 assumption 的影响：{candidate.get('assumption_impact') or 'automatic writeback cannot promote final scientific status'}
- 对 system design 的影响：{candidate.get('system_design_impact') or 'adds one-click intake, contract, trace, and restart surface'}

## Follow-up

{markdown_list(ensure_list(candidate.get('follow_up') or intake.get('next_bottleneck') or []))}
"""


def apply_candidates(intake: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    warnings: list[str] = []
    written: list[str] = []
    skipped: list[str] = []
    registries_changed: set[str] = set()
    candidates = ensure_dict(intake.get("candidates"))
    project_root = Path(str(intake.get("project_root")))
    project_id = str(intake.get("project_id") or detect_project_id(project_root))
    task_title = ensure_dict(intake.get("task")).get("title") or "self-evolution"

    existing_knowledge = registry_ids("knowledge")
    for candidate in ensure_list(candidates.get("knowledge")):
        if not isinstance(candidate, dict):
            skipped.append("knowledge:not_dict")
            continue
        status = safe_status("knowledge", candidate.get("status"), warnings)
        category = slugify(str(candidate.get("category") or "system_engineering"))
        base = slugify(str(candidate.get("id") or candidate.get("title") or task_title), f"{today()}_knowledge")
        candidate_id = unique_id(base, existing_knowledge)
        path = REUSABLE_KNOWLEDGE_ROOT / category / f"{candidate_id}.md"
        assert_under(path, REUSABLE_KNOWLEDGE_ROOT, "knowledge")
        changed = write_text_if_needed(path, knowledge_card(candidate_id, candidate, intake, status))
        item = {
            "id": candidate_id,
            "title": candidate.get("title") or candidate_id,
            "category": category,
            "path": str(path),
            "source_project": project_id,
            "status": status,
            "verification": candidate.get("verification") or "pending validation via self-evolution loop",
            "evidence_refs": evidence_refs(candidate, intake),
            "updated_at": datetime.now().date().isoformat(),
        }
        if add_registry_item("knowledge", item):
            registries_changed.add("knowledge")
        if changed:
            written.append(str(path))
            trace(run_dir, "write_candidate", {"kind": "knowledge", "id": candidate_id, "path": str(path), "status": status})
        else:
            skipped.append(f"knowledge:{candidate_id}:file_exists")

    existing_prompts = registry_ids("prompts")
    for candidate in ensure_list(candidates.get("prompts")):
        if not isinstance(candidate, dict):
            skipped.append("prompts:not_dict")
            continue
        status = safe_status("prompts", candidate.get("status"), warnings)
        category = slugify(str(candidate.get("category") or "codex"))
        base = slugify(str(candidate.get("id") or candidate.get("title") or task_title), f"{today()}_prompt")
        candidate_id = unique_id(base, existing_prompts)
        path = REUSABLE_PROMPTS_ROOT / category / f"{candidate_id}.md"
        assert_under(path, REUSABLE_PROMPTS_ROOT, "prompt")
        changed = write_text_if_needed(path, prompt_card(candidate_id, candidate, intake, status))
        item = {
            "id": candidate_id,
            "title": candidate.get("title") or candidate_id,
            "category": category,
            "path": str(path),
            "task": candidate.get("task") or task_title,
            "model": candidate.get("model") or DEFAULT_MODEL,
            "status": status,
            "updated_at": datetime.now().date().isoformat(),
        }
        if add_registry_item("prompts", item):
            registries_changed.add("prompts")
        if changed:
            written.append(str(path))
            trace(run_dir, "write_candidate", {"kind": "prompts", "id": candidate_id, "path": str(path), "status": status})
        else:
            skipped.append(f"prompts:{candidate_id}:file_exists")

    existing_assets = registry_ids("research_assets")
    for candidate in ensure_list(candidates.get("research_assets")):
        if not isinstance(candidate, dict):
            skipped.append("research_assets:not_dict")
            continue
        status = safe_status("research_assets", candidate.get("status"), warnings)
        base = slugify(str(candidate.get("id") or candidate.get("task_name") or candidate.get("title") or task_title), f"{today()}_asset")
        candidate_id = unique_id(base, existing_assets)
        asset_dir = project_root / "research_assets" / candidate_id
        assert_not_forbidden(asset_dir)
        manifest = asset_dir / "00_asset_manifest.md"
        reproduction = asset_dir / "reproduction_entry.md"
        write_text_if_needed(manifest, asset_manifest(candidate_id, candidate, intake, status))
        write_text_if_needed(reproduction, asset_reproduction(candidate, intake))
        written.extend([str(manifest), str(reproduction)])
        item = {
            "id": candidate_id,
            "project_id": project_id,
            "task_name": candidate.get("task_name") or slugify(str(candidate.get("title") or task_title)),
            "asset_dir": str(asset_dir),
            "intended_uses": ensure_list(candidate.get("intended_uses") or ["Codex retrieval", "future G drive task reuse"]),
            "manifest": str(manifest),
            "reproduction_entry": str(reproduction),
            "status": status,
        }
        if add_registry_item("research_assets", item):
            registries_changed.add("research_assets")
        trace(run_dir, "write_candidate", {"kind": "research_assets", "id": candidate_id, "path": str(asset_dir), "status": status})

    existing_decisions = registry_ids("decisions")
    for candidate in ensure_list(candidates.get("decisions")):
        if not isinstance(candidate, dict):
            skipped.append("decisions:not_dict")
            continue
        status = safe_status("decisions", candidate.get("status"), warnings)
        base = slugify(str(candidate.get("id") or candidate.get("title") or task_title), f"{today()}_decision")
        if not base.startswith(today()):
            base = f"{today()}_{base}"
        candidate_id = unique_id(base, existing_decisions)
        path = ROOT / "decisions" / f"{candidate_id}.md"
        assert_under(path, ROOT / "decisions", "decision")
        changed = write_text_if_needed(path, decision_record(candidate_id, candidate, intake, status))
        item = {
            "id": candidate_id,
            "date": datetime.now().date().isoformat(),
            "project_id": project_id,
            "decision": candidate.get("decision") or candidate.get("summary") or candidate.get("title") or task_title,
            "changed_route_or_assumption": bool(candidate.get("changed_route_or_assumption", True)),
            "path": str(path),
            "status": status,
            "created_at": datetime.now().date().isoformat(),
            "last_verified_at": "",
            "verification_due_days": 7,
            "encoding_status": "ok",
            "confidence": 0.45,
            "confidence_reason": "candidate-first self-evolution writeback; pending validation",
            "reference_count": 0,
            "last_used_at": "",
            "last_reviewed_at": "",
            "quality_level": "bronze",
            "superseded_by": "",
        }
        if add_registry_item("decisions", item):
            registries_changed.add("decisions")
        if changed:
            written.append(str(path))
            trace(run_dir, "write_candidate", {"kind": "decisions", "id": candidate_id, "path": str(path), "status": status})
        else:
            skipped.append(f"decisions:{candidate_id}:file_exists")

    existing_backlog = registry_ids("workflow_improvement_backlog")
    for candidate in ensure_list(candidates.get("workflow_improvements")):
        if not isinstance(candidate, dict):
            skipped.append("workflow_improvements:not_dict")
            continue
        status = safe_status("workflow_improvements", candidate.get("status"), warnings)
        base = slugify(str(candidate.get("issue_id") or candidate.get("title") or candidate.get("pain_point") or task_title), "self-evolution-issue")
        if not base.upper().startswith("WIB-"):
            base = f"WIB-{today()}-{base}"
        issue_id = unique_id(base, existing_backlog)
        item = {
            "issue_id": issue_id,
            "source_retro": candidate.get("source_retro") or str(run_dir / "closeout_report.md"),
            "source_project": project_id,
            "pain_point": candidate.get("pain_point") or candidate.get("title") or task_title,
            "affected_workflow": candidate.get("affected_workflow") or "self_evolution_loop.v1",
            "affected_assets": ensure_list(candidate.get("affected_assets")),
            "proposed_fix": candidate.get("proposed_fix") or candidate.get("summary") or "Review this self-evolution loop candidate.",
            "priority": candidate.get("priority") or "medium",
            "status": status,
            "validation_method": candidate.get("validation_method") or "self_evolution_loop validation chain",
            "updated_at": datetime.now().date().isoformat(),
        }
        if add_registry_item("workflow_improvement_backlog", item):
            registries_changed.add("workflow_improvement_backlog")
            written.append(str(REGISTRY_FILES["workflow_improvement_backlog"]))
            trace(run_dir, "write_candidate", {"kind": "workflow_improvements", "id": issue_id, "path": str(REGISTRY_FILES["workflow_improvement_backlog"]), "status": status})
        else:
            skipped.append(f"workflow_improvements:{issue_id}:registry_exists")

    return {
        "written": sorted(dict.fromkeys(written)),
        "skipped": sorted(dict.fromkeys(skipped)),
        "warnings": warnings,
        "registries_changed": sorted(registries_changed),
    }


def run_command(name: str, argv: list[str], run_dir: Path, timeout: int = 180) -> dict[str, Any]:
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    stdout_path = artifacts / f"{name}.stdout.txt"
    stderr_path = artifacts / f"{name}.stderr.txt"
    started = now_iso()
    try:
        completed = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        result = {"name": name, "argv": argv, "returncode": completed.returncode, "timed_out": False}
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        result = {"name": name, "argv": argv, "returncode": 124, "timed_out": True}
        stdout = exc.stdout or ""
        stderr = exc.stderr or f"command timed out after {timeout}s"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    result.update({"started_at": started, "finished_at": now_iso(), "stdout": str(stdout_path), "stderr": str(stderr_path)})
    trace(run_dir, "validation_step", result)
    return result


def run_validations(run_dir: Path) -> dict[str, Any]:
    py = sys.executable
    steps = [
        ("py_compile_self_evolution", [py, "-m", "py_compile", "scripts/self_evolution_loop.py"]),
        ("registry_validate", [py, "scripts/registry_tool.py", "validate"]),
        ("evaluate", [py, "scripts/evaluator.py", "evaluate", "--target", "all", "--json"]),
        ("kb_index_rebuild", [py, "scripts/kb_index.py", "rebuild"]),
        ("closeout_check", [py, "scripts/closeout_check.py"]),
    ]
    results = [run_command(name, argv, run_dir) for name, argv in steps]
    return {"ok": all(item["returncode"] == 0 for item in results), "steps": results}


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Self-Evolution Loop Report",
        "",
        f"- run_id: `{result['run_id']}`",
        f"- status: `{result['status']}`",
        f"- apply_candidates: `{result['apply_candidates']}`",
        f"- project_id: `{result.get('project_id', '')}`",
        f"- project_root: `{result.get('project_root', '')}`",
        "",
        "## Written Files",
        "",
    ]
    lines.extend(f"- {path}" for path in result.get("written", [])) if result.get("written") else lines.append("- none")
    lines.extend(["", "## Skipped / Not Written", ""])
    lines.extend(f"- {item}" for item in result.get("skipped", [])) if result.get("skipped") else lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in result.get("warnings", [])) if result.get("warnings") else lines.append("- none")
    lines.extend(["", "## Validation", ""])
    validation = result.get("validation") or {}
    if validation:
        lines.append(f"- ok: `{validation.get('ok')}`")
        for step in validation.get("steps", []):
            lines.append(f"- {step['name']}: returncode={step['returncode']}")
    else:
        lines.append("- not run")
    lines.extend(["", "## Next Bottleneck", "", f"- {result.get('next_bottleneck') or 'none'}", ""])
    return "\n".join(lines)


def execute_run(
    intake_path: Path,
    apply: bool,
    run_validators: bool = True,
    existing_run_dir: Path | None = None,
) -> dict[str, Any]:
    intake_raw = read_yaml(intake_path)
    intake, errors, warnings = validate_intake(intake_raw)
    run_id: str
    run_dir: Path
    if existing_run_dir:
        run_dir = existing_run_dir
        run_id = run_dir.name
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    else:
        run_id, run_dir = make_self_run_dir("self-evolution-loop")
    trace(run_dir, "start", {"run_id": run_id, "intake": str(intake_path), "apply": apply})
    intake_copy = run_dir / "artifacts" / "intake.yaml"
    intake_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(intake_path, intake_copy)

    contract = {
        "schema": CONTRACT_SCHEMA,
        "run_id": run_id,
        "trigger": intake.get("trigger"),
        "project_root": intake.get("project_root"),
        "project_id": intake.get("project_id"),
        "intake": str(intake_copy),
        "apply_candidates": apply,
        "allowed_auto_status": ["candidate", "pending validation"],
        "forbidden_auto_status": sorted(FORBIDDEN_AUTO_STATUSES),
        "created_at": now_iso(),
    }
    write_yaml(run_dir / "contract.yaml", contract)
    state_update(
        run_dir,
        schema="research_harness_self_evolution_state.v1",
        run_id=run_id,
        status="validating_intake",
        intake=str(intake_copy),
        apply_candidates=apply,
    )
    trace(run_dir, "validate_intake", {"errors": errors, "warnings": warnings})
    if errors:
        result = {
            "ok": False,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "status": "intake_error",
            "apply_candidates": apply,
            "errors": errors,
            "warnings": warnings,
            "written": [],
            "skipped": [],
            "next_bottleneck": "fix intake schema errors",
        }
        state_update(run_dir, status="intake_error", errors=errors, warnings=warnings)
        (run_dir / "closeout_report.md").write_text(markdown_report(result), encoding="utf-8")
        return result

    recall_result = recall(
        f"{ensure_dict(intake.get('task')).get('title', '')} {ensure_dict(intake.get('task')).get('summary', '')}",
        Path(str(intake.get("project_root"))),
        limit=8,
        write_ledger=False,
    )
    write_json(run_dir / "artifacts" / "recall.json", recall_result)
    trace(run_dir, "recall", {"result_count": recall_result.get("result_count")})

    writeback = {"written": [], "skipped": [], "warnings": [], "registries_changed": []}
    snapshot: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    if apply:
        snapshot = snapshot_files(REGISTRY_FILES.values(), run_id, "self_evolution_loop_apply")
        trace(run_dir, "snapshot", snapshot)
        state_update(run_dir, status="applying_candidates", snapshot=snapshot)
        writeback = apply_candidates(intake, run_dir)
        warnings.extend(writeback.get("warnings", []))
        state_update(run_dir, status="validating_writeback", writeback=writeback)
        if run_validators:
            validation = run_validations(run_dir)
    else:
        trace(run_dir, "skip_candidate", {"reason": "apply_candidates_false"})

    ok = (validation or {"ok": True}).get("ok", True)
    status = "completed" if ok else "failed_validation"
    result = {
        "ok": ok,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": status,
        "apply_candidates": apply,
        "project_id": intake.get("project_id"),
        "project_root": intake.get("project_root"),
        "contract": str(run_dir / "contract.yaml"),
        "state": str(run_dir / "state.json"),
        "trace": str(run_dir / "trace.jsonl"),
        "intake": str(intake_copy),
        "snapshot": snapshot,
        "written": writeback.get("written", []),
        "skipped": writeback.get("skipped", []),
        "warnings": warnings,
        "validation": validation,
        "next_bottleneck": intake.get("next_bottleneck") or "review candidate material and promote only with evidence",
    }
    write_json(run_dir / "artifacts" / "self_evolution_result.json", result)
    (run_dir / "closeout_report.md").write_text(markdown_report(result), encoding="utf-8")
    state_update(run_dir, status=status, result=str(run_dir / "artifacts" / "self_evolution_result.json"))
    trace(run_dir, "complete", {"status": status, "ok": ok, "written_count": len(result["written"])})
    append_ledger(
        "self_evolution_loop",
        {
            "run_id": run_id,
            "status": status,
            "ok": ok,
            "project_id": intake.get("project_id"),
            "project_root": intake.get("project_root"),
            "apply_candidates": apply,
            "written_count": len(result["written"]),
            "run_dir": str(run_dir),
        },
    )
    return result


def find_run_dir(run_id: str) -> Path:
    direct = RUNS_DIR / today() / run_id
    if direct.exists():
        return direct
    matches = sorted(RUNS_DIR.glob(f"*/{run_id}"))
    if not matches:
        raise SystemExit(f"run_id not found: {run_id}")
    return matches[-1]


def resume(run_id: str) -> dict[str, Any]:
    run_dir = find_run_dir(run_id)
    state = load_json(run_dir / "state.json")
    contract = read_yaml(run_dir / "contract.yaml")
    if contract.get("schema") != CONTRACT_SCHEMA:
        return {
            "ok": False,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "status": "blocked",
            "error": "run is not a self-evolution loop run",
        }
    if state.get("status") == "completed":
        trace(run_dir, "resume", {"status": "already_completed"})
        return {"ok": True, "run_id": run_id, "run_dir": str(run_dir), "status": "already_completed", "state": state}
    intake = Path(str(contract.get("intake") or state.get("intake") or ""))
    if not intake.exists() or not intake.is_file():
        return {"ok": False, "run_id": run_id, "run_dir": str(run_dir), "status": "blocked", "error": "intake copy missing"}
    trace(run_dir, "resume", {"status": state.get("status"), "intake": str(intake)})
    return execute_run(intake, apply=bool(contract.get("apply_candidates")), existing_run_dir=run_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="One-click self-evolution loop for ResearchLoop")
    sub = parser.add_subparsers(dest="command", required=True)

    recall_parser = sub.add_parser("recall")
    recall_parser.add_argument("--query", required=True)
    recall_parser.add_argument("--project-root", default=str(Path.cwd()))
    recall_parser.add_argument("--limit", type=int, default=10)
    recall_parser.add_argument("--json", action="store_true")

    init_parser = sub.add_parser("init")
    init_parser.add_argument("--project-root", default=str(Path.cwd()))
    init_parser.add_argument("--trigger", default="自进化")
    init_parser.add_argument("--out", required=True)
    init_parser.add_argument("--json", action="store_true")

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--intake", required=True)
    run_parser.add_argument("--apply-candidates", action="store_true")
    run_parser.add_argument("--skip-validation", action="store_true")
    run_parser.add_argument("--json", action="store_true")

    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("--run-id", required=True)
    resume_parser.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.command == "recall":
        result = recall(args.query, Path(args.project_root), args.limit)
        print_json(result)
        return 0
    if args.command == "init":
        result = init_intake(Path(args.project_root), args.trigger, Path(args.out))
        print_json(result)
        return 0
    if args.command == "run":
        result = execute_run(Path(args.intake), apply=args.apply_candidates, run_validators=not args.skip_validation)
        print_json(result)
        return 0 if result.get("ok") else 1
    if args.command == "resume":
        result = resume(args.run_id)
        print_json(result)
        return 0 if result.get("ok") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
