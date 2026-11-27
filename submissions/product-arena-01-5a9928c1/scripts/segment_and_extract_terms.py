#!/usr/bin/env python3
"""
segment_and_extract_terms.py — Layout-Aware Paragraph/Sentence Segmenter & Term/Token Extractor

Supports PDF, Markdown, LaTeX, plain-text, and JSON inputs:
1. Reconstructs 1/2-column reading order (XY-Cut + vertical gutter projection), isolates running
   headers/footers, and stitches cross-column/cross-page broken paragraphs so zero paragraphs
   are dropped, duplicated, or misaligned.
2. Assigns deterministic segment IDs (S0001..SNNNN) and sentence boundaries (S0001.1..S0001.k)
   across Title, Abstract, Headings, Body Paragraphs, Figure/Table Captions, Table Headers,
   Equations, and Footnotes.
3. Extracts Protected Scientific Tokens (numbers/units, citations, cross-refs, equations) and
   candidate domain terminology + paper-internal abbreviation definitions.
4. Emits context-carrying translation batches (translation_batches.json) sized safely for LLM
   context windows while carrying preceding paragraph context and section paths.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
import shutil
import statistics
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = SCRIPT_DIR.parent / "resources"

FIDELITY_RULES = json.loads((RESOURCES_DIR / "fidelity_rules.json").read_text(encoding="utf-8"))
SEED_GLOSSARY = json.loads((RESOURCES_DIR / "seed_scientific_glossary.json").read_text(encoding="utf-8"))

LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\u00ad": "", "\u200b": "", "\u00a0": " ", "\u2212": "-",
}

ABBR_STOP = {"fig", "figs", "eq", "eqs", "tab", "sec", "al", "eg", "ie", "vs", "dr", "prof", "no", "vol", "pp", "ref", "refs", "ca", "approx"}


def clean_text(raw: str) -> str:
    txt = raw
    for k, v in LIGATURES.items():
        txt = txt.replace(k, v)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in txt.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def join_wrapped_lines(lines: List[str]) -> str:
    if not lines:
        return ""
    out = lines[0]
    for nxt in lines[1:]:
        if not nxt:
            continue
        if out.endswith("-") and len(out) >= 3 and out[-2].islower() and nxt[0].islower() and not out.endswith("--"):
            out = out[:-1] + nxt
        elif re.search(r"[\u4e00-\u9fff]$", out) and re.match(r"^[\u4e00-\u9fff]", nxt):
            out = out + nxt
        else:
            out = out + " " + nxt
    return out.strip()


def split_academic_sentences(text: str) -> List[str]:
    """Splits academic English/Chinese text into sentences without breaking 'et al.', 'Fig. 2', '3.14', etc."""
    text = text.strip()
    if not text:
        return []
    # Protect common abbreviations with temporary token
    protected = text
    protected = re.sub(r"\be\.g\.", "e<DOT>g<DOT>", protected, flags=re.I)
    protected = re.sub(r"\bi\.e\.", "i<DOT>e<DOT>", protected, flags=re.I)
    protected = re.sub(r"\bet\s+al\.", "et al<DOT>", protected, flags=re.I)
    for ab in ABBR_STOP:
        protected = re.sub(rf"\b({ab})\.", r"\1<DOT>", protected, flags=re.I)

    parts = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\[\(\"'\u4e00-\u9fff])", protected)
    sentences = [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]
    return sentences or [text]


# ---------------------------------------------------------------------------
# Multi-Backend PDF Extraction (PyMuPDF -> pdfplumber -> Poppler XML -> Stdlib)
# ---------------------------------------------------------------------------

def _decode_pdf_literal(raw_bytes: bytes) -> str:
    out = bytearray()
    i, n = 0, len(raw_bytes)
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("("): 40, ord(")"): 41, ord("\\"): 92}
    while i < n:
        c = raw_bytes[i]
        if c == 92 and i + 1 < n:
            nxt = raw_bytes[i + 1]
            if nxt in esc:
                out.append(esc[nxt])
                i += 2
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("latin-1", errors="replace")


def extract_pdf_pages(pdf_path: Path, page_allowlist: Optional[set[int]] = None) -> Tuple[str, bool, List[Dict[str, Any]]]:
    """Returns (backend_name, is_encrypted, pages_list)."""
    # Tier 1: PyMuPDF
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf_path))
        if doc.is_encrypted and not doc.authenticate(""):
            return "pymupdf", True, []
        pages = []
        for idx in range(len(doc)):
            p_num = idx + 1
            if page_allowlist and p_num not in page_allowlist:
                continue
            pg = doc[idx]
            w, h = float(pg.rect.width), float(pg.rect.height)
            label = pg.get_label() or str(p_num)
            blks = []
            for b in pg.get_text("dict").get("blocks", []):
                if b.get("type", 0) != 0:
                    continue
                lns = []
                sizes = []
                bold_c, tot_c = 0, 0
                for ln in b.get("lines", []):
                    sp_txts = []
                    for sp in ln.get("spans", []):
                        st = sp.get("text", "")
                        if st.strip():
                            sizes.append(float(sp.get("size", 10.0)))
                            tot_c += len(st.strip())
                            if (int(sp.get("flags", 0)) & 16) or "bold" in str(sp.get("font", "")).lower():
                                bold_c += len(st.strip())
                        sp_txts.append(st)
                    s_line = "".join(sp_txts).strip()
                    if s_line:
                        lns.append(s_line)
                if lns:
                    blks.append({
                        "bbox": [round(float(v), 2) for v in b.get("bbox", (0, 0, 0, 0))],
                        "text": join_wrapped_lines(lns),
                        "size": round(statistics.median(sizes), 2) if sizes else 10.0,
                        "bold": bold_c > 0.5 * max(1, tot_c),
                    })
            pages.append({"page": p_num, "label": label, "width": w, "height": h, "blocks": blks})
        doc.close()
        return "pymupdf", False, pages
    except Exception:
        pass

    # Tier 2: pdfplumber
    try:
        import pdfplumber  # type: ignore
        pages = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for idx, pg in enumerate(pdf.pages):
                p_num = idx + 1
                if page_allowlist and p_num not in page_allowlist:
                    continue
                words = pg.extract_words(extra_attrs=["fontname", "size"]) or []
                words.sort(key=lambda w: (round(float(w["top"]) / 3.0), float(w["x0"])))
                lines: List[Dict[str, Any]] = []
                for w in words:
                    x0, top, x1, bot = float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"])
                    sz = float(w.get("size", 10.0))
                    fn = str(w.get("fontname", ""))
                    if lines and abs(top - lines[-1]["top"]) <= max(3.5, 0.45 * sz) and abs(x0 - lines[-1]["x1"]) <= max(28.0, 2.8 * sz):
                        lines[-1]["words"].append(w["text"])
                        lines[-1]["x1"] = max(lines[-1]["x1"], x1)
                        lines[-1]["bot"] = max(lines[-1]["bot"], bot)
                    else:
                        lines.append({"words": [w["text"]], "x0": x0, "top": top, "x1": x1, "bot": bot, "sz": sz, "bold": "bold" in fn.lower()})
                blks = [{
                    "bbox": [round(l["x0"], 2), round(l["top"], 2), round(l["x1"], 2), round(l["bot"], 2)],
                    "text": " ".join(l["words"]),
                    "size": round(l["sz"], 2),
                    "bold": l["bold"],
                } for l in lines]
                pages.append({"page": p_num, "label": str(p_num), "width": float(pg.width), "height": float(pg.height), "blocks": blks})
        return "pdfplumber", False, pages
    except Exception:
        pass

    # Tier 3: Poppler pdftohtml -xml
    pdftohtml_bin = shutil.which("pdftohtml")
    if pdftohtml_bin:
        with tempfile.TemporaryDirectory(prefix="trans_poppler_") as tmp:
            xml_p = Path(tmp) / "doc.xml"
            subprocess.run([pdftohtml_bin, "-xml", "-i", "-nodrm", str(pdf_path), str(xml_p)], capture_output=True, timeout=90)
            if xml_p.exists():
                xml_txt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_p.read_text(encoding="utf-8", errors="replace"))
                root = ET.fromstring(xml_txt)
                fspecs: Dict[str, float] = {}
                pages = []
                for p_el in root.findall(".//page"):
                    p_num = int(p_el.attrib.get("number", len(pages) + 1))
                    if page_allowlist and p_num not in page_allowlist:
                        continue
                    for fs in p_el.findall("fontspec"):
                        fspecs[fs.attrib.get("id", "")] = float(fs.attrib.get("size", 10.0))
                    blks = []
                    for t_el in p_el.findall("text"):
                        txt = "".join(t_el.itertext()).strip()
                        if not txt:
                            continue
                        l, t = float(t_el.attrib.get("left", 0)), float(t_el.attrib.get("top", 0))
                        w, h = float(t_el.attrib.get("width", 40)), float(t_el.attrib.get("height", 12))
                        sz = fspecs.get(t_el.attrib.get("font", ""), h)
                        blks.append({"bbox": [l, t, l + w, t + h], "text": txt, "size": sz, "bold": t_el.find("b") is not None})
                    pages.append({"page": p_num, "label": str(p_num), "width": float(p_el.attrib.get("width", 612)), "height": float(p_el.attrib.get("height", 792)), "blocks": blks})
                return "poppler_xml", False, pages

    # Tier 4: Pure-Python stdlib PDF content stream parser
    raw_bytes = pdf_path.read_bytes()
    if re.search(rb"/Encrypt\b", raw_bytes):
        return "stdlib_pdf_stream", True, []
    objs = {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", raw_bytes, flags=re.DOTALL)}
    page_bodies = [b for _, b in sorted(objs.items()) if re.search(rb"/Type\s*/Page\b(?!s)", b.split(b"stream", 1)[0])]
    pages = []
    op_re = re.compile(
        rb"(?P<tm>([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm)"
        rb"|(?P<tf>/(\w+)\s+([-\d.]+)\s+Tf)"
        rb"|(?P<tj>\((?:\\.|[^\\)])*\)\s*Tj)",
        flags=re.DOTALL,
    )
    for idx, pb in enumerate(page_bodies):
        p_num = idx + 1
        if page_allowlist and p_num not in page_allowlist:
            continue
        mb = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", pb)
        w = float(mb.group(3)) - float(mb.group(1)) if mb else 612.0
        h = float(mb.group(4)) - float(mb.group(2)) if mb else 792.0
        c_one = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", pb)
        t_bodies = [objs[int(c_one.group(1))]] if (c_one and int(c_one.group(1)) in objs) else [pb]
        streams = []
        for tb in t_bodies:
            for sm in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", tb, flags=re.DOTALL):
                chunk = sm.group(1)
                if b"/FlateDecode" in tb.split(b"stream", 1)[0]:
                    try:
                        chunk = zlib.decompress(chunk)
                    except Exception:
                        pass
                streams.append(chunk)
        full_s = b"\n".join(streams)
        lines_raw = []
        for bm in re.finditer(rb"BT\b(.*?)ET\b", full_s, flags=re.DOTALL):
            cx, cy, cf, csz = 54.0, h - 54.0, "F1", 10.0
            for m in op_re.finditer(bm.group(1)):
                if m.group("tm"):
                    vs = m.group("tm").split()
                    cx, cy = float(vs[4]), float(vs[5])
                elif m.group("tf"):
                    mt = re.match(rb"/(\w+)\s+([-\d.]+)\s+Tf", m.group("tf"))
                    if mt:
                        cf, csz = mt.group(1).decode("latin-1"), abs(float(mt.group(2)))
                elif m.group("tj"):
                    lit = re.search(rb"\((.*)\)\s*Tj", m.group("tj"), flags=re.DOTALL)
                    if lit:
                        txt = _decode_pdf_literal(lit.group(1)).strip()
                        if txt:
                            top_y = max(0.0, h - cy - csz)
                            ew = max(12.0, len(txt) * csz * 0.52)
                            lines_raw.append({
                                "x0": cx, "y0": top_y, "x1": min(w, cx + ew), "y1": top_y + csz * 1.15,
                                "text": txt, "size": csz, "bold": cf in {"F2", "FB"} or "bold" in cf.lower(),
                            })
        # Merge adjacent lines in same column
        lines_raw.sort(key=lambda r: (round(r["y0"], 1), r["x0"]))
        used = [False] * len(lines_raw)
        blks = []
        for i, ln in enumerate(lines_raw):
            if used[i]:
                continue
            used[i] = True
            grp = [ln]
            cur = ln
            for j in range(i + 1, len(lines_raw)):
                if used[j]:
                    continue
                cand = lines_raw[j]
                vg = cand["y0"] - cur["y1"]
                if vg > max(7.0, 0.85 * cur["size"]):
                    if vg > 2.2 * cur["size"]:
                        break
                    continue
                if abs(cur["x0"] - cand["x0"]) <= 22.0 and abs(cur["size"] - cand["size"]) <= 0.8 and cur["bold"] == cand["bold"] and vg >= -3.0:
                    grp.append(cand)
                    used[j] = True
                    cur = cand
            txt_j = "\n".join(g["text"] for g in grp) if sum(1 for g in grp if "|" in g["text"]) >= 2 else join_wrapped_lines([g["text"] for g in grp])
            blks.append({
                "bbox": [round(min(g["x0"] for g in grp), 2), round(min(g["y0"] for g in grp), 2), round(max(g["x1"] for g in grp), 2), round(max(g["y1"] for g in grp), 2)],
                "text": txt_j,
                "size": round(statistics.median(g["size"] for g in grp), 2),
                "bold": any(g["bold"] for g in grp),
            })
        pages.append({"page": p_num, "label": str(p_num), "width": w, "height": h, "blocks": blks})
    return "stdlib_pdf_stream", False, pages


def order_page_xycut(blocks: List[Dict[str, Any]], width: float, height: float, repeated_sigs: set[str]) -> List[Tuple[int, Dict[str, Any]]]:
    kept = []
    for b in blocks:
        txt = clean_text(b["text"])
        if not txt:
            continue
        sig = re.sub(r"\d+", "#", txt.lower())
        in_margin = b["bbox"][3] <= 0.088 * height or b["bbox"][1] >= 0.912 * height
        is_pagenum = bool(re.fullmatch(r"(?:page\s*)?\d{1,5}", txt, re.I))
        if in_margin and (sig in repeated_sigs or is_pagenum or ("vol." in txt.lower() and b["bbox"][3] <= 0.068 * height)):
            continue
        b_copy = dict(b)
        b_copy["text"] = txt
        kept.append(b_copy)

    if not kept:
        return []
    min_x = min(b["bbox"][0] for b in kept)
    max_x = max(b["bbox"][2] for b in kept)
    cw = max(120.0, max_x - min_x)
    narrow = [b for b in kept if (b["bbox"][2] - b["bbox"][0]) <= 0.56 * cw]

    gutter: Optional[float] = None
    if len(narrow) >= 2:
        gs, ge = int(min_x + 0.28 * cw), int(min_x + 0.72 * cw)
        if ge > gs + 8:
            occ = [0] * (ge - gs)
            mid = min_x + 0.5 * cw
            lh = any(b["bbox"][2] <= mid + 12 for b in narrow)
            rh = any(b["bbox"][0] >= mid - 12 for b in narrow)
            for b in narrow:
                for x in range(max(gs, int(math.floor(b["bbox"][0]))), min(ge, int(math.ceil(b["bbox"][2])))):
                    occ[x - gs] += 1
            if lh and rh and min(occ) == 0:
                zeros = [gs + i for i, c in enumerate(occ) if c == 0]
                gutter = float(statistics.median(zeros))

    if gutter is None:
        kept.sort(key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
        return [(1, b) for b in kept]

    y_sorted = sorted(kept, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    out: List[Tuple[int, Dict[str, Any]]] = []
    buf: List[Dict[str, Any]] = []

    def flush() -> None:
        if not buf:
            return
        left = sorted([b for b in buf if 0.5 * (b["bbox"][0] + b["bbox"][2]) <= gutter], key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
        right = sorted([b for b in buf if 0.5 * (b["bbox"][0] + b["bbox"][2]) > gutter], key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
        out.extend((1, b) for b in left)
        out.extend((2, b) for b in right)
        buf.clear()

    for b in y_sorted:
        if (b["bbox"][0] < gutter - 18 and b["bbox"][2] > gutter + 18) or (b["bbox"][2] - b["bbox"][0]) > 0.64 * cw:
            flush()
            out.append((0, b))
        else:
            buf.append(b)
    flush()
    return out


# ---------------------------------------------------------------------------
# Protected Spans & Candidate Terminology Extraction
# ---------------------------------------------------------------------------

def extract_protected_tokens(text: str) -> Dict[str, List[str]]:
    pats = FIDELITY_RULES.get("protected_patterns", {})
    found: Dict[str, List[str]] = {}
    for key, pat in pats.items():
        hits = [m.group(0).strip() for m in re.finditer(pat, text)]
        if hits:
            found[key] = list(dict.fromkeys(hits))
    return found


def extract_term_candidates(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    full_text = "\n".join(s["source_text"] for s in segments if s["role"] != "reference")
    low_full = full_text.lower()

    # 1. Explicit abbreviation definitions: "Density Functional Theory (DFT)"
    abbr_defs: List[Dict[str, str]] = []
    for m in re.finditer(r"\b([A-Za-z][A-Za-z\-]+(?:\s+[A-Za-z][A-Za-z\-]+){1,5})\s*\(([A-Z]{2,8})\)", full_text):
        phrase, abbr = m.group(1).strip(), m.group(2).strip()
        words = [w for w in phrase.split() if w.lower() not in {"of", "in", "for", "and", "the", "with", "by"}]
        if len(words) >= len(abbr) - 1 and phrase[0].lower() == abbr[0].lower():
            abbr_defs.append({"full_term": phrase, "abbreviation": abbr})

    # 2. Seed glossary matches
    seed_hits: List[Dict[str, Any]] = []
    for entry in SEED_GLOSSARY.get("entries", []):
        st = entry["source_term"]
        rx = re.compile(rf"\b{re.escape(st)}s?\b", re.I)
        matched_segs = [s["seg_id"] for s in segments if rx.search(s["source_text"])]
        if matched_segs:
            seed_hits.append({
                "source_term": st,
                "target_term": entry["target_term"],
                "domain": entry.get("domain", "general"),
                "rationale": entry.get("rationale", ""),
                "occurrence_segments": matched_segs,
            })

    # 3. Frequent technical bigrams/trigrams in paper (>=2 occurrences across segments)
    ngram_segs: Dict[str, List[str]] = collections.defaultdict(list)
    stop = {"the", "and", "for", "with", "that", "this", "from", "were", "was", "are", "have", "has", "been", "into", "under", "over", "between", "within", "only", "when", "than", "more", "most", "such", "each", "both", "also", "table", "figure", "section"}
    for s in segments:
        if s["role"] == "reference":
            continue
        words = re.findall(r"\b[A-Za-z][a-z\-]{2,}\b", s["source_text"].lower())
        seen_in_seg: set[str] = set()
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                gram = words[i : i + n]
                if gram[0] in stop or gram[-1] in stop or any(len(w) < 3 for w in gram):
                    continue
                phrase = " ".join(gram)
                if phrase not in seen_in_seg:
                    seen_in_seg.add(phrase)
                    ngram_segs[phrase].append(s["seg_id"])

    frequent_phrases = [
        {"phrase": ph, "count": len(sids), "occurrence_segments": sids}
        for ph, sids in sorted(ngram_segs.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        if len(sids) >= 2 and not any(ph == sh["source_term"].lower() for sh in seed_hits)
    ][:30]

    # 4. Polysemous / false-friend alerts present in document
    polysemy_alerts = []
    for ff in SEED_GLOSSARY.get("false_friends_notes", []):
        w = ff["word"]
        rx = re.compile(rf"\b{re.escape(w)}(?:s|es|ed|ing)?\b", re.I)
        sids = [s["seg_id"] for s in segments if rx.search(s["source_text"])]
        if sids:
            polysemy_alerts.append({
                "word": w,
                "academic_meaning_guide": ff["academic_meaning"],
                "pitfall_to_avoid": ff["pitfall"],
                "occurrence_segments": sids,
            })

    return {
        "seed_glossary_matches": seed_hits,
        "paper_abbreviation_definitions": abbr_defs,
        "frequent_technical_phrases": frequent_phrases,
        "polysemy_and_false_friend_alerts": polysemy_alerts,
    }


def segment_source(source_path: Path, out_dir: Path, page_allowlist: Optional[set[int]] = None, batch_chars: int = 1800) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = source_path.suffix.lower()

    raw_units: List[Dict[str, Any]] = []
    backend_used = "plain_text"
    is_encrypted = False
    untranslatable_notices: List[Dict[str, Any]] = []

    if ext == ".pdf":
        backend_used, is_encrypted, pages = extract_pdf_pages(source_path, page_allowlist)
        if is_encrypted:
            payload = {
                "source_file": str(source_path),
                "backend_used": backend_used,
                "is_encrypted": True,
                "status": "unverifiable",
                "segments": [],
                "untranslatable_notices": [{"page": 1, "reason": "Encrypted PDF locked by password"}],
            }
            (out_dir / "source_segments.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return payload

        sig_counts: collections.Counter[str] = collections.Counter()
        for p in pages:
            h = max(1.0, p["height"])
            for b in p["blocks"]:
                if b["bbox"][3] <= 0.09 * h or b["bbox"][1] >= 0.91 * h:
                    sig_counts[re.sub(r"\d+", "#", b["text"].strip().lower())] += 1
        repeated_sigs = {k for k, c in sig_counts.items() if c >= 2 and len(pages) >= 2}

        all_sizes = [b["size"] for p in pages for b in p["blocks"] if len(b["text"]) >= 30]
        modal_sz = statistics.median(all_sizes) if all_sizes else 10.0

        sec_path = "Front Matter"
        title_done = False
        for p in pages:
            ordered = order_page_xycut(p["blocks"], p["width"], p["height"], repeated_sigs)
            if not ordered:
                untranslatable_notices.append({"page": p["page"], "reason": "Page contains no extractable text layer (scanned or image-only)."})
                continue
            for idx, (col, b) in enumerate(ordered, start=1):
                txt = b["text"]
                role = "paragraph"
                if p["page"] == 1 and not title_done and idx <= 2 and b["size"] >= modal_sz * 1.2:
                    role = "title"
                    sec_path = "Title"
                    title_done = True
                elif re.match(r"^(?:Abstract|摘要)\b[:：—\-]?\s*$", txt, re.I):
                    role = "heading"
                    sec_path = "Abstract"
                elif re.match(r"^(?:References|Bibliography|参考文献)\b", txt, re.I) and len(txt) <= 40:
                    role = "heading"
                    sec_path = "References"
                elif re.match(r"^(?:\d+(?:\.\d+){0,2}|[IVX]+\.)\s+[A-Z\u4e00-\u9fff]", txt) and "\n" not in txt and len(txt) <= 110 and (b["bold"] or b["size"] >= modal_sz * 1.03):
                    role = "heading"
                    sec_path = txt
                elif re.match(r"^(?:Fig\.?|Figure|图)\s*[S]?\d+", txt, re.I):
                    role = "figure_caption"
                elif re.match(r"^(?:Table|Tab\.?|表|续表)\s*[S]?\d+", txt, re.I):
                    role = "table_caption"
                elif "|" in txt and len(txt.splitlines()) >= 2:
                    role = "table_content"
                elif sec_path == "References":
                    role = "reference"
                elif re.search(r"\((?:Eq\.?\s*)?\d+[a-z]?\)\s*$", txt) and any(op in txt for op in ("=", "≤", "≥", "≈", "+", "-")):
                    role = "equation"
                elif b["bbox"][1] >= 0.84 * p["height"] and b["size"] <= 0.93 * modal_sz and re.match(r"^[*†‡§\d]", txt):
                    role = "footnote"

                raw_units.append({
                    "page": p["page"],
                    "printed_page_label": p["label"],
                    "column": col,
                    "section_path": sec_path,
                    "role": role,
                    "block_id": f"p{p['page']:03d}_b{idx:03d}",
                    "bbox": b["bbox"],
                    "text": txt,
                })
    elif source_path.suffix.lower() == ".json":
        # Structured JSON / JATS material (preserve real paragraph_id, never fabricate PDF page 1)
        backend_used = "structured_json"
        data = json.loads(source_path.read_text(encoding="utf-8", errors="replace"))
        rows: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            if isinstance(data.get("paragraphs"), list):
                rows = data["paragraphs"]
            elif isinstance(data.get("passages"), list):
                rows = data["passages"]
            elif isinstance(data.get("sections"), list):
                for sec in data["sections"]:
                    if isinstance(sec, dict):
                        stitle = str(sec.get("title") or sec.get("heading") or "Section")
                        if sec.get("text"):
                            rows.append({"section": stitle, "text": sec["text"], "paragraph_id": sec.get("id")})
                        for p_item in sec.get("paragraphs", []):
                            if isinstance(p_item, dict):
                                rows.append({**p_item, "section": p_item.get("section") or stitle})
                            elif isinstance(p_item, str):
                                rows.append({"section": stitle, "text": p_item})
            elif isinstance(data.get("text"), str):
                rows = [{"section": "Document", "text": c.strip()} for c in re.split(r"\n\s*\n", data["text"]) if c.strip()]
        elif isinstance(data, list):
            rows = [r if isinstance(r, dict) else {"text": str(r)} for r in data]

        for idx, row in enumerate(rows, start=1):
            txt = clean_text(str(row.get("text") or row.get("content") or row.get("body") or ""))
            if not txt:
                continue
            pid = str(row.get("paragraph_id") or row.get("id") or f"p_{idx:04d}")
            sec = str(row.get("section") or row.get("section_path") or row.get("heading") or "Document")
            role = str(row.get("role") or ("caption" if re.match(r"^(?:Table|Fig\.?|Figure|表|图)\s*\d+", txt, re.I) else "paragraph"))
            raw_units.append({
                "page": row.get("page"),  # None when JATS/JSON has no PDF page!
                "printed_page_label": str(row["page"]) if row.get("page") is not None else pid,
                "column": 1,
                "section_path": sec,
                "role": role,
                "block_id": pid,
                "bbox": row.get("bbox"),
                "text": txt,
            })
    elif source_path.suffix.lower() == ".docx":
        # Native DOCX extraction via stdlib zipfile + XML
        backend_used = "stdlib_docx"
        try:
            with zipfile.ZipFile(source_path, "r") as zf:
                root = ET.fromstring(zf.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            sec_path = "Document"
            idx = 0
            for p_el in root.findall(".//w:p", ns):
                txt = clean_text("".join(t.text for t in p_el.findall(".//w:t", ns) if t.text))
                if not txt:
                    continue
                idx += 1
                role = "caption" if re.match(r"^(?:Table|Fig\.?|Figure|表|图)\s*\d+", txt, re.I) else "paragraph"
                raw_units.append({
                    "page": None,
                    "printed_page_label": f"para_{idx:04d}",
                    "column": 1,
                    "section_path": sec_path,
                    "role": role,
                    "block_id": f"docx_p{idx:04d}",
                    "bbox": None,
                    "text": txt,
                })
        except Exception as exc:
            untranslatable_notices.append({"page": None, "reason": f"Failed to parse DOCX: {exc}"})
    elif source_path.suffix.lower() == ".xml":
        # JATS / scholarly XML paragraph extraction preserving real @id
        backend_used = "stdlib_jats_xml"
        try:
            root = ET.fromstring(source_path.read_text(encoding="utf-8", errors="replace"))
            idx = 0
            for el in root.iter():
                tag = el.tag.split("}")[-1].lower()
                if tag in {"article-title", "title", "p"}:
                    txt = clean_text("".join(el.itertext()))
                    if not txt:
                        continue
                    idx += 1
                    pid = el.attrib.get("id") or f"xml_p{idx:04d}"
                    role = "title" if tag == "article-title" else ("heading" if tag == "title" else "paragraph")
                    raw_units.append({
                        "page": None,
                        "printed_page_label": pid,
                        "column": 1,
                        "section_path": txt[:80] if role in {"title", "heading"} else "Body",
                        "role": role,
                        "block_id": pid,
                        "bbox": None,
                        "text": txt,
                    })
        except Exception as exc:
            untranslatable_notices.append({"page": None, "reason": f"Failed to parse XML: {exc}"})
    else:
        # Markdown / LaTeX / Plain text segmentation
        raw_text = source_path.read_text(encoding="utf-8", errors="replace")
        sec_path = "Document"
        chunks = [c.strip() for c in re.split(r"\n\s*\n", raw_text) if c.strip()]
        for idx, ch in enumerate(chunks, start=1):
            role = "paragraph"
            if ch.startswith("#"):
                role = "heading"
                sec_path = ch.lstrip("#").strip()
                if idx == 1:
                    role = "title"
            elif re.match(r"^(?:Table|Fig\.?|Figure|表|图)\s*\d+", ch, re.I):
                role = "caption"
            raw_units.append({
                "page": 1,
                "printed_page_label": "1",
                "column": 1,
                "section_path": sec_path,
                "role": role,
                "block_id": f"txt_b{idx:03d}",
                "bbox": [0.0, 0.0, 0.0, 0.0],
                "text": clean_text(ch),
            })

    # Stitch cross-column / cross-page broken paragraphs
    stitched: List[Dict[str, Any]] = []
    for u in raw_units:
        if (
            stitched
            and stitched[-1]["role"] == "paragraph"
            and u["role"] == "paragraph"
            and stitched[-1]["section_path"] == u["section_path"]
            and (u["page"] > stitched[-1]["page"] or u["column"] > stitched[-1]["column"])
            and not re.search(r"[.!?。！？:：;；]$", stitched[-1]["text"].strip())
            and bool(re.match(r"^[a-z(,]", u["text"].strip()))
        ):
            stitched[-1]["text"] = join_wrapped_lines([stitched[-1]["text"], u["text"]])
            stitched[-1]["page_end"] = u["page"]
        else:
            u["page_end"] = u["page"]
            stitched.append(u)

    segments: List[Dict[str, Any]] = []
    for i, u in enumerate(stitched, start=1):
        seg_id = f"S{i:04d}"
        sents = split_academic_sentences(u["text"])
        garbled = bool(re.search(r"\(cid:\d+\)|\ufffd", u["text"]))
        # Bibliography references stay verbatim by academic convention unless explicitly requested
        translatable = u["role"] != "reference" and not garbled
        segments.append({
            "seg_id": seg_id,
            "order": i,
            "role": u["role"],
            "translatable": translatable,
            "untranslatable_reason": "Garbled CID font glyphs in source" if garbled else ("Bibliography entry kept in original citation form" if u["role"] == "reference" else None),
            "page": u["page"],
            "page_end": u["page_end"],
            "printed_page_label": u["printed_page_label"],
            "column": u["column"],
            "section_path": u["section_path"],
            "block_id": u["block_id"],
            "bbox": u["bbox"],
            "source_text": u["text"],
            "source_sentences": [{"sent_id": f"{seg_id}.{k}", "text": st} for k, st in enumerate(sents, start=1)],
            "protected_tokens": extract_protected_tokens(u["text"]),
        })

    candidates = extract_term_candidates(segments)

    # Build context-carrying translation batches
    batches: List[Dict[str, Any]] = []
    cur_batch: List[Dict[str, Any]] = []
    cur_len = 0
    prev_tail = ""
    for seg in segments:
        if not seg["translatable"]:
            continue
        s_len = len(seg["source_text"])
        if cur_batch and cur_len + s_len > batch_chars:
            batches.append({
                "batch_id": f"B{len(batches) + 1:03d}",
                "prev_context_tail": prev_tail,
                "segment_ids": [x["seg_id"] for x in cur_batch],
                "segments": cur_batch,
            })
            prev_tail = cur_batch[-1]["source_text"][-240:]
            cur_batch = []
            cur_len = 0
        cur_batch.append({
            "seg_id": seg["seg_id"],
            "role": seg["role"],
            "page": seg["page"],
            "section_path": seg["section_path"],
            "source_text": seg["source_text"],
            "protected_tokens": seg["protected_tokens"],
        })
        cur_len += s_len
    if cur_batch:
        batches.append({
            "batch_id": f"B{len(batches) + 1:03d}",
            "prev_context_tail": prev_tail,
            "segment_ids": [x["seg_id"] for x in cur_batch],
            "segments": cur_batch,
        })

    seg_payload = {
        "source_file": str(source_path),
        "backend_used": backend_used,
        "is_encrypted": False,
        "status": "success" if not untranslatable_notices else "partial",
        "total_segments": len(segments),
        "translatable_segments": sum(1 for s in segments if s["translatable"]),
        "untranslatable_notices": untranslatable_notices,
        "segments": segments,
    }
    (out_dir / "source_segments.json").write_text(json.dumps(seg_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "term_candidates.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "translation_batches.json").write_text(json.dumps({"batch_count": len(batches), "batches": batches}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return seg_payload


def parse_page_range(spec: Optional[str]) -> Optional[set[int]]:
    if not spec:
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            pages.add(int(part))
    return pages or None


def main() -> int:
    parser = argparse.ArgumentParser(description="Segment scientific document and extract candidate terminology + protected tokens.")
    parser.add_argument("--source", required=True, help="Path to source PDF/MD/TXT/TEX file.")
    parser.add_argument("--out-dir", required=True, help="Output directory for segmented artifacts.")
    parser.add_argument("--pages", default=None, help="Optional page range, e.g. '1-3,5'.")
    parser.add_argument("--batch-chars", type=int, default=1800)
    args = parser.parse_args()

    res = segment_source(Path(args.source), Path(args.out_dir), parse_page_range(args.pages), args.batch_chars)
    print(json.dumps({
        "status": res["status"],
        "backend_used": res["backend_used"],
        "total_segments": res["total_segments"],
        "translatable_segments": res.get("translatable_segments", 0),
        "out_dir": str(args.out_dir),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
