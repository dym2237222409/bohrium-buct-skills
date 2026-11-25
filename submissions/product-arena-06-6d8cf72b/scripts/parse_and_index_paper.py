#!/usr/bin/env python3
"""
parse_and_index_paper.py — Section-Skeleton & Fine-Grained Anchor Indexer

Parses scientific PDFs (4-tier XY-Cut multi-column + cross-page stitch + header/footer
isolation) or Markdown/LaTeX documents into:
  1. section_skeleton.json — Exact section hierarchy (SEC01..SECNN) in the paper's true
     argument order, with paragraph/table/figure/equation anchor ranges and condition/
     limitation cue highlights.
  2. anchor_registry.json — Dereferenceable fine-grained anchors:
       - Paragraphs: P0001..PNNNN
       - Tables:     TAB01..TABNN (with extracted Markdown grid when available)
       - Figures:    FIG01..FIGNN
       - Equations:  EQ01..EQNN
     Each anchor stores (source_file, page, printed_page_label, column, section_id,
     section_path, bbox, verbatim_text).
  3. abbreviation_and_symbol_candidates.json — Extracted 'Full Term (ABBR)' pairs and
     equation variables to drive synonym/abbreviation deduplication in knowledge items.
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
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
RULES = json.loads((SCRIPT_DIR.parent / "resources" / "anchor_and_heading_rules.json").read_text(encoding="utf-8"))

LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\u00ad": "", "\u200b": "", "\u00a0": " ", "\u2212": "-",
}


def clean_str(s: str) -> str:
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def join_lines_smart(lines: List[str]) -> str:
    if not lines:
        return ""
    out = lines[0]
    for nxt in lines[1:]:
        if not nxt:
            continue
        if out.endswith("-") and len(out) >= 3 and out[-2].islower() and nxt[0].islower() and not out.endswith("--"):
            out = out[:-1] + nxt
        elif re.search(r"[\u4e00-\u9fff]$", out) and re.match(r"^[\u4e00-\u9fff]", nxt):
            out += nxt
        else:
            out += " " + nxt
    return out.strip()


def _decode_pdf_str(raw: bytes) -> str:
    out = bytearray()
    i, n = 0, len(raw)
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("("): 40, ord(")"): 41, ord("\\"): 92}
    while i < n:
        c = raw[i]
        if c == 92 and i + 1 < n:
            nxt = raw[i + 1]
            out.append(esc.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("latin-1", errors="replace")


def extract_pdf_multi_tier(pdf_path: Path) -> Tuple[str, bool, List[Dict[str, Any]]]:
    # Tier 1: PyMuPDF
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf_path))
        if doc.is_encrypted and not doc.authenticate(""):
            return "pymupdf", True, []
        pages = []
        for idx in range(len(doc)):
            pg = doc[idx]
            p_num = idx + 1
            w, h = float(pg.rect.width), float(pg.rect.height)
            lbl = pg.get_label() or str(p_num)
            blks = []
            for b in pg.get_text("dict").get("blocks", []):
                if b.get("type", 0) != 0:
                    continue
                lns, szs = [], []
                bc, tc = 0, 0
                for ln in b.get("lines", []):
                    stxts = []
                    for sp in ln.get("spans", []):
                        t = sp.get("text", "")
                        if t.strip():
                            szs.append(float(sp.get("size", 10.0)))
                            tc += len(t.strip())
                            if (int(sp.get("flags", 0)) & 16) or "bold" in str(sp.get("font", "")).lower():
                                bc += len(t.strip())
                        stxts.append(t)
                    sl = "".join(stxts).strip()
                    if sl:
                        lns.append(sl)
                if lns:
                    txt_j = "\n".join(lns) if sum(1 for x in lns if "|" in x) >= 2 else join_lines_smart(lns)
                    blks.append({
                        "bbox": [round(float(v), 2) for v in b.get("bbox", (0, 0, 0, 0))],
                        "text": txt_j,
                        "size": round(statistics.median(szs), 2) if szs else 10.0,
                        "bold": bc > 0.5 * max(1, tc),
                    })
            native_tabs = []
            if hasattr(pg, "find_tables"):
                try:
                    for tb in pg.find_tables().tables:
                        g = tb.extract()
                        if g and len(g) >= 2:
                            native_tabs.append({"bbox": [round(float(v), 2) for v in tb.bbox], "grid": [[(c or "").strip() for c in r] for r in g]})
                except Exception:
                    pass
            pages.append({"page": p_num, "label": lbl, "width": w, "height": h, "blocks": blks, "native_tables": native_tabs})
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
                words = pg.extract_words(extra_attrs=["fontname", "size"]) or []
                words.sort(key=lambda w: (round(float(w["top"]) / 3.0), float(w["x0"])))
                lns: List[Dict[str, Any]] = []
                for w in words:
                    x0, top, x1, bot = float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"])
                    sz = float(w.get("size", 10.0))
                    fn = str(w.get("fontname", ""))
                    if lns and abs(top - lns[-1]["top"]) <= max(3.5, 0.45 * sz) and abs(x0 - lns[-1]["x1"]) <= max(28.0, 2.8 * sz):
                        lns[-1]["words"].append(w["text"])
                        lns[-1]["x1"] = max(lns[-1]["x1"], x1)
                        lns[-1]["bot"] = max(lns[-1]["bot"], bot)
                    else:
                        lns.append({"words": [w["text"]], "x0": x0, "top": top, "x1": x1, "bot": bot, "sz": sz, "bold": "bold" in fn.lower()})
                blks = [{"bbox": [round(l["x0"], 2), round(l["top"], 2), round(l["x1"], 2), round(l["bot"], 2)], "text": " ".join(l["words"]), "size": round(l["sz"], 2), "bold": l["bold"]} for l in lns]
                ntabs = []
                try:
                    for tb in pg.find_tables():
                        g = tb.extract()
                        if g and len(g) >= 2:
                            ntabs.append({"bbox": [round(float(v), 2) for v in tb.bbox], "grid": [[(c or "").strip() for c in r] for r in g]})
                except Exception:
                    pass
                pages.append({"page": p_num, "label": str(p_num), "width": float(pg.width), "height": float(pg.height), "blocks": blks, "native_tables": ntabs})
        return "pdfplumber", False, pages
    except Exception:
        pass

    # Tier 3: Poppler pdftohtml -xml
    p2h = shutil.which("pdftohtml")
    if p2h:
        with tempfile.TemporaryDirectory(prefix="notes_pop_") as tmp:
            xp = Path(tmp) / "d.xml"
            subprocess.run([p2h, "-xml", "-i", "-nodrm", str(pdf_path), str(xp)], capture_output=True, timeout=90)
            if xp.exists():
                xt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xp.read_text(encoding="utf-8", errors="replace"))
                root = ET.fromstring(xt)
                fspecs: Dict[str, float] = {}
                pages = []
                for pel in root.findall(".//page"):
                    pn = int(pel.attrib.get("number", len(pages) + 1))
                    for fs in pel.findall("fontspec"):
                        fspecs[fs.attrib.get("id", "")] = float(fs.attrib.get("size", 10.0))
                    blks = []
                    for tel in pel.findall("text"):
                        t = "".join(tel.itertext()).strip()
                        if not t:
                            continue
                        l, top = float(tel.attrib.get("left", 0)), float(tel.attrib.get("top", 0))
                        w, h = float(tel.attrib.get("width", 40)), float(tel.attrib.get("height", 12))
                        blks.append({"bbox": [l, top, l + w, top + h], "text": t, "size": fspecs.get(tel.attrib.get("font", ""), h), "bold": tel.find("b") is not None})
                    pages.append({"page": pn, "label": str(pn), "width": float(pel.attrib.get("width", 612)), "height": float(pel.attrib.get("height", 792)), "blocks": blks, "native_tables": []})
                return "poppler_xml", False, pages

    # Tier 4: Pure-Python stdlib PDF content stream parser
    data = pdf_path.read_bytes()
    if re.search(rb"/Encrypt\b", data):
        return "stdlib_pdf_stream", True, []
    objs = {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", data, flags=re.DOTALL)}
    p_bodies = [b for _, b in sorted(objs.items()) if re.search(rb"/Type\s*/Page\b(?!s)", b.split(b"stream", 1)[0])]
    op_re = re.compile(
        rb"(?P<tm>([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm)"
        rb"|(?P<tf>/(\w+)\s+([-\d.]+)\s+Tf)"
        rb"|(?P<tj>\((?:\\.|[^\\)])*\)\s*Tj)",
        flags=re.DOTALL,
    )
    pages = []
    for idx, pb in enumerate(p_bodies):
        pn = idx + 1
        mb = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", pb)
        w = float(mb.group(3)) - float(mb.group(1)) if mb else 612.0
        h = float(mb.group(4)) - float(mb.group(2)) if mb else 792.0
        co = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", pb)
        tbs = [objs[int(co.group(1))]] if (co and int(co.group(1)) in objs) else [pb]
        streams = []
        for tb in tbs:
            for sm in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", tb, flags=re.DOTALL):
                ch = sm.group(1)
                if b"/FlateDecode" in tb.split(b"stream", 1)[0]:
                    try:
                        ch = zlib.decompress(ch)
                    except Exception:
                        pass
                streams.append(ch)
        raw_lns = []
        for bm in re.finditer(rb"BT\b(.*?)ET\b", b"\n".join(streams), flags=re.DOTALL):
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
                        t = _decode_pdf_str(lit.group(1)).strip()
                        if t:
                            ty = max(0.0, h - cy - csz)
                            ew = max(12.0, len(t) * csz * 0.52)
                            raw_lns.append({"x0": cx, "y0": ty, "x1": min(w, cx + ew), "y1": ty + csz * 1.15, "text": t, "size": csz, "bold": cf in {"F2", "FB"} or "bold" in cf.lower()})
        raw_lns.sort(key=lambda r: (round(r["y0"], 1), r["x0"]))
        used = [False] * len(raw_lns)
        blks = []
        for i, ln in enumerate(raw_lns):
            if used[i]:
                continue
            used[i] = True
            grp = [ln]
            cur = ln
            for j in range(i + 1, len(raw_lns)):
                if used[j]:
                    continue
                cand = raw_lns[j]
                vg = cand["y0"] - cur["y1"]
                if vg > max(7.0, 0.85 * cur["size"]):
                    if vg > 2.2 * cur["size"]:
                        break
                    continue
                if abs(cur["x0"] - cand["x0"]) <= 22.0 and abs(cur["size"] - cand["size"]) <= 0.8 and cur["bold"] == cand["bold"] and vg >= -3.0:
                    grp.append(cand)
                    used[j] = True
                    cur = cand
            tj = "\n".join(g["text"] for g in grp) if sum(1 for g in grp if "|" in g["text"]) >= 2 else join_lines_smart([g["text"] for g in grp])
            blks.append({
                "bbox": [round(min(g["x0"] for g in grp), 2), round(min(g["y0"] for g in grp), 2), round(max(g["x1"] for g in grp), 2), round(max(g["y1"] for g in grp), 2)],
                "text": tj,
                "size": round(statistics.median(g["size"] for g in grp), 2),
                "bold": any(g["bold"] for g in grp),
            })
        pages.append({"page": pn, "label": str(pn), "width": w, "height": h, "blocks": blks, "native_tables": []})
    return "stdlib_pdf_stream", False, pages


def order_xycut(blocks: List[Dict[str, Any]], width: float, height: float, rep_sigs: set[str], page_obj: Dict[str, Any]) -> List[Tuple[int, Dict[str, Any]]]:
    kept = []
    for b in blocks:
        txt = clean_str(b["text"])
        if not txt:
            continue
        sig = re.sub(r"\d+", "#", txt.lower())
        in_m = b["bbox"][3] <= 0.088 * height or b["bbox"][1] >= 0.912 * height
        is_pn = bool(re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]{1,5}", txt, re.I))
        if in_m and (sig in rep_sigs or is_pn or ("vol." in txt.lower() and b["bbox"][3] <= 0.068 * height)):
            if is_pn and page_obj["label"] == str(page_obj["page"]):
                page_obj["label"] = re.search(r"[ivxlcdm\d]+", txt, re.I).group(0)  # type: ignore[union-attr]
            continue
        bc = dict(b)
        bc["text"] = txt
        kept.append(bc)
    if not kept:
        return []
    min_x, max_x = min(b["bbox"][0] for b in kept), max(b["bbox"][2] for b in kept)
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
                gutter = float(statistics.median([gs + i for i, c in enumerate(occ) if c == 0]))
    if gutter is None:
        kept.sort(key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
        return [(1, b) for b in kept]
    ys = sorted(kept, key=lambda b: (b["bbox"][1], b["bbox"][0]))
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

    for b in ys:
        if (b["bbox"][0] < gutter - 18 and b["bbox"][2] > gutter + 18) or (b["bbox"][2] - b["bbox"][0]) > 0.64 * cw:
            flush()
            out.append((0, b))
        else:
            buf.append(b)
    flush()
    return out


def match_heading(txt: str, sz: float, bold: bool, modal_sz: float) -> Optional[Tuple[int, str]]:
    if "\n" in txt or len(txt) > 125 or len(txt) < 2:
        return None
    low = txt.lower().rstrip(":：")
    if low in set(RULES.get("canonical_headings", [])) and (bold or sz >= modal_sz * 0.98 or len(txt) <= 32):
        return (1, txt.rstrip(":："))
    for pat in RULES.get("numbered_heading_patterns", []):
        m = re.match(pat, txt)
        if m:
            num, title = m.group("num").strip(), m.group("title").strip()
            if len(title.split()) <= 14 and not re.match(r"^(?:samples?|cases?|days?|hours?|mg|ml|%)\b", title, re.I):
                if bold or sz >= modal_sz * 1.03 or "." in num or num in {"1", "2", "3", "4", "5", "6", "I", "II", "III", "IV", "V"}:
                    return (min(4, num.count(".") + 1), f"{num} {title}".strip())
    return None


def index_document(source_path: Path, out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = source_path.suffix.lower()

    anchors: Dict[str, Dict[str, Any]] = {}
    sections: List[Dict[str, Any]] = []
    scanned_or_missing_pages: List[int] = []
    backend = "plain_text"

    def ensure_section(heading: str, level: int, page: int) -> Dict[str, Any]:
        sec = {
            "section_id": f"SEC{len(sections) + 1:02d}",
            "level": level,
            "heading": heading,
            "page_start": page,
            "page_end": page,
            "paragraph_anchors": [],
            "table_anchors": [],
            "figure_anchors": [],
            "equation_anchors": [],
            "precondition_cue_anchors": [],
            "limitation_cue_anchors": [],
        }
        sections.append(sec)
        return sec

    p_cnt, tab_cnt, fig_cnt, eq_cnt = 0, 0, 0, 0
    pre_res = [re.compile(p, re.I) for p in RULES.get("precondition_and_scope_cues", [])]
    lim_res = [re.compile(p, re.I) for p in RULES.get("limitation_cues", [])]
    title_guess: Optional[str] = None

    if ext == ".pdf":
        backend, encrypted, pages = extract_pdf_multi_tier(source_path)
        if encrypted:
            payload = {"source_file": str(source_path), "backend_used": backend, "is_encrypted": True, "status": "unverifiable"}
            (out_dir / "section_skeleton.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
            (out_dir / "anchor_registry.json").write_text(json.dumps({"anchors": {}}, indent=2), encoding="utf-8")
            return payload

        sigs: collections.Counter[str] = collections.Counter()
        for p in pages:
            h = max(1.0, p["height"])
            for b in p["blocks"]:
                if b["bbox"][3] <= 0.09 * h or b["bbox"][1] >= 0.91 * h:
                    sigs[re.sub(r"\d+", "#", b["text"].strip().lower())] += 1
        rep_sigs = {k for k, c in sigs.items() if c >= 2 and len(pages) >= 2}
        all_sz = [b["size"] for p in pages for b in p["blocks"] if len(b["text"]) >= 30]
        modal_sz = statistics.median(all_sz) if all_sz else 10.0

        cur_sec = ensure_section("Front Matter", 1, 1)
        for p in pages:
            pn = p["page"]
            ordered = order_xycut(p["blocks"], p["width"], p["height"], rep_sigs, p)
            if not ordered and not p["native_tables"]:
                scanned_or_missing_pages.append(pn)
                continue

            # Register native geometric tables
            for nt in p.get("native_tables", []):
                tab_cnt += 1
                aid = f"TAB{tab_cnt:02d}"
                grid_txt = "\n".join("| " + " | ".join(r) + " |" for r in nt["grid"])
                anchors[aid] = {
                    "anchor_id": aid, "kind": "table", "source_file": str(source_path),
                    "page": pn, "printed_page_label": p["label"], "column": 0,
                    "section_id": cur_sec["section_id"], "section_path": cur_sec["heading"],
                    "bbox": nt["bbox"], "verbatim_text": grid_txt, "grid": nt["grid"],
                }
                cur_sec["table_anchors"].append(aid)

            for idx, (col, b) in enumerate(ordered, start=1):
                txt = b["text"]
                cur_sec["page_end"] = max(cur_sec["page_end"], pn)

                if pn == 1 and title_guess is None and idx <= 2 and b["size"] >= modal_sz * 1.2:
                    title_guess = txt
                    p_cnt += 1
                    aid = f"P{p_cnt:04d}"
                    anchors[aid] = {
                        "anchor_id": aid, "kind": "title", "source_file": str(source_path),
                        "page": pn, "printed_page_label": p["label"], "column": col,
                        "section_id": cur_sec["section_id"], "section_path": cur_sec["heading"],
                        "bbox": b["bbox"], "verbatim_text": txt,
                    }
                    cur_sec["paragraph_anchors"].append(aid)
                    continue

                hd = match_heading(txt, b["size"], b["bold"], modal_sz)
                if hd is not None:
                    lvl, h_title = hd
                    cur_sec = ensure_section(h_title, lvl, pn)
                    continue

                if re.match(r"^(?:Table|Tab\.?|表|续表)\s*[S]?\d+", txt, re.I):
                    # Attach caption or inline pipe grid to table anchor
                    lines = txt.splitlines()
                    pipe_rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines[1:] if "|" in ln]
                    tab_cnt += 1
                    aid = f"TAB{tab_cnt:02d}"
                    anchors[aid] = {
                        "anchor_id": aid, "kind": "table", "source_file": str(source_path),
                        "page": pn, "printed_page_label": p["label"], "column": col,
                        "section_id": cur_sec["section_id"], "section_path": cur_sec["heading"],
                        "bbox": b["bbox"], "verbatim_text": txt, "grid": pipe_rows,
                    }
                    cur_sec["table_anchors"].append(aid)
                    continue

                if "|" in txt and len(txt.splitlines()) >= 2:
                    pipe_rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in txt.splitlines() if "|" in ln and not re.fullmatch(r"[\s|:\-]+", ln)]
                    # Attach to preceding caption-only table on same page if empty grid
                    if cur_sec["table_anchors"] and not anchors[cur_sec["table_anchors"][-1]].get("grid"):
                        prev_tid = cur_sec["table_anchors"][-1]
                        anchors[prev_tid]["grid"] = pipe_rows
                        anchors[prev_tid]["verbatim_text"] += "\n" + txt
                    else:
                        tab_cnt += 1
                        aid = f"TAB{tab_cnt:02d}"
                        anchors[aid] = {
                            "anchor_id": aid, "kind": "table", "source_file": str(source_path),
                            "page": pn, "printed_page_label": p["label"], "column": col,
                            "section_id": cur_sec["section_id"], "section_path": cur_sec["heading"],
                            "bbox": b["bbox"], "verbatim_text": txt, "grid": pipe_rows,
                        }
                        cur_sec["table_anchors"].append(aid)
                    continue

                if re.match(r"^(?:Fig\.?|Figure|Scheme|图)\s*[S]?\d+", txt, re.I):
                    fig_cnt += 1
                    aid = f"FIG{fig_cnt:02d}"
                    anchors[aid] = {
                        "anchor_id": aid, "kind": "figure", "source_file": str(source_path),
                        "page": pn, "printed_page_label": p["label"], "column": col,
                        "section_id": cur_sec["section_id"], "section_path": cur_sec["heading"],
                        "bbox": b["bbox"], "verbatim_text": txt,
                    }
                    cur_sec["figure_anchors"].append(aid)
                    continue

                if re.search(r"(?:\(|\[)(?:Eq\.?\s*)?[A-Z]?\d+(?:\.\d+)?[a-z]?(?:\)|\])\s*$", txt) and len(txt) <= 240 and any(op in txt for op in ("=", "≈", "≤", "≥", "+", "-")):
                    eq_cnt += 1
                    aid = f"EQ{eq_cnt:02d}"
                    anchors[aid] = {
                        "anchor_id": aid, "kind": "equation", "source_file": str(source_path),
                        "page": pn, "printed_page_label": p["label"], "column": col,
                        "section_id": cur_sec["section_id"], "section_path": cur_sec["heading"],
                        "bbox": b["bbox"], "verbatim_text": txt,
                    }
                    cur_sec["equation_anchors"].append(aid)
                    continue

                p_cnt += 1
                aid = f"P{p_cnt:04d}"
                anchors[aid] = {
                    "anchor_id": aid, "kind": "paragraph", "source_file": str(source_path),
                    "page": pn, "printed_page_label": p["label"], "column": col,
                    "section_id": cur_sec["section_id"], "section_path": cur_sec["heading"],
                    "bbox": b["bbox"], "verbatim_text": txt,
                }
                cur_sec["paragraph_anchors"].append(aid)
                if any(rx.search(txt) for rx in pre_res):
                    cur_sec["precondition_cue_anchors"].append(aid)
                if any(rx.search(txt) for rx in lim_res):
                    cur_sec["limitation_cue_anchors"].append(aid)
    else:
        raw_t = source_path.read_text(encoding="utf-8", errors="replace")
        cur_sec = ensure_section("Front Matter", 1, 1)
        for idx, ch in enumerate([c.strip() for c in re.split(r"\n\s*\n", raw_t) if c.strip()], start=1):
            if ch.startswith("#"):
                lvl = len(ch) - len(ch.lstrip("#"))
                ht = ch.lstrip("#").strip()
                if idx == 1 and title_guess is None:
                    title_guess = ht
                cur_sec = ensure_section(ht, min(4, lvl), 1)
                continue
            p_cnt += 1
            aid = f"P{p_cnt:04d}"
            anchors[aid] = {
                "anchor_id": aid, "kind": "paragraph", "source_file": str(source_path),
                "page": 1, "printed_page_label": "1", "column": 1,
                "section_id": cur_sec["section_id"], "section_path": cur_sec["heading"],
                "bbox": [0.0, 0.0, 0.0, 0.0], "verbatim_text": clean_str(ch),
            }
            cur_sec["paragraph_anchors"].append(aid)

    # Prune empty Front Matter if no anchors landed in it
    sections = [
        s for s in sections
        if s["paragraph_anchors"] or s["table_anchors"] or s["figure_anchors"] or s["equation_anchors"]
    ]
    for i, s in enumerate(sections, start=1):
        old_id = s["section_id"]
        new_id = f"SEC{i:02d}"
        s["section_id"] = new_id
        for a in anchors.values():
            if a["section_id"] == old_id:
                a["section_id"] = new_id

    # Extract abbreviation & symbol candidates across all anchors (suffix initial-matching)
    full_blob = "\n".join(a["verbatim_text"] for a in anchors.values())
    abbr_pairs = []
    seen_abbr: set[str] = set()
    stop_linkers = {"of", "in", "for", "and", "the", "with", "on", "to", "by", "at"}
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9\-]*(?:\s+[A-Za-z][A-Za-z0-9\-]*){1,7})\s*\(([A-Z][A-Za-z0-9\-]{1,8})\)", full_blob):
        raw_words = m.group(1).strip().split()
        ab = m.group(2).strip()
        if ab in seen_abbr:
            continue
        # Walk backwards from the parenthesis to find the shortest trailing phrase matching acronym initials
        matched_phrase: Optional[str] = None
        alpha_ab = re.sub(r"[^A-Za-z]", "", ab).lower()
        if alpha_ab:
            for start_i in range(max(0, len(raw_words) - len(alpha_ab) - 3), len(raw_words)):
                sub = raw_words[start_i:]
                content_w = [w for w in sub if w.lower() not in stop_linkers]
                if not content_w or sub[0].lower() in stop_linkers:
                    continue
                initials = "".join(w[0].lower() for w in content_w)
                # Match exact initials or prefix/subsequence (e.g. "Turnover Frequency" -> "tf" in "tof" with internal 'o' in Turnover)
                letters_joined = "".join(w.lower() for w in content_w)
                if initials == alpha_ab or (
                    content_w[0][0].lower() == alpha_ab[0]
                    and len(content_w) <= len(alpha_ab) + 1
                    and all(ch in letters_joined for ch in alpha_ab)
                ):
                    matched_phrase = " ".join(sub)
            if matched_phrase:
                seen_abbr.add(ab)
                hit_aids = [aid for aid, a in anchors.items() if ab in a["verbatim_text"] or matched_phrase.lower() in a["verbatim_text"].lower()]
                abbr_pairs.append({"canonical_name": matched_phrase, "abbreviation": ab, "anchors": hit_aids[:10]})

    var_symbols = []
    seen_sym: set[str] = set()
    for aid, a in anchors.items():
        if a["kind"] == "equation":
            lhs = a["verbatim_text"].split("=", 1)[0].strip()
            if 1 <= len(lhs) <= 18 and lhs not in seen_sym:
                seen_sym.add(lhs)
                var_symbols.append({"symbol": lhs, "equation_anchor": aid, "expression": a["verbatim_text"]})

    skeleton = {
        "source_file": str(source_path),
        "backend_used": backend,
        "is_encrypted": False,
        "status": "success" if not scanned_or_missing_pages else "partial",
        "title_detected": title_guess,
        "section_count": len(sections),
        "anchor_counts": {
            "total": len(anchors),
            "paragraphs": p_cnt,
            "tables": tab_cnt,
            "figures": fig_cnt,
            "equations": eq_cnt,
        },
        "scanned_or_empty_pages": scanned_or_missing_pages,
        "sections": sections,
    }
    (out_dir / "section_skeleton.json").write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "anchor_registry.json").write_text(json.dumps({"source_file": str(source_path), "anchors": anchors}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "abbreviation_and_symbol_candidates.json").write_text(
        json.dumps({"abbreviations": abbr_pairs, "equation_symbols": var_symbols}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return skeleton


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse paper into section skeleton and fine-grained anchor registry.")
    ap.add_argument("--source", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    sk = index_document(Path(args.source), Path(args.out_dir))
    print(json.dumps({
        "status": sk["status"],
        "backend_used": sk["backend_used"],
        "title_detected": sk.get("title_detected"),
        "section_count": sk.get("section_count", 0),
        "anchor_counts": sk.get("anchor_counts", {}),
        "out_dir": str(args.out_dir),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
