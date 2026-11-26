#!/usr/bin/env python3
"""
index_source_passages.py — Universal Multi-Format Passage Indexer (JATS/JSON Corpora + PDF + MD/TXT)

Indexes every source passage with honest, non-fabricated location types:
  - JATS / JSON corpora (corpus-manifest.json, material.json, primary-material.json,
    secondary-material.json, *.xml): records location_type="jats_paragraph_id" with
    exact (source_id, paragraph_id) and pdf_page=null — NEVER fabricates PDF page 1!
  - Actual PDF files (.pdf): uses multi-backend extraction (PyMuPDF -> pdfplumber ->
    Poppler XML -> pure-Python stdlib stream) and records location_type="pdf_page_bbox"
    with 1-based physical page, printed_page_label, column, and bbox.
  - Markdown / Plain Text (.md, .txt): records location_type="text_block_id".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _decode_pdf_str(raw: bytes) -> str:
    out = bytearray()
    i, n = 0, len(raw)
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("("): 40, ord(")"): 41, ord("\\"): 92}
    while i < n:
        c = raw[i]
        if c == 92 and i + 1 < n:
            out.append(esc.get(raw[i + 1], raw[i + 1]))
            i += 2
            continue
        out.append(c)
        i += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("latin-1", errors="replace")


def extract_pdf_passages(pdf_path: Path, source_id: str) -> Tuple[List[Dict[str, Any]], bool, str]:
    passages: List[Dict[str, Any]] = []
    # Tier 1: PyMuPDF
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf_path))
        if doc.is_encrypted and not doc.authenticate(""):
            return [], True, "pymupdf"
        idx_c = 0
        for p_i in range(len(doc)):
            pg = doc[p_i]
            pn = p_i + 1
            lbl = pg.get_label() or str(pn)
            for b in pg.get_text("dict").get("blocks", []):
                if b.get("type", 0) != 0:
                    continue
                txt = clean_ws(" ".join(
                    "".join(sp.get("text", "") for sp in ln.get("spans", []))
                    for ln in b.get("lines", [])
                ))
                if len(txt) < 4:
                    continue
                idx_c += 1
                pid = f"P{idx_c:04d}"
                passages.append({
                    "passage_id": pid,
                    "source_id": source_id,
                    "source_file": str(pdf_path),
                    "location_type": "pdf_page_bbox",
                    "page": pn,
                    "printed_page_label": lbl,
                    "paragraph_id": None,
                    "bbox": [round(float(v), 2) for v in b.get("bbox", (0, 0, 0, 0))],
                    "section_path": "PDF Body",
                    "text": txt,
                })
        doc.close()
        return passages, False, "pymupdf"
    except Exception:
        pass

    # Tier 2: Poppler pdftohtml -xml
    p2h = shutil.which("pdftohtml")
    if p2h:
        with tempfile.TemporaryDirectory(prefix="np_pop_") as tmp:
            xp = Path(tmp) / "d.xml"
            subprocess.run([p2h, "-xml", "-i", "-nodrm", str(pdf_path), str(xp)], capture_output=True, timeout=90)
            if xp.exists():
                xt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xp.read_text(encoding="utf-8", errors="replace"))
                root = ET.fromstring(xt)
                idx_c = 0
                for pel in root.findall(".//page"):
                    pn = int(pel.attrib.get("number", 1))
                    for tel in pel.findall("text"):
                        t = clean_ws("".join(tel.itertext()))
                        if len(t) < 4:
                            continue
                        idx_c += 1
                        l, top = float(tel.attrib.get("left", 0)), float(tel.attrib.get("top", 0))
                        w, h = float(tel.attrib.get("width", 40)), float(tel.attrib.get("height", 12))
                        passages.append({
                            "passage_id": f"P{idx_c:04d}",
                            "source_id": source_id,
                            "source_file": str(pdf_path),
                            "location_type": "pdf_page_bbox",
                            "page": pn,
                            "printed_page_label": str(pn),
                            "paragraph_id": None,
                            "bbox": [l, top, l + w, top + h],
                            "section_path": "PDF Body",
                            "text": t,
                        })
                return passages, False, "poppler_xml"

    # Tier 3: Stdlib PDF stream fallback
    data = pdf_path.read_bytes()
    if re.search(rb"/Encrypt\b", data):
        return [], True, "stdlib_pdf_stream"
    objs = {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", data, flags=re.DOTALL)}
    p_bodies = [b for _, b in sorted(objs.items()) if re.search(rb"/Type\s*/Page\b(?!s)", b.split(b"stream", 1)[0])]
    op_re = re.compile(
        rb"(?P<tm>([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm)"
        rb"|(?P<tf>/(\w+)\s+([-\d.]+)\s+Tf)"
        rb"|(?P<tj>\((?:\\.|[^\\)])*\)\s*Tj)",
        flags=re.DOTALL,
    )
    idx_c = 0
    for pi, pb in enumerate(p_bodies):
        pn = pi + 1
        mb = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", pb)
        w = float(mb.group(3)) - float(mb.group(1)) if mb else 612.0
        h = float(mb.group(4)) - float(mb.group(2)) if mb else 792.0
        co = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", pb)
        tbs = [objs[int(co.group(1))]] if (co and int(co.group(1)) in objs) else [pb]
        chunks = []
        for tb in tbs:
            for sm in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", tb, flags=re.DOTALL):
                ch = sm.group(1)
                if b"/FlateDecode" in tb.split(b"stream", 1)[0]:
                    try:
                        ch = zlib.decompress(ch)
                    except Exception:
                        pass
                chunks.append(ch)
        for bm in re.finditer(rb"BT\b(.*?)ET\b", b"\n".join(chunks), flags=re.DOTALL):
            cx, cy, csz = 54.0, h - 54.0, 10.0
            for m in op_re.finditer(bm.group(1)):
                if m.group("tm"):
                    vs = m.group("tm").split()
                    cx, cy = float(vs[4]), float(vs[5])
                elif m.group("tf"):
                    mt = re.match(rb"/(\w+)\s+([-\d.]+)\s+Tf", m.group("tf"))
                    if mt:
                        csz = abs(float(mt.group(2)))
                elif m.group("tj"):
                    lit = re.search(rb"\((.*)\)\s*Tj", m.group("tj"), flags=re.DOTALL)
                    if lit:
                        t = clean_ws(_decode_pdf_str(lit.group(1)))
                        if len(t) >= 4:
                            idx_c += 1
                            ty = round(max(0.0, h - cy - csz), 2)
                            passages.append({
                                "passage_id": f"P{idx_c:04d}",
                                "source_id": source_id,
                                "source_file": str(pdf_path),
                                "location_type": "pdf_page_bbox",
                                "page": pn,
                                "printed_page_label": str(pn),
                                "paragraph_id": None,
                                "bbox": [round(cx, 2), ty, round(min(w, cx + len(t) * csz * 0.52), 2), round(ty + csz * 1.15, 2)],
                                "section_path": "PDF Body",
                                "text": t,
                            })
    return passages, False, "stdlib_pdf_stream"


def extract_json_material_passages(json_path: Path, default_source_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
    passages: List[Dict[str, Any]] = []
    meta_out: Dict[str, Any] = {}

    if isinstance(data, dict):
        for mk in ("source_id", "doi", "title", "authors", "year", "license"):
            if mk in data:
                meta_out[mk] = data[mk]
        rows = data.get("paragraphs") or data.get("passages") or data.get("sections") or data.get("chunks") or data.get("items") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []

    for idx, row in enumerate(rows, start=1):
        if isinstance(row, str):
            txt = clean_ws(row)
            if not txt:
                continue
            passages.append({
                "passage_id": f"{default_source_id}_p{idx:04d}",
                "source_id": default_source_id,
                "source_file": str(json_path),
                "location_type": "jats_paragraph_id",
                "page": None,
                "printed_page_label": None,
                "paragraph_id": f"p{idx:04d}",
                "bbox": None,
                "section_path": "Body",
                "text": txt,
            })
        elif isinstance(row, dict):
            sid = str(row.get("source_id") or meta_out.get("source_id") or default_source_id)
            pid_raw = str(row.get("paragraph_id") or row.get("id") or row.get("para_id") or f"p{idx:04d}")
            txt = clean_ws(str(row.get("text") or row.get("content") or row.get("body") or ""))
            if not txt:
                continue
            sec = str(row.get("section") or row.get("section_path") or row.get("heading") or "Body")
            passages.append({
                "passage_id": f"{sid}:{pid_raw}" if ":" not in pid_raw else pid_raw,
                "source_id": sid,
                "source_file": str(json_path),
                "location_type": "jats_paragraph_id",
                "page": None,
                "printed_page_label": None,
                "paragraph_id": pid_raw,
                "bbox": None,
                "section_path": sec,
                "text": txt,
            })
    return passages, meta_out


def extract_jats_xml_passages(xml_path: Path, source_id: str) -> List[Dict[str, Any]]:
    tree = ET.parse(str(xml_path))
    root = tree.getroot()
    passages: List[Dict[str, Any]] = []
    idx = 0
    for p_el in root.iter():
        tag = p_el.tag.split("}")[-1]
        if tag != "p":
            continue
        txt = clean_ws("".join(p_el.itertext()))
        if len(txt) < 4:
            continue
        idx += 1
        pid = p_el.attrib.get("id") or f"xml_p{idx:04d}"
        passages.append({
            "passage_id": f"{source_id}:{pid}",
            "source_id": source_id,
            "source_file": str(xml_path),
            "location_type": "jats_paragraph_id",
            "page": None,
            "printed_page_label": None,
            "paragraph_id": pid,
            "bbox": None,
            "section_path": "JATS XML",
            "text": txt,
        })
    return passages


def index_directory(input_dir: Path, explicit_source: Optional[Path], out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    all_passages: List[Dict[str, Any]] = []
    sources_meta: Dict[str, Dict[str, Any]] = {}
    encrypted_files: List[str] = []

    if explicit_source and explicit_source.is_file():
        files_to_index = [explicit_source]
    else:
        # Prefer structured corpus files if present (material.json / primary-material.json / secondary-material.json / PDFs)
        all_files = sorted(p for p in input_dir.rglob("*") if p.is_file())
        manifest_file = next((p for p in all_files if p.name.lower() == "corpus-manifest.json"), None)
        if manifest_file:
            mdata = json.loads(manifest_file.read_text(encoding="utf-8", errors="replace"))
            items = mdata.get("sources") or mdata.get("papers") or mdata.get("documents") or (mdata if isinstance(mdata, list) else [])
            for item in items:
                if isinstance(item, dict):
                    sid = str(item.get("source_id") or item.get("id") or f"src_{len(sources_meta)+1}")
                    sources_meta[sid] = item

        json_mats = [p for p in all_files if p.name.lower() in {"material.json", "primary-material.json", "secondary-material.json"}]
        pdfs = [p for p in all_files if p.suffix.lower() == ".pdf"]
        xmls = [p for p in all_files if p.suffix.lower() == ".xml"]
        mds = [p for p in all_files if p.suffix.lower() in {".md", ".txt"} and p.name.lower() not in {"task.md", "instruction.md", "readme.md"}]

        if json_mats:
            # Prefer primary+secondary over combined material.json to avoid duplicate indexing, or use material.json if alone
            split_mats = [p for p in json_mats if p.name.lower() in {"primary-material.json", "secondary-material.json"}]
            files_to_index = split_mats if split_mats else json_mats
        elif pdfs:
            files_to_index = pdfs
        elif xmls:
            files_to_index = xmls
        else:
            files_to_index = mds

    for f in files_to_index:
        sid = f.stem
        if "primary" in f.name.lower():
            sid = "primary"
        elif "secondary" in f.name.lower():
            sid = "secondary"
        f_sha = sha256_file(f)

        if f.suffix.lower() == ".pdf":
            ps, enc, backend = extract_pdf_passages(f, sid)
            if enc:
                encrypted_files.append(str(f))
            all_passages.extend(ps)
            sources_meta.setdefault(sid, {"source_id": sid, "file": str(f), "sha256": f_sha, "backend": backend, "location_type": "pdf_page_bbox"})
        elif f.suffix.lower() == ".json":
            ps, meta = extract_json_material_passages(f, sid)
            all_passages.extend(ps)
            sources_meta.setdefault(sid, {}).update({"source_id": sid, "file": str(f), "sha256": f_sha, "location_type": "jats_paragraph_id", **meta})
        elif f.suffix.lower() == ".xml":
            ps = extract_jats_xml_passages(f, sid)
            all_passages.extend(ps)
            sources_meta.setdefault(sid, {"source_id": sid, "file": str(f), "sha256": f_sha, "location_type": "jats_paragraph_id"})
        else:
            chunks = [clean_ws(c) for c in re.split(r"\n\s*\n", f.read_text(encoding="utf-8", errors="replace")) if clean_ws(c)]
            for idx, ch in enumerate(chunks, start=1):
                all_passages.append({
                    "passage_id": f"{sid}_B{idx:04d}",
                    "source_id": sid,
                    "source_file": str(f),
                    "location_type": "text_block_id",
                    "page": None,
                    "printed_page_label": None,
                    "paragraph_id": f"B{idx:04d}",
                    "bbox": None,
                    "section_path": "Text",
                    "text": ch,
                })
            sources_meta.setdefault(sid, {"source_id": sid, "file": str(f), "sha256": f_sha, "location_type": "text_block_id"})

    payload = {
        "status": "unverifiable" if (encrypted_files and not all_passages) else ("partial" if encrypted_files else "success"),
        "source_count": len(sources_meta),
        "passage_count": len(all_passages),
        "encrypted_files": encrypted_files,
        "sources_metadata": sources_meta,
        "passages": all_passages,
    }
    out_p = out_dir / "indexed_passages.json"
    out_p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="Index PDF, JATS JSON/XML, or text sources into provenance-tagged passages.")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--source", default=None, help="Optional single source file override.")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    res = index_directory(Path(args.input_dir), Path(args.source) if args.source else None, Path(args.out_dir))
    print(json.dumps({
        "status": res["status"],
        "source_count": res["source_count"],
        "passage_count": res["passage_count"],
        "sources": list(res["sources_metadata"].keys()),
        "out_json": str(Path(args.out_dir) / "indexed_passages.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
