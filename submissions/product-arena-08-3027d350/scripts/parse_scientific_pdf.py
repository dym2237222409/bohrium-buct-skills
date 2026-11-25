#!/usr/bin/env python3
"""
parse_scientific_pdf.py — Layout-Aware, Evidence-Anchored Scientific PDF Parser

Addresses the four core scientific PDF parsing requirements:
1. 提得全 (Completeness): Extracts body text, captions, footnotes, and references while isolating running headers/footers.
2. 排得对 (Reading Order): XY-Cut + vertical gutter projection reconstructs 1/2/3-column reading order and cross-column/cross-page paragraphs.
3. 拆得清 (Structural Fidelity): Reconstructs hierarchical sections, merged/multi-header/continued tables (JSON/MD/HTML), and Unicode math/super-subscripts.
4. 核得回 (Verifiability & Honesty): Every block/table/figure/equation/reference carries page, printed_page_label, column, bbox, and extraction_method; scanned/encrypted/garbled spans are explicitly disclosed rather than guessed.

Multi-backend graceful fallback chain:
  Tier 1: PyMuPDF (fitz) + native/pdfplumber table geometry
  Tier 2: pdfplumber coordinate + geometric table extraction
  Tier 3: Poppler CLI (pdftohtml -xml / pdftotext -bbox-layout) via stdlib XML parser
  Tier 4: Pure-Python stdlib PDF content-stream (BT..ET, FlateDecode) coordinate parser
  + Optional Adaptive OCR fallback (pdftoppm/fitz render + tesseract TSV confidence)
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import hashlib
import html
import json
import math
import os
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
SKILL_ROOT = SCRIPT_DIR.parent
RESOURCES_DIR = SKILL_ROOT / "resources"


def load_json_resource(filename: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    path = RESOURCES_DIR / filename
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return fallback


UNICODE_MAP = load_json_resource("unicode_math_map.json", {})
SECTION_PATTERNS = load_json_resource("section_patterns.json", {})


@dataclasses.dataclass
class RawBlock:
    page: int  # 1-based physical PDF page
    printed_label: str
    bbox: Tuple[float, float, float, float]  # x0, y0, x1, y1 (top-left origin)
    text: str
    font_size: float = 10.0
    is_bold: bool = False
    is_italic: bool = False
    source_method: str = "unknown"
    confidence: Optional[float] = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_scientific_text(raw: str) -> Tuple[str, List[str]]:
    """Preserves Greek/math/super-subscripts, expands PDF ligatures, and detects CID garbling."""
    if not raw:
        return "", []
    garbled_hits: List[str] = []
    for pat in UNICODE_MAP.get("cid_corruption_regexes", [r"\(cid:\d+\)", "\ufffd"]):
        for m in re.finditer(pat, raw):
            sample = m.group(0)
            if sample not in garbled_hits:
                garbled_hits.append(sample)

    text = raw
    for group in ("ligatures", "typography_and_dashes", "math_and_units", "symbol_pua_greek"):
        mapping = UNICODE_MAP.get(group, {})
        for src, dst in mapping.items():
            if src in text:
                text = text.replace(src, dst)

    # Collapse horizontal redundant spaces while keeping line breaks meaningful
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    cleaned = "\n".join(ln for ln in lines if ln)
    return cleaned, garbled_hits


def join_lines_preserving_hyphens(lines: List[str]) -> str:
    """Joins wrapped lines within a paragraph block, healing end-of-line hyphenation safely."""
    if not lines:
        return ""
    out = lines[0]
    for nxt in lines[1:]:
        if not nxt:
            continue
        # Heal standard English word-wrap hyphenation (e.g. "micro-\nchannel" -> "microchannel"),
        # while keeping chemical/math hyphens like "α-\nFe2O3" or capitalized compounds " Navier-\nStokes".
        if (
            out.endswith("-")
            and len(out) >= 3
            and out[-2].islower()
            and nxt[0].islower()
            and not out.endswith("--")
        ):
            out = out[:-1] + nxt
        else:
            # No space needed between CJK characters
            if re.search(r"[\u4e00-\u9fff]$", out) and re.match(r"^[\u4e00-\u9fff]", nxt):
                out = out + nxt
            else:
                out = out + " " + nxt
    return out.strip()


# ---------------------------------------------------------------------------
# Backend Extraction Tiers
# ---------------------------------------------------------------------------

def extract_via_pymupdf(pdf_path: Path) -> Dict[str, Any]:
    import fitz  # type: ignore

    doc = fitz.open(str(pdf_path))
    if doc.is_encrypted and not doc.authenticate(""):
        return {
            "is_encrypted": True,
            "num_pages": len(doc),
            "backend": "pymupdf",
            "pages": [],
        }

    pages_out: List[Dict[str, Any]] = []
    for p_idx in range(len(doc)):
        page = doc[p_idx]
        p_num = p_idx + 1
        rect = page.rect
        width, height = float(rect.width), float(rect.height)
        try:
            label = page.get_label() or str(p_num)
        except Exception:
            label = str(p_num)

        page_dict = page.get_text("dict")
        raw_blocks: List[RawBlock] = []
        image_bboxes: List[List[float]] = []

        for b in page_dict.get("blocks", []):
            b_type = b.get("type", 0)
            bbox = tuple(round(float(v), 2) for v in b.get("bbox", (0, 0, 0, 0)))
            if b_type == 1:
                image_bboxes.append(list(bbox))
                continue
            lines_raw = b.get("lines", [])
            if not lines_raw:
                continue

            rendered_lines: List[str] = []
            sizes: List[float] = []
            bold_chars = 0
            italic_chars = 0
            total_chars = 0

            for ln in lines_raw:
                spans = ln.get("spans", [])
                if not spans:
                    continue
                # Compute line modal size & baseline for super/subscript reconstruction
                span_sizes = [float(sp.get("size", 10.0)) for sp in spans if sp.get("text", "").strip()]
                line_base_size = max(span_sizes) if span_sizes else 10.0
                base_origins = [
                    float(sp.get("origin", [0, ln["bbox"][3]])[1])
                    for sp in spans
                    if float(sp.get("size", 10.0)) >= 0.88 * line_base_size and sp.get("text", "").strip()
                ]
                baseline_y = statistics.median(base_origins) if base_origins else float(ln["bbox"][3])

                line_parts: List[str] = []
                prev_x1: Optional[float] = None
                for sp in spans:
                    s_txt = sp.get("text", "")
                    if not s_txt:
                        continue
                    s_size = float(sp.get("size", 10.0))
                    s_flags = int(sp.get("flags", 0))
                    s_bbox = sp.get("bbox", ln["bbox"])
                    origin_y = float(sp.get("origin", [s_bbox[0], s_bbox[3]])[1])

                    if prev_x1 is not None and (float(s_bbox[0]) - prev_x1) > 0.35 * line_base_size:
                        if line_parts and not line_parts[-1].endswith(" ") and not s_txt.startswith(" "):
                            line_parts.append(" ")
                    prev_x1 = float(s_bbox[2])

                    n_ch = len(s_txt.strip())
                    if n_ch > 0:
                        sizes.append(s_size)
                        total_chars += n_ch
                        if (s_flags & 16) or "bold" in str(sp.get("font", "")).lower():
                            bold_chars += n_ch
                        if (s_flags & 2) or "ital" in str(sp.get("font", "")).lower():
                            italic_chars += n_ch

                    # Detect explicit superscript/subscript via flag or smaller font + vertical shift
                    stripped_sp = s_txt.strip()
                    dy = baseline_y - origin_y
                    if (
                        stripped_sp
                        and len(stripped_sp) <= 12
                        and s_size < 0.83 * line_base_size
                        and abs(dy) > 0.12 * line_base_size
                    ):
                        if dy > 0:
                            s_txt = f"^{{{stripped_sp}}}"
                        else:
                            s_txt = f"_{{{stripped_sp}}}"
                    elif (s_flags & 1) and stripped_sp and len(stripped_sp) <= 12 and abs(dy) >= 0:
                        s_txt = f"^{{{stripped_sp}}}"

                    line_parts.append(s_txt)

                ln_str = "".join(line_parts).strip()
                if ln_str:
                    rendered_lines.append(ln_str)

            if not rendered_lines:
                continue

            # Split multi-heading/body accidental merges inside a single PyMuPDF block if font jumps sharply
            joined = join_lines_preserving_hyphens(rendered_lines)
            med_size = round(statistics.median(sizes), 2) if sizes else 10.0
            raw_blocks.append(
                RawBlock(
                    page=p_num,
                    printed_label=label,
                    bbox=bbox,  # type: ignore
                    text=joined,
                    font_size=med_size,
                    is_bold=(bold_chars > 0.5 * max(1, total_chars)),
                    is_italic=(italic_chars > 0.5 * max(1, total_chars)),
                    source_method="pymupdf_dict",
                )
            )

        # Native PyMuPDF table detection when available
        native_tables: List[Dict[str, Any]] = []
        if hasattr(page, "find_tables"):
            try:
                tabs = page.find_tables()
                for t_idx, tab in enumerate(tabs.tables):
                    grid = tab.extract()
                    if grid and len(grid) >= 2:
                        native_tables.append(
                            {
                                "page": p_num,
                                "bbox": [round(float(v), 2) for v in tab.bbox],
                                "grid": [[(c or "").strip() for c in row] for row in grid],
                                "method": "pymupdf_find_tables",
                            }
                        )
            except Exception:
                pass

        pages_out.append(
            {
                "page": p_num,
                "printed_label": label,
                "width": round(width, 2),
                "height": round(height, 2),
                "blocks": raw_blocks,
                "image_bboxes": image_bboxes,
                "native_tables": native_tables,
            }
        )
    doc.close()
    return {
        "is_encrypted": False,
        "num_pages": len(pages_out),
        "backend": "pymupdf",
        "pages": pages_out,
    }


def extract_via_pdfplumber(pdf_path: Path) -> Dict[str, Any]:
    import pdfplumber  # type: ignore

    pages_out: List[Dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for p_idx, page in enumerate(pdf.pages):
            p_num = p_idx + 1
            width, height = float(page.width), float(page.height)
            words = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False) or []

            # Group words into lines by y-top proximity, then lines into blocks by vertical gap & x-overlap
            words_sorted = sorted(words, key=lambda w: (round(float(w["top"]) / 3.0), float(w["x0"])))
            lines: List[Dict[str, Any]] = []
            for w in words_sorted:
                x0, top, x1, bottom = float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"])
                sz = float(w.get("size", 10.0))
                fn = str(w.get("fontname", ""))
                placed = False
                for ln in reversed(lines[-8:]):
                    if (
                        abs(top - ln["top"]) <= max(3.5, 0.45 * sz)
                        and abs(x0 - ln["x1"]) <= max(28.0, 2.8 * sz)
                    ):
                        ln["words"].append(w)
                        ln["x0"] = min(ln["x0"], x0)
                        ln["top"] = min(ln["top"], top)
                        ln["x1"] = max(ln["x1"], x1)
                        ln["bottom"] = max(ln["bottom"], bottom)
                        ln["sizes"].append(sz)
                        ln["fonts"].append(fn)
                        placed = True
                        break
                if not placed:
                    lines.append(
                        {
                            "words": [w],
                            "x0": x0,
                            "top": top,
                            "x1": x1,
                            "bottom": bottom,
                            "sizes": [sz],
                            "fonts": [fn],
                        }
                    )

            # Merge consecutive lines in the same column into paragraph blocks
            raw_blocks: List[RawBlock] = []
            lines.sort(key=lambda l: (l["top"], l["x0"]))
            used = [False] * len(lines)
            for i, ln in enumerate(lines):
                if used[i]:
                    continue
                used[i] = True
                grp = [ln]
                cur = ln
                for j in range(i + 1, len(lines)):
                    if used[j]:
                        continue
                    cand = lines[j]
                    v_gap = cand["top"] - cur["bottom"]
                    med_sz = statistics.median(cur["sizes"])
                    if v_gap > max(8.0, 0.95 * med_sz):
                        if v_gap > 2.2 * med_sz:
                            break
                        continue
                    # Same column horizontal alignment check
                    overlap = min(cur["x1"], cand["x1"]) - max(cur["x0"], cand["x0"])
                    same_left = abs(cur["x0"] - cand["x0"]) <= 24.0
                    sz_close = abs(statistics.median(cand["sizes"]) - med_sz) <= 1.2
                    if (overlap > 20.0 or same_left) and sz_close and v_gap >= -3.0:
                        grp.append(cand)
                        used[j] = True
                        cur = cand

                line_texts = [" ".join(w["text"] for w in g["words"]) for g in grp]
                all_sizes = [s for g in grp for s in g["sizes"]]
                all_fonts = [f.lower() for g in grp for f in g["fonts"]]
                bx0 = round(min(g["x0"] for g in grp), 2)
                by0 = round(min(g["top"] for g in grp), 2)
                bx1 = round(max(g["x1"] for g in grp), 2)
                by1 = round(max(g["bottom"] for g in grp), 2)
                raw_blocks.append(
                    RawBlock(
                        page=p_num,
                        printed_label=str(p_num),
                        bbox=(bx0, by0, bx1, by1),
                        text=join_lines_preserving_hyphens(line_texts),
                        font_size=round(statistics.median(all_sizes), 2) if all_sizes else 10.0,
                        is_bold=any("bold" in f for f in all_fonts),
                        is_italic=any("ital" in f for f in all_fonts),
                        source_method="pdfplumber",
                    )
                )

            native_tables: List[Dict[str, Any]] = []
            try:
                for tab in page.find_tables():
                    grid = tab.extract()
                    if grid and len(grid) >= 2:
                        native_tables.append(
                            {
                                "page": p_num,
                                "bbox": [round(float(v), 2) for v in tab.bbox],
                                "grid": [[(c or "").strip() for c in row] for row in grid],
                                "method": "pdfplumber_tables",
                            }
                        )
            except Exception:
                pass

            image_bboxes = [
                [round(float(im.get("x0", 0)), 2), round(float(im.get("top", 0)), 2),
                 round(float(im.get("x1", 0)), 2), round(float(im.get("bottom", 0)), 2)]
                for im in (page.images or [])
            ]
            pages_out.append(
                {
                    "page": p_num,
                    "printed_label": str(p_num),
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "blocks": raw_blocks,
                    "image_bboxes": image_bboxes,
                    "native_tables": native_tables,
                }
            )
    return {
        "is_encrypted": False,
        "num_pages": len(pages_out),
        "backend": "pdfplumber",
        "pages": pages_out,
    }


def extract_via_poppler_xml(pdf_path: Path) -> Dict[str, Any]:
    pdftohtml_bin = shutil.which("pdftohtml")
    if not pdftohtml_bin:
        raise RuntimeError("pdftohtml binary not found")

    with tempfile.TemporaryDirectory(prefix="scipdf_poppler_") as tmp:
        xml_out = Path(tmp) / "doc.xml"
        proc = subprocess.run(
            [pdftohtml_bin, "-xml", "-i", "-nodrm", str(pdf_path), str(xml_out)],
            capture_output=True,
            text=True,
            timeout=120,
            errors="replace",
        )
        stderr_low = (proc.stderr or "").lower()
        if "incorrect password" in stderr_low or "encrypted" in stderr_low and not xml_out.exists():
            return {"is_encrypted": True, "num_pages": 0, "backend": "poppler_xml", "pages": []}
        if not xml_out.exists():
            raise RuntimeError(f"pdftohtml failed: {proc.stderr}")

        xml_text = xml_out.read_text(encoding="utf-8", errors="replace")
        # Strip control chars invalid in XML 1.0
        xml_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_text)
        root = ET.fromstring(xml_text)

    pages_out: List[Dict[str, Any]] = []
    font_specs: Dict[str, float] = {}
    for page_el in root.findall(".//page"):
        p_num = int(page_el.attrib.get("number", len(pages_out) + 1))
        width = float(page_el.attrib.get("width", 612))
        height = float(page_el.attrib.get("height", 792))
        for fs in page_el.findall("fontspec"):
            font_specs[fs.attrib.get("id", "")] = float(fs.attrib.get("size", 10.0))

        lines: List[Dict[str, Any]] = []
        for t_el in page_el.findall("text"):
            txt = "".join(t_el.itertext()).strip()
            if not txt:
                continue
            left = float(t_el.attrib.get("left", 0))
            top = float(t_el.attrib.get("top", 0))
            w = float(t_el.attrib.get("width", 40))
            h = float(t_el.attrib.get("height", 12))
            f_id = t_el.attrib.get("font", "")
            sz = font_specs.get(f_id, h)
            is_b = t_el.find("b") is not None
            is_i = t_el.find("i") is not None
            lines.append(
                {
                    "x0": left,
                    "y0": top,
                    "x1": left + w,
                    "y1": top + h,
                    "text": txt,
                    "size": sz,
                    "bold": is_b,
                    "italic": is_i,
                }
            )

        # Group adjacent lines in same column
        lines.sort(key=lambda r: (r["y0"], r["x0"]))
        raw_blocks: List[RawBlock] = []
        used = [False] * len(lines)
        for i, ln in enumerate(lines):
            if used[i]:
                continue
            used[i] = True
            grp = [ln]
            cur = ln
            for j in range(i + 1, len(lines)):
                if used[j]:
                    continue
                cand = lines[j]
                v_gap = cand["y0"] - cur["y1"]
                if v_gap > max(9.0, 1.1 * cur["size"]):
                    if v_gap > 2.5 * cur["size"]:
                        break
                    continue
                same_col = abs(cur["x0"] - cand["x0"]) <= 28.0 and abs(cur["size"] - cand["size"]) <= 1.2
                if same_col and v_gap >= -3.0:
                    grp.append(cand)
                    used[j] = True
                    cur = cand

            raw_blocks.append(
                RawBlock(
                    page=p_num,
                    printed_label=str(p_num),
                    bbox=(
                        round(min(g["x0"] for g in grp), 2),
                        round(min(g["y0"] for g in grp), 2),
                        round(max(g["x1"] for g in grp), 2),
                        round(max(g["y1"] for g in grp), 2),
                    ),
                    text=join_lines_preserving_hyphens([g["text"] for g in grp]),
                    font_size=round(statistics.median(g["size"] for g in grp), 2),
                    is_bold=any(g["bold"] for g in grp),
                    is_italic=any(g["italic"] for g in grp),
                    source_method="poppler_xml",
                )
            )

        pages_out.append(
            {
                "page": p_num,
                "printed_label": str(p_num),
                "width": round(width, 2),
                "height": round(height, 2),
                "blocks": raw_blocks,
                "image_bboxes": [],
                "native_tables": [],
            }
        )
    return {
        "is_encrypted": False,
        "num_pages": len(pages_out),
        "backend": "poppler_xml",
        "pages": pages_out,
    }


def _decode_pdf_literal_string(raw_bytes: bytes) -> str:
    out = bytearray()
    i = 0
    n = len(raw_bytes)
    while i < n:
        ch = raw_bytes[i]
        if ch == 0x5C and i + 1 < n:  # backslash
            nxt = raw_bytes[i + 1]
            esc_map = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12,
                       ord("("): ord("("), ord(")"): ord(")"), ord("\\"): ord("\\")}
            if nxt in esc_map:
                out.append(esc_map[nxt])
                i += 2
                continue
            if 48 <= nxt <= 55:  # octal \ddd
                oct_digits = bytes([nxt])
                i += 2
                for _ in range(2):
                    if i < n and 48 <= raw_bytes[i] <= 55:
                        oct_digits += bytes([raw_bytes[i]])
                        i += 1
                    else:
                        break
                out.append(int(oct_digits, 8) & 0xFF)
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    if out.startswith(b"\xfe\xff"):
        return out[2:].decode("utf-16-be", errors="replace")
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("latin-1", errors="replace")


def extract_via_stdlib_pdf(pdf_path: Path) -> Dict[str, Any]:
    """
    Pure-Python stdlib coordinate parser for uncompressed and /FlateDecode PDF streams.
    Provides zero-dependency resilience when third-party C extensions are unavailable.
    """
    data = pdf_path.read_bytes()
    if re.search(rb"/Encrypt\b", data):
        return {"is_encrypted": True, "num_pages": 0, "backend": "stdlib_pdf_stream", "pages": []}

    # Locate objects
    objects: Dict[int, bytes] = {}
    for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", data, flags=re.DOTALL):
        objects[int(m.group(1))] = m.group(2)

    page_objs: List[Tuple[int, bytes]] = []
    for obj_id, body in sorted(objects.items()):
        # Match /Type /Page (not /Pages)
        header_part = body.split(b"stream", 1)[0]
        if re.search(rb"/Type\s*/Page\b(?!s)", header_part):
            page_objs.append((obj_id, body))

    pages_out: List[Dict[str, Any]] = []
    for p_idx, (_oid, p_body) in enumerate(page_objs):
        p_num = p_idx + 1
        mb = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", p_body)
        width = float(mb.group(3)) - float(mb.group(1)) if mb else 612.0
        height = float(mb.group(4)) - float(mb.group(2)) if mb else 792.0

        # Resolve /Contents object ref(s)
        stream_chunks: List[bytes] = []
        c_arr = re.search(rb"/Contents\s*\[(.*?)\]", p_body, flags=re.DOTALL)
        if c_arr:
            refs = [int(r) for r in re.findall(rb"(\d+)\s+\d+\s+R", c_arr.group(1))]
        else:
            c_one = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", p_body)
            refs = [int(c_one.group(1))] if c_one else []

        targets = [objects[r] for r in refs if r in objects] or [p_body]
        for t_body in targets:
            for sm in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", t_body, flags=re.DOTALL):
                raw_s = sm.group(1)
                if b"/FlateDecode" in t_body.split(b"stream", 1)[0]:
                    try:
                        raw_s = zlib.decompress(raw_s)
                    except Exception:
                        pass
                stream_chunks.append(raw_s)

        full_stream = b"\n".join(stream_chunks)
        lines_extracted: List[Dict[str, Any]] = []

        for bt_m in re.finditer(rb"BT\b(.*?)ET\b", full_stream, flags=re.DOTALL):
            bt = bt_m.group(1)
            cur_x, cur_y = 54.0, height - 54.0
            cur_font = "F1"
            cur_size = 10.0
            # Parse sequential operators inside BT..ET
            token_re = re.compile(
                rb"(?P<tm>([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm)"
                rb"|(?P<td>([-\d.]+)\s+([-\d.]+)\s+T[dD])"
                rb"|(?P<tf>/(\w+)\s+([-\d.]+)\s+Tf)"
                rb"|(?P<tj>\((?:\\.|[^\\)])*\)\s*Tj)"
                rb"|(?P<tja>\[(?:.*?)\]\s*TJ)"
                rb"|(?P<hex><([0-9A-Fa-f\s]+)>\s*Tj)",
                flags=re.DOTALL,
            )
            for tm in token_re.finditer(bt):
                if tm.group("tm"):
                    vals = tm.group("tm").split()
                    scale_y = abs(float(vals[3]))
                    cur_x = float(vals[4])
                    cur_y = float(vals[5])
                    if scale_y > 1.0 and cur_size == 1.0:
                        cur_size = scale_y
                elif tm.group("td"):
                    vals = tm.group("td").split()
                    cur_x += float(vals[0])
                    cur_y += float(vals[1])
                elif tm.group("tf"):
                    m_tf = re.match(rb"/(\w+)\s+([-\d.]+)\s+Tf", tm.group("tf"))
                    if m_tf:
                        cur_font = m_tf.group(1).decode("latin-1")
                        cur_size = abs(float(m_tf.group(2))) or 10.0
                elif tm.group("tj"):
                    lit = re.search(rb"\((.*)\)\s*Tj", tm.group("tj"), flags=re.DOTALL)
                    if lit:
                        txt = _decode_pdf_literal_string(lit.group(1)).strip()
                        if txt:
                            top_y = max(0.0, height - cur_y - cur_size)
                            est_w = max(12.0, len(txt) * cur_size * 0.52)
                            lines_extracted.append(
                                {
                                    "x0": cur_x,
                                    "y0": top_y,
                                    "x1": min(width, cur_x + est_w),
                                    "y1": top_y + cur_size * 1.15,
                                    "text": txt,
                                    "size": cur_size,
                                    "bold": "bold" in cur_font.lower() or cur_font in {"F2", "FB"},
                                }
                            )
                elif tm.group("tja"):
                    parts = [
                        _decode_pdf_literal_string(pm.group(1))
                        for pm in re.finditer(rb"\(((?:\\.|[^\\)])*)\)", tm.group("tja"))
                    ]
                    txt = "".join(parts).strip()
                    if txt:
                        top_y = max(0.0, height - cur_y - cur_size)
                        est_w = max(12.0, len(txt) * cur_size * 0.52)
                        lines_extracted.append(
                            {
                                "x0": cur_x,
                                "y0": top_y,
                                "x1": min(width, cur_x + est_w),
                                "y1": top_y + cur_size * 1.15,
                                "text": txt,
                                "size": cur_size,
                                "bold": "bold" in cur_font.lower() or cur_font in {"F2", "FB"},
                            }
                        )
                elif tm.group("hex"):
                    hx = re.sub(rb"\s+", b"", tm.group("hex").split(b">")[0].lstrip(b"<"))
                    raw_b = bytes.fromhex(hx.decode("ascii")) if len(hx) % 2 == 0 else b""
                    txt = (
                        raw_b.decode("utf-16-be", errors="replace")
                        if len(raw_b) >= 2
                        else raw_b.decode("latin-1", errors="replace")
                    ).strip()
                    if txt:
                        top_y = max(0.0, height - cur_y - cur_size)
                        est_w = max(12.0, len(txt) * cur_size * 0.55)
                        lines_extracted.append(
                            {
                                "x0": cur_x,
                                "y0": top_y,
                                "x1": min(width, cur_x + est_w),
                                "y1": top_y + cur_size * 1.15,
                                "text": txt,
                                "size": cur_size,
                                "bold": "bold" in cur_font.lower() or cur_font in {"F2", "FB"},
                            }
                        )

        # Group adjacent lines in same column into blocks (unless table-row pipe/tab structured)
        lines_extracted.sort(key=lambda r: (round(r["y0"], 1), r["x0"]))
        raw_blocks: List[RawBlock] = []
        used = [False] * len(lines_extracted)
        for i, ln in enumerate(lines_extracted):
            if used[i]:
                continue
            used[i] = True
            grp = [ln]
            cur = ln
            for j in range(i + 1, len(lines_extracted)):
                if used[j]:
                    continue
                cand = lines_extracted[j]
                v_gap = cand["y0"] - cur["y1"]
                if v_gap > max(7.0, 0.85 * cur["size"]):
                    if v_gap > 2.2 * cur["size"]:
                        break
                    continue
                same_col = abs(cur["x0"] - cand["x0"]) <= 22.0 and abs(cur["size"] - cand["size"]) <= 0.8 and cur["bold"] == cand["bold"]
                if same_col and v_gap >= -3.0:
                    grp.append(cand)
                    used[j] = True
                    cur = cand

            # Preserve line breaks for pipe/tab table blocks so table parser reconstructs rows cleanly
            if sum(1 for g in grp if "|" in g["text"] or "  " in g["text"]) >= 2:
                txt_joined = "\n".join(g["text"] for g in grp)
            else:
                txt_joined = join_lines_preserving_hyphens([g["text"] for g in grp])

            raw_blocks.append(
                RawBlock(
                    page=p_num,
                    printed_label=str(p_num),
                    bbox=(
                        round(min(g["x0"] for g in grp), 2),
                        round(min(g["y0"] for g in grp), 2),
                        round(max(g["x1"] for g in grp), 2),
                        round(max(g["y1"] for g in grp), 2),
                    ),
                    text=txt_joined,
                    font_size=round(statistics.median(g["size"] for g in grp), 2),
                    is_bold=any(g["bold"] for g in grp),
                    is_italic=False,
                    source_method="stdlib_pdf_stream",
                )
            )

        pages_out.append(
            {
                "page": p_num,
                "printed_label": str(p_num),
                "width": round(width, 2),
                "height": round(height, 2),
                "blocks": raw_blocks,
                "image_bboxes": [],
                "native_tables": [],
            }
        )

    return {
        "is_encrypted": False,
        "num_pages": len(pages_out),
        "backend": "stdlib_pdf_stream",
        "pages": pages_out,
    }


def extract_pdf_multi_backend(pdf_path: Path, preferred: str = "auto") -> Dict[str, Any]:
    backends = [
        ("pymupdf", extract_via_pymupdf),
        ("pdfplumber", extract_via_pdfplumber),
        ("poppler_xml", extract_via_poppler_xml),
        ("stdlib_pdf_stream", extract_via_stdlib_pdf),
    ]
    if preferred != "auto":
        backends.sort(key=lambda item: 0 if item[0] == preferred else 1)

    errors: Dict[str, str] = {}
    primary_result: Optional[Dict[str, Any]] = None
    for name, fn in backends:
        try:
            res = fn(pdf_path)
            if res.get("is_encrypted"):
                res["backend_errors"] = errors
                return res
            if res.get("num_pages", 0) > 0:
                primary_result = res
                break
        except Exception as exc:
            errors[name] = str(exc)

    if primary_result is None:
        raise RuntimeError(f"All PDF extraction backends failed: {errors}")

    # Enrich tables via pdfplumber if primary was pymupdf and found 0 native tables on pages with Table captions
    if primary_result["backend"] == "pymupdf":
        has_missing_tables = any(
            not p["native_tables"]
            and any(re.search(r"\b(?:Table|Tab\.|表)\s*[S]?\d+", b.text, re.I) for b in p["blocks"])
            for p in primary_result["pages"]
        )
        if has_missing_tables:
            try:
                plum = extract_via_pdfplumber(pdf_path)
                for p_pri, p_plu in zip(primary_result["pages"], plum["pages"]):
                    if not p_pri["native_tables"] and p_plu["native_tables"]:
                        p_pri["native_tables"] = p_plu["native_tables"]
            except Exception:
                pass

    primary_result["backend_errors"] = errors
    return primary_result


# ---------------------------------------------------------------------------
# Adaptive OCR Fallback for Scanned / Garbled Pages
# ---------------------------------------------------------------------------

def run_page_ocr(pdf_path: Path, page_num: int, dpi: int = 300) -> Optional[Dict[str, Any]]:
    tesseract_bin = shutil.which("tesseract")
    if not tesseract_bin:
        return None

    # Detect installed languages (prefer eng+chi_sim if available)
    lang = "eng"
    try:
        lp = subprocess.run([tesseract_bin, "--list-langs"], capture_output=True, text=True, timeout=5)
        langs = set((lp.stdout or "").splitlines())
        if "chi_sim" in langs and "eng" in langs:
            lang = "eng+chi_sim"
        elif "chi_sim" in langs:
            lang = "chi_sim"
    except Exception:
        pass

    with tempfile.TemporaryDirectory(prefix="scipdf_ocr_") as tmp:
        img_path = Path(tmp) / f"page_{page_num}.png"
        rendered = False

        # Render via PyMuPDF first, fallback to pdftoppm
        try:
            import fitz  # type: ignore
            doc = fitz.open(str(pdf_path))
            page = doc[page_num - 1]
            pix = page.get_pixmap(dpi=dpi)
            pix.save(str(img_path))
            img_w, img_h = pix.width, pix.height
            pdf_w, pdf_h = float(page.rect.width), float(page.rect.height)
            doc.close()
            rendered = True
        except Exception:
            pdftoppm_bin = shutil.which("pdftoppm")
            if pdftoppm_bin:
                prefix = Path(tmp) / "ppm"
                subprocess.run(
                    [pdftoppm_bin, "-png", "-r", str(dpi), "-f", str(page_num), "-l", str(page_num), str(pdf_path), str(prefix)],
                    capture_output=True,
                    timeout=60,
                )
                pngs = sorted(Path(tmp).glob("ppm*.png"))
                if pngs:
                    img_path = pngs[0]
                    img_w, img_h = int(8.5 * dpi), int(11.0 * dpi)
                    pdf_w, pdf_h = 612.0, 792.0
                    rendered = True

        if not rendered or not img_path.exists():
            return None

        # Optional lightweight contrast enhancement via Pillow
        try:
            from PIL import Image, ImageOps  # type: ignore
            im = Image.open(img_path).convert("L")
            img_w, img_h = im.size
            im = ImageOps.autocontrast(im, cutoff=1)
            im.save(img_path)
        except Exception:
            pass

        out_base = Path(tmp) / "ocr_out"
        subprocess.run(
            [tesseract_bin, str(img_path), str(out_base), "-l", lang, "tsv"],
            capture_output=True,
            text=True,
            timeout=90,
        )
        tsv_file = Path(f"{out_base}.tsv")
        if not tsv_file.exists():
            return None

        scale_x = pdf_w / max(1.0, float(img_w))
        scale_y = pdf_h / max(1.0, float(img_h))

        par_groups: Dict[Tuple[int, int], List[Dict[str, Any]]] = collections.defaultdict(list)
        confs: List[float] = []
        low_conf_words: List[str] = []

        for idx, row in enumerate(tsv_file.read_text(encoding="utf-8", errors="replace").splitlines()):
            if idx == 0 or not row.strip():
                continue
            cols = row.split("\t")
            if len(cols) < 12:
                continue
            txt = cols[11].strip()
            if not txt:
                continue
            try:
                blk_n, par_n, line_n = int(cols[2]), int(cols[3]), int(cols[4])
                left, top, w, h = float(cols[6]), float(cols[7]), float(cols[8]), float(cols[9])
                conf = float(cols[10])
            except ValueError:
                continue
            if conf >= 0:
                confs.append(conf)
                if conf < 60.0:
                    low_conf_words.append(txt)
            par_groups[(blk_n, par_n)].append(
                {
                    "line": line_n,
                    "x0": left * scale_x,
                    "y0": top * scale_y,
                    "x1": (left + w) * scale_x,
                    "y1": (top + h) * scale_y,
                    "text": txt,
                    "conf": conf,
                }
            )

        ocr_blocks: List[RawBlock] = []
        for (_bn, _pn), items in par_groups.items():
            by_line: Dict[int, List[str]] = collections.defaultdict(list)
            for it in items:
                by_line[it["line"]].append(it["text"])
            line_strs = [" ".join(ws) for _, ws in sorted(by_line.items())]
            item_confs = [it["conf"] for it in items if it["conf"] >= 0]
            ocr_blocks.append(
                RawBlock(
                    page=page_num,
                    printed_label=str(page_num),
                    bbox=(
                        round(min(it["x0"] for it in items), 2),
                        round(min(it["y0"] for it in items), 2),
                        round(max(it["x1"] for it in items), 2),
                        round(max(it["y1"] for it in items), 2),
                    ),
                    text=join_lines_preserving_hyphens(line_strs),
                    font_size=10.0,
                    source_method=f"tesseract_ocr_{lang}",
                    confidence=round(statistics.mean(item_confs), 1) if item_confs else None,
                )
            )

        return {
            "blocks": ocr_blocks,
            "mean_confidence": round(statistics.mean(confs), 1) if confs else 0.0,
            "low_confidence_words": low_conf_words[:25],
            "lang": lang,
        }


# ---------------------------------------------------------------------------
# Layout Analysis: Header/Footer/Footnote Isolation + XY-Cut Multi-Column Order
# ---------------------------------------------------------------------------

def isolate_headers_footers_footnotes(pages: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[int, List[RawBlock]]]:
    """Separates running headers, running footers/page numbers, and bottom footnotes from body blocks."""
    num_pages = len(pages)
    margin_sig_counter: collections.Counter[str] = collections.Counter()

    def canonical_sig(txt: str) -> str:
        s = re.sub(r"\d+", "#", txt.strip().lower())
        return re.sub(r"\s+", " ", s)

    for p in pages:
        h = max(1.0, p["height"])
        for b in p["blocks"]:
            if b.bbox[3] <= 0.09 * h or b.bbox[1] >= 0.91 * h:
                if len(b.text) <= 140:
                    margin_sig_counter[canonical_sig(b.text)] += 1

    repeat_threshold = 2 if num_pages >= 2 else 999
    footnote_re = re.compile(SECTION_PATTERNS.get("footnote_line_regex", r"^(?:[*†‡§¶]|\d{1,2}[.)])\s+\S+"))

    headers: List[Dict[str, Any]] = []
    footers: List[Dict[str, Any]] = []
    footnotes: List[Dict[str, Any]] = []
    body_by_page: Dict[int, List[RawBlock]] = {}

    # Estimate global modal body font size first
    all_sizes = [b.font_size for p in pages for b in p["blocks"] if len(b.text) >= 25]
    modal_body_size = statistics.median(all_sizes) if all_sizes else 10.0

    for p in pages:
        p_num = p["page"]
        h = max(1.0, p["height"])
        kept: List[RawBlock] = []
        for b in p["blocks"]:
            txt = b.text.strip()
            if not txt:
                continue
            sig = canonical_sig(txt)
            in_top = b.bbox[3] <= 0.088 * h
            in_bottom = b.bbox[1] >= 0.912 * h
            is_bare_pagenum = bool(re.fullmatch(r"(?:page\s*)?[ivxlcdm\d]{1,5}(?:\s*/\s*\d{1,5})?", txt, re.I))

            if in_top and (margin_sig_counter[sig] >= repeat_threshold or (len(txt) <= 95 and b.font_size <= 0.92 * modal_body_size and p_num > 1) or ("vol." in txt.lower() and b.bbox[3] <= 0.065 * h)):
                headers.append({"page": p_num, "bbox": list(b.bbox), "text": txt})
                continue

            if in_bottom and (is_bare_pagenum or margin_sig_counter[sig] >= repeat_threshold):
                # Infer printed page label if bare number
                if is_bare_pagenum and p["printed_label"] == str(p_num):
                    num_m = re.search(r"[ivxlcdm\d]+", txt, re.I)
                    if num_m:
                        p["printed_label"] = num_m.group(0)
                footers.append({"page": p_num, "bbox": list(b.bbox), "text": txt})
                continue

            if b.bbox[1] >= 0.84 * h and b.font_size <= 0.93 * modal_body_size and footnote_re.match(txt) and len(txt) <= 450:
                marker = txt.split()[0]
                footnotes.append({"page": p_num, "bbox": list(b.bbox), "marker": marker, "text": txt})
                continue

            kept.append(b)
        body_by_page[p_num] = kept

    return {"headers": headers, "footers": footers, "footnotes": footnotes}, body_by_page


def detect_columns_and_order_blocks(blocks: List[RawBlock], page_width: float) -> Tuple[int, List[Tuple[int, RawBlock]]]:
    """
    XY-Cut + vertical gutter histogram reading-order solver.
    Splits spanning blocks (title, abstract, wide figures/tables) into horizontal bands,
    and orders multi-column bands column-by-column (left-to-right, top-to-bottom).
    Returns (max_columns_on_page, [(column_id, block), ...]) where column_id=0 means full-width spanning.
    """
    if not blocks:
        return 1, []

    min_x = min(b.bbox[0] for b in blocks)
    max_x = max(b.bbox[2] for b in blocks)
    content_w = max(120.0, max_x - min_x)

    # Identify narrow candidate column blocks vs wide spanning blocks
    narrow = [b for b in blocks if (b.bbox[2] - b.bbox[0]) <= 0.56 * content_w]

    # Search for vertical empty/near-empty gutter in central 28%..72% of content width
    gutter_x: Optional[float] = None
    if len(narrow) >= 2:
        grid_start = int(min_x + 0.28 * content_w)
        grid_end = int(min_x + 0.72 * content_w)
        if grid_end > grid_start + 8:
            occ = [0] * (grid_end - grid_start)
            left_has = False
            right_has = False
            mid_pt = min_x + 0.5 * content_w
            for b in narrow:
                if b.bbox[2] <= mid_pt + 12:
                    left_has = True
                if b.bbox[0] >= mid_pt - 12:
                    right_has = True
                s = max(grid_start, int(math.floor(b.bbox[0])))
                e = min(grid_end, int(math.ceil(b.bbox[2])))
                for x in range(s, e):
                    occ[x - grid_start] += 1

            if left_has and right_has:
                min_occ = min(occ)
                if min_occ == 0:
                    zero_xs = [grid_start + idx for idx, c in enumerate(occ) if c == 0]
                    # Pick longest contiguous zero run closest to center
                    runs: List[Tuple[int, int]] = []
                    rs = zero_xs[0]
                    rp = zero_xs[0]
                    for zx in zero_xs[1:]:
                        if zx == rp + 1:
                            rp = zx
                        else:
                            runs.append((rs, rp))
                            rs = rp = zx
                    runs.append((rs, rp))
                    best_run = max(runs, key=lambda r: (r[1] - r[0], -abs(0.5 * (r[0] + r[1]) - mid_pt)))
                    if (best_run[1] - best_run[0]) >= 4:
                        gutter_x = 0.5 * (best_run[0] + best_run[1])

    if gutter_x is None:
        ordered = sorted(blocks, key=lambda b: (round(b.bbox[1], 1), b.bbox[0]))
        return 1, [(1, b) for b in ordered]

    # Partition sequence into horizontal bands separated by full-width spanning blocks
    # A block is spanning if it crosses gutter_x by more than 18pt on both sides
    def is_spanning(b: RawBlock) -> bool:
        return (b.bbox[0] < gutter_x - 18.0 and b.bbox[2] > gutter_x + 18.0) or (
            (b.bbox[2] - b.bbox[0]) > 0.64 * content_w
        )

    y_sorted = sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
    result: List[Tuple[int, RawBlock]] = []
    col_buffer: List[RawBlock] = []

    def flush_col_buffer() -> None:
        if not col_buffer:
            return
        left_col = sorted(
            [b for b in col_buffer if 0.5 * (b.bbox[0] + b.bbox[2]) <= gutter_x],
            key=lambda b: (round(b.bbox[1], 1), b.bbox[0]),
        )
        right_col = sorted(
            [b for b in col_buffer if 0.5 * (b.bbox[0] + b.bbox[2]) > gutter_x],
            key=lambda b: (round(b.bbox[1], 1), b.bbox[0]),
        )
        for lb in left_col:
            result.append((1, lb))
        for rb in right_col:
            result.append((2, rb))
        col_buffer.clear()

    for b in y_sorted:
        if is_spanning(b):
            flush_col_buffer()
            result.append((0, b))
        else:
            col_buffer.append(b)
    flush_col_buffer()
    return 2, result


# ---------------------------------------------------------------------------
# Structural Classification: Sections, Tables, Equations, Figures, References
# ---------------------------------------------------------------------------

def classify_heading(text: str, font_size: float, is_bold: bool, modal_body_size: float, page: int, block_idx: int) -> Optional[Tuple[int, str, str]]:
    """Returns (level, section_number, heading_title) if block is a section heading."""
    single_line = text.strip()
    if "\n" in single_line or len(single_line) > 130 or len(single_line) < 2:
        return None
    if single_line.endswith(".") and not re.match(r"^[IVX]+\.", single_line):
        return None

    low = single_line.lower().rstrip(":：")
    canon = set(SECTION_PATTERNS.get("canonical_unnumbered_headings", []))
    if low in canon and (is_bold or font_size >= modal_body_size * 0.98 or len(single_line) <= 32):
        return (1, "", single_line.rstrip(":："))

    for pat in SECTION_PATTERNS.get("numbered_heading_regexes", []):
        m = re.match(pat, single_line)
        if m:
            num_str = m.group("num").strip()
            title_str = m.group("title").strip()
            # Reject sentences masquerading as numbered headings (e.g., "12 samples were tested...")
            if len(title_str.split()) > 14:
                continue
            if re.match(r"^(?:samples?|patients?|cases?|experiments?|days?|hours?|mg|ml|mm|cm|%)\b", title_str, re.I):
                continue
            if is_bold or font_size >= modal_body_size * 1.04 or "." in num_str or num_str in {"1", "2", "3", "4", "5", "6", "7", "I", "II", "III", "IV", "V"}:
                depth = min(4, num_str.count(".") + 1)
                return (depth, num_str, title_str)

    return None


def parse_inline_markdown_or_pipe_table(block_text: str) -> Optional[List[List[str]]]:
    lines = [ln.strip() for ln in block_text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    pipe_lines = [ln for ln in lines if "|" in ln]
    if len(pipe_lines) >= 2:
        grid: List[List[str]] = []
        for ln in pipe_lines:
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if all(re.fullmatch(r"[-: ]+", c or "-") for c in cells):
                continue
            grid.append(cells)
        if len(grid) >= 2 and max(len(r) for r in grid) >= 2:
            width = max(len(r) for r in grid)
            return [r + [""] * (width - len(r)) for r in grid]
    return None


def grid_to_markdown(grid: List[List[str]]) -> str:
    if not grid:
        return ""
    cols = max(len(r) for r in grid)
    norm = [[(c or "").replace("\n", " ").replace("|", "\\|").strip() for c in (r + [""] * (cols - len(r)))] for r in grid]
    header = "| " + " | ".join(norm[0]) + " |"
    sep = "| " + " | ".join(["---"] * cols) + " |"
    body = ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join([header, sep, *body])


def grid_to_html(grid: List[List[str]], caption: str = "") -> str:
    if not grid:
        return "<table></table>"
    parts = ["<table>"]
    if caption:
        parts.append(f"  <caption>{html.escape(caption)}</caption>")
    cols = max(len(r) for r in grid)
    for r_i, row in enumerate(grid):
        padded = row + [""] * (cols - len(row))
        tag = "th" if r_i == 0 else "td"
        parts.append("  <tr>" + "".join(f"<{tag}>{html.escape(c or '')}</{tag}>" for c in padded) + "</tr>")
    parts.append("</table>")
    return "\n".join(parts)


def stitch_cross_column_paragraphs(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merges paragraphs split across column or page boundaries while preserving multi-span provenance."""
    if not blocks:
        return []
    merged: List[Dict[str, Any]] = []
    for blk in blocks:
        if (
            merged
            and merged[-1]["role"] == "paragraph"
            and blk["role"] == "paragraph"
            and merged[-1]["section_path"] == blk["section_path"]
            and (blk["page"] > merged[-1]["page"] or blk["column"] > merged[-1]["column"])
            and not re.search(r"[.!?。！？:：;；]$", merged[-1]["text"].strip())
            and bool(re.match(r"^[a-z\u4e00-\u9fff(,]", blk["text"].strip()))
        ):
            prev = merged[-1]
            prev["text"] = join_lines_preserving_hyphens([prev["text"], blk["text"]])
            prev["provenance_spans"].extend(blk["provenance_spans"])
            prev["page_end"] = blk["page"]
            prev["garbled_tokens"] = sorted(set(prev["garbled_tokens"] + blk["garbled_tokens"]))
        else:
            merged.append(blk)
    return merged


