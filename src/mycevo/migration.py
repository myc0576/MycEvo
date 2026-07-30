"""Fail-closed characterization of legacy workspace mappings.

This module classifies old records before any migration writes occur. It does
not turn legacy approval-like fields into graph authority.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Any

import yaml


PROMOTION_LIKE_STATUSES = {"validated", "reusable", "approved", "pass", "paper_ready"}
KNOWN_LEGACY_STATUSES = {
    "candidate",
    "pending validation",
    "hypothesis",
    "reviewed",
    "draft",
    "implemented",
    "deprecated",
    "replaced",
    "open",
    "reverted",
    "candidate_accepted_for_experiment",
    *PROMOTION_LIKE_STATUSES,
}
ID_KEYS_BY_COLLECTION = {
    "projects": "project_id",
    "output_objects": "output_id",
    "asset_evolution": "asset_id",
    "workflow_improvement_backlog": "issue_id",
}
LEGACY_AUTHORITY_FLAGS = {
    "apply_allowed",
    "promotion_allowed",
    "promotion_performed",
    "automatic_promotion",
    "auto_promote",
    "approved",
    "human_approved",
}


def _rejected(mapping_class: str, code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "mapping_class": mapping_class,
        "reject_code": code,
        "promotion_allowed": False,
    }


def classify_legacy_mapping(source: dict[str, Any]) -> dict[str, Any]:
    """Classify one legacy mapping without guessing missing authority."""
    if source.get("direction", "resevo-to-mycevo") != "resevo-to-mycevo":
        return _rejected("rejected_unrepresentable", "MIGRATION_DOWNGRADE_UNSUPPORTED")
    source_version = source.get("source_schema_version", 1)
    if type(source_version) is not int or source_version != 1:
        return _rejected("rejected_unrepresentable", "MIGRATION_SCHEMA_UNKNOWN")
    if source.get("opaque_executable_hook") or source.get("source_object_type") == "executable_hook":
        return _rejected("rejected_unrepresentable", "MIGRATION_AUTHORITY_UNSUPPORTED")
    if source.get("name") == "target_collision" or source.get("target_conflict"):
        return _rejected("ambiguous", "MIGRATION_TARGET_CONFLICT")

    status = source.get("source_status")
    if status is not None and status not in KNOWN_LEGACY_STATUSES:
        return _rejected("rejected_unrepresentable", "MIGRATION_STATUS_UNKNOWN")

    object_type = source.get("source_object_type")
    if object_type not in {"file", "registry"} and not source.get("source_id"):
        return _rejected("ambiguous", "MIGRATION_ID_UNSTABLE")

    authority = {key: source.get(key) for key in sorted(LEGACY_AUTHORITY_FLAGS) if source.get(key)}
    if authority or status in PROMOTION_LIKE_STATUSES:
        return {
            "ok": True,
            "mapping_class": "lossy_candidate_only",
            "target_status": "candidate",
            "legacy_status": status,
            "legacy_authority_flags": authority,
            "promotion_allowed": False,
        }

    return {
        "ok": True,
        "mapping_class": "lossless",
        "target_status": status,
        "promotion_allowed": False,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry_records(path: Path) -> tuple[int, list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("registry root must be a mapping")
    version = data.get("version")
    if type(version) is not int or version != 1:
        raise ValueError("unsupported registry version")
    collection = data.get(path.stem)
    if not isinstance(collection, list):
        raise ValueError(f"registry collection must be a list named {path.stem}")
    records = [item for item in collection if isinstance(item, dict)]
    if len(records) != len(collection):
        raise ValueError("registry collection entries must be mappings")
    return version, records


def _record_identity(collection: str, record: dict[str, Any]) -> tuple[str, Any]:
    key = ID_KEYS_BY_COLLECTION.get(collection, "id")
    return key, record.get(key)


def is_path_redirect(path: Path) -> bool:
    """Detect symbolic links and Windows junction/reparse-point redirects."""
    if path.is_symlink():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def symlink_component(root: Path, relative: Path = Path()) -> Path | None:
    """Return the first redirect in a root-relative path, including broken links."""
    current = root
    if is_path_redirect(current):
        return current
    for part in relative.parts:
        current = current / part
        if is_path_redirect(current):
            return current
    return None


def inspect_legacy_tree(legacy: Path, target: Path) -> dict[str, Any]:
    """Inspect the source tree and return a write-preflight manifest."""
    mappings: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for root_name, root in (("legacy_root", legacy), ("target_root", target)):
        if is_path_redirect(root):
            issue = {
                "source_path": ".",
                "mapping_class": "rejected_unrepresentable",
                "promotion_allowed": False,
                "reject_code": "MIGRATION_AUTHORITY_UNSUPPORTED",
                "detail": f"{root_name} is a symlink",
            }
            return {
                "ok": False,
                "status": "blocked",
                "reject_code": issue["reject_code"],
                "mappings": [issue],
                "issues": [issue],
            }
    if not legacy.exists():
        return {"ok": True, "status": "source_absent", "mappings": mappings, "issues": issues}

    for source in sorted(path for path in legacy.rglob("*") if path.is_file() or path.is_symlink()):
        relative = source.relative_to(legacy)
        destination = target / relative
        source_link = symlink_component(legacy, relative)
        destination_link = symlink_component(target, relative)
        if source_link or destination_link:
            row = {
                "source_path": relative.as_posix(),
                "mapping_class": "rejected_unrepresentable",
                "promotion_allowed": False,
                "reject_code": "MIGRATION_AUTHORITY_UNSUPPORTED",
                "detail": f"symlink path component is not followed: {source_link or destination_link}",
            }
            mappings.append(row)
            issues.append(row.copy())
            continue
        source_hash = _sha256(source)
        row: dict[str, Any] = {
            "source_path": relative.as_posix(),
            "source_sha256": source_hash,
            "mapping_class": "lossless",
            "promotion_allowed": False,
        }
        if destination.exists():
            target_hash = _sha256(destination) if destination.is_file() else None
            row["target_sha256"] = target_hash
            if target_hash is None or target_hash != source_hash:
                row.update({"mapping_class": "ambiguous", "reject_code": "MIGRATION_TARGET_CONFLICT"})
                issues.append(row.copy())

        if relative.parts and relative.parts[0].casefold() == "hooks":
            row.update(
                {
                    "mapping_class": "rejected_unrepresentable",
                    "reject_code": "MIGRATION_AUTHORITY_UNSUPPORTED",
                }
            )
            issues.append(row.copy())
        elif relative.parts and relative.parts[0].casefold() == "registry" and source.suffix.casefold() in {".yaml", ".yml"}:
            try:
                version, records = _registry_records(source)
            except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
                row.update(
                    {
                        "mapping_class": "rejected_unrepresentable",
                        "reject_code": "MIGRATION_SCHEMA_UNKNOWN",
                        "detail": type(exc).__name__,
                    }
                )
                issues.append(row.copy())
            else:
                if version > 1:
                    row.update(
                        {
                            "mapping_class": "rejected_unrepresentable",
                            "reject_code": "MIGRATION_SCHEMA_UNKNOWN",
                            "source_schema_version": version,
                        }
                    )
                    issues.append(row.copy())
                for record in records:
                    identity_key, identity = _record_identity(relative.stem, record)
                    classified = classify_legacy_mapping(
                        {
                            "source_schema_version": version,
                            "source_object_type": relative.stem,
                            "source_id_key": identity_key,
                            "source_id": identity,
                            "source_status": record.get("status"),
                            **{key: record.get(key) for key in LEGACY_AUTHORITY_FLAGS},
                        }
                    )
                    mappings.append(
                        {
                            "source_path": relative.as_posix(),
                            "source_id_key": identity_key,
                            "source_id": identity,
                            "source_status": record.get("status"),
                            **classified,
                        }
                    )
                    if not classified["ok"]:
                        issues.append(mappings[-1].copy())
        elif relative.as_posix().casefold() in {"config.yaml", "config.yml"}:
            try:
                config = yaml.safe_load(source.read_text(encoding="utf-8"))
                version = config.get("version") if isinstance(config, dict) else None
                if not isinstance(config, dict) or type(version) is not int or version != 1:
                    raise ValueError("unsupported config schema")
            except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
                row.update(
                    {
                        "mapping_class": "rejected_unrepresentable",
                        "reject_code": "MIGRATION_SCHEMA_UNKNOWN",
                        "detail": type(exc).__name__,
                    }
                )
                issues.append(row.copy())
        mappings.append(row)

    return {
        "ok": not issues,
        "status": "ready" if not issues else "blocked",
        "reject_code": issues[0].get("reject_code") if issues else None,
        "mappings": mappings,
        "issues": issues,
    }
