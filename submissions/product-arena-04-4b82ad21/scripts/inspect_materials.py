#!/usr/bin/env python3
"""Inspect and index input research materials and bibliographies for Introduction & Related Work writing.

Pure Python stdlib (with optional graceful PDF fallback if pypdf/fitz/pdftotext is installed).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


TEXT_EXTS = {".md", ".txt", ".tex", ".rst", ".csv", ".tsv", ".yaml", ".yml"}
BIB_EXTS = {".bib", ".ris"}
JSON_EXTS = {".json", ".jsonl"}


def extract_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paras = []
        for p in root.findall(".//w:p", ns):
            line = "".join(t.text for t in p.findall(".//w:t", ns) if t.text).strip()
            if line:
                paras.append(line)
        return "\n\n".join(paras)
    except Exception:
        return ""


def safe_read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return ""


def extract_pdf_text(path: Path) -> Tuple[str, Optional[str]]:
    """Attempt best-effort PDF text extraction without failing if no backend exists."""
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(path))
        pages = [page.get_text("text") for page in doc]
        doc.close()
        return "\n".join(pages), None
    except Exception:
        pass
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(path))
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n".join(pages), None
    except Exception:
        pass
    if shutil.which("pdftotext"):
        try:
            proc = subprocess.run(
                ["pdftotext", str(path), "-"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout, None
        except Exception:
            pass
    return "", f"No PDF text extractor (pymupdf/pypdf/pdftotext) succeeded for {path.name}"


def parse_bibtex(text: str, source_file: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    # Match @type{key, ... } using brace counting for nested braces
    pattern = re.compile(r"@([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)
    for m in pattern.finditer(text):
        entry_type = m.group(1).lower()
        if entry_type in {"comment", "preamble", "string"}:
            continue
        key = m.group(2).strip()
        start = m.end()
        depth = 1
        pos = start
        while pos < len(text) and depth > 0:
            ch = text[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1
        body = text[start : pos - 1]
        fields: Dict[str, str] = {}
        field_re = re.compile(r"([A-Za-z0-9_-]+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|\d+)")
        for fm in field_re.finditer(body):
            fname = fm.group(1).lower()
            raw_val = fm.group(2).strip()
            if (raw_val.startswith("{") and raw_val.endswith("}")) or (
                raw_val.startswith('"') and raw_val.endswith('"')
            ):
                raw_val = raw_val[1:-1]
            fields[fname] = re.sub(r"\s+", " ", raw_val).strip()
        venue = fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or ""
        entries.append(
            {
                "ref_id": key,
                "entry_type": entry_type,
                "title": fields.get("title", ""),
                "authors": fields.get("author", ""),
                "year": fields.get("year", ""),
                "venue": venue,
                "doi": fields.get("doi", ""),
                "abstract": fields.get("abstract", ""),
                "source_file": source_file,
                "metadata_complete": bool(fields.get("author") and fields.get("year") and (venue or fields.get("title"))),
            }
        )
    return entries


def parse_ris(text: str, source_file: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    authors: List[str] = []
    idx = 0
    for line in text.splitlines():
        m = re.match(r"^([A-Z0-9]{2})\s*-\s*(.*)$", line.strip())
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            current = {"entry_type": val}
            authors = []
        elif tag in {"AU", "A1"}:
            authors.append(val)
        elif tag in {"TI", "T1"}:
            current["title"] = val
        elif tag in {"PY", "Y1"}:
            ym = re.search(r"(19|20)\d{2}", val)
            current["year"] = ym.group(0) if ym else val
        elif tag in {"JO", "JF", "T2", "BT"}:
            current.setdefault("venue", val)
        elif tag == "DO":
            current["doi"] = val
        elif tag == "AB":
            current["abstract"] = val
        elif tag == "ID":
            current["ref_id"] = val
        elif tag == "ER":
            idx += 1
            ref_id = current.get("ref_id") or f"ris_ref_{idx}"
            auth_str = "; ".join(authors)
            entries.append(
                {
                    "ref_id": ref_id,
                    "entry_type": current.get("entry_type", "article"),
                    "title": current.get("title", ""),
                    "authors": auth_str,
                    "year": current.get("year", ""),
                    "venue": current.get("venue", ""),
                    "doi": current.get("doi", ""),
                    "abstract": current.get("abstract", ""),
                    "source_file": source_file,
                    "metadata_complete": bool(auth_str and current.get("year") and (current.get("venue") or current.get("title"))),
                }
            )
            current = {}
            authors = []
    return entries


def extract_refs_from_json_obj(obj: Any, source_file: str) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, dict):
            # Heuristic: looks like a bibliographic record
            keys_lower = {k.lower(): k for k in node.keys()}
            has_title = "title" in keys_lower
            has_author_or_year = any(k in keys_lower for k in ("author", "authors", "year", "citation_key", "bibtex_key", "ref_id", "doi"))
            if has_title and has_author_or_year:
                ref_id = (
                    node.get(keys_lower.get("ref_id", ""))
                    or node.get(keys_lower.get("citation_key", ""))
                    or node.get(keys_lower.get("bibtex_key", ""))
                    or node.get(keys_lower.get("id", ""))
                    or node.get(keys_lower.get("key", ""))
                )
                authors_val = node.get(keys_lower.get("authors", "")) or node.get(keys_lower.get("author", "")) or ""
                if isinstance(authors_val, list):
                    authors_val = "; ".join(str(a) for a in authors_val)
                year_val = str(node.get(keys_lower.get("year", ""), "")).strip()
                venue_val = (
                    node.get(keys_lower.get("venue", ""))
                    or node.get(keys_lower.get("journal", ""))
                    or node.get(keys_lower.get("booktitle", ""))
                    or node.get(keys_lower.get("source", ""))
                    or ""
                )
                title_val = str(node.get(keys_lower["title"], "")).strip()
                if not ref_id:
                    first_tok = re.sub(r"[^A-Za-z0-9]+", "", str(authors_val).split(",")[0].split(";")[0])[:12].lower() or "ref"
                    ref_id = f"{first_tok}{year_val or len(found)+1}"
                found.append(
                    {
                        "ref_id": str(ref_id).strip(),
                        "entry_type": str(node.get("type", "reference")),
                        "title": title_val,
                        "authors": str(authors_val).strip(),
                        "year": year_val,
                        "venue": str(venue_val).strip(),
                        "doi": str(node.get(keys_lower.get("doi", ""), "")).strip(),
                        "abstract": str(node.get(keys_lower.get("abstract", ""), "")).strip(),
                        "source_file": source_file,
                        "metadata_complete": bool(authors_val and year_val and (venue_val or title_val)),
                    }
                )
            for v in node.values():
                if isinstance(v, (dict, list)):
                    _walk(v)

    _walk(obj)
    return found


def extract_inline_refs_from_text(text: str, source_file: str) -> List[Dict[str, Any]]:
    """Extract numbered or bracketed reference list lines from plain text/markdown when no .bib is given."""
    refs: List[Dict[str, Any]] = []
    # Match lines starting with [1] Author... (2023) ...
    for line in text.splitlines():
        m = re.match(r"^\s*\[(\d{1,3}|[A-Za-z][A-Za-z0-9_.:-]{2,30})\]\s+(.{20,})$", line)
        if not m:
            continue
        rid, rest = m.group(1).strip(), m.group(2).strip()
        ym = re.search(r"\b((?:19|20)\d{2})[a-z]?\b", rest)
        year = ym.group(1) if ym else ""
        # Require a plausible year or DOI/venue indicator to avoid false positives on checklists
        if not year and "doi" not in rest.lower() and "et al" not in rest.lower():
            continue
        author_part = rest.split(".")[0][:80].strip()
        refs.append(
            {
                "ref_id": rid,
                "entry_type": "text_ref",
                "title": rest[:220],
                "authors": author_part,
                "year": year,
                "venue": "",
                "doi": "",
                "abstract": "",
                "source_file": source_file,
                "metadata_complete": bool(author_part and year),
            }
        )
    return refs


def extract_author_evidence(text: str, source_file: str) -> Dict[str, Any]:
    """Extract figures, tables, sections, equations, and numeric tokens from author materials."""
    tables = sorted(set(re.findall(r"(?:Table|Tab\.|表)\s*[A-Za-z]?\d+(?:\.\d+)?", text, re.IGNORECASE)))
    figures = sorted(set(re.findall(r"(?:Figure|Fig\.|图)\s*[A-Za-z]?\d+(?:\.\d+)?", text, re.IGNORECASE)))
    sections = sorted(set(re.findall(r"(?:Section|Sec\.|第)\s*\d+(?:\.\d+)?(?:\s*节)?", text, re.IGNORECASE)))
    numbers = sorted(
        set(
            re.findall(
                r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?(?:\s*(?:%|GPU|bar|MPa|kPa|K|°C|℃|h|hr|hours|ms|s|nm|μm|um|mm|cm|mL|mg|g|kg|mol|mM|eV|W|mW|Hz|MHz|GHz|GB|MB|x|×))?",
                text,
            )
        )
    )
    return {
        "source_file": source_file,
        "tables": tables[:60],
        "figures": figures[:60],
        "sections": sections[:40],
        "numeric_tokens": numbers[:300],
        "char_count": len(text),
    }


def detect_task_preferences(case_data: Dict[str, Any], task_md: str) -> Dict[str, Any]:
    combined = json.dumps(case_data, ensure_ascii=False) + "\n" + task_md
    lang = case_data.get("language")
    if not lang:
        zh_chars = len(re.findall(r"[\u4e00-\u9fa5]", task_md))
        en_words = len(re.findall(r"\b[A-Za-z]{3,}\b", task_md))
        lang = "zh" if zh_chars > en_words else "en"
    style = case_data.get("citation_style")
    if not style:
        if "\\cite" in combined or "latex" in combined.lower() or ".tex" in combined.lower():
            style = "latex-cite"
        elif "apa" in combined.lower() or "author-year" in combined.lower() or "作者-年份" in combined:
            style = "author-year"
        elif "gb/t" in combined.lower() or "ieee" in combined.lower() or "顺序编码" in combined:
            style = "numeric"
        else:
            style = "pandoc-key"
    output_contract = case_data.get("output_contract") or {}
    return {
        "case_id": str(case_data.get("case_id") or case_data.get("id") or "unknown-case"),
        "language": lang,
        "citation_style": style,
        "output_contract": output_contract,
        "raw_case_keys": sorted(case_data.keys()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect and index inputs for Introduction & Related Work writing.")
    parser.add_argument("--input-dir", required=True, help="Directory containing case.json, task.md, and source files.")
    parser.add_argument("--out", required=True, help="Output path for material_manifest.json.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"ERROR: input directory not found: {input_dir}", file=sys.stderr)
        return 2

    case_data: Dict[str, Any] = {}
    task_md = ""
    warnings: List[str] = []
    files_indexed: List[Dict[str, Any]] = []
    literature_by_id: Dict[str, Dict[str, Any]] = {}
    author_evidence: List[Dict[str, Any]] = []

    case_path = input_dir / "case.json"
    if case_path.exists():
        try:
            case_data = json.loads(safe_read_text(case_path))
        except Exception as exc:
            warnings.append(f"Failed to parse case.json: {exc}")
    else:
        warnings.append("case.json not found in input directory; using fallback defaults.")

    task_path = input_dir / "task.md"
    if task_path.exists():
        task_md = safe_read_text(task_path)

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_dir).as_posix()
        ext = path.suffix.lower()
        files_indexed.append({"path": rel, "ext": ext, "size_bytes": path.stat().st_size})

        if ext in BIB_EXTS:
            text = safe_read_text(path)
            parsed = parse_bibtex(text, rel) if ext == ".bib" else parse_ris(text, rel)
            for ref in parsed:
                literature_by_id[ref["ref_id"]] = ref
        elif ext in JSON_EXTS:
            raw = safe_read_text(path)
            try:
                if ext == ".jsonl":
                    objs = [json.loads(line) for line in raw.splitlines() if line.strip()]
                else:
                    objs = json.loads(raw)
                for ref in extract_refs_from_json_obj(objs, rel):
                    literature_by_id.setdefault(ref["ref_id"], ref)
                if path.name != "case.json":
                    author_evidence.append(extract_author_evidence(raw, rel))
            except Exception as exc:
                warnings.append(f"Could not parse JSON file {rel}: {exc}")
        elif ext in TEXT_EXTS:
            text = safe_read_text(path)
            if path.name != "task.md":
                author_evidence.append(extract_author_evidence(text, rel))
                for ref in extract_inline_refs_from_text(text, rel):
                    literature_by_id.setdefault(ref["ref_id"], ref)
        elif ext == ".docx":
            text = extract_docx_text(path)
            if text:
                author_evidence.append(extract_author_evidence(text, rel))
                for ref in extract_inline_refs_from_text(text, rel):
                    literature_by_id.setdefault(ref["ref_id"], ref)
        elif ext == ".pdf":
            text, err = extract_pdf_text(path)
            if err:
                warnings.append(err)
            if text:
                author_evidence.append(extract_author_evidence(text, rel))
                for ref in extract_inline_refs_from_text(text, rel):
                    literature_by_id.setdefault(ref["ref_id"], ref)

    prefs = detect_task_preferences(case_data, task_md)
    literature_list = list(literature_by_id.values())
    if not literature_list:
        warnings.append(
            "No structured bibliography or reference entries detected in input materials. "
            "Do NOT fabricate citations; emit [CITATION_NEEDED] markers and status='partial'."
        )

    years = [int(r["year"]) for r in literature_list if re.fullmatch(r"(19|20)\d{2}", str(r.get("year", "")))]
    manifest = {
        "case_id": prefs["case_id"],
        "language": prefs["language"],
        "citation_style": prefs["citation_style"],
        "output_contract": prefs["output_contract"],
        "raw_case_keys": prefs["raw_case_keys"],
        "files_indexed": files_indexed,
        "literature_inventory": literature_list,
        "literature_stats": {
            "total_refs": len(literature_list),
            "complete_metadata_refs": sum(1 for r in literature_list if r.get("metadata_complete")),
            "min_year": min(years) if years else None,
            "max_year": max(years) if years else None,
        },
        "author_evidence_index": {
            "files": author_evidence,
            "all_tables": sorted({t for item in author_evidence for t in item["tables"]}),
            "all_figures": sorted({f for item in author_evidence for f in item["figures"]}),
            "all_numeric_tokens": sorted({n for item in author_evidence for n in item["numeric_tokens"]}),
        },
        "input_warnings": warnings,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "case_id": manifest["case_id"],
                "files_indexed": len(files_indexed),
                "total_refs": len(literature_list),
                "warnings": len(warnings),
                "out": str(out_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
