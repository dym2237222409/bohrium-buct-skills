#!/usr/bin/env python3
"""
sync_format_and_verify.py — Deterministic in-text & bibliography synchronization,
contiguous renumbering / author-year disambiguation, multi-format export
(GB/T 7714-2015, APA 7th, IEEE, Vancouver, BibTeX, RIS), and 6-gate audit compiler
for scipdf-citation-manager. Zero external dependencies (Python 3.9+ stdlib).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_STYLES = SCRIPT_DIR.parent / "resources" / "citation_styles.json"
RE_PLACEHOLDER = re.compile(r"\{\{CITE:([^}]+)\}\}")
RE_CJK = re.compile(r"[\u4e00-\u9fff]")
RE_VALID_DOI = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")


def compress_numeric_list(nums: List[int], range_sep: str, item_sep: str, min_len: int = 3) -> str:
    uniq = sorted(set(nums))
    if not uniq:
        return ""
    ranges: List[Tuple[int, int]] = []
    start = prev = uniq[0]
    for x in uniq[1:]:
        if x == prev + 1:
            prev = x
        else:
            ranges.append((start, prev))
            start = prev = x
    ranges.append((start, prev))
    parts: List[str] = []
    for a, b in ranges:
        if b - a + 1 >= min_len:
            parts.append(f"{a}{range_sep}{b}")
        elif b == a:
            parts.append(str(a))
        else:
            parts.extend(str(k) for k in range(a, b + 1))
    return item_sep.join(parts)


def surname_of(author: str) -> str:
    a = author.strip()
    if RE_CJK.search(a):
        return a
    if "," in a:
        return a.split(",", 1)[0].strip()
    parts = a.split()
    return parts[-1].strip() if parts else a


def format_gbt_author(author: str, is_zh: bool) -> str:
    a = author.strip()
    if is_zh or RE_CJK.search(a):
        return a
    if "," in a:
        last, first = [x.strip() for x in a.split(",", 1)]
        initials = " ".join(p[0].upper() for p in re.split(r"[\s\-]+", first) if p)
        return f"{last.upper()} {initials}".strip()
    parts = a.split()
    if len(parts) >= 2:
        last = parts[-1].upper()
        initials = " ".join(p[0].upper() for p in parts[:-1] if p)
        return f"{last} {initials}".strip()
    return a.upper()


def format_apa_author(author: str) -> str:
    a = author.strip()
    if RE_CJK.search(a):
        return a
    if "," in a:
        last, first = [x.strip() for x in a.split(",", 1)]
        initials = " ".join(f"{p[0].upper()}." for p in re.split(r"[\s\-]+", first) if p)
        return f"{last}, {initials}".strip()
    parts = a.split()
    if len(parts) >= 2:
        last = parts[-1]
        initials = " ".join(f"{p[0].upper()}." for p in parts[:-1] if p)
        return f"{last}, {initials}".strip()
    return a


def format_authors_for_style(ref: Dict[str, Any], style_name: str, style_cfg: Dict[str, Any]) -> str:
    authors = ref.get("authors", [])
    if not authors:
        return "[Author Missing]"
    is_zh = ref.get("language") == "zh" or bool(RE_CJK.search(ref.get("title", "")))
    if style_name in ("gbt7714_numeric", "gbt7714_author_year"):
        max_a = int(style_cfg.get("max_authors_before_etal", 3))
        shown = [format_gbt_author(a, is_zh) for a in authors[:max_a]]
        s = ", ".join(shown)
        if len(authors) > max_a:
            s += f", {style_cfg.get('etal_zh' if is_zh else 'etal_en', 'et al.')}"
        return s
    if style_name == "apa7":
        formatted = [format_apa_author(a) for a in authors[:20]]
        if len(formatted) == 1:
            return formatted[0]
        if len(formatted) == 2:
            return f"{formatted[0]}, & {formatted[1]}"
        return ", ".join(formatted[:-1]) + f", & {formatted[-1]}"
    # IEEE / Vancouver
    max_a = int(style_cfg.get("max_authors_before_etal", 6))
    shown = authors[:max_a]
    s = ", ".join(shown)
    if len(authors) > max_a:
        s += ", et al."
    return s


def format_reference_entry(ref: Dict[str, Any], style_name: str, style_cfg: Dict[str, Any], num: int | None, year_label: str) -> str:
    auth_str = format_authors_for_style(ref, style_name, style_cfg)
    title = ref.get("title") or "[Title Missing]"
    venue = ref.get("venue") or "[Venue Missing]"
    vol = ref.get("volume", "")
    iss = ref.get("issue", "")
    pages = ref.get("pages", "")
    doi = ref.get("doi", "")
    url = ref.get("url", "")
    arxiv = ref.get("arxiv_id", "")

    if style_name in ("gbt7714_numeric", "gbt7714_author_year"):
        code_map = style_cfg.get("doc_type_codes", {})
        dtype = code_map.get(ref.get("entry_type", "article"), "[J]")
        vol_iss = f"{vol}({iss})" if (vol and iss) else vol
        pub_tail = ", ".join(x for x in (venue, year_label, vol_iss) if x)
        if pages:
            pub_tail = f"{pub_tail}: {pages}"
        tail = f"{pub_tail}."
        if doi:
            tail += f" DOI: {doi}."
        elif arxiv:
            tail += f" arXiv:{arxiv}."
        elif url:
            tail += f" {url}."
        prefix = f"[{num}] " if (style_name == "gbt7714_numeric" and num is not None) else ""
        return f"{prefix}{auth_str}. {title}{dtype}. {tail}"

    if style_name == "apa7":
        vol_iss = f"*{vol}*({iss})" if (vol and iss) else (f"*{vol}*" if vol else "")
        parts = [f"*{venue}*"]
        if vol_iss:
            parts.append(vol_iss)
        if pages:
            parts.append(pages)
        loc = ", ".join(parts) + "."
        doi_part = f" https://doi.org/{doi}" if doi else (f" https://arxiv.org/abs/{arxiv}" if arxiv else (f" {url}" if url else ""))
        return f"{auth_str} ({year_label or 'n.d.'}). {title}. {loc}{doi_part}"

    # IEEE / Vancouver
    vol_iss = f"vol. {vol}, no. {iss}" if (vol and iss) else (f"vol. {vol}" if vol else "")
    pp = f"pp. {pages}" if pages else ""
    tail = ", ".join(x for x in (venue, vol_iss, pp, year_label) if x) + "."
    if doi:
        tail += f" doi: {doi}."
    prefix = f"[{num}] " if num is not None else ""
    return f'{prefix}{auth_str}, "{title}," {tail}'


def format_author_year_intext(ref: Dict[str, Any], year_label: str, style_name: str) -> str:
    authors = ref.get("authors", [])
    is_zh = ref.get("language") == "zh" or bool(RE_CJK.search(ref.get("title", "")))
    if not authors:
        lead = "Unknown"
    elif len(authors) == 1:
        lead = surname_of(authors[0])
    elif len(authors) == 2:
        joiner = "和" if is_zh else (" & " if style_name == "apa7" else " and ")
        lead = f"{surname_of(authors[0])}{joiner}{surname_of(authors[1])}"
    else:
        lead = f"{surname_of(authors[0])}{'等' if is_zh else ' et al.'}"
    return f"{lead}, {year_label or 'n.d.'}"


def export_bibtex(refs: List[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    for r in refs:
        etype = r.get("entry_type", "article")
        if etype == "preprint":
            etype = "misc"
        key = r["cite_keys"][0] if r.get("cite_keys") else r["ref_id"]
        lines = [f"@{etype}{{{key},"]
        if r.get("authors"):
            lines.append(f"  author = {{{' and '.join(r['authors'])}}},")
        if r.get("title"):
            lines.append(f"  title = {{{r['title']}}},")
        if r.get("venue"):
            vf = "booktitle" if etype == "inproceedings" else "journal"
            lines.append(f"  {vf} = {{{r['venue']}}},")
        if r.get("year"):
            lines.append(f"  year = {{{r['year']}}},")
        if r.get("volume"):
            lines.append(f"  volume = {{{r['volume']}}},")
        if r.get("issue"):
            lines.append(f"  number = {{{r['issue']}}},")
        if r.get("pages"):
            lines.append(f"  pages = {{{r['pages']}}},")
        if r.get("doi"):
            lines.append(f"  doi = {{{r['doi']}}},")
        if r.get("arxiv_id"):
            lines.append(f"  eprint = {{{r['arxiv_id']}}},")
        if r.get("url"):
            lines.append(f"  url = {{{r['url']}}},")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def export_ris(refs: List[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    ty_rev = {"article": "JOUR", "inproceedings": "CONF", "book": "BOOK", "phdthesis": "THES", "preprint": "GEN"}
    for r in refs:
        lines = [f"TY  - {ty_rev.get(r.get('entry_type', 'article'), 'JOUR')}"]
        lines.append(f"ID  - {r['ref_id']}")
        for a in r.get("authors", []):
            lines.append(f"AU  - {a}")
        if r.get("title"):
            lines.append(f"TI  - {r['title']}")
        if r.get("venue"):
            lines.append(f"JO  - {r['venue']}")
        if r.get("year"):
            lines.append(f"PY  - {r['year']}")
        if r.get("volume"):
            lines.append(f"VL  - {r['volume']}")
        if r.get("issue"):
            lines.append(f"IS  - {r['issue']}")
        if r.get("pages"):
            sp_ep = r["pages"].split("-", 1)
            lines.append(f"SP  - {sp_ep[0]}")
            if len(sp_ep) > 1:
                lines.append(f"EP  - {sp_ep[1]}")
        if r.get("doi"):
            lines.append(f"DO  - {r['doi']}")
        if r.get("url"):
            lines.append(f"UR  - {r['url']}")
        lines.append("ER  -")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize citations, format bibliography, and run 6-gate audit.")
    parser.add_argument("--parsed", type=Path, required=True, help="Path to parsed_case.json")
    parser.add_argument("--deduped", type=Path, required=True, help="Path to deduped_refs.json")
    parser.add_argument("--plan", type=Path, required=True, help="Path to citation_plan.json")
    parser.add_argument("--styles", type=Path, default=DEFAULT_STYLES, help="Path to citation_styles.json")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory")
    parser.add_argument("--strict", action="store_true", help="Exit code 2 if any hard gate ERROR exists")
    args = parser.parse_args()

    parsed = json.loads(args.parsed.read_text(encoding="utf-8-sig"))
    deduped = json.loads(args.deduped.read_text(encoding="utf-8-sig"))
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    styles_all = json.loads(args.styles.read_text(encoding="utf-8")).get("styles", {})

    style_name = plan.get("target_style") or parsed.get("target_style") or "gbt7714_numeric"
    style_cfg = styles_all.get(style_name, styles_all["gbt7714_numeric"])
    mode = style_cfg.get("mode", "numeric")
    superscript = bool(plan.get("superscript_numeric", style_cfg.get("superscript_default", False)))

    ref_by_id: Dict[str, Dict[str, Any]] = {r["ref_id"]: r for r in deduped.get("references", [])}
    key_to_ref: Dict[str, str] = deduped.get("cite_key_to_ref_id", {})

    issues: List[Dict[str, Any]] = []
    human_review: List[Dict[str, Any]] = []

    # Step A: Merge zero-copy sentence patches onto full parsed manuscript so NO untouched sentences are ever dropped!
    ordered_cited_ids: List[str] = []
    cited_pairs: List[Tuple[str, str]] = []
    raw_plan_sents: List[Dict[str, Any]] = plan.get("sentences") or plan.get("sentence_patches") or []
    orig_parsed_sents: List[Dict[str, Any]] = parsed.get("sentences", [])
    if orig_parsed_sents and raw_plan_sents:
        plan_by_sid = {s["sentence_id"]: s for s in raw_plan_sents if "sentence_id" in s}
        orig_sids = {s["sentence_id"] for s in orig_parsed_sents}
        # If plan explicitly covers a subset (patch mode), preserve all untouched sentences in original order
        if len(plan_by_sid) < len(orig_parsed_sents) and set(plan_by_sid.keys()).issubset(orig_sids):
            sentences_plan: List[Dict[str, Any]] = []
            for osent in orig_parsed_sents:
                sid = osent["sentence_id"]
                if sid in plan_by_sid:
                    merged = {**osent, **plan_by_sid[sid]}
                    merged.setdefault("original_text", osent.get("text", ""))
                    sentences_plan.append(merged)
                else:
                    sentences_plan.append({
                        **osent,
                        "original_text": osent.get("text", ""),
                        "text_with_placeholders": osent.get("text", ""),
                        "action": "unchanged",
                    })
        else:
            sentences_plan = raw_plan_sents
    else:
        sentences_plan = raw_plan_sents

    def resolve_id(tok: str) -> str:
        t = tok.strip()
        if t in ref_by_id:
            return t
        if t in key_to_ref:
            return key_to_ref[t]
        return t

    for s in sentences_plan:
        sid = s["sentence_id"]
        txt = s["text_with_placeholders"]
        matches = list(RE_PLACEHOLDER.finditer(txt))
        if s.get("is_caption") and matches and not s.get("caption_cite_is_source_attribution", False):
            issues.append({
                "gate": "Gate6_PlacementHygiene",
                "severity": "WARNING",
                "sentence_id": sid,
                "message": "Citation placed inside figure/table caption without 'caption_cite_is_source_attribution: true'.",
            })
        for m in matches:
            raw_tokens = [x.strip() for x in m.group(1).split(",") if x.strip()]
            if len(raw_tokens) >= 5 and txt.rstrip().endswith(m.group(0)):
                issues.append({
                    "gate": "Gate6_PlacementHygiene",
                    "severity": "WARNING",
                    "sentence_id": sid,
                    "message": f"Large citation cluster ({len(raw_tokens)} refs) placed at sentence end; verify claim-level placement.",
                })
            for rt in raw_tokens:
                rid = resolve_id(rt)
                cited_pairs.append((sid, rid))
                if rid not in ref_by_id:
                    issues.append({
                        "gate": "Gate1_NoOrphanCitations",
                        "severity": "ERROR",
                        "sentence_id": sid,
                        "message": f"Orphan or fabricated citation '{rt}' does not exist in deduplicated library.",
                    })
                    human_review.append({
                        "type": "orphan_or_fabricated_citation",
                        "sentence_id": sid,
                        "ref_id": rt,
                        "note": "Citation not found in provided bibliography; cannot fabricate record.",
                    })
                elif rid not in ordered_cited_ids:
                    ordered_cited_ids.append(rid)

    # Step B: Assign numbers (numeric) or alphabetical order + disambiguation letters (author-year)
    ref_number_map: Dict[str, int] = {}
    ref_year_label_map: Dict[str, str] = {}
    if mode in ("numeric", "latex_cite"):
        final_bib_ids = list(ordered_cited_ids)
        for i, rid in enumerate(final_bib_ids, start=1):
            ref_number_map[rid] = i
            ref_year_label_map[rid] = ref_by_id[rid].get("year", "")
    else:
        # Sort alphabetically by first author surname, year, title
        final_bib_ids = sorted(
            ordered_cited_ids,
            key=lambda rid: (
                surname_of(ref_by_id[rid]["authors"][0]).lower() if ref_by_id[rid].get("authors") else "zzz",
                ref_by_id[rid].get("year", "9999"),
                ref_by_id[rid].get("title", "").lower(),
            ),
        )
        # Disambiguate same-author-same-year (2023a, 2023b)
        groups: Dict[Tuple[str, str], List[str]] = {}
        for rid in final_bib_ids:
            r = ref_by_id[rid]
            fa = surname_of(r["authors"][0]).lower() if r.get("authors") else "unknown"
            yr = r.get("year", "")
            groups.setdefault((fa, yr), []).append(rid)
        for (_, yr), rids in groups.items():
            if len(rids) == 1:
                ref_year_label_map[rids[0]] = yr
            else:
                for idx_l, rid in enumerate(rids):
                    ref_year_label_map[rid] = f"{yr}{chr(ord('a') + idx_l)}"

    # Step C: Rewrite manuscript sentences with final synchronized citations
    updated_sentences: List[Dict[str, Any]] = []
    change_records: List[Dict[str, Any]] = []
    old_to_new_map: List[Dict[str, Any]] = []

    for s in sentences_plan:
        sid = s["sentence_id"]
        txt = s["text_with_placeholders"]
        new_markers_in_sent: List[str] = []

        def repl_cite(match: re.Match[str]) -> str:
            raw_tokens = [x.strip() for x in match.group(1).split(",") if x.strip()]
            rids = [resolve_id(rt) for rt in raw_tokens]
            if any(rid not in ref_by_id for rid in rids):
                tag = f"[UNVERIFIED:{','.join(rids)}]"
                new_markers_in_sent.append(tag)
                return tag
            if mode == "latex_cite":
                keys = [
                    (ref_by_id[rid]["cite_keys"][0] if ref_by_id[rid].get("cite_keys") else rid)
                    for rid in rids
                ]
                marker = f"\\cite{{{','.join(keys)}}}"
            elif mode == "numeric":
                nums = [ref_number_map[rid] for rid in rids]
                inner = compress_numeric_list(
                    nums,
                    range_sep=style_cfg.get("range_sep", "-"),
                    item_sep=style_cfg.get("in_text_sep", ","),
                    min_len=int(style_cfg.get("min_range_length", 3)),
                )
                marker = f"{style_cfg.get('in_text_open', '[')}{inner}{style_cfg.get('in_text_close', ']')}"
                if superscript:
                    marker = f"^{marker}"
            else:
                parts = [format_author_year_intext(ref_by_id[rid], ref_year_label_map[rid], style_name) for rid in rids]
                marker = f"{style_cfg.get('in_text_open', '(')}{style_cfg.get('in_text_sep', '; ').join(parts)}{style_cfg.get('in_text_close', ')')}"
            new_markers_in_sent.append(marker)
            return marker

        rendered = RE_PLACEHOLDER.sub(repl_cite, txt)
        updated_sentences.append({
            **s,
            "rendered_text": rendered,
            "new_citation_markers": new_markers_in_sent,
        })
        if s.get("old_citation_markers") or new_markers_in_sent:
            old_to_new_map.append({
                "sentence_id": sid,
                "old_markers": s.get("old_citation_markers", []),
                "new_markers": new_markers_in_sent,
                "action": s.get("action", "unchanged"),
            })
        if s.get("action", "unchanged") != "unchanged" or s.get("original_text", rendered) != rendered:
            change_records.append({
                "sentence_id": sid,
                "section": s.get("section", "Body"),
                "action": s.get("action", "update"),
                "original": s.get("original_text", ""),
                "updated": rendered,
                "reason": s.get("change_reason", ""),
            })

    # Step D: Gate 2, Gate 3, Gate 4, Gate 5 checks
    if mode == "numeric":
        expected_nums = list(range(1, len(final_bib_ids) + 1))
        actual_nums = [ref_number_map[rid] for rid in final_bib_ids]
        if actual_nums != expected_nums:
            issues.append({
                "gate": "Gate3_ContiguousNumbering",
                "severity": "ERROR",
                "sentence_id": None,
                "message": f"Non-contiguous numbering detected: expected {expected_nums}, got {actual_nums}.",
            })

    cited_set = set(final_bib_ids)
    uncited_ids = [rid for rid in ref_by_id if rid not in cited_set]

    req_fields = style_cfg.get("required_fields", ["authors", "title", "year", "venue"])
    for rid in final_bib_ids:
        r = ref_by_id[rid]
        miss = [f for f in req_fields if not r.get(f)]
        if miss:
            issues.append({
                "gate": "Gate4_MetadataCompleteness",
                "severity": "WARNING",
                "sentence_id": None,
                "message": f"Cited reference {rid} is missing required metadata fields: {miss}.",
            })
            human_review.append({
                "type": "missing_metadata",
                "ref_id": rid,
                "missing_fields": miss,
                "note": "Do not fabricate missing fields; supply verified metadata manually.",
            })
        if r.get("doi") and not RE_VALID_DOI.match(r["doi"]):
            issues.append({
                "gate": "Gate4_MetadataCompleteness",
                "severity": "WARNING",
                "sentence_id": None,
                "message": f"Cited reference {rid} has malformed DOI '{r['doi']}'.",
            })

    # Gate 5: Claim-to-reference alignment audit
    align_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for al in plan.get("claim_alignments", []):
        rid = resolve_id(al["ref_id"])
        align_map[(al["sentence_id"], rid)] = {**al, "ref_id": rid}

    for sid, rid in cited_pairs:
        if rid not in ref_by_id:
            continue
        al = align_map.get((sid, rid))
        if not al:
            issues.append({
                "gate": "Gate5_ClaimAlignmentCoverage",
                "severity": "WARNING",
                "sentence_id": sid,
                "message": f"Missing claim-to-reference alignment record for ({sid}, {rid}).",
            })
        else:
            st = al.get("alignment_status", "")
            if st in ("mismatch", "fabricated_or_unverified", "insufficient_evidence_in_metadata"):
                sev = "ERROR" if st == "fabricated_or_unverified" else "WARNING"
                issues.append({
                    "gate": "Gate5_ClaimAlignmentCoverage",
                    "severity": sev,
                    "sentence_id": sid,
                    "message": f"Claim alignment for ({sid}, {rid}) flagged as '{st}': {al.get('evidence_basis', '')}",
                })
                human_review.append({
                    "type": f"claim_{st}",
                    "sentence_id": sid,
                    "ref_id": rid,
                    "note": al.get("evidence_basis", ""),
                })

    # Surface dedup field conflicts to human review
    for fc in deduped.get("field_conflicts", []):
        human_review.append({"type": "dedup_field_conflict", **fc})

    # Step E: Build final deliverables
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Reconstruct updated manuscript
    blocks: List[str] = []
    cur_pid = None
    buf: List[str] = []

    def flush_blk() -> None:
        nonlocal buf
        if buf:
            joined = ""
            for p in buf:
                if not joined:
                    joined = p
                elif re.search(r"[\u4e00-\u9fff。！？]$", joined) and re.match(r"^[\u4e00-\u9fff]", p):
                    joined += p
                else:
                    joined += " " + p
            blocks.append(joined)
            buf = []

    for s in updated_sentences:
        pid = s.get("paragraph_id")
        if s.get("is_structural_block") or pid != cur_pid:
            flush_blk()
            cur_pid = pid
        buf.append(s["rendered_text"])
        if s.get("is_structural_block"):
            flush_blk()
            cur_pid = None
    flush_blk()

    formatted_bib_entries: List[Dict[str, Any]] = []
    bib_md_lines = [f"# References ({style_cfg.get('label', style_name)})", ""]
    for rid in final_bib_ids:
        r = ref_by_id[rid]
        num = ref_number_map.get(rid)
        yl = ref_year_label_map.get(rid, r.get("year", ""))
        formatted_str = format_reference_entry(r, style_name, style_cfg, num, yl)
        bib_md_lines.append(formatted_str)
        bib_md_lines.append("")
        formatted_bib_entries.append({
            "ref_id": rid,
            "number": num,
            "year_label": yl,
            "formatted": formatted_str,
            "doi": r.get("doi", ""),
            "title": r.get("title", ""),
            "merged_raw_ids": r.get("merged_raw_ids", []),
        })

    manuscript_body = "\n\n".join(blocks)
    manuscript_full = manuscript_body + "\n\n## References\n\n" + "\n\n".join(e["formatted"] for e in formatted_bib_entries) + "\n"
    (out_dir / "manuscript_updated.md").write_text(manuscript_full, encoding="utf-8")

    # Preserve native .tex / .docx deliverables when source manuscript is LaTeX or Word
    src_exts = {Path(str(s.get("path", s.get("file", "")))).suffix.lower() for s in parsed.get("sources", []) if isinstance(s, dict)}
    manuscript_tex_name = None
    if mode == "latex_cite" or ".tex" in src_exts:
        manuscript_tex_name = "manuscript_updated.tex"
        (out_dir / manuscript_tex_name).write_text(manuscript_body + "\n", encoding="utf-8")

    manuscript_docx_name = None
    if ".docx" in src_exts:
        try:
            import docx as _docx  # type: ignore
            doc = _docx.Document()
            for blk in manuscript_full.split("\n\n"):
                if blk.strip():
                    doc.add_paragraph(blk.strip())
            manuscript_docx_name = "manuscript_updated.docx"
            doc.save(str(out_dir / manuscript_docx_name))
        except Exception:
            manuscript_docx_name = None

    (out_dir / "references_formatted.md").write_text("\n".join(bib_md_lines), encoding="utf-8")

    cited_ref_objs = [ref_by_id[rid] for rid in final_bib_ids]
    (out_dir / "references_clean.bib").write_text(export_bibtex(cited_ref_objs), encoding="utf-8")
    (out_dir / "references_clean.ris").write_text(export_ris(cited_ref_objs), encoding="utf-8")

    # Dedup report
    (out_dir / "dedup_and_merge_report.json").write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    dedup_md = [
        "# 文献去重与防误合并审计报告",
        "",
        f"- 原始条目数：{deduped.get('raw_count', 0)} → 去重后规范条目数：{deduped.get('deduplicated_count', 0)}（归并组数：{deduped.get('merged_duplicate_groups', 0)}）",
        f"- 防误合并保护对数（`kept_separate_audit`）：{len(deduped.get('kept_separate_audit', []))} | 字段冲突数：{len(deduped.get('field_conflicts', []))}",
        "",
        "## 已归并重复组",
        "",
    ]
    for r in deduped.get("references", []):
        if len(r.get("merged_raw_ids", [])) > 1:
            dedup_md.append(f"- `{r['ref_id']}`（主记录 `{r['canonical_raw_id']}`）合并 `{r['merged_raw_ids']}`：{r['title']} | 依据：`{r.get('merge_rationale', [])}`")
    dedup_md.extend(["", "## 防误合并保留对（严禁合并）", ""])
    for ks in deduped.get("kept_separate_audit", []):
        dedup_md.append(f"- `{ks['raw_id_a']}` vs `{ks['raw_id_b']}`：原因 `{ks['guard_reason']}`（标题相似度 `{ks['title_similarity']}`）")
    (out_dir / "dedup_and_merge_report.md").write_text("\n".join(dedup_md) + "\n", encoding="utf-8")

    # Citation Audit Report
    hard_errors = [i for i in issues if i["severity"] == "ERROR"]
    warnings = [i for i in issues if i["severity"] == "WARNING"]
    audit_report = {
        "case_id": parsed.get("case_id", "local-citation-case"),
        "target_style": style_name,
        "passed_all_hard_gates": len(hard_errors) == 0,
        "hard_error_count": len(hard_errors),
        "warning_count": len(warnings),
        "old_to_new_map": old_to_new_map,
        "cited_count": len(final_bib_ids),
        "uncited_library_entries": [
            {"ref_id": rid, "title": ref_by_id[rid].get("title", ""), "year": ref_by_id[rid].get("year", "")}
            for rid in uncited_ids
        ],
        "claim_alignments": list(align_map.values()),
        "issues": issues,
        "human_review_items": human_review,
    }
    (out_dir / "citation_audit_report.json").write_text(json.dumps(audit_report, ensure_ascii=False, indent=2), encoding="utf-8")

    audit_md = [
        "# 引用双向同步与 6 道门禁核验报告",
        "",
        f"- 目标规范：`{style_cfg.get('label', style_name)}` | 硬门禁全通过：`{audit_report['passed_all_hard_gates']}`",
        f"- 已引用文献数：{len(final_bib_ids)} | 文献库未引用条目：{len(uncited_ids)} | 硬错误：{len(hard_errors)} | 警告：{len(warnings)}",
        "",
        "## 旧→新引用编号映射",
        "",
        "| 句号 | 动作 | 原引用标记 | 新引用标记 |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for m in old_to_new_map:
        audit_md.append(f"| `{m['sentence_id']}` | `{m['action']}` | `{', '.join(m['old_markers'])}` | `{', '.join(m['new_markers'])}` |")
    audit_md.extend(["", "## 论断—文献逐句匹配核验", "", "| 句号 | 文献 ID | 匹配状态 | 依据 |", "| :--- | :--- | :--- | :--- |"])
    for al in align_map.values():
        audit_md.append(f"| `{al['sentence_id']}` | `{al['ref_id']}` | `{al['alignment_status']}` | {al.get('evidence_basis', '').replace('|', '\\|')} |")
    (out_dir / "citation_audit_report.md").write_text("\n".join(audit_md) + "\n", encoding="utf-8")

    # Harbor result.json preserving case_id & ALL original output_contract fields
    uncertainty = list(parsed.get("warnings", [])) + list(plan.get("uncertainty", []))
    result_payload: Dict[str, Any] = {}
    if isinstance(parsed.get("output_contract"), dict):
        for k, v in parsed["output_contract"].items():
            result_payload[k] = v

    result_payload.update({
        "case_id": parsed.get("case_id", "local-citation-case"),
        "status": "completed" if len(hard_errors) == 0 else "partial",
        "target_style": style_name,
        "deliverables": {
            "manuscript_updated": "manuscript_updated.md",
            "references_formatted": "references_formatted.md",
            "references_bib": "references_clean.bib",
            "references_ris": "references_clean.ris",
            "dedup_report_json": "dedup_and_merge_report.json",
            "dedup_report_md": "dedup_and_merge_report.md",
            "citation_audit_json": "citation_audit_report.json",
            "citation_audit_md": "citation_audit_report.md",
        },
        "summary_metrics": {
            "raw_library_entries": deduped.get("raw_count", 0),
            "deduplicated_library_entries": deduped.get("deduplicated_count", 0),
            "merged_duplicate_groups": deduped.get("merged_duplicate_groups", 0),
            "kept_separate_guarded_pairs": len(deduped.get("kept_separate_audit", [])),
            "cited_reference_count": len(final_bib_ids),
            "uncited_library_count": len(uncited_ids),
            "hard_gate_errors": len(hard_errors),
            "warnings": len(warnings),
        },
        "sources": parsed.get("sources", []),
        "old_to_new_map": old_to_new_map,
        "cited_references": formatted_bib_entries,
        "uncited_library_entries": audit_report["uncited_library_entries"],
        "claim_alignments": list(align_map.values()),
        "change_records": change_records,
        "human_review_items": human_review,
        "uncertainty": uncertainty,
    })
    (out_dir / "result.json").write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": result_payload["status"],
        "case_id": result_payload["case_id"],
        "target_style": style_name,
        "cited_count": len(final_bib_ids),
        "hard_gate_errors": len(hard_errors),
        "warnings": len(warnings),
        "out_dir": str(out_dir),
    }, ensure_ascii=False))
    if args.strict and hard_errors:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
