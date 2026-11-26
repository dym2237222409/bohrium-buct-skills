#!/usr/bin/env python3
"""
parse_bib_and_manuscript.py — Universal manuscript & multi-format bibliography parser
(BibTeX, RIS, EndNote .enw, CNKI export, CSL-JSON/JSON, plain-text lists, Markdown/LaTeX/DOCX)
for scipdf-citation-manager. Zero external dependencies (Python 3.9+ stdlib only).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RE_DOI = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
RE_ARXIV = re.compile(r"(?:arXiv[:\s]*)(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
RE_BRACKET_CITE = re.compile(r"\^?\[\s*\d+(?:\s*[-–—,]\s*\d+)*\s*\]")
RE_LATEX_CITE = re.compile(r"\\(?:cite|citep|citet|parencite|textcite)(?:\[[^\]]*\])*\{[^}]+\}")
RE_AUTHORYEAR_CITE = re.compile(
    r"\((?:[A-Z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff\-]+(?:\s*(?:and|&|和)\s*[A-Z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff\-]+|\s*(?:et\s+al\.|等))?[,，]\s*(?:19|20)\d{2}[a-z]?(?:\s*[;；]\s*)?)+\)"
)
RE_CAPTION = re.compile(r"^(?:Fig(?:ure|\.)?|Table|Tab\.|图|表)\s*\d+", re.IGNORECASE)
RE_CJK = re.compile(r"[\u4e00-\u9fff]")


def read_text_safe(path: Path) -> Tuple[str, List[str]]:
    warns: List[str] = []
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc not in ("utf-8-sig", "utf-8"):
                warns.append(f"Decoded {path.name} using fallback encoding {enc}.")
            return text, warns
        except UnicodeDecodeError:
            continue
    warns.append(f"Encoding errors detected in {path.name}.")
    return raw.decode("utf-8", errors="replace"), warns


def read_docx_text(path: Path) -> Tuple[str, List[str]]:
    warns: List[str] = []
    paras: List[str] = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for p in root.findall(".//w:p", ns):
            line = "".join(t.text for t in p.findall(".//w:t", ns) if t.text).strip()
            if line:
                paras.append(line)
    except Exception as exc:
        warns.append(f"Failed to read DOCX {path.name}: {exc}")
    return "\n\n".join(paras), warns


def normalize_doi(raw: Optional[str]) -> str:
    if not raw:
        return ""
    m = RE_DOI.search(raw.strip())
    if not m:
        return ""
    return m.group(1).rstrip(".,;)").lower()


def normalize_arxiv(raw: Optional[str]) -> str:
    if not raw:
        return ""
    m = RE_ARXIV.search(raw)
    return m.group(1).lower() if m else ""


def clean_bib_val(val: str) -> str:
    v = val.strip().rstrip(",").strip()
    if (v.startswith("{") and v.endswith("}")) or (v.startswith('"') and v.endswith('"')):
        v = v[1:-1].strip()
    v = re.sub(r"\s+", " ", v)
    return v


def split_authors(raw: str) -> List[str]:
    if not raw:
        return []
    cleaned = raw.strip()
    if " and " in cleaned or " AND " in cleaned:
        parts = re.split(r"\s+(?:and|AND)\s+", cleaned)
    elif ";" in cleaned or "；" in cleaned:
        parts = re.split(r"[;；]", cleaned)
    elif "、" in cleaned:
        parts = cleaned.split("、")
    else:
        # Handle comma-separated Chinese authors or single "Last, First"
        if RE_CJK.search(cleaned):
            parts = [p.strip() for p in re.split(r"[,，]", cleaned) if p.strip()]
        else:
            parts = [cleaned]
    return [p.strip().strip("{}") for p in parts if p.strip()]


def build_canonical_raw_record(
    idx: int,
    source_file: str,
    source_format: str,
    cite_key: str,
    entry_type: str,
    authors: List[str],
    title: str,
    year: str,
    venue: str,
    volume: str = "",
    issue: str = "",
    pages: str = "",
    doi: str = "",
    arxiv_id: str = "",
    url: str = "",
    abstract: str = "",
    title_zh: str = "",
    title_en: str = "",
    raw_text: str = "",
) -> Dict[str, Any]:
    doi_norm = normalize_doi(doi or url or raw_text)
    arxiv_norm = normalize_arxiv(arxiv_id or url or venue or raw_text)
    etype = (entry_type or "article").lower().strip()
    if arxiv_norm and etype in ("misc", "online", "article", "") and ("arxiv" in venue.lower() or not venue):
        etype = "preprint"

    is_zh = bool(RE_CJK.search(title or ""))
    if is_zh and not title_zh:
        title_zh = title
    if not is_zh and title and not title_en:
        title_en = title

    missing = []
    for fld, val in (("authors", authors), ("title", title), ("year", year), ("venue", venue)):
        if not val:
            missing.append(fld)

    return {
        "raw_id": f"RAW{idx:03d}",
        "cite_key": cite_key or f"ref_{idx:03d}",
        "source_file": source_file,
        "source_format": source_format,
        "entry_type": etype,
        "language": "zh" if is_zh else "en",
        "authors": authors,
        "title": title.strip(),
        "title_zh": title_zh.strip(),
        "title_en": title_en.strip(),
        "year": re.search(r"(19|20)\d{2}", str(year)).group(0) if re.search(r"(19|20)\d{2}", str(year or "")) else "",
        "venue": venue.strip(),
        "volume": str(volume or "").strip(),
        "issue": str(issue or "").strip(),
        "pages": str(pages or "").strip().replace("--", "-"),
        "doi": doi_norm,
        "arxiv_id": arxiv_norm,
        "url": str(url or "").strip(),
        "abstract": str(abstract or "").strip(),
        "missing_fields": missing,
    }


def parse_bibtex(text: str, source_file: str, start_idx: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    # Match @type{key, ... } entries using brace depth tracking
    i = 0
    n = len(text)
    idx = start_idx
    while i < n:
        at = text.find("@", i)
        if at == -1:
            break
        m = re.match(r"@([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,", text[at:])
        if not m:
            i = at + 1
            continue
        etype = m.group(1).lower()
        key = m.group(2).strip()
        if etype in ("comment", "preamble", "string"):
            i = at + m.end()
            continue
        # Find matching closing brace
        depth = 1
        pos = at + m.end()
        body_start = pos
        while pos < n and depth > 0:
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
            pos += 1
        body = text[body_start : pos - 1]
        i = pos

        fields: Dict[str, str] = {}
        # Parse key = {value} or "value" or bareword
        f_pos = 0
        while f_pos < len(body):
            fm = re.match(r"\s*([A-Za-z0-9_\-]+)\s*=\s*", body[f_pos:])
            if not fm:
                f_pos += 1
                continue
            fname = fm.group(1).lower()
            v_start = f_pos + fm.end()
            if v_start >= len(body):
                break
            ch = body[v_start]
            if ch == "{":
                d = 1
                p = v_start + 1
                while p < len(body) and d > 0:
                    if body[p] == "{":
                        d += 1
                    elif body[p] == "}":
                        d -= 1
                    p += 1
                fval = body[v_start + 1 : p - 1]
                f_pos = p
            elif ch == '"':
                p = v_start + 1
                while p < len(body) and body[p] != '"':
                    p += 1
                fval = body[v_start + 1 : p]
                f_pos = p + 1
            else:
                p = v_start
                while p < len(body) and body[p] not in ",\n\r":
                    p += 1
                fval = body[v_start:p]
                f_pos = p + 1
            fields[fname] = clean_bib_val(fval)

        idx += 1
        records.append(build_canonical_raw_record(
            idx=idx,
            source_file=source_file,
            source_format="bibtex",
            cite_key=key,
            entry_type=etype,
            authors=split_authors(fields.get("author", "")),
            title=fields.get("title", ""),
            year=fields.get("year", ""),
            venue=fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or fields.get("school") or "",
            volume=fields.get("volume", ""),
            issue=fields.get("number", ""),
            pages=fields.get("pages", ""),
            doi=fields.get("doi", ""),
            arxiv_id=fields.get("eprint", ""),
            url=fields.get("url", ""),
            abstract=fields.get("abstract", ""),
            raw_text=body,
        ))
    return records


def parse_ris(text: str, source_file: str, start_idx: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    idx = start_idx
    cur: Dict[str, Any] = {}
    authors: List[str] = []

    def flush() -> None:
        nonlocal idx, cur, authors
        if not cur and not authors:
            return
        idx += 1
        ty_map = {"JOUR": "article", "CONF": "inproceedings", "CPAPER": "inproceedings", "BOOK": "book", "THES": "phdthesis", "ELEC": "online", "GEN": "misc"}
        etype = ty_map.get(cur.get("TY", "JOUR").upper(), "article")
        records.append(build_canonical_raw_record(
            idx=idx,
            source_file=source_file,
            source_format="ris",
            cite_key=cur.get("ID", f"ris_{idx:03d}"),
            entry_type=etype,
            authors=authors,
            title=cur.get("TI") or cur.get("T1") or "",
            year=cur.get("PY") or cur.get("Y1") or "",
            venue=cur.get("JO") or cur.get("JF") or cur.get("T2") or cur.get("PB") or "",
            volume=cur.get("VL", ""),
            issue=cur.get("IS", ""),
            pages=f"{cur.get('SP', '')}-{cur.get('EP', '')}".strip("-") if cur.get("SP") else "",
            doi=cur.get("DO", ""),
            url=cur.get("UR", ""),
            abstract=cur.get("AB", ""),
            raw_text=" ".join(f"{k}:{v}" for k, v in cur.items()),
        ))
        cur = {}
        authors = []

    for line in text.splitlines():
        m = re.match(r"^([A-Z0-9]{2})\s*-\s*(.*)$", line.strip())
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "ER":
            flush()
        elif tag in ("AU", "A1"):
            if val:
                authors.append(val)
        else:
            cur[tag] = (cur[tag] + " " + val).strip() if tag in cur and tag in ("AB", "TI", "T1") else val
    flush()
    return records


def parse_endnote_or_cnki(text: str, source_file: str, start_idx: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    idx = start_idx
    blocks = re.split(r"\n\s*\n", text.strip())
    for blk in blocks:
        lines = [l.strip() for l in blk.splitlines() if l.strip()]
        if not lines:
            continue
        is_enw = any(l.startswith("%") for l in lines)
        is_cnki = any(re.match(r"^(?:Title|Title-题名|Author|Author-作者|Source|Source-文献来源|Year|Year-年|PubTime|DOI|RT|A1|T1)[:：\s]", l) for l in lines)
        if not (is_enw or is_cnki):
            continue

        authors: List[str] = []
        fields: Dict[str, str] = {}
        for l in lines:
            if is_enw and l.startswith("%") and len(l) >= 3:
                tag, val = l[:2], l[2:].strip()
                if tag == "%A":
                    authors.append(val)
                else:
                    fields[tag] = val
            else:
                m = re.match(r"^([A-Za-z0-9_\-\u4e00-\u9fff]+)\s*[:：]\s*(.+)$", l)
                if m:
                    k = m.group(1).strip().lower()
                    v = m.group(2).strip()
                    if any(x in k for x in ("author", "作者", "a1")):
                        authors.extend(split_authors(v))
                    else:
                        fields[k] = v

        title = fields.get("%T") or fields.get("title") or fields.get("title-题名") or fields.get("t1") or ""
        if not title:
            continue
        idx += 1
        etype_raw = fields.get("%0") or fields.get("srcdatabase") or fields.get("rt") or "article"
        etype = "phdthesis" if any(k in etype_raw.lower() for k in ("thesis", "博士", "硕士")) else ("inproceedings" if "conf" in etype_raw.lower() or "会议" in etype_raw else "article")
        records.append(build_canonical_raw_record(
            idx=idx,
            source_file=source_file,
            source_format="endnote" if is_enw else "cnki",
            cite_key=f"{'enw' if is_enw else 'cnki'}_{idx:03d}",
            entry_type=etype,
            authors=authors,
            title=title,
            title_en=fields.get("title_en") or fields.get("english title") or "",
            year=fields.get("%D") or fields.get("year") or fields.get("year-年") or fields.get("pubtime") or "",
            venue=fields.get("%J") or fields.get("%B") or fields.get("source") or fields.get("source-文献来源") or fields.get("journal") or "",
            volume=fields.get("%V") or fields.get("volume") or fields.get("卷") or "",
            issue=fields.get("%N") or fields.get("issue") or fields.get("period") or fields.get("期") or "",
            pages=fields.get("%P") or fields.get("pages") or fields.get("page") or fields.get("页码") or "",
            doi=fields.get("%R") or fields.get("doi") or "",
            url=fields.get("%U") or fields.get("url") or "",
            abstract=fields.get("%X") or fields.get("summary") or fields.get("abstract") or fields.get("摘要") or "",
            raw_text=blk,
        ))
    return records


def parse_json_refs(data: Any, source_file: str, start_idx: int) -> List[Dict[str, Any]]:
    items = data if isinstance(data, list) else (data.get("references") or data.get("items") or data.get("library") or [])
    if not isinstance(items, list):
        return []
    records: List[Dict[str, Any]] = []
    idx = start_idx
    for obj in items:
        if not isinstance(obj, dict):
            continue
        title = obj.get("title") or obj.get("title_en") or obj.get("title_zh") or ""
        if not title:
            continue
        raw_auth = obj.get("authors") or obj.get("author") or []
        authors: List[str] = []
        if isinstance(raw_auth, str):
            authors = split_authors(raw_auth)
        elif isinstance(raw_auth, list):
            for a in raw_auth:
                if isinstance(a, str):
                    authors.append(a.strip())
                elif isinstance(a, dict):
                    name = a.get("literal") or f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
                    if name:
                        authors.append(name)
        year = obj.get("year") or ""
        if not year and isinstance(obj.get("issued"), dict):
            dp = obj["issued"].get("date-parts")
            if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
                year = str(dp[0][0])
        idx += 1
        records.append(build_canonical_raw_record(
            idx=idx,
            source_file=source_file,
            source_format="json",
            cite_key=str(obj.get("id") or obj.get("cite_key") or f"json_{idx:03d}"),
            entry_type=str(obj.get("type") or obj.get("entry_type") or "article"),
            authors=authors,
            title=str(title),
            title_zh=str(obj.get("title_zh") or ""),
            title_en=str(obj.get("title_en") or ""),
            year=str(year),
            venue=str(obj.get("venue") or obj.get("container-title") or obj.get("journal") or ""),
            volume=str(obj.get("volume") or ""),
            issue=str(obj.get("issue") or obj.get("number") or ""),
            pages=str(obj.get("pages") or obj.get("page") or ""),
            doi=str(obj.get("doi") or obj.get("DOI") or ""),
            arxiv_id=str(obj.get("arxiv_id") or ""),
            url=str(obj.get("url") or obj.get("URL") or ""),
            abstract=str(obj.get("abstract") or ""),
            raw_text=json.dumps(obj, ensure_ascii=False),
        ))
    return records


def parse_plaintext_refs(text: str, source_file: str, start_idx: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    idx = start_idx
    for line in text.splitlines():
        s = line.strip()
        if not s or len(s) < 15:
            continue
        m_num = re.match(r"^(?:\[(\d+)\]|(\d+)\.)\s+(.+)$", s)
        if not m_num:
            continue
        orig_num = m_num.group(1) or m_num.group(2)
        body = m_num.group(3).strip()
        year_m = re.search(r"\b((?:19|20)\d{2})\b", body)
        year = year_m.group(1) if year_m else ""
        # Heuristic split: Authors. Title[J]. Venue, Year...
        parts = [p.strip() for p in re.split(r"\.\s+|。", body) if p.strip()]
        authors = split_authors(parts[0]) if len(parts) >= 2 else []
        title = re.sub(r"\[[A-Z/]+\]$", "", parts[1]).strip() if len(parts) >= 2 else body
        venue = parts[2].split(",")[0].strip() if len(parts) >= 3 else ""
        etype = "article"
        if "[C]" in body:
            etype = "inproceedings"
        elif "[M]" in body:
            etype = "book"
        elif "[D]" in body:
            etype = "phdthesis"
        elif "[EB/OL]" in body or "arxiv" in body.lower():
            etype = "preprint"
        idx += 1
        records.append(build_canonical_raw_record(
            idx=idx,
            source_file=source_file,
            source_format="plaintext_list",
            cite_key=f"orig_{orig_num}",
            entry_type=etype,
            authors=authors,
            title=title,
            year=year,
            venue=venue,
            raw_text=body,
        ))
    return records


def parse_manuscript_sentences(text: str, source_file: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    sentences: List[Dict[str, Any]] = []
    warnings: List[str] = []
    section = "Body"
    para_idx = 0
    sent_idx = 0
    para_buf: List[str] = []

    def flush_para() -> None:
        nonlocal para_idx, sent_idx, para_buf
        if not para_buf:
            return
        para_idx += 1
        pid = f"P{para_idx:03d}"
        joined = " ".join(x.strip() for x in para_buf if x.strip())
        # Split sentences carefully
        parts = re.split(r"(?<=[。！？])\s*|(?<=[.!?])\s+(?=[A-Z\u4e00-\u9fff0-9\[(\"'])", joined)
        for p in parts:
            s = p.strip()
            if not s:
                continue
            sent_idx += 1
            markers = RE_LATEX_CITE.findall(s) + RE_BRACKET_CITE.findall(s) + RE_AUTHORYEAR_CITE.findall(s)
            is_cap = bool(RE_CAPTION.match(s))
            if is_cap and markers:
                warnings.append(f"Sentence S{sent_idx:03d} appears to be a caption with citation marker(s) {markers}; verify if citing figure source.")
            sentences.append({
                "sentence_id": f"S{sent_idx:03d}",
                "section": section,
                "paragraph_id": pid,
                "source_file": source_file,
                "is_structural_block": False,
                "is_caption": is_cap,
                "text": s,
                "existing_citation_markers": markers,
            })
        para_buf = []

    for raw_line in text.splitlines():
        st = raw_line.strip()
        mh = re.match(r"^(#{1,6})\s+(.+)$", st)
        th = re.match(r"^\\(?:section|subsection|subsubsection)\*?\{([^}]+)\}", st)
        if mh or th:
            flush_para()
            section = (mh.group(2) if mh else th.group(1)).strip()
            para_idx += 1
            sent_idx += 1
            sentences.append({
                "sentence_id": f"S{sent_idx:03d}",
                "section": section,
                "paragraph_id": f"P{para_idx:03d}",
                "source_file": source_file,
                "is_structural_block": True,
                "is_heading": True,
                "is_caption": False,
                "text": st,
                "existing_citation_markers": [],
            })
            continue
        if not st:
            flush_para()
            continue
        para_buf.append(st)
    flush_para()
    return sentences, warnings


def detect_target_style(task_text: str, case_data: Dict[str, Any], explicit_style: Optional[str]) -> Tuple[str, bool]:
    if explicit_style:
        return explicit_style, False
    hay = (task_text + "\n" + json.dumps(case_data, ensure_ascii=False)).lower()
    if "apa" in hay:
        return "apa7", False
    if "ieee" in hay:
        return "ieee", False
    if "vancouver" in hay:
        return "vancouver", False
    if ("7714" in hay or "gb/t" in hay) and ("著者" in hay or "author-year" in hay or "author_year" in hay):
        return "gbt7714_author_year", False
    if "7714" in hay or "gb/t" in hay or "顺序编码" in hay:
        return "gbt7714_numeric", False
    return "gbt7714_numeric", True


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse manuscript & multi-format bibliography files.")
    parser.add_argument("--case", type=Path, default=None, help="Path to /input/case.json")
    parser.add_argument("--task", type=Path, default=None, help="Path to /input/task.md")
    parser.add_argument("--manuscript", type=Path, default=None, help="Direct manuscript path")
    parser.add_argument("--bib", type=Path, action="append", default=[], help="Direct bibliography file path(s)")
    parser.add_argument("--style", type=str, default=None, help="Explicit target citation style")
    parser.add_argument("--out", type=Path, required=True, help="Output parsed_case.json")
    args = parser.parse_args()

    case_data: Dict[str, Any] = {}
    if args.case and args.case.exists():
        case_data = json.loads(args.case.read_text(encoding="utf-8-sig"))
    task_text = ""
    if args.task and args.task.exists():
        task_text, _ = read_text_safe(args.task)

    manuscript_paths: List[Path] = [args.manuscript] if args.manuscript and args.manuscript.exists() else []
    bib_paths: List[Path] = [p for p in args.bib if p.exists()]

    if args.case and args.case.exists():
        base = args.case.parent
        cands: List[Path] = []
        for fld in ("files", "inputs", "materials", "manuscript", "bibliography", "references", "bib_files"):
            v = case_data.get(fld)
            if isinstance(v, str):
                p = Path(v)
                cands.append(p if p.is_absolute() else base / p)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        p = Path(item)
                        cands.append(p if p.is_absolute() else base / p)
                    elif isinstance(item, dict) and item.get("path"):
                        p = Path(item["path"])
                        cands.append(p if p.is_absolute() else base / p)
        if not cands and not manuscript_paths and not bib_paths:
            for p in sorted(base.rglob("*")):
                if p.is_file() and p.name not in ("case.json", "task.md"):
                    cands.append(p)
        for p in cands:
            if not p.exists() or not p.is_file():
                continue
            suf = p.suffix.lower()
            lname = p.name.lower()
            if suf in (".bib", ".ris", ".enw") or any(k in lname for k in ("ref", "bib", "cnki", "endnote", "library", "文献")):
                if p not in bib_paths:
                    bib_paths.append(p)
            elif suf in (".md", ".txt", ".tex", ".docx"):
                if p not in manuscript_paths:
                    manuscript_paths.append(p)

    all_warnings: List[str] = []
    sources: List[Dict[str, Any]] = []
    all_sentences: List[Dict[str, Any]] = []
    raw_refs: List[Dict[str, Any]] = []

    for mp in manuscript_paths:
        text, warns = read_docx_text(mp) if mp.suffix.lower() == ".docx" else read_text_safe(mp)
        all_warnings.extend(warns)
        sents, sw = parse_manuscript_sentences(text, mp.name)
        offset = len(all_sentences)
        for i, s in enumerate(sents, start=1):
            s["sentence_id"] = f"S{offset + i:03d}"
            all_sentences.append(s)
        all_warnings.extend(sw)
        sources.append({"role": "manuscript", "file": mp.name, "format": mp.suffix.lower().lstrip("."), "sentence_count": len(sents)})

    for bp in bib_paths:
        text, warns = read_text_safe(bp)
        all_warnings.extend(warns)
        suf = bp.suffix.lower()
        parsed: List[Dict[str, Any]] = []
        if suf == ".bib" or re.search(r"@\w+\s*\{", text):
            parsed = parse_bibtex(text, bp.name, len(raw_refs))
        elif suf == ".ris" or re.search(r"^TY\s*-", text, re.MULTILINE):
            parsed = parse_ris(text, bp.name, len(raw_refs))
        elif suf == ".json":
            parsed = parse_json_refs(json.loads(text), bp.name, len(raw_refs))
        elif suf == ".enw" or re.search(r"^%[A-Z0-9]\s+", text, re.MULTILINE) or re.search(r"^(?:Title|Title-题名|SrcDatabase)\s*[:：]", text, re.MULTILINE):
            parsed = parse_endnote_or_cnki(text, bp.name, len(raw_refs))
        else:
            parsed = parse_plaintext_refs(text, bp.name, len(raw_refs))
        if not parsed:
            all_warnings.append(f"No reference records extracted from {bp.name}; verify format.")
        raw_refs.extend(parsed)
        sources.append({"role": "bibliography", "file": bp.name, "format": suf.lstrip("."), "record_count": len(parsed)})

    target_style, style_defaulted = detect_target_style(task_text, case_data, args.style)
    if style_defaulted:
        all_warnings.append("Target citation style not explicitly specified; defaulted to 'gbt7714_numeric'.")

    output = {
        "case_id": case_data.get("case_id", "local-citation-case"),
        "output_contract": case_data.get("output_contract", {}),
        "target_style": target_style,
        "style_was_defaulted": style_defaulted,
        "sources": sources,
        "warnings": all_warnings,
        "manuscript_sentences": all_sentences,
        "raw_references": raw_refs,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "case_id": output["case_id"],
        "target_style": target_style,
        "manuscript_sentences": len(all_sentences),
        "raw_references": len(raw_refs),
        "warnings": len(all_warnings),
        "out": str(args.out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
