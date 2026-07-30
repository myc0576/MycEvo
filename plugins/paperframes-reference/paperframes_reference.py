"""Reference plugin entry point with fail-closed runtime diagnostics."""

from __future__ import annotations

import importlib.util
import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path

from runtime import TypstRuntime


def inspect_runtime(workspace: str | Path) -> dict[str, object]:
    missing = [name for name in ("docling", "typst") if importlib.util.find_spec(name) is None]
    if missing:
        return {
            "ok": False,
            "status": "missing_runtime_dependency",
            "missing": missing,
            "workspace": str(Path(workspace).resolve()),
            "promotion_allowed": False,
        }
    return {"ok": True, "status": "runtime_available", "workspace": str(Path(workspace).resolve())}


def plugin(*, workspace: str, **_: object) -> dict[str, object]:
    """Runner entry point; actual parsing/rendering remains dependency-gated."""
    return inspect_runtime(workspace)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def run(input_pdf: str | Path, workspace: str | Path) -> dict[str, object]:
    """Run the real text-PDF golden path with OCR and network disabled."""
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    source = Path(input_pdf).resolve()
    root = Path(workspace).resolve()
    root = root / "build" / "golden-path"
    root.mkdir(parents=True, exist_ok=True)
    options = PdfPipelineOptions()
    options.do_ocr = False
    options.force_backend_text = True
    options.do_table_structure = False
    options.do_picture_classification = False
    options.do_picture_description = False
    options.document_timeout = 120
    document = DocumentConverter(format_options={"pdf": PdfFormatOption(pipeline_options=options)}).convert(source).document
    markdown = document.export_to_markdown()
    headings = [line[3:].strip() for line in markdown.splitlines() if line.startswith("## ")]
    ir = {"schema": "paperframes.ir.v1", "source": {"path": str(source), "sha256": _sha256(source)}, "title": headings[0] if headings else "untitled", "sections": [{"id": f"S{i+1}", "title": title} for i, title in enumerate(headings)], "markdown": markdown}
    ir_path = root / "paperframes.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    typ_path = root / "regenerated.typ"
    typ_source = "#set page(paper: \"a4\", margin: 2cm)\n#set text(size: 10pt)\n= " + ir["title"] + "\n\n" + "\n\n".join(f"== {s['title']}\nParsed from the verified text-PDF input." for s in ir["sections"]) + "\n"
    typ_path.write_text(typ_source, encoding="utf-8")
    pdf_path = root / "reversed-paper.pdf"
    typst = TypstRuntime()
    compiled = typst.compile(typ_path, pdf_path, root, timeout=120)
    outputs = {"paperframes.ir.json": _sha256(ir_path), "regenerated.typ": _sha256(typ_path)}
    if compiled.get("ok"):
        outputs["regenerated.pdf"] = _sha256(pdf_path)
    sections = [{"id": s["id"], "title": s["title"], "status": "candidate"} for s in ir["sections"]]
    (root / "document-tree.json").write_text(json.dumps({"schema": "paperframes.document-tree.v1", "sections": sections}, indent=2) + "\n", encoding="utf-8")
    (root / "figures-manifest.json").write_text(json.dumps({"schema": "paperframes.figures-manifest.v1", "figures": []}, indent=2) + "\n", encoding="utf-8")
    (root / "cards-candidates.json").write_text(json.dumps({"schema": "paperframes.cards-candidates.v1", "status": "candidate", "cards": sections}, indent=2) + "\n", encoding="utf-8")
    (root / "input-manifest.json").write_text(json.dumps({"schema": "paperframes.input-manifest.v1", "inputs": [{"path": str(source), "sha256": _sha256(source)}]}, indent=2) + "\n", encoding="utf-8")
    status = "executed_unverified" if compiled.get("ok") else compiled.get("status")
    provenance = {"schema": "paperframes.provenance.v1", "plugin": "paperframes-reference", "plugin_version": "0.1.0", "docling_version": __import__("docling").__version__, "python_version": platform.python_version(), "python_executable": str(Path(__import__("sys").executable).resolve()), "python_executable_digest": _sha256(Path(__import__("sys").executable)), "typst": compiled.get("runtime"), "outputs": outputs, "status": status}
    provenance_path = root / "provenance.json"
    provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    all_outputs = {p.name: _sha256(p) for p in root.iterdir() if p.is_file()}
    (root / "checksums.json").write_text(json.dumps({"schema": "paperframes.checksums.v1", "files": all_outputs}, indent=2) + "\n", encoding="utf-8")
    (root / "execution-record.json").write_text(json.dumps({"schema": "paperframes.execution-record.v1", "status": status, "plugin": "paperframes-reference", "outputs": all_outputs}, indent=2) + "\n", encoding="utf-8")
    return {"ok": bool(compiled.get("ok")), "status": status, "ir": str(ir_path), "typst": str(typ_path), "pdf": str(pdf_path), "provenance": str(provenance_path), "outputs": all_outputs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.workspace), ensure_ascii=False))