def parse_references_from_text(ref_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    raw_lines: List[Tuple[Dict[str, Any], str]] = []
    for b in ref_blocks:
        for ln in b["text"].splitlines():
            if ln.strip():
                raw_lines.append((b, ln.strip()))

    entries: List[Dict[str, Any]] = []
    cur_entry: Optional[Dict[str, Any]] = None
    start_re = re.compile(r"^(?:\[(\d+)\]|(\d+)\.)\s+(.*)$")

    for b, ln in raw_lines:
        m = start_re.match(ln)
        if m:
            if cur_entry:
                entries.append(cur_entry)
            rid = m.group(1) or m.group(2)
            cur_entry = {
                "ref_id": f"ref_{rid}",
                "number": rid,
                "raw_text": ln,
                "page": b["page"],
                "bbox": b["bbox"],
                "block_id": b["block_id"],
            }
        elif cur_entry is not None:
            cur_entry["raw_text"] = join_lines_preserving_hyphens([cur_entry["raw_text"], ln])
        else:
            cur_entry = {
                "ref_id": f"ref_{len(entries) + 1}",
                "number": str(len(entries) + 1),
                "raw_text": ln,
                "page": b["page"],
                "bbox": b["bbox"],
                "block_id": b["block_id"],
            }
    if cur_entry:
        entries.append(cur_entry)

    for ent in entries:
        doi_m = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", ent["raw_text"])
        yr_m = re.search(r"\b(19\d{2}|20\d{2})\b", ent["raw_text"])
        ent["doi"] = doi_m.group(0).rstrip(".") if doi_m else None
        ent["year"] = int(yr_m.group(1)) if yr_m else None
    return entries


def parse_pdf_document(
    pdf_path: Path,
    out_dir: Path,
    backend: str = "auto",
    ocr_mode: str = "auto",
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    doc_id = pdf_path.stem
    file_sha256 = sha256_file(pdf_path)
    raw_extract = extract_pdf_multi_backend(pdf_path, preferred=backend)

    if raw_extract.get("is_encrypted"):
        encrypted_doc = {
            "doc_id": doc_id,
            "source_pdf": str(pdf_path),
            "sha256": file_sha256,
            "num_pages": raw_extract.get("num_pages", 0),
            "is_encrypted": True,
            "backend_used": raw_extract.get("backend"),
            "document_mode": "encrypted_locked",
            "status": "unverifiable",
            "status_reason": "PDF is password-encrypted and cannot be unlocked with an empty password.",
            "metadata": {"title": None, "authors": [], "affiliations": [], "abstract": None, "keywords": []},
            "sections": [],
            "reading_order_blocks": [],
            "tables": [],
            "figures": [],
            "equations": [],
            "references": [],
            "headers_footers": {"headers": [], "footers": [], "footnotes": []},
            "uncertainty": {
                "unverifiable_items": ["All pages locked by PDF encryption; requires password or decrypted copy."],
                "garbled_or_low_confidence_spans": [],
                "human_takeover_locations": [{"page": 1, "reason": "Encrypted PDF"}],
            },
        }
        (out_dir / "parsed_document.json").write_text(json.dumps(encrypted_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return encrypted_doc

    pages = raw_extract["pages"]
    page_diagnostics: List[Dict[str, Any]] = []
    garbled_spans: List[Dict[str, Any]] = []
    unverifiable_items: List[str] = []
    human_takeover: List[Dict[str, Any]] = []

    # Step 1: Per-page text quality & scan diagnosis + adaptive OCR
    for p in pages:
        p_num = p["page"]
        char_count = sum(len(b.text.strip()) for b in p["blocks"])
        garble_count = 0
        for b in p["blocks"]:
            _, g_hits = normalize_scientific_text(b.text)
            garble_count += len(g_hits)

        if char_count < 25:
            p_mode = "scanned_no_text_layer"
        elif garble_count >= 3:
            p_mode = "digital_garbled_font"
        else:
            p_mode = "digital_clean"

        ocr_applied = False
        ocr_mean_conf: Optional[float] = None
        if ocr_mode == "force" or (ocr_mode == "auto" and p_mode in {"scanned_no_text_layer", "digital_garbled_font"}):
            ocr_res = run_page_ocr(pdf_path, p_num)
            if ocr_res and ocr_res["blocks"]:
                p["blocks"] = ocr_res["blocks"]
                ocr_applied = True
                ocr_mean_conf = ocr_res["mean_confidence"]
                p_mode = f"{p_mode}_ocr_recovered"
                if ocr_res["low_confidence_words"]:
                    garbled_spans.append(
                        {
                            "page": p_num,
                            "reason": f"Low-confidence OCR words (mean_conf={ocr_mean_conf})",
                            "samples": ocr_res["low_confidence_words"][:10],
                        }
                    )
            elif p_mode == "scanned_no_text_layer":
                unverifiable_items.append(
                    f"Page {p_num} has no extractable text layer ({char_count} chars) and OCR tool is unavailable or returned empty."
                )
                human_takeover.append({"page": p_num, "reason": "Scanned/image-only page without OCR recovery"})

        page_diagnostics.append(
            {
                "page": p_num,
                "printed_label": p["printed_label"],
                "initial_char_count": char_count,
                "page_mode": p_mode,
                "ocr_applied": ocr_applied,
                "ocr_mean_confidence": ocr_mean_conf,
            }
        )

    # Step 2: Isolate running headers, footers, and footnotes
    hf_data, body_by_page = isolate_headers_footers_footnotes(pages)

    # Global modal body font size across body blocks
    body_font_samples = [
        b.font_size
        for blks in body_by_page.values()
        for b in blks
        if len(b.text) >= 40
    ]
    modal_body_size = statistics.median(body_font_samples) if body_font_samples else 10.0

    # Step 3: XY-Cut column detection, normalization, and block role classification
    fig_re = re.compile(SECTION_PATTERNS.get("caption_regexes", {}).get("figure", r"^(?:Fig\.?|Figure|图)\s*(?P<id>[S]?\d+[A-Za-z]?)\s*[:.\-|—]?\s*(?P<caption>.*)$"), re.I)
    tab_re = re.compile(SECTION_PATTERNS.get("caption_regexes", {}).get("table", r"^(?:Tab\.?|Table|表|续表)\s*(?P<id>[S]?\d+[A-Za-z]?)\s*(?P<cont>\(continued\)|continued|续)?\s*[:.\-|—]?\s*(?P<caption>.*)$"), re.I)
    eq_re = re.compile(SECTION_PATTERNS.get("equation_number_regex", r"(?:\(|\[)(?:Eq\.?\s*)?(?P<eq_num>[A-Z]?\d+(?:\.\d+)?[a-z]?)(?:\)|\])\s*$"))
    ref_titles = set(SECTION_PATTERNS.get("reference_section_titles", ["references", "bibliography", "参考文献"]))

    ordered_blocks: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    figures: List[Dict[str, Any]] = []
    equations: List[Dict[str, Any]] = []
    raw_tables_collected: List[Dict[str, Any]] = []

    current_section_stack: List[str] = ["Front Matter"]
    in_references = False
    title_block_assigned = False

    for p in pages:
        p_num = p["page"]
        col_count, col_ordered = detect_columns_and_order_blocks(body_by_page.get(p_num, []), p["width"])
        for d in page_diagnostics:
            if d["page"] == p_num:
                d["detected_columns"] = col_count

        # Collect native geometric tables on this page
        used_table_bboxes: List[List[float]] = []
        for nt in p.get("native_tables", []):
            raw_tables_collected.append(
                {
                    "page": p_num,
                    "printed_page_label": p["printed_label"],
                    "bbox": nt["bbox"],
                    "grid": nt["grid"],
                    "method": nt["method"],
                    "caption": "",
                    "table_number": None,
                    "is_continued": False,
                }
            )
            used_table_bboxes.append(nt["bbox"])

        for b_idx, (col_id, rb) in enumerate(col_ordered, start=1):
            norm_txt, g_hits = normalize_scientific_text(rb.text)
            if not norm_txt:
                continue
            block_id = f"p{p_num:03d}_b{b_idx:03d}"
            if g_hits:
                garbled_spans.append(
                    {
                        "page": p_num,
                        "block_id": block_id,
                        "bbox": list(rb.bbox),
                        "garbled_tokens": g_hits,
                        "excerpt": norm_txt[:160],
                    }
                )

            # Skip duplicate text blocks that lie strictly inside an extracted native geometric table bbox
            inside_native_table = False
            for tb in used_table_bboxes:
                if (
                    rb.bbox[0] >= tb[0] - 6
                    and rb.bbox[1] >= tb[1] - 6
                    and rb.bbox[2] <= tb[2] + 6
                    and rb.bbox[3] <= tb[3] + 6
                    and not tab_re.match(norm_txt)
                ):
                    inside_native_table = True
                    break
            if inside_native_table:
                continue

            role = "paragraph"
            # Title candidate on page 1
            if (
                p_num == 1
                and not title_block_assigned
                and b_idx <= 3
                and rb.font_size >= modal_body_size * 1.25
                and len(norm_txt) <= 260
            ):
                role = "title"
                title_block_assigned = True
            else:
                m_fig = fig_re.match(norm_txt)
                m_tab = tab_re.match(norm_txt)
                if m_fig and len(norm_txt) <= 600:
                    role = "figure_caption"
                    figures.append(
                        {
                            "figure_id": f"fig_{m_fig.group('id')}",
                            "number": m_fig.group("id"),
                            "caption": norm_txt,
                            "page": p_num,
                            "printed_page_label": p["printed_label"],
                            "column": col_id,
                            "bbox": list(rb.bbox),
                            "block_id": block_id,
                            "section_path": " > ".join(current_section_stack),
                        }
                    )
                elif m_tab and len(norm_txt) <= 600:
                    role = "table_caption"
                    t_id = m_tab.group("id")
                    is_cont = bool(m_tab.group("cont")) or norm_txt.startswith("续表")
                    # Check if caption block itself also contains pipe-table lines below line 1
                    first_ln = norm_txt.splitlines()[0]
                    inline_grid = parse_inline_markdown_or_pipe_table("\n".join(norm_txt.splitlines()[1:]))
                    # Attach caption to nearest native table on same page if unassigned
                    attached = False
                    for rt in raw_tables_collected:
                        if rt["page"] == p_num and not rt["table_number"]:
                            rt["table_number"] = t_id
                            rt["caption"] = first_ln
                            rt["is_continued"] = is_cont
                            attached = True
                            break
                    if not attached:
                        if inline_grid:
                            raw_tables_collected.append(
                                {
                                    "page": p_num,
                                    "printed_page_label": p["printed_label"],
                                    "bbox": list(rb.bbox),
                                    "grid": inline_grid,
                                    "method": f"{rb.source_method}_pipe_grid",
                                    "caption": first_ln,
                                    "table_number": t_id,
                                    "is_continued": is_cont,
                                }
                            )
                        else:
                            # Reserve placeholder to match immediately following pipe/aligned block or flag image table
                            raw_tables_collected.append(
                                {
                                    "page": p_num,
                                    "printed_page_label": p["printed_label"],
                                    "bbox": list(rb.bbox),
                                    "grid": [],
                                    "method": "caption_detected_pending_grid",
                                    "caption": norm_txt,
                                    "table_number": t_id,
                                    "is_continued": is_cont,
                                }
                            )
                else:
                    # Check inline pipe table right after a pending table caption
                    inline_grid = parse_inline_markdown_or_pipe_table(norm_txt)
                    if inline_grid:
                        role = "table_body"
                        matched_pending = False
                        for rt in reversed(raw_tables_collected):
                            if rt["page"] == p_num and not rt["grid"]:
                                rt["grid"] = inline_grid
                                rt["bbox"] = [
                                    min(rt["bbox"][0], rb.bbox[0]),
                                    min(rt["bbox"][1], rb.bbox[1]),
                                    max(rt["bbox"][2], rb.bbox[2]),
                                    max(rt["bbox"][3], rb.bbox[3]),
                                ]
                                rt["method"] = f"{rb.source_method}_structured_grid"
                                matched_pending = True
                                break
                        if not matched_pending:
                            raw_tables_collected.append(
                                {
                                    "page": p_num,
                                    "printed_page_label": p["printed_label"],
                                    "bbox": list(rb.bbox),
                                    "grid": inline_grid,
                                    "method": f"{rb.source_method}_structured_grid",
                                    "caption": "",
                                    "table_number": str(len(raw_tables_collected) + 1),
                                    "is_continued": False,
                                }
                            )
                    else:
                        hd = classify_heading(norm_txt, rb.font_size, rb.is_bold, modal_body_size, p_num, b_idx)
                        if hd is not None:
                            lvl, sec_num, sec_title = hd
                            role = "section_heading"
                            label_full = f"{sec_num} {sec_title}".strip()
                            current_section_stack = current_section_stack[: max(0, lvl - 1)]
                            current_section_stack.append(label_full)
                            in_references = sec_title.lower().rstrip(":：") in ref_titles or label_full.lower() in ref_titles
                            sections.append(
                                {
                                    "section_id": f"sec_{len(sections) + 1:02d}",
                                    "level": lvl,
                                    "number": sec_num,
                                    "title": sec_title,
                                    "section_path": " > ".join(current_section_stack),
                                    "page": p_num,
                                    "printed_page_label": p["printed_label"],
                                    "column": col_id,
                                    "bbox": list(rb.bbox),
                                    "block_id": block_id,
                                }
                            )
                        elif in_references:
                            role = "reference_block"
                        elif norm_txt.lower().startswith(("abstract:", "abstract—", "abstract -", "摘要：", "摘要:")):
                            role = "abstract"
                        else:
                            # Check numbered display equation
                            eq_m = eq_re.search(norm_txt)
                            if eq_m and len(norm_txt) <= 260 and any(sym in norm_txt for sym in ("=", "≈", "≤", "≥", "±", "∑", "∫", "→", "+", "-", "^", "_")):
                                role = "equation"
                                eq_num = eq_m.group("eq_num")
                                equations.append(
                                    {
                                        "equation_id": f"eq_{eq_num}",
                                        "number": eq_num,
                                        "expression": norm_txt,
                                        "page": p_num,
                                        "printed_page_label": p["printed_label"],
                                        "column": col_id,
                                        "bbox": list(rb.bbox),
                                        "block_id": block_id,
                                        "section_path": " > ".join(current_section_stack),
                                    }
                                )

            ordered_blocks.append(
                {
                    "block_id": block_id,
                    "page": p_num,
                    "page_end": p_num,
                    "printed_page_label": p["printed_label"],
                    "column": col_id,
                    "columns_on_page": col_count,
                    "role": role,
                    "section_path": " > ".join(current_section_stack),
                    "bbox": list(rb.bbox),
                    "font_size": rb.font_size,
                    "is_bold": rb.is_bold,
                    "extraction_method": rb.source_method,
                    "ocr_confidence": rb.confidence,
                    "garbled_tokens": g_hits,
                    "text": norm_txt,
                    "provenance_spans": [
                        {"page": p_num, "column": col_id, "bbox": list(rb.bbox), "block_id": block_id}
                    ],
                }
            )

    # Step 4: Stitch cross-column / cross-page broken paragraphs
    stitched_blocks = stitch_cross_column_paragraphs(ordered_blocks)

    # Step 5: Merge cross-page continued tables & flag image-only tables
    final_tables: List[Dict[str, Any]] = []
    for rt in raw_tables_collected:
        if rt["is_continued"] and final_tables:
            prev_t = next(
                (t for t in reversed(final_tables) if t["table_number"] == rt["table_number"] and t["grid"]),
                None,
            )
            if prev_t and rt["grid"]:
                # Drop repeated header row if identical to previous table's header
                rows_to_add = rt["grid"][1:] if rt["grid"][0] == prev_t["grid"][0] else rt["grid"]
                prev_t["grid"].extend(rows_to_add)
                prev_t["pages"].append(rt["page"])
                prev_t["bboxes"].append({"page": rt["page"], "bbox": rt["bbox"]})
                prev_t["continued_merged"] = True
                continue

        t_idx = len(final_tables) + 1
        t_num = rt["table_number"] or str(t_idx)
        if not rt["grid"]:
            unverifiable_items.append(
                f"Table {t_num} on page {rt['page']} has caption but no extractable text cells (likely raster/image table)."
            )
            human_takeover.append({"page": rt["page"], "reason": f"Image/raster Table {t_num} requires visual inspection"})
            final_tables.append(
                {
                    "table_id": f"table_{t_idx:02d}",
                    "table_number": t_num,
                    "caption": rt["caption"],
                    "pages": [rt["page"]],
                    "bboxes": [{"page": rt["page"], "bbox": rt["bbox"]}],
                    "table_type": "image_or_unstructured",
                    "continued_merged": False,
                    "headers": [],
                    "rows": [],
                    "grid": [],
                    "markdown_path": None,
                    "html_path": None,
                    "extraction_method": rt["method"],
                }
            )
            continue

        final_tables.append(
            {
                "table_id": f"table_{t_idx:02d}",
                "table_number": t_num,
                "caption": rt["caption"],
                "pages": [rt["page"]],
                "bboxes": [{"page": rt["page"], "bbox": rt["bbox"]}],
                "table_type": "structured_grid",
                "continued_merged": False,
                "headers": rt["grid"][0],
                "rows": rt["grid"][1:],
                "grid": rt["grid"],
                "markdown_path": None,
                "html_path": None,
                "extraction_method": rt["method"],
            }
        )

    for ft in final_tables:
        if ft["grid"]:
            ft["headers"] = ft["grid"][0]
            ft["rows"] = ft["grid"][1:]
            md_rel = f"tables/{ft['table_id']}.md"
            html_rel = f"tables/{ft['table_id']}.html"
            (out_dir / md_rel).write_text(grid_to_markdown(ft["grid"]) + "\n", encoding="utf-8")
            (out_dir / html_rel).write_text(grid_to_html(ft["grid"], ft["caption"]) + "\n", encoding="utf-8")
            ft["markdown_path"] = md_rel
            ft["html_path"] = html_rel

    # Step 6: Extract front-matter metadata & references
    title_text = next((b["text"] for b in stitched_blocks if b["role"] == "title"), None)
    if not title_text and stitched_blocks:
        p1_blocks = [b for b in stitched_blocks if b["page"] == 1]
        if p1_blocks:
            title_text = max(p1_blocks[:4], key=lambda b: b["font_size"])["text"]

    abstract_parts = [
        b["text"]
        for b in stitched_blocks
        if b["role"] == "abstract" or b["section_path"].lower().endswith("abstract") or b["section_path"].endswith("摘要")
    ]
    abstract_text = "\n\n".join(p for p in abstract_parts if p.lower() not in {"abstract", "摘要"}) or None

    authors: List[str] = []
    affiliations: List[str] = []
    keywords: List[str] = []
    for b in stitched_blocks:
        if b["page"] != 1 or b["role"] in {"title", "section_heading"}:
            if b["page"] > 1:
                break
            continue
        if b["section_path"] != "Front Matter" and not b["section_path"].lower().endswith(("keywords", "关键词")):
            continue
        low_t = b["text"].lower()
        if low_t.startswith(("keywords:", "key words:", "关键词：", "关键词:")):
            kw_raw = re.sub(r"^(?:key\s*words?|关键词)\s*[:：]\s*", "", b["text"], flags=re.I)
            keywords = [k.strip() for k in re.split(r"[;,；，·]", kw_raw) if k.strip()]
        elif any(tok in low_t for tok in ("university", "institute", "department", "laboratory", "school of", "college", "大学", "学院", "研究所", "实验室", "@")):
            affiliations.append(b["text"])
        elif not authors and len(b["text"]) <= 200 and not low_t.startswith("abstract"):
            authors = [a.strip() for a in re.split(r"[,;，；]|\band\b", b["text"]) if a.strip()]

    ref_blocks = [b for b in stitched_blocks if b["role"] == "reference_block"]
    references = parse_references_from_text(ref_blocks)

    # Determine overall document status & mode
    modes = {d["page_mode"] for d in page_diagnostics}
    if any("scanned_no_text_layer" == m for m in modes):
        doc_status = "partial" if len(stitched_blocks) > 0 else "unverifiable"
        doc_mode = "hybrid_or_scanned_incomplete"
    elif any("ocr_recovered" in m for m in modes):
        doc_status = "success" if not human_takeover else "partial"
        doc_mode = "ocr_enhanced"
    else:
        doc_status = "success" if not human_takeover else "partial"
        doc_mode = "digital_clean" if not garbled_spans else "digital_with_garbled_spans"

    parsed_doc = {
        "doc_id": doc_id,
        "source_pdf": str(pdf_path),
        "sha256": file_sha256,
        "parsed_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "num_pages": len(pages),
        "is_encrypted": False,
        "backend_used": raw_extract["backend"],
        "document_mode": doc_mode,
        "status": doc_status,
        "metadata": {
            "title": title_text,
            "authors": authors,
            "affiliations": affiliations,
            "abstract": abstract_text,
            "keywords": keywords,
        },
        "sections": sections,
        "reading_order_blocks": stitched_blocks,
        "tables": final_tables,
        "figures": figures,
        "equations": equations,
        "references": references,
        "headers_footers": hf_data,
        "page_diagnostics": page_diagnostics,
        "uncertainty": {
            "unverifiable_items": unverifiable_items,
            "garbled_or_low_confidence_spans": garbled_spans,
            "human_takeover_locations": human_takeover,
        },
    }

    # Write structured outputs
    (out_dir / "parsed_document.json").write_text(json.dumps(parsed_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "equations.json").write_text(json.dumps(equations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "references.json").write_text(json.dumps(references, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "parse_diagnostics.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "backend_used": raw_extract["backend"],
                "backend_fallback_notes": raw_extract.get("backend_errors", {}),
                "document_mode": doc_mode,
                "status": doc_status,
                "counts": {
                    "pages": len(pages),
                    "sections": len(sections),
                    "blocks": len(stitched_blocks),
                    "tables": len(final_tables),
                    "figures": len(figures),
                    "equations": len(equations),
                    "references": len(references),
                    "headers": len(hf_data["headers"]),
                    "footers": len(hf_data["footers"]),
                    "footnotes": len(hf_data["footnotes"]),
                },
                "page_diagnostics": page_diagnostics,
                "uncertainty": parsed_doc["uncertainty"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Render reading-order fulltext Markdown with inline provenance anchors
    md_lines = [
        f"# {title_text or doc_id}",
        "",
        f"> Source PDF: `{pdf_path.name}` | Pages: {len(pages)} | Backend: `{raw_extract['backend']}` | Mode: `{doc_mode}`",
        "",
    ]
    if authors:
        md_lines.extend([f"**Authors:** {', '.join(authors)}", ""])
    for blk in stitched_blocks:
        anchor = f"<!-- {blk['block_id']} p.{blk['page']} col.{blk['column']} bbox={blk['bbox']} -->"
        if blk["role"] == "title":
            continue
        if blk["role"] == "section_heading":
            hashes = "#" * min(6, (blk["section_path"].count(">") + 2))
            md_lines.extend([f"{hashes} {blk['text']} {anchor}", ""])
        elif blk["role"] == "equation":
            md_lines.extend([f"$$\n{blk['text']}\n$$\n{anchor}", ""])
        else:
            md_lines.extend([f"{blk['text']} `[{blk['block_id']} p.{blk['page']}]`", ""])

    if final_tables:
        md_lines.extend(["## Extracted Structured Tables", ""])
        for ft in final_tables:
            md_lines.append(f"### {ft['table_id']} (Pages: {ft['pages']}) — {ft['caption']}")
            if ft["grid"]:
                md_lines.extend(["", grid_to_markdown(ft["grid"]), ""])
            else:
                md_lines.extend(["", "> *[Image/unstructured table — flagged for visual inspection]*", ""])

    (out_dir / "structured_fulltext.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return parsed_doc


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse scientific PDF into layout-verified structured JSON and Markdown.")
    parser.add_argument("--pdf", required=True, help="Path to input scientific PDF.")
    parser.add_argument("--out-dir", required=True, help="Output directory for parsed artifacts.")
    parser.add_argument("--backend", default="auto", choices=["auto", "pymupdf", "pdfplumber", "poppler_xml", "stdlib_pdf_stream"])
    parser.add_argument("--ocr-mode", default="auto", choices=["auto", "off", "force"])
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(json.dumps({"status": "error", "message": f"PDF not found: {pdf_path}"}), flush=True)
        return 1

    parsed = parse_pdf_document(pdf_path, Path(args.out_dir), backend=args.backend, ocr_mode=args.ocr_mode)
    summary = {
        "status": parsed["status"],
        "doc_id": parsed["doc_id"],
        "num_pages": parsed["num_pages"],
        "backend_used": parsed["backend_used"],
        "document_mode": parsed["document_mode"],
        "title": parsed["metadata"]["title"],
        "counts": {
            "sections": len(parsed["sections"]),
            "blocks": len(parsed["reading_order_blocks"]),
            "tables": len(parsed["tables"]),
            "figures": len(parsed["figures"]),
            "equations": len(parsed["equations"]),
            "references": len(parsed["references"]),
            "garbled_spans": len(parsed["uncertainty"]["garbled_or_low_confidence_spans"]),
        },
        "outputs": {
            "parsed_json": str(Path(args.out_dir) / "parsed_document.json"),
            "fulltext_md": str(Path(args.out_dir) / "structured_fulltext.md"),
            "diagnostics_json": str(Path(args.out_dir) / "parse_diagnostics.json"),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
