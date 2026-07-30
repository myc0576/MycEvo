from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mmd",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def load_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("schema") != "mycevo.public_file_manifest.v1":
        raise ValueError(f"unsupported public manifest schema: {data.get('schema')!r}")
    if data.get("default_deny") is not True:
        raise ValueError("public manifest must be default_deny: true")
    return data


def normalize(path: Path) -> str:
    return path.as_posix()


def is_allowed(relative: str, manifest: dict[str, Any]) -> bool:
    allow = manifest.get("allow") or {}
    exact = {str(item).strip("/") for item in allow.get("exact") or []}
    prefixes = [str(item).lstrip("/") for item in allow.get("prefixes") or []]
    return relative in exact or any(relative.startswith(prefix) for prefix in prefixes)


def is_ignored(relative: str, manifest: dict[str, Any]) -> bool:
    padded = f"/{relative.lower()}/"
    return any(
        str(marker).lower() in padded
        for marker in (manifest.get("ignore") or {}).get("path_markers") or []
    )


def deny_reasons(relative: str, path: Path, manifest: dict[str, Any]) -> list[str]:
    deny = manifest.get("deny") or {}
    lowered = relative.lower()
    padded = f"/{lowered}/"
    reasons: list[str] = []
    for marker in deny.get("path_markers") or []:
        if str(marker).lower() in padded:
            reasons.append(f"denied_path_marker:{marker}")
    for marker in deny.get("filename_markers") or []:
        if str(marker).lower() in path.name.lower():
            reasons.append(f"denied_filename_marker:{marker}")
    if path.suffix.lower() in {str(item).lower() for item in deny.get("extensions") or []}:
        reasons.append(f"denied_extension:{path.suffix.lower()}")
    return reasons


def content_reasons(relative: str, path: Path, manifest: dict[str, Any]) -> list[str]:
    if relative == "docs/release/public-file-manifest.yaml" or path.suffix.lower() not in TEXT_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["non_utf8_text_candidate"]
    reasons: list[str] = []
    for marker in (manifest.get("deny") or {}).get("content_markers") or []:
        if str(marker) in text:
            reasons.append(f"denied_content_marker:{marker}")
    return reasons


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_snapshot(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists():
        return {"head": None, "status_porcelain_v1_uall": None, "remotes": None}

    def run(*args: str) -> str | None:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    return {
        "head": run("rev-parse", "HEAD"),
        "status_porcelain_v1_uall": run("status", "--porcelain=v1", "-uall"),
        "remotes": run("remote", "-v"),
    }


def audit(root: Path, manifest_path: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    manifest = load_manifest(manifest_path)
    selected: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    ignored_count = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = normalize(path.relative_to(root))
        if relative.startswith(".git/") or is_ignored(relative, manifest):
            ignored_count += 1
            continue
        if not is_allowed(relative, manifest):
            ignored_count += 1
            continue
        reasons = deny_reasons(relative, path, manifest)
        reasons.extend(content_reasons(relative, path, manifest))
        if reasons:
            blocked.append({"path": relative, "reasons": sorted(set(reasons))})
            continue
        selected.append({"path": relative, "sha256": sha256(path), "size": path.stat().st_size})

    missing_exact = []
    for item in (manifest.get("allow") or {}).get("exact") or []:
        if not (root / str(item)).is_file():
            missing_exact.append(str(item))

    return {
        "ok": not blocked and not missing_exact,
        "schema": "mycevo.public_release_audit.v1",
        "root": str(root),
        "manifest": str(manifest_path),
        "release_identity": manifest.get("release_identity"),
        "source_git": git_snapshot(root),
        "selected_count": len(selected),
        "selected": selected,
        "blocked": blocked,
        "missing_exact": missing_exact,
        "ignored_count": ignored_count,
        "required_gates": manifest.get("required_gates") or [],
    }


def stage(root: Path, manifest_path: Path, destination: Path) -> dict[str, Any]:
    result = audit(root, manifest_path)
    if not result["ok"]:
        return {**result, "staged": False}
    destination = destination.resolve()
    if destination.exists():
        return {
            **result,
            "ok": False,
            "staged": False,
            "stage_error": f"destination already exists: {destination}",
        }
    destination.mkdir(parents=True)
    for item in result["selected"]:
        source = root.resolve() / item["path"]
        target = destination / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return {**result, "staged": True, "destination": str(destination)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit and stage a default-deny MycEvo public release tree.")
    parser.add_argument("action", choices=["audit", "stage"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=Path("docs/release/public-file-manifest.yaml"))
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "stage":
        if args.destination is None:
            raise SystemExit("stage requires --destination")
        result = stage(args.root, args.manifest, args.destination)
    else:
        result = audit(args.root, args.manifest)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
