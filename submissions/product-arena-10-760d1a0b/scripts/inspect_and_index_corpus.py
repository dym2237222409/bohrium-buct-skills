#!/usr/bin/env python3
"""
inspect_and_index_corpus.py — Multi-Paper Corpus Inspector & Honest Location Indexer

Designed specifically for multi-document Literature Knowledge Graph construction:
1. Reads /input/case.json, task.md, corpus-manifest.json, primary-material.json,
   secondary-material.json, material.json, JATS XML, and/or PDF files.
2. Strictly isolates per-paper metadata (source_id, doi, title, authors, year, sha256,
   license) so root Paper-1 metadata is NEVER copied onto Paper 2.
3. Preserves exact JATS paragraph_id locations (location_type="jats_paragraph_id",
   page=null) when no PDF pages exist — NEVER fabricates PDF page 1.
4. Tracks distinct content hashes so multiple formats (.json + .md) of the same paper
   are never mistaken for cross-paper corroboration.
5. Saves an early honest partial checkpoint at /app/output/result.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def clean_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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


def parse_material_json(path: Path, fallback_sid: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    passages: List[Dict[str, Any]] = []
    meta_by_sid: Dict[str, Dict[str, Any]] = {}

    if isinstance(data, dict):
        top_sid = str(data.get("source_id") or data.get("id") or fallback_sid)
        top_meta = {
            k: data[k] for k in ("source_id", "doi", "title", "authors", "year", "license", "sha256")
            if k in data
        }
        if top_meta:
            top_meta.setdefault("source_id", top_sid)
            meta_by_sid[top_sid] = top_meta

        # Support multi-paper containers (e.g. {"documents": [...]} or {"sources": [...]}) or flat paragraphs list
        docs = data.get("documents") or data.get("papers") or data.get("sources")
        if isinstance(docs, list) and docs and isinstance(docs[0], dict) and any(k in docs[0] for k in ("paragraphs", "passages", "sections")):
            for d_i, doc in enumerate(docs, start=1):
                dsid = str(doc.get("source_id") or doc.get("id") or f"paper_{d_i}")
                meta_by_sid[dsid] = {
                    "source_id": dsid,
                    "doi": doc.get("doi"),
                    "title": doc.get("title"),
                    "authors": doc.get("authors"),
                    "year": doc.get("year"),
                    "license": doc.get("license"),
                    "file": str(path),
                }
                for p_i, row in enumerate(doc.get("paragraphs") or doc.get("passages") or [], start=1):
                    if isinstance(row, dict):
                        pid = str(row.get("paragraph_id") or row.get("id") or f"p_{p_i:04d}")
                        txt = clean_ws(str(row.get("text") or row.get("content") or ""))
                        if txt:
                            passages.append({
                                "uid": f"{dsid}:{pid}",
                                "source_id": dsid,
                                "paragraph_id": pid,
                                "location_type": "jats_paragraph_id",
                                "page": None,
                                "section": str(row.get("section") or row.get("section_path") or "Body"),
                                "source_file": str(path),
                                "text": txt,
                            })
            return passages, meta_by_sid

        rows = data.get("paragraphs") or data.get("passages") or data.get("items") or data.get("chunks") or []
    elif isinstance(data, list):
        rows = data
        top_sid = fallback_sid
    else:
        rows = []
        top_sid = fallback_sid

    for idx, row in enumerate(rows, start=1):
        if isinstance(row, dict):
            sid = str(row.get("source_id") or top_sid)
            pid = str(row.get("paragraph_id") or row.get("id") or row.get("para_id") or f"p_{idx:04d}")
            txt = clean_ws(str(row.get("text") or row.get("content") or row.get("body") or ""))
            if not txt:
                continue
            passages.append({
                "uid": f"{sid}:{pid}",
                "source_id": sid,
                "paragraph_id": pid,
                "location_type": "jats_paragraph_id",
                "page": None,
                "section": str(row.get("section") or row.get("section_path") or row.get("heading") or "Body"),
                "source_file": str(path),
                "text": txt,
            })
            meta_by_sid.setdefault(sid, {"source_id": sid, "file": str(path)})
        elif isinstance(row, str) and clean_ws(row):
            pid = f"p_{idx:04d}"
            passages.append({
                "uid": f"{top_sid}:{pid}",
                "source_id": top_sid,
                "paragraph_id": pid,
                "location_type": "jats_paragraph_id",
                "page": None,
                "section": "Body",
                "source_file": str(path),
                "text": clean_ws(row),
            })
    return passages, meta_by_sid


def parse_pdf_simple(pdf_path: Path, source_id: str) -> Tuple[List[Dict[str, Any]], bool]:
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf_path))
        if doc.is_encrypted and not doc.authenticate(""):
            return [], True
        out = []
        idx = 0
        for pi in range(len(doc)):
            pg = doc[pi]
            for b in pg.get_text("dict").get("blocks", []):
                if b.get("type", 0) != 0:
                    continue
                txt = clean_ws(" ".join("".join(sp.get("text", "") for sp in ln.get("spans", [])) for ln in b.get("lines", [])))
                if len(txt) < 4:
                    continue
                idx += 1
                pid = f"p{pi+1}_b{idx:04d}"
                out.append({
                    "uid": f"{source_id}:{pid}",
                    "source_id": source_id,
                    "paragraph_id": pid,
                    "location_type": "pdf_page_bbox",
                    "page": pi + 1,
                    "bbox": [round(float(v), 2) for v in b.get("bbox", (0, 0, 0, 0))],
                    "section": "PDF Body",
                    "source_file": str(pdf_path),
                    "text": txt,
                })
        doc.close()
        return out, False
    except Exception:
        pass

    data = pdf_path.read_bytes()
    if re.search(rb"/Encrypt\b", data):
        return [], True
    objs = {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", data, flags=re.DOTALL)}
    p_bodies = [b for _, b in sorted(objs.items()) if re.search(rb"/Type\s*/Page\b(?!s)", b.split(b"stream", 1)[0])]
    out = []
    idx = 0
    for pi, pb in enumerate(p_bodies):
        co = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", pb)
        tbs = [objs[int(co.group(1))]] if (co and int(co.group(1)) in objs) else [pb]
        for tb in tbs:
            for sm in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", tb, flags=re.DOTALL):
                ch = sm.group(1)
                if b"/FlateDecode" in tb.split(b"stream", 1)[0]:
                    try:
                        ch = zlib.decompress(ch)
                    except Exception:
                        pass
                for tm in re.finditer(rb"\(((?:\\.|[^\\)])+)\)\s*Tj", ch):
                    txt = clean_ws(tm.group(1).decode("utf-8", errors="replace").replace(r"\(", "(").replace(r"\)", ")"))
                    if len(txt) >= 4:
                        idx += 1
                        pid = f"p{pi+1}_b{idx:04d}"
                        out.append({
                            "uid": f"{source_id}:{pid}",
                            "source_id": source_id,
                            "paragraph_id": pid,
                            "location_type": "pdf_page_bbox",
                            "page": pi + 1,
                            "section": "PDF Body",
                            "source_file": str(pdf_path),
                            "text": txt,
                        })
    return out, False


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect and index multi-paper KG corpus with independent per-paper provenance.")
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
    case_id = case_data.get("case_id") or case_data.get("id") or "unknown_case"

    all_files = sorted(p for p in inp.rglob("*") if p.is_file()) if inp.exists() else []
    sources_meta: Dict[str, Dict[str, Any]] = {}

    # 1. Load corpus-manifest.json first so each paper keeps independent DOI/title/authors/hash/license
    manifest_p = next((p for p in all_files if p.name.lower() == "corpus-manifest.json"), None)
    if manifest_p:
        mdata = json.loads(manifest_p.read_text(encoding="utf-8", errors="replace"))
        entries = mdata.get("sources") or mdata.get("papers") or mdata.get("documents") or (mdata if isinstance(mdata, list) else [])
        for ent in entries:
            if isinstance(ent, dict):
                sid = str(ent.get("source_id") or ent.get("id") or f"paper_{len(sources_meta)+1}")
                sources_meta[sid] = dict(ent)
                sources_meta[sid]["source_id"] = sid

    # 2. Index material files (prioritize primary/secondary-material.json, fallback to material.json, then PDFs/XML/MD)
    split_jsons = [p for p in all_files if p.name.lower() in {"primary-material.json", "secondary-material.json"}]
    combined_json = [p for p in all_files if p.name.lower() == "material.json"]
    pdfs = [p for p in all_files if p.suffix.lower() == ".pdf"]
    other_jsons = [p for p in all_files if p.suffix.lower() == ".json" and p.name.lower() not in {"case.json", "corpus-manifest.json", "material.json", "primary-material.json", "secondary-material.json"}]
    xmls = [p for p in all_files if p.suffix.lower() == ".xml"]

    chosen_files = split_jsons or combined_json or pdfs or other_jsons or xmls
    all_passages: List[Dict[str, Any]] = []
    seen_uids: set[str] = set()
    encrypted_pdfs: List[str] = []

    for f in chosen_files:
        sid = "primary" if "primary" in f.name.lower() else ("secondary" if "secondary" in f.name.lower() else f.stem)
        if f.suffix.lower() == ".json":
            ps, m_map = parse_material_json(f, sid)
            for p_item in ps:
                if p_item["uid"] not in seen_uids:
                    seen_uids.add(p_item["uid"])
                    all_passages.append(p_item)
            for k, v in m_map.items():
                sources_meta.setdefault(k, {}).update({mk: mv for mk, mv in v.items() if mv is not None})
                sources_meta[k].setdefault("file", str(f))
                sources_meta[k].setdefault("file_sha256", sha256_file(f))
        elif f.suffix.lower() == ".pdf":
            ps, enc = parse_pdf_simple(f, sid)
            if enc:
                encrypted_pdfs.append(str(f))
            for p_item in ps:
                if p_item["uid"] not in seen_uids:
                    seen_uids.add(p_item["uid"])
                    all_passages.append(p_item)
            sources_meta.setdefault(sid, {"source_id": sid, "file": str(f), "file_sha256": sha256_file(f), "location_type": "pdf_page_bbox"})
        elif f.suffix.lower() == ".xml":
            tree = ET.parse(str(f))
            idx = 0
            for el in tree.getroot().iter():
                if el.tag.split("}")[-1] == "p":
                    t = clean_ws("".join(el.itertext()))
                    if len(t) >= 4:
                        idx += 1
                        pid = el.attrib.get("id") or f"xml_p{idx:04d}"
                        uid = f"{sid}:{pid}"
                        if uid not in seen_uids:
                            seen_uids.add(uid)
                            all_passages.append({
                                "uid": uid, "source_id": sid, "paragraph_id": pid,
                                "location_type": "jats_paragraph_id", "page": None,
                                "section": "JATS XML", "source_file": str(f), "text": t,
                            })
            sources_meta.setdefault(sid, {"source_id": sid, "file": str(f), "file_sha256": sha256_file(f), "location_type": "jats_paragraph_id"})

    # Compute per-source normalized text hash to detect same-paper format duplicates!
    text_by_sid: Dict[str, List[str]] = {}
    for p in all_passages:
        text_by_sid.setdefault(p["source_id"], []).append(p["text"])
    for sid, texts in text_by_sid.items():
        sources_meta.setdefault(sid, {"source_id": sid})
        sources_meta[sid]["content_sha256"] = sha256_bytes("\n".join(texts).encode("utf-8"))
        sources_meta[sid]["paragraph_count"] = len(texts)

    corpus_index = {
        "indexed_at": utc_now_iso(),
        "case_id": case_id,
        "manifest_file": str(manifest_p) if manifest_p else None,
        "xml_reference_files": [str(x) for x in xmls],
        "encrypted_pdfs": encrypted_pdfs,
        "distinct_source_count": len(sources_meta),
        "distinct_content_hash_count": len({m.get("content_sha256") for m in sources_meta.values() if m.get("content_sha256")}),
        "sources": sources_meta,
        "passage_count": len(all_passages),
        "passages": all_passages,
    }
    (work / "corpus_index.json").write_text(json.dumps(corpus_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Early honest partial /app/output/result.json
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
        res["status_reason"] = f"Indexed {len(all_passages)} passages across {len(sources_meta)} independent source(s); KG extraction pending."
        res.setdefault("input_identification", {"case_json_path": str(cj) if cj else None, "corpus_sources": list(sources_meta.values())})
        res.setdefault("results", {})
        res.setdefault("sources", [])
        res.setdefault("uncertainty", {"unreported_in_source": [], "unverifiable_queries_or_relations": [], "material_conflicts": [], "human_takeover_locations": []})
        res["change_records"] = [{"step": "inspect_and_index_corpus", "timestamp": utc_now_iso(), "summary": res["status_reason"]}]
        rp.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "case_id": case_id,
        "distinct_sources": list(sources_meta.keys()),
        "distinct_content_hashes": corpus_index["distinct_content_hash_count"],
        "passage_count": len(all_passages),
        "corpus_index_path": str(work / "corpus_index.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
