from __future__ import annotations

import importlib.util
import hashlib
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mycevo_public_release", REPO_ROOT / "scripts" / "public_release.py")
assert SPEC and SPEC.loader
public_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(public_release)


def write_manifest(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "schema": "mycevo.public_file_manifest.v1",
                "release_identity": "test-preview",
                "default_deny": True,
                "allow": {"exact": ["README.md"], "prefixes": [".github/", "src/"]},
                "ignore": {"path_markers": ["__pycache__/"]},
                "deny": {
                    "filename_markers": ["conflicted copy"],
                    "extensions": [".db"],
                    "content_markers": ["PRIVATE_ROOT"],
                },
                "required_gates": ["G0"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_public_release_audit_is_default_deny_and_hashes_selected_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Public\n", encoding="utf-8")
    (tmp_path / "private.txt").write_text("not selected\n", encoding="utf-8")
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_text("print('ok')\n", encoding="utf-8")
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: test\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    write_manifest(manifest)

    result = public_release.audit(tmp_path, manifest)

    assert result["ok"] is True
    assert {item["path"] for item in result["selected"]} == {
        ".github/workflows/ci.yml",
        "README.md",
        "src/app.py",
    }
    assert result["ignored_count"] == 2  # manifest and private.txt
    assert all(len(item["sha256"]) == 64 for item in result["selected"])


def test_public_release_audit_blocks_private_content(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("PRIVATE_ROOT/customer\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    write_manifest(manifest)

    result = public_release.audit(tmp_path, manifest)

    assert result["ok"] is False
    assert result["blocked"][0]["path"] == "README.md"
    assert "denied_content_marker:PRIVATE_ROOT" in result["blocked"][0]["reasons"]


def test_public_release_stage_refuses_existing_destination(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "README.md").write_text("# Public\n", encoding="utf-8")
    manifest = root / "manifest.yaml"
    write_manifest(manifest)
    destination = tmp_path / "stage"
    destination.mkdir()

    result = public_release.stage(root, manifest, destination)

    assert result["ok"] is False
    assert result["staged"] is False
    assert "already exists" in result["stage_error"]


def test_preview_name_and_handoff_fixture_are_fail_closed() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    contract = (REPO_ROOT / "docs" / "release" / "community-release-contract.md").read_text(encoding="utf-8")
    candidate = yaml.safe_load((REPO_ROOT / "examples" / "cross-agent-handoff" / "candidate.yaml").read_text(encoding="utf-8"))
    decision = yaml.safe_load((REPO_ROOT / "examples" / "cross-agent-handoff" / "human-decision.yaml").read_text(encoding="utf-8"))
    handoff = yaml.safe_load((REPO_ROOT / "examples" / "cross-agent-handoff" / "handoff-context.yaml").read_text(encoding="utf-8"))

    assert "Source-Available Technical Preview" in readme
    assert "default release identity is **Source-Available Technical Preview**" in contract
    assert candidate["status"] == "pending validation"
    assert candidate["promotion_allowed"] is False
    assert decision["status"] == "pending"
    assert decision["actor"] is None
    assert handoff["canonical"] is False


def test_license_candidate_matches_recorded_upstream_hash() -> None:
    text = (REPO_ROOT / "LICENSE.CANDIDATE.md").read_text(encoding="utf-8").replace("\r\n", "\n").rstrip() + "\n"
    assert hashlib.sha256(text.encode("utf-8")).hexdigest().upper() == (
        "919F3C042E934CE1F258C85C6F99F0AA57C34E20CCCB023DD95B0C41EBED564E"
    )
