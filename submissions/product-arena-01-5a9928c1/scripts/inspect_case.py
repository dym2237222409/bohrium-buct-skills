#!/usr/bin/env python3
"""
inspect_case.py — Harbor Case, Source Material & Glossary Discovery for Literature Translation

Reads /input/case.json, task.md, and environment/resources.yaml (when present),
discovers input papers (.pdf, .md, .txt, .tex, .json) and any case-provided
glossaries, probes PDF/OCR backends, writes /app/work/case_manifest.json, and
initializes an honest schema-preserving early checkpoint at /app/output/result.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def default_value_for_spec(spec: Any) -> Any:
    if isinstance(spec, dict):
        if "default" in spec:
            return spec["default"]
        t = str(spec.get("type", "")).lower()
        if t == "array" or "items" in spec:
            return []
        if t == "object" or "properties" in spec:
            props = spec.get("properties")
            if isinstance(props, dict):
                return {k: default_value_for_spec(v) for k, v in props.items()}
            return {}
        return None
    if isinstance(spec, list):
        return []
    return None


def discover_inputs(roots: List[Path]) -> Dict[str, List[Dict[str, Any]]]:
    sources: List[Dict[str, Any]] = []
    glossaries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for r in roots:
        if not r.exists():
            continue
        files = [r] if r.is_file() else sorted(r.rglob("*"))
        for p in files:
            if not p.is_file():
                continue
            rp = str(p.resolve())
            if rp in seen:
                continue
            seen.add(rp)
            low_name = p.name.lower()
            if low_name in {"case.json", "resources.yaml", "task.toml"}:
                continue
            info = {
                "path": str(p),
                "name": p.name,
                "ext": p.suffix.lower(),
                "size_bytes": p.stat().st_size,
                "sha256": sha256_file(p),
            }
            if any(k in low_name for k in ("glossary", "term", "vocab", "lexicon", "术语", "词表")):
                glossaries.append(info)
            elif p.suffix.lower() in {".pdf", ".md", ".txt", ".tex", ".html", ".json", ".docx", ".xml"}:
                sources.append(info)
    return {"source_candidates": sources, "glossary_files": glossaries}


def build_initial_result(
    case_data: Dict[str, Any],
    case_json_path: Optional[Path],
    task_md_path: Optional[Path],
    discovered: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    case_id = case_data.get("case_id") or case_data.get("id") or case_data.get("task_id") or "unknown_case"
    result: Dict[str, Any] = {"case_id": case_id}

    contract = case_data.get("output_contract")
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            for k, v in contract["properties"].items():
                result.setdefault(k, default_value_for_spec(v))
        elif isinstance(contract.get("required_fields"), list):
            for k in contract["required_fields"]:
                if isinstance(k, str):
                    result.setdefault(k, None)
        else:
            for k, v in contract.items():
                if k not in {"type", "required", "$schema", "description"}:
                    result.setdefault(k, default_value_for_spec(v))
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                result.setdefault(item, None)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                result.setdefault(item["name"], default_value_for_spec(item))

    result["status"] = "partial"
    result["status_reason"] = (
        "Initial case discovery checkpoint created; segmentation, glossary locking, and translation pending."
    )
    result.setdefault(
        "input_identification",
        {
            "case_json_path": str(case_json_path) if case_json_path else None,
            "task_md_path": str(task_md_path) if task_md_path else None,
            "source_candidates": discovered["source_candidates"],
            "glossary_files": discovered["glossary_files"],
            "target_language": case_data.get("target_language") or case_data.get("target_lang") or "zh",
        },
    )
    result.setdefault("results", {})
    result.setdefault("sources", [])
    result.setdefault(
        "uncertainty",
        {
            "unreported_in_source": [],
            "unverifiable_or_untranslated_spans": ["Translation pipeline initialized; awaiting segment completion."],
            "ambiguous_expressions_for_human_review": [],
            "material_conflicts": [],
            "human_takeover_locations": [],
        },
    )
    result["change_records"] = [
        {
            "step": "inspect_case_initial_checkpoint",
            "timestamp": utc_now_iso(),
            "summary": f"Initialized schema-preserving checkpoint for case_id={case_id}.",
        }
    ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect literature translation case and initialize early result.json.")
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--case-json", default=None)
    parser.add_argument("--task-md", default=None)
    parser.add_argument("--work-dir", default="/app/work")
    parser.add_argument("--output-dir", default="/app/output")
    parser.add_argument("--no-init-output", action="store_true")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    work_dir = Path(args.work_dir)
    output_dir = Path(args.output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    case_json_path = Path(args.case_json) if args.case_json else (input_dir / "case.json")
    if not case_json_path.is_file():
        case_json_path = None

    task_candidates = [
        Path(args.task_md) if args.task_md else None,
        input_dir / "task.md",
        Path("task.md"),
        Path("instruction.md"),
    ]
    task_md_path = next((p for p in task_candidates if p is not None and p.is_file()), None)

    case_data: Dict[str, Any] = {}
    if case_json_path:
        case_data = json.loads(case_json_path.read_text(encoding="utf-8", errors="replace"))

    roots = [input_dir, Path("environment")]
    for k in ("pdf_path", "source_path", "input_file", "document", "paper_path"):
        v = case_data.get(k)
        if isinstance(v, str) and v.strip():
            p = Path(v)
            if not p.is_absolute() and (input_dir / p).exists():
                p = input_dir / p
            roots.append(p)

    discovered = discover_inputs(roots)

    # Extract inline glossary from case.json if supplied
    inline_glossary = case_data.get("glossary") or case_data.get("terminology") or case_data.get("terms")
    inline_glossary_path: Optional[str] = None
    if inline_glossary:
        ig_path = work_dir / "case_inline_glossary.json"
        ig_path.write_text(json.dumps(inline_glossary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        inline_glossary_path = str(ig_path)

    toolchain = {
        "pymupdf": importlib.util.find_spec("fitz") is not None,
        "pdfplumber": importlib.util.find_spec("pdfplumber") is not None,
        "pdftohtml": shutil.which("pdftohtml") is not None,
        "tesseract": shutil.which("tesseract") is not None,
    }

    manifest = {
        "inspected_at": utc_now_iso(),
        "python_version": sys.version.split()[0],
        "case_id": case_data.get("case_id") or case_data.get("id") or "unknown_case",
        "case_json_path": str(case_json_path) if case_json_path else None,
        "task_md_path": str(task_md_path) if task_md_path else None,
        "output_contract": case_data.get("output_contract"),
        "translation_scope": {
            "source_lang": case_data.get("source_language") or case_data.get("source_lang") or "en",
            "target_lang": case_data.get("target_language") or case_data.get("target_lang") or "zh",
            "page_range": case_data.get("page_range") or case_data.get("pages"),
            "sections": case_data.get("sections") or case_data.get("target_sections"),
        },
        "inline_glossary_path": inline_glossary_path,
        "discovered_sources": discovered["source_candidates"],
        "discovered_glossary_files": discovered["glossary_files"],
        "task_md_preview": task_md_path.read_text(encoding="utf-8", errors="replace")[:3500] if task_md_path else "",
        "toolchain": toolchain,
    }
    manifest_path = work_dir / "case_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result_path = output_dir / "result.json"
    if not args.no_init_output and not result_path.exists():
        init_res = build_initial_result(case_data, case_json_path, task_md_path, discovered)
        result_path.write_text(json.dumps(init_res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "case_id": manifest["case_id"],
                "manifest_path": str(manifest_path),
                "sources_found": [s["path"] for s in discovered["source_candidates"]],
                "glossaries_found": [g["path"] for g in discovered["glossary_files"]]
                + ([inline_glossary_path] if inline_glossary_path else []),
                "toolchain": toolchain,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
