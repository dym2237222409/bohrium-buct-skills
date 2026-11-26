#!/usr/bin/env python3
"""
inspect_case.py — Harbor Case & Source Corpus Discovery for Nanopublication Authoring

Reads /input/case.json, task.md, corpus-manifest.json (when present), and discovers
all PDF, JSON, JATS XML, Markdown, or text materials. Initializes an honest early
partial /app/output/result.json preserving case_id and every output_contract field.
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
            return {k: default_value_for_spec(v) for k, v in props.items()} if isinstance(props, dict) else {}
        return None
    return [] if isinstance(spec, list) else None


def discover_materials(roots: List[Path]) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
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
            if p.name.lower() in {"case.json", "resources.yaml", "task.toml"}:
                continue
            if p.suffix.lower() in {".pdf", ".json", ".xml", ".md", ".txt", ".tex"}:
                found.append({
                    "path": str(p),
                    "name": p.name,
                    "ext": p.suffix.lower(),
                    "size_bytes": p.stat().st_size,
                    "sha256": sha256_file(p),
                })
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect Nanopublication case inputs and save early result.json checkpoint.")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--case-json", default=None)
    ap.add_argument("--task-md", default=None)
    ap.add_argument("--work-dir", default="/app/work")
    ap.add_argument("--output-dir", default="/app/output")
    ap.add_argument("--no-init-output", action="store_true")
    args = ap.parse_args()

    inp, work, out = Path(args.input_dir), Path(args.work_dir), Path(args.output_dir)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    cj = Path(args.case_json) if args.case_json else (inp / "case.json")
    if not cj.is_file():
        cj = None
    tm = next((p for p in [Path(args.task_md) if args.task_md else None, inp / "task.md", Path("task.md"), Path("instruction.md")] if p and p.is_file()), None)

    case_data = json.loads(cj.read_text(encoding="utf-8", errors="replace")) if cj else {}
    roots = [inp, Path("environment")]
    for k in ("pdf_path", "source_path", "paper_path", "material_path", "document"):
        v = case_data.get(k)
        if isinstance(v, str) and v.strip():
            p = Path(v)
            if not p.is_absolute() and (inp / p).exists():
                p = inp / p
            roots.append(p)

    mats = discover_materials(roots)
    case_id = case_data.get("case_id") or case_data.get("id") or "unknown_case"

    manifest = {
        "inspected_at": utc_now_iso(),
        "python_version": sys.version.split()[0],
        "case_id": case_id,
        "case_json_path": str(cj) if cj else None,
        "task_md_path": str(tm) if tm else None,
        "output_contract": case_data.get("output_contract"),
        "discovered_materials": mats,
        "task_md_preview": tm.read_text(encoding="utf-8", errors="replace")[:3500] if tm else "",
        "toolchain": {
            "pymupdf": importlib.util.find_spec("fitz") is not None,
            "pdfplumber": importlib.util.find_spec("pdfplumber") is not None,
            "rdflib": importlib.util.find_spec("rdflib") is not None,
            "pdftohtml": shutil.which("pdftohtml") is not None,
        },
    }
    (work / "case_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rp = out / "result.json"
    if not args.no_init_output and not rp.exists():
        res: Dict[str, Any] = {"case_id": case_id}
        contract = case_data.get("output_contract")
        if isinstance(contract, dict):
            if isinstance(contract.get("properties"), dict):
                for k, v in contract["properties"].items():
                    res.setdefault(k, default_value_for_spec(v))
            elif isinstance(contract.get("required_fields"), list):
                for k in contract["required_fields"]:
                    if isinstance(k, str):
                        res.setdefault(k, None)
            else:
                for k, v in contract.items():
                    if k not in {"type", "required", "$schema", "description"}:
                        res.setdefault(k, default_value_for_spec(v))
        elif isinstance(contract, list):
            for item in contract:
                if isinstance(item, str):
                    res.setdefault(item, None)
                elif isinstance(item, dict) and isinstance(item.get("name"), str):
                    res.setdefault(item["name"], default_value_for_spec(item))
        res["status"] = "partial"
        res["status_reason"] = "Initial Nanopublication case discovery saved; passage indexing and assertion extraction pending."
        res.setdefault("input_identification", {"case_json_path": str(cj) if cj else None, "source_files": mats})
        res.setdefault("results", {})
        res.setdefault("sources", [])
        res.setdefault("uncertainty", {"unreported_in_source": [], "unverifiable_items": ["Assertion extraction pending."], "material_conflicts": [], "human_takeover_locations": []})
        res["change_records"] = [{"step": "inspect_case_initial_checkpoint", "timestamp": utc_now_iso(), "summary": f"Initialized checkpoint for {case_id}."}]
        rp.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"status": "ok", "case_id": case_id, "materials_found": [m["path"] for m in mats]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
