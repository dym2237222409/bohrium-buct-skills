#!/usr/bin/env python3
"""
inspect_case.py — Harbor Case & Toolchain Discovery + Honest Early Checkpoint

Reads /input/case.json, task.md, and environment/resources.yaml (when present),
discovers actual PDF and auxiliary input files, probes installed PDF/OCR backends,
writes /app/work/case_manifest.json, and initializes an honest schema-preserving
early checkpoint at /app/output/result.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PYTHON_MODULES = [
    ("fitz", "PyMuPDF"),
    ("pdfplumber", "pdfplumber"),
    ("pypdf", "pypdf"),
    ("pypdfium2", "pypdfium2"),
    ("pdfminer", "pdfminer.six"),
    ("PIL", "Pillow"),
    ("numpy", "numpy"),
    ("pytesseract", "pytesseract"),
    ("lxml", "lxml"),
]

CLI_TOOLS = [
    ("pdftotext", ["-v"]),
    ("pdftohtml", ["-v"]),
    ("pdfinfo", ["-v"]),
    ("pdftoppm", ["-v"]),
    ("tesseract", ["--version"]),
    ("qpdf", ["--version"]),
    ("mutool", ["-v"]),
    ("soffice", ["--version"]),
    ("pdflatex", ["--version"]),
]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_python_modules() -> Dict[str, Dict[str, Any]]:
    report: Dict[str, Dict[str, Any]] = {}
    for mod_name, dist_name in PYTHON_MODULES:
        spec = importlib.util.find_spec(mod_name)
        version: Optional[str] = None
        if spec is not None:
            for candidate in (dist_name, mod_name):
                try:
                    version = importlib.metadata.version(candidate)
                    break
                except Exception:
                    continue
        report[mod_name] = {
            "available": spec is not None,
            "package": dist_name,
            "version": version,
        }
    return report


def probe_cli_tools() -> Dict[str, Dict[str, Any]]:
    report: Dict[str, Dict[str, Any]] = {}
    for binary, version_args in CLI_TOOLS:
        resolved = shutil.which(binary)
        first_line: Optional[str] = None
        if resolved:
            try:
                proc = subprocess.run(
                    [resolved, *version_args],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    errors="replace",
                )
                combined = (proc.stdout or proc.stderr or "").strip()
                if combined:
                    first_line = combined.splitlines()[0][:160]
            except Exception as exc:
                first_line = f"probe_error: {exc}"
        report[binary] = {
            "available": resolved is not None,
            "path": resolved,
            "version_hint": first_line,
        }
    return report


def parse_simple_resources_yaml(yaml_path: Path) -> List[Dict[str, str]]:
    """Zero-dependency conservative parser for Harbor environment/resources.yaml."""
    if not yaml_path.is_file():
        return []
    entries: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    for raw_line in yaml_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip() or line.strip() == "resources:":
            continue
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if current:
                entries.append(current)
            current = {}
            rest = stripped[2:].strip()
            if ":" in rest:
                k, v = rest.split(":", 1)
                current[k.strip()] = v.strip().strip("'\"")
        elif current is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            current[k.strip()] = v.strip().strip("'\"")
    if current:
        entries.append(current)
    return entries


def discover_files(search_roots: List[Path]) -> Dict[str, List[Dict[str, Any]]]:
    pdfs: List[Dict[str, Any]] = []
    docs: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for root in search_roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        for p in candidates:
            if not p.is_file():
                continue
            resolved_str = str(p.resolve())
            if resolved_str in seen:
                continue
            seen.add(resolved_str)
            ext = p.suffix.lower()
            info = {
                "path": str(p),
                "name": p.name,
                "size_bytes": p.stat().st_size,
                "sha256": sha256_file(p),
            }
            if ext == ".pdf":
                pdfs.append(info)
            elif ext in {".json", ".md", ".txt", ".yaml", ".yml", ".tex", ".csv", ".tsv", ".html"}:
                docs.append(info)
    return {"pdfs": pdfs, "auxiliary_files": docs}


def default_value_for_spec(spec: Any) -> Any:
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if "default" in spec:
            return spec["default"]
        if t == "array" or "items" in spec:
            return []
        if t == "object" or "properties" in spec:
            props = spec.get("properties")
            if isinstance(props, dict):
                return {k: default_value_for_spec(v) for k, v in props.items()}
            return {}
        if t in {"boolean", "bool"}:
            return None
        if t in {"integer", "number", "float", "int"}:
            return None
        return None
    if isinstance(spec, list):
        return []
    return None


def build_initial_result(
    case_data: Dict[str, Any],
    case_json_path: Optional[Path],
    task_md_path: Optional[Path],
    discovered_pdfs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    case_id = (
        case_data.get("case_id")
        or case_data.get("id")
        or case_data.get("task_id")
        or "unknown_case"
    )
    result: Dict[str, Any] = {
        "case_id": case_id,
    }

    # Preserve every field declared in output_contract (supports dict of properties,
    # JSON Schema object, or list of required field names).
    contract = case_data.get("output_contract")
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            for field_name, field_spec in contract["properties"].items():
                if field_name not in result:
                    result[field_name] = default_value_for_spec(field_spec)
        elif isinstance(contract.get("required_fields"), list):
            for field_name in contract["required_fields"]:
                if isinstance(field_name, str) and field_name not in result:
                    result[field_name] = None
        else:
            for field_name, field_spec in contract.items():
                if field_name not in {"type", "required", "$schema", "description"} and field_name not in result:
                    result[field_name] = default_value_for_spec(field_spec)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str) and item not in result:
                result[item] = None
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                fname = item["name"]
                if fname not in result:
                    result[fname] = default_value_for_spec(item)

    # Ensure standard honest reporting keys are always present without overwriting contract defaults.
    result["status"] = "partial"
    result["status_reason"] = (
        "Initial case discovery checkpoint saved before full PDF layout parsing; "
        "no scientific claims or answers have been fabricated."
    )
    result.setdefault(
        "input_identification",
        {
            "case_json_path": str(case_json_path) if case_json_path else None,
            "task_md_path": str(task_md_path) if task_md_path else None,
            "discovered_pdfs": [
                {"path": p["path"], "size_bytes": p["size_bytes"], "sha256": p["sha256"]}
                for p in discovered_pdfs
            ],
        },
    )
    result.setdefault("results", {})
    result.setdefault("sources", [])
    result.setdefault(
        "uncertainty",
        {
            "unreported_in_source": [],
            "unverifiable_items": ["Full PDF parsing not yet completed at initial checkpoint."],
            "material_conflicts": [],
            "garbled_or_low_confidence_spans": [],
            "human_takeover_locations": [],
        },
    )
    result["change_records"] = [
        {
            "step": "inspect_case_initial_checkpoint",
            "timestamp": utc_now_iso(),
            "summary": (
                f"Read case_id={case_id}, discovered {len(discovered_pdfs)} PDF file(s), "
                "and initialized honest partial /app/output/result.json."
            ),
        }
    ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Harbor case inputs and initialize early result.json.")
    parser.add_argument("--input-dir", default="/input", help="Directory containing case.json and case inputs.")
    parser.add_argument("--case-json", default=None, help="Explicit path to case.json (overrides --input-dir/case.json).")
    parser.add_argument("--task-md", default=None, help="Explicit path to task.md.")
    parser.add_argument("--resources-yaml", default="environment/resources.yaml", help="Path to resources.yaml.")
    parser.add_argument("--work-dir", default="/app/work", help="Working directory for intermediate artifacts.")
    parser.add_argument("--output-dir", default="/app/output", help="Output directory for final deliverables.")
    parser.add_argument("--no-init-output", action="store_true", help="Skip writing initial partial result.json.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    work_dir = Path(args.work_dir)
    output_dir = Path(args.output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    case_json_path: Optional[Path] = Path(args.case_json) if args.case_json else (input_dir / "case.json")
    if case_json_path and not case_json_path.is_file():
        case_json_path = None

    task_md_candidates = [
        Path(args.task_md) if args.task_md else None,
        input_dir / "task.md",
        Path("task.md"),
        Path("instruction.md"),
        Path("/app/task.md"),
    ]
    task_md_path = next((p for p in task_md_candidates if p is not None and p.is_file()), None)

    case_data: Dict[str, Any] = {}
    if case_json_path:
        case_data = json.loads(case_json_path.read_text(encoding="utf-8", errors="replace"))

    task_md_excerpt = ""
    if task_md_path:
        task_md_excerpt = task_md_path.read_text(encoding="utf-8", errors="replace")[:4000]

    resources_path = Path(args.resources_yaml)
    resources_list = parse_simple_resources_yaml(resources_path)

    search_roots = [input_dir, Path("environment")]
    for key in ("pdf_path", "input_pdf", "file", "document", "paper_path", "source_pdf"):
        val = case_data.get(key)
        if isinstance(val, str) and val.strip():
            cand = Path(val)
            if not cand.is_absolute() and (input_dir / cand).exists():
                cand = input_dir / cand
            search_roots.append(cand)
    if isinstance(case_data.get("files"), list):
        for item in case_data["files"]:
            p_str = item.get("path") if isinstance(item, dict) else (item if isinstance(item, str) else None)
            if p_str:
                cand = Path(p_str)
                if not cand.is_absolute() and (input_dir / cand).exists():
                    cand = input_dir / cand
                search_roots.append(cand)

    discovered = discover_files(search_roots)
    py_mods = probe_python_modules()
    cli_tools = probe_cli_tools()

    recommended_backend = "stdlib_pdf_stream"
    if py_mods["fitz"]["available"]:
        recommended_backend = "pymupdf"
    elif py_mods["pdfplumber"]["available"]:
        recommended_backend = "pdfplumber"
    elif cli_tools["pdftohtml"]["available"] or cli_tools["pdftotext"]["available"]:
        recommended_backend = "poppler_xml"
    elif py_mods["pypdf"]["available"]:
        recommended_backend = "pypdf"

    manifest = {
        "inspected_at": utc_now_iso(),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "case_id": case_data.get("case_id") or case_data.get("id") or "unknown_case",
        "case_json_path": str(case_json_path) if case_json_path else None,
        "task_md_path": str(task_md_path) if task_md_path else None,
        "case_keys": list(case_data.keys()),
        "output_contract": case_data.get("output_contract"),
        "questions_or_tasks": (
            case_data.get("questions")
            or case_data.get("tasks")
            or case_data.get("instructions")
            or case_data.get("prompt")
        ),
        "task_md_preview": task_md_excerpt,
        "resources_yaml_entries": resources_list,
        "discovered_pdfs": discovered["pdfs"],
        "auxiliary_files": discovered["auxiliary_files"],
        "toolchain": {
            "recommended_pdf_backend": recommended_backend,
            "ocr_available": bool(cli_tools["tesseract"]["available"]),
            "python_modules": py_mods,
            "cli_tools": cli_tools,
        },
    }

    manifest_path = work_dir / "case_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result_path = output_dir / "result.json"
    if not args.no_init_output and not result_path.exists():
        initial_result = build_initial_result(case_data, case_json_path, task_md_path, discovered["pdfs"])
        result_path.write_text(json.dumps(initial_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "status": "ok",
        "case_id": manifest["case_id"],
        "manifest_path": str(manifest_path),
        "initial_result_path": str(result_path) if result_path.exists() else None,
        "pdf_count": len(discovered["pdfs"]),
        "pdfs": [p["path"] for p in discovered["pdfs"]],
        "recommended_pdf_backend": recommended_backend,
        "ocr_available": manifest["toolchain"]["ocr_available"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
