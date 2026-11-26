#!/usr/bin/env python3
"""
inspect_and_segment.py — Universal case inspector, manuscript segmenter,
protected-anchor extractor, and glossary-conflict detector for scipdf-academic-polisher.
Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
DEFAULT_RESOURCES = SKILL_ROOT / "resources"

# Regex patterns for protected anchors
RE_MATH_DISPLAY = re.compile(r"\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\begin\{(?:equation|align|gather|multline)\*?\}[\s\S]+?\\end\{(?:equation|align|gather|multline)\*?\}")
RE_MATH_INLINE = re.compile(r"(?<!\\)\$(?!\$)(?:\\.|[^$\n])+?(?<!\\)\$|\\\((?:\\.|[^)])+?\\\)")
RE_LATEX_CITE = re.compile(r"\\(?:cite|citep|citet|citealp|citealt|parencite|textcite|autocite)(?:\[[^\]]*\])*\{[^}]+\}")
RE_LATEX_REF = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|pageref)\{[^}]+\}")
RE_BRACKET_CITE = re.compile(r"\[\s*\d+(?:\s*[-–—,]\s*\d+)*\s*\]")
RE_AUTHORYEAR_CITE = re.compile(
    r"\((?:[A-Z][A-Za-z\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z\-]+|\s+et\s+al\.)?,\s*(?:19|20)\d{2}[a-z]?(?:\s*;\s*)?)+\)"
)
RE_CROSSREF = re.compile(
    r"\b(?:Fig(?:ure|\.)?|Table|Tab\.|Eq(?:uation|\.)?|Section|Sec\.|Scheme|Algorithm|Appendix)\s*\(?\s*[A-Za-z]?\d+(?:\.\d+)*[a-z]?\s*\)?"
    r"|(?:图|表|公式|式|第)\s*\(?\s*[A-Za-z]?\d+(?:\.\d+)*\s*\)?(?:\s*(?:节|章))?"
)
RE_NUMBER_UNIT = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:[pP]\s*[<=>≤≥]\s*0?\.\d+(?:[eE][+-]?\d+)?|"
    r"[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*[±]\s*\d+(?:\.\d+)?)?"
    r"(?:\s*[×xX*]\s*10\^?\{?[+-]?\d+\}?)?"
    r"(?:\s*(?:%|wt%|vol%|at%|mol%|°C|℃|K|mV|V|mA|A|W|mW|kW|Hz|kHz|MHz|GHz|nm|μm|um|mm|cm|m|km|mg|g|kg|μg|ug|ng|mL|μL|uL|L|mol|mmol|μmol|M|mM|μM|Pa|kPa|MPa|GPa|bar|atm|h|hr|hrs|min|s|ms|ns|eV|keV|MeV|kJ|J|kcal|rpm|ppm|ppb|h⁻¹|s⁻¹|m²/g|cm⁻²|cm-2|mA/cm2|mA\s*cm-2))?)"
    r"(?![A-Za-z0-9_])"
)
RE_URL = re.compile(r"https?://[^\s<>)\]]+")
RE_ABBR_DEF = re.compile(r"([A-Za-z][A-Za-z0-9\-\s]{3,80}?)\s*\(([A-Z][A-Z0-9\-/]{1,12})\)")
RE_MOJIBAKE = re.compile(r"\ufffd|Ã[\x80-\xbf]|â€[™œ\x9d]|锘?")
RE_JAPANESE_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
RE_CJK = re.compile(r"[\u4e00-\u9fff]")

# Common English abbreviations that should NOT trigger sentence splitting
NON_SPLIT_ABBRS = (
    "Fig.", "Figs.", "Eq.", "Eqs.", "Tab.", "Ref.", "Refs.", "Sec.", "Vol.", "No.",
    "Dr.", "Prof.", "Mr.", "Ms.", "vs.", "e.g.", "i.e.", "et al.", "cf.", "approx.",
    "ca.", "etc.", "St.", "Inc.", "Ltd.", "Dept.", "Univ.", "Jan.", "Feb.", "Mar.",
    "Apr.", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."
)


def read_text_with_diagnostics(path: Path) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc != "utf-8-sig" and enc != "utf-8":
                warnings.append(f"Non-UTF-8 encoding detected for {path.name}; decoded via {enc}.")
            if "\ufffd" in text:
                warnings.append(f"Replacement character (U+FFFD / mojibake) detected in {path.name}.")
            return text, warnings
        except UnicodeDecodeError:
            continue
    text = raw.decode("utf-8", errors="replace")
    warnings.append(f"Encoding errors detected in {path.name}; decoded with replacement characters.")
    return text, warnings


def read_docx_text(path: Path) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    paras: List[str] = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for p in root.findall(".//w:p", ns):
            runs = [t.text for t in p.findall(".//w:t", ns) if t.text]
            line = "".join(runs).strip()
            if line:
                paras.append(line)
    except Exception as exc:
        warnings.append(f"Failed to parse DOCX {path.name}: {exc}")
    return "\n\n".join(paras), warnings


def load_lexicon(resources_dir: Path) -> Dict[str, Any]:
    lex_path = resources_dir / "hedge_causality_lexicon.json"
    if lex_path.exists():
        return json.loads(lex_path.read_text(encoding="utf-8"))
    return {}


def detect_language(text: str) -> str:
    cjk_count = len(RE_CJK.findall(text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    if cjk_count > 30 and latin_count > 60:
        return "bilingual"
    if cjk_count > latin_count:
        return "zh"
    return "en"


def detect_register(task_text: str, case_data: Dict[str, Any], explicit_register: Optional[str]) -> Tuple[str, bool]:
    if explicit_register:
        return explicit_register, False
    haystack = (task_text + "\n" + json.dumps(case_data, ensure_ascii=False)).lower()
    if any(k in haystack for k in ("rebuttal", "response to reviewer", "审稿回复", "点对点回复")):
        return "rebuttal", False
    if any(k in haystack for k in ("grant", "proposal", "nsfc", "基金申请", "自然基金", "申请书")):
        return "grant_proposal", False
    if any(k in haystack for k in ("thesis", "dissertation", "学位论文", "硕士论文", "博士论文")):
        return "thesis", False
    if any(k in haystack for k in ("abstract only", "english abstract", "英文摘要", "摘要润色")):
        return "abstract", False
    if any(k in haystack for k in ("journal", "manuscript", "paper", "期刊论文", "论文润色")):
        return "journal_article", False
    return "journal_article", True


def protect_spans_for_split(text: str) -> Tuple[str, Dict[str, str]]:
    placeholders: Dict[str, str] = {}
    counter = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        key = f"__PROT_{counter}__"
        placeholders[key] = match.group(0)
        counter += 1
        return key

    masked = RE_MATH_DISPLAY.sub(repl, text)
    masked = RE_MATH_INLINE.sub(repl, masked)
    masked = RE_URL.sub(repl, masked)
    masked = RE_LATEX_CITE.sub(repl, masked)
    masked = RE_LATEX_REF.sub(repl, masked)
    return masked, placeholders


def restore_spans(text: str, placeholders: Dict[str, str]) -> str:
    out = text
    for key, val in placeholders.items():
        out = out.replace(key, val)
    return out


def split_sentences(paragraph: str) -> List[str]:
    masked, placeholders = protect_spans_for_split(paragraph.strip())
    if not masked:
        return []

    # Protect known abbreviations from triggering English sentence split
    abbr_map: Dict[str, str] = {}
    for idx, abbr in enumerate(NON_SPLIT_ABBRS):
        token = f"__ABBR{idx}__"
        if abbr in masked:
            masked = masked.replace(abbr, token)
            abbr_map[token] = abbr

    # Protect decimals like 3.14 or p < 0.05
    masked = re.sub(r"(\d)\.(\d)", r"\1__DOT__\2", masked)

    # Split on Chinese sentence-ending punctuation or English [.!?] followed by whitespace
    parts = re.split(r"(?<=[。！？])\s*|(?<=[.!?])(?:\s+(?=[A-Z0-9\u4e00-\u9fff\"'“‘(\[]|__PROT_|__ABBR))", masked)
    sentences: List[str] = []
    for part in parts:
        s = part.strip()
        if not s:
            continue
        s = s.replace("__DOT__", ".")
        for token, abbr in abbr_map.items():
            s = s.replace(token, abbr)
        s = restore_spans(s, placeholders).strip()
        if s:
            sentences.append(s)
    return sentences


def extract_claim_signals(sentence: str, lexicon: Dict[str, Any]) -> Dict[str, Any]:
    lower = sentence.lower()
    tiers = lexicon.get("claim_strength_tiers", {})
    matched_tiers: List[int] = []
    matched_terms: List[str] = []

    for tier_data in tiers.values():
        lvl = int(tier_data.get("level", 0))
        for term in tier_data.get("en", []):
            if re.search(r"\b" + re.escape(term.lower()) + r"\b", lower):
                matched_tiers.append(lvl)
                matched_terms.append(term)
        for term in tier_data.get("zh", []):
            if term in sentence:
                matched_tiers.append(lvl)
                matched_terms.append(term)

    causality = []
    for term in lexicon.get("causality_markers", {}).get("en", []):
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", lower):
            causality.append(term)
    for term in lexicon.get("causality_markers", {}).get("zh", []):
        if term in sentence:
            causality.append(term)

    correlation = []
    for term in lexicon.get("correlation_markers", {}).get("en", []):
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", lower):
            correlation.append(term)
    for term in lexicon.get("correlation_markers", {}).get("zh", []):
        if term in sentence:
            correlation.append(term)

    negations = []
    for term in lexicon.get("negation_and_scope_markers", {}).get("en", []):
        if re.search(r"\b" + re.escape(term.lower()) + r"\b", lower):
            negations.append(term)
    for term in lexicon.get("negation_and_scope_markers", {}).get("zh", []):
        if term in sentence:
            negations.append(term)

    return {
        "max_strength_tier": max(matched_tiers) if matched_tiers else None,
        "min_strength_tier": min(matched_tiers) if matched_tiers else None,
        "strength_markers": sorted(set(matched_terms)),
        "causality_markers": sorted(set(causality)),
        "correlation_markers": sorted(set(correlation)),
        "negation_markers": sorted(set(negations)),
    }


def extract_protected_anchors(sentence: str) -> Dict[str, List[str]]:
    # Strip math temporarily for numeric extraction to avoid duplicating LaTeX internals
    math_blocks = RE_MATH_DISPLAY.findall(sentence) + RE_MATH_INLINE.findall(sentence)
    text_no_math = RE_MATH_INLINE.sub(" ", RE_MATH_DISPLAY.sub(" ", sentence))
    # Remove citations before extracting bare numbers so [1,2] isn't double-counted as data numbers
    citations = (
        RE_LATEX_CITE.findall(text_no_math)
        + RE_BRACKET_CITE.findall(text_no_math)
        + RE_AUTHORYEAR_CITE.findall(text_no_math)
    )
    text_no_cites = RE_LATEX_CITE.sub(" ", text_no_math)
    text_no_cites = RE_BRACKET_CITE.sub(" ", text_no_cites)
    text_no_cites = RE_AUTHORYEAR_CITE.sub(" ", text_no_cites)

    cross_refs = RE_LATEX_REF.findall(text_no_math) + RE_CROSSREF.findall(text_no_cites)
    text_no_refs = RE_CROSSREF.sub(" ", RE_LATEX_REF.sub(" ", text_no_cites))
    urls = RE_URL.findall(text_no_refs)
    text_clean = RE_URL.sub(" ", text_no_refs)

    numbers = [m.group(0).strip() for m in RE_NUMBER_UNIT.finditer(text_clean) if m.group(0).strip()]
    abbr_defs = [f"{m.group(1).strip()} ({m.group(2).strip()})" for m in RE_ABBR_DEF.finditer(sentence)]

    return {
        "numbers_and_units": numbers,
        "citations": citations,
        "cross_refs": cross_refs,
        "math_blocks": math_blocks,
        "urls": urls,
        "abbr_definitions": abbr_defs,
    }


def segment_manuscript(text: str, source_file: str, lexicon: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    lines = text.splitlines()
    sentences: List[Dict[str, Any]] = []
    anomalies: List[str] = []

    if RE_JAPANESE_KANA.search(text):
        anomalies.append(f"Mixed CJK/Japanese kana characters detected in {source_file}; verify intentionality.")

    current_section = "Preamble"
    para_lines: List[str] = []
    para_start_line = 1
    para_idx = 0
    sent_idx = 0

    def flush_paragraph(end_line: int) -> None:
        nonlocal para_idx, sent_idx, para_lines
        if not para_lines:
            return
        para_idx += 1
        pid = f"P{para_idx:03d}"
        joined = "\n".join(para_lines).strip()
        # Keep markdown table / code fence / pure display math as atomic unit
        if joined.startswith("```") or joined.startswith("|") or joined.startswith("$$"):
            sent_idx += 1
            sentences.append({
                "sentence_id": f"S{sent_idx:03d}",
                "section": current_section,
                "paragraph_id": pid,
                "source_file": source_file,
                "line_start": para_start_line,
                "line_end": end_line,
                "is_structural_block": True,
                "text": joined,
                "anchors": extract_protected_anchors(joined),
                "claim_signals": extract_claim_signals(joined, lexicon),
            })
        else:
            single_para = " ".join(line.strip() for line in para_lines if line.strip())
            for s in split_sentences(single_para):
                sent_idx += 1
                sentences.append({
                    "sentence_id": f"S{sent_idx:03d}",
                    "section": current_section,
                    "paragraph_id": pid,
                    "source_file": source_file,
                    "line_start": para_start_line,
                    "line_end": end_line,
                    "is_structural_block": False,
                    "text": s,
                    "anchors": extract_protected_anchors(s),
                    "claim_signals": extract_claim_signals(s, lexicon),
                })
        para_lines = []

    in_code_fence = False
    for line_no, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            if not in_code_fence:
                flush_paragraph(line_no - 1)
                para_start_line = line_no
                para_lines.append(raw_line)
                in_code_fence = True
            else:
                para_lines.append(raw_line)
                flush_paragraph(line_no)
                in_code_fence = False
            continue

        if in_code_fence:
            para_lines.append(raw_line)
            continue

        # Detect Markdown or LaTeX headings
        md_heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        tex_heading = re.match(r"^\\(?:section|subsection|subsubsection|chapter)\*?\{([^}]+)\}", stripped)
        if md_heading or tex_heading:
            flush_paragraph(line_no - 1)
            current_section = (md_heading.group(2) if md_heading else tex_heading.group(1)).strip()
            para_idx += 1
            sent_idx += 1
            sentences.append({
                "sentence_id": f"S{sent_idx:03d}",
                "section": current_section,
                "paragraph_id": f"P{para_idx:03d}",
                "source_file": source_file,
                "line_start": line_no,
                "line_end": line_no,
                "is_heading": True,
                "is_structural_block": True,
                "text": stripped,
                "anchors": extract_protected_anchors(stripped),
                "claim_signals": extract_claim_signals(stripped, lexicon),
            })
            continue

        if not stripped:
            flush_paragraph(line_no - 1)
            continue

        if not para_lines:
            para_start_line = line_no
        para_lines.append(raw_line)

    flush_paragraph(len(lines))
    return sentences, anomalies


def parse_glossary_file(path: Path) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    entries: List[Dict[str, str]] = []
    conflicts: List[Dict[str, Any]] = []
    if not path.exists():
        return entries, conflicts

    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            for k, v in data.items():
                entries.append({"source": str(k).strip(), "target": str(v).strip(), "origin": path.name})
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    src = item.get("source") or item.get("zh") or item.get("term") or ""
                    tgt = item.get("target") or item.get("canonical") or item.get("en") or ""
                    if src and tgt:
                        entries.append({"source": str(src).strip(), "target": str(tgt).strip(), "origin": path.name})
    elif suffix in (".csv", ".tsv"):
        delim = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, delimiter=delim)
            for row_idx, row in enumerate(reader):
                if len(row) >= 2:
                    if row_idx == 0 and row[0].strip().lower() in ("source", "term", "zh", "中文"):
                        continue
                    entries.append({"source": row[0].strip(), "target": row[1].strip(), "origin": path.name})
    else:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("->", "=>", "→", ":", "：", "\t"):
                if sep in line:
                    left, right = line.split(sep, 1)
                    if left.strip() and right.strip():
                        entries.append({"source": left.strip(" -*"), "target": right.strip(), "origin": path.name})
                    break

    # Detect internal glossary conflicts (same source mapped to different targets)
    seen: Dict[str, List[str]] = {}
    for e in entries:
        key = e["source"].lower()
        seen.setdefault(key, [])
        if e["target"] not in seen[key]:
            seen[key].append(e["target"])
    for key, targets in seen.items():
        if len(targets) > 1:
            conflicts.append({
                "source_term": key,
                "conflicting_targets": targets,
                "severity": "high",
                "action_required": "Choose one canonical target provisionally and flag for human confirmation."
            })
    return entries, conflicts


def discover_input_files(case_path: Optional[Path], explicit_input: Optional[Path], case_data: Dict[str, Any]) -> Tuple[List[Path], List[Path]]:
    manuscripts: List[Path] = []
    glossaries: List[Path] = []

    if explicit_input and explicit_input.exists():
        manuscripts.append(explicit_input)

    if case_path and case_path.exists():
        base_dir = case_path.parent
        # Scan common fields in case.json
        candidate_paths: List[Path] = []
        for field in ("files", "inputs", "materials", "artifacts", "documents", "manuscript", "manuscript_path", "glossary", "glossary_path"):
            val = case_data.get(field)
            if isinstance(val, str):
                p = Path(val)
                candidate_paths.append(p if p.is_absolute() else base_dir / p)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        p = Path(item)
                        candidate_paths.append(p if p.is_absolute() else base_dir / p)
                    elif isinstance(item, dict) and item.get("path"):
                        p = Path(item["path"])
                        candidate_paths.append(p if p.is_absolute() else base_dir / p)

        # Also scan directory for text/manuscript files if none specified
        if not candidate_paths and not manuscripts:
            for p in sorted(base_dir.rglob("*")):
                if p.is_file() and p.name not in ("case.json", "task.md") and p.suffix.lower() in (".md", ".txt", ".tex", ".docx", ".csv", ".tsv", ".json"):
                    candidate_paths.append(p)

        for p in candidate_paths:
            if not p.exists() or not p.is_file():
                continue
            lname = p.name.lower()
            if any(k in lname for k in ("glossary", "term", "terminology", "术语")):
                if p not in glossaries:
                    glossaries.append(p)
            elif p.suffix.lower() in (".md", ".txt", ".tex", ".docx"):
                if p not in manuscripts:
                    manuscripts.append(p)
            elif p.suffix.lower() == ".json" and p.name != "case.json":
                # Check if JSON contains text/sections
                if p not in manuscripts:
                    manuscripts.append(p)

    return manuscripts, glossaries


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect case & segment manuscript for academic polishing.")
    parser.add_argument("--case", type=Path, default=None, help="Path to /input/case.json")
    parser.add_argument("--task", type=Path, default=None, help="Path to /input/task.md")
    parser.add_argument("--input", type=Path, default=None, help="Direct path to manuscript file")
    parser.add_argument("--glossary", type=Path, default=None, help="Optional path to glossary file")
    parser.add_argument("--register", type=str, default=None, help="Explicit target register")
    parser.add_argument("--resources", type=Path, default=DEFAULT_RESOURCES, help="Path to resources directory")
    parser.add_argument("--out", type=Path, required=True, help="Output path for segmented_case.json")
    args = parser.parse_args()

    lexicon = load_lexicon(args.resources)
    case_data: Dict[str, Any] = {}
    if args.case and args.case.exists():
        case_data = json.loads(args.case.read_text(encoding="utf-8-sig"))

    task_text = ""
    if args.task and args.task.exists():
        task_text, _ = read_text_with_diagnostics(args.task)

    manuscript_files, glossary_files = discover_input_files(args.case, args.input, case_data)
    if args.glossary and args.glossary.exists() and args.glossary not in glossary_files:
        glossary_files.append(args.glossary)

    all_sentences: List[Dict[str, Any]] = []
    all_warnings: List[str] = []
    raw_texts: List[str] = []
    source_records: List[Dict[str, Any]] = []

    for mpath in manuscript_files:
        if mpath.suffix.lower() == ".docx":
            text, warns = read_docx_text(mpath)
        elif mpath.suffix.lower() == ".json":
            raw_obj = json.loads(mpath.read_text(encoding="utf-8-sig"))
            warns = []
            if isinstance(raw_obj, dict):
                parts = []
                for k in ("title", "abstract", "body", "text", "content", "manuscript"):
                    if isinstance(raw_obj.get(k), str):
                        parts.append(f"## {k}\n\n{raw_obj[k]}")
                if isinstance(raw_obj.get("sections"), list):
                    for sec in raw_obj["sections"]:
                        if isinstance(sec, dict):
                            heading = sec.get("heading") or sec.get("title") or "Section"
                            body = sec.get("text") or sec.get("content") or ""
                            if isinstance(sec.get("paragraphs"), list):
                                body = "\n\n".join(str(p.get("text", p) if isinstance(p, dict) else p) for p in sec["paragraphs"])
                            parts.append(f"## {heading}\n\n{body}")
                text = "\n\n".join(parts)
            else:
                text = json.dumps(raw_obj, ensure_ascii=False, indent=2)
        else:
            text, warns = read_text_with_diagnostics(mpath)

        all_warnings.extend(warns)
        raw_texts.append(text)
        sents, seg_warns = segment_manuscript(text, mpath.name, lexicon)
        # Re-index globally if multiple files
        offset = len(all_sentences)
        for i, s in enumerate(sents, start=1):
            s["sentence_id"] = f"S{offset + i:03d}"
            all_sentences.append(s)
        all_warnings.extend(seg_warns)
        source_records.append({
            "file": mpath.name,
            "path": str(mpath),
            "format": mpath.suffix.lower().lstrip("."),
            "char_count": len(text),
            "sentence_count": len(sents),
        })

    # Fallback: check inline text inside case.json
    if not all_sentences:
        inline_text = case_data.get("text") or case_data.get("manuscript") or case_data.get("draft") or ""
        if isinstance(inline_text, str) and inline_text.strip():
            raw_texts.append(inline_text)
            sents, seg_warns = segment_manuscript(inline_text, "case.json#inline", lexicon)
            all_sentences.extend(sents)
            all_warnings.extend(seg_warns)
            source_records.append({
                "file": "case.json#inline",
                "path": str(args.case) if args.case else "inline",
                "format": "inline_text",
                "char_count": len(inline_text),
                "sentence_count": len(sents),
            })
        else:
            all_warnings.append("No manuscript text found in input files or case.json; check input directory.")

    glossary_entries: List[Dict[str, str]] = []
    glossary_conflicts: List[Dict[str, Any]] = []
    for gpath in glossary_files:
        g_ent, g_conf = parse_glossary_file(gpath)
        glossary_entries.extend(g_ent)
        glossary_conflicts.extend(g_conf)

    # Also check inline glossary inside case.json
    if isinstance(case_data.get("glossary"), dict):
        for k, v in case_data["glossary"].items():
            glossary_entries.append({"source": str(k).strip(), "target": str(v).strip(), "origin": "case.json"})

    combined_text = "\n".join(raw_texts)
    target_register, register_inferred = detect_register(task_text, case_data, args.register)
    if register_inferred:
        all_warnings.append("Target register was not explicitly specified; defaulted to 'journal_article' (flag for human confirmation).")

    target_lang = detect_language(combined_text)
    if len(all_sentences) > 150:
        all_warnings.append(f"Long manuscript detected ({len(all_sentences)} sentences); process section-by-section and save partial outputs early.")

    output = {
        "case_id": case_data.get("case_id", "local-case"),
        "output_contract": case_data.get("output_contract", {}),
        "target_register": target_register,
        "register_was_defaulted": register_inferred,
        "target_language": target_lang,
        "sources": source_records,
        "glossary_raw": glossary_entries,
        "glossary_conflicts": glossary_conflicts,
        "anomalies_and_warnings": all_warnings,
        "total_sentences": len(all_sentences),
        "sentences": all_sentences,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "case_id": output["case_id"],
        "total_sentences": output["total_sentences"],
        "target_register": target_register,
        "target_language": target_lang,
        "glossary_entries": len(glossary_entries),
        "glossary_conflicts": len(glossary_conflicts),
        "warnings": len(all_warnings),
        "out": str(args.out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
