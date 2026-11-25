#!/usr/bin/env python3
"""
query_evidence.py — Evidence-Anchored Retriever over Parsed Scientific PDFs

Searches parsed_document.json across reading-order paragraphs, tables, equations,
figures, footnotes, and references. Returns verbatim quotes with exact page,
printed_page_label, column, section_path, block_id, and bbox coordinates ready
to insert directly into /app/output/result.json["sources"].
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


TOKEN_RE = re.compile(r"[A-Za-z0-9_α-ωΑ-ΩΔ±≤≥×÷°μ]+|[\u4e00-\u9fff]{1,4}")


def tokenize(text: str) -> List[str]:
    low = text.lower()
    tokens = TOKEN_RE.findall(low)
    # Add CJK bigrams for fine-grained Chinese matching
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", low)
    for run in cjk_runs:
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])
    return tokens


def score_candidate(query: str, query_tokens: List[str], text: str, idf: Dict[str, float], regex_pat: Optional[re.Pattern[str]] = None) -> float:
    if not text:
        return 0.0
    score = 0.0
    low_t = text.lower()
    low_q = query.strip().lower()
    if low_q and low_q in low_t:
        score += 6.0
    if regex_pat is not None and regex_pat.search(text):
        score += 8.0

    doc_tokens = set(tokenize(text))
    for qt in set(query_tokens):
        if qt in doc_tokens:
            score += idf.get(qt, 1.0)
    return round(score, 4)


def search_parsed_document(
    parsed_doc: Dict[str, Any],
    query: str = "",
    regex_str: Optional[str] = None,
    section_filter: Optional[str] = None,
    page_filter: Optional[int] = None,
    include_tables: bool = True,
    include_equations: bool = True,
    include_references: bool = False,
    top_k: int = 8,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    src_pdf = parsed_doc.get("source_pdf", "")

    for blk in parsed_doc.get("reading_order_blocks", []):
        if blk.get("role") == "reference_block" and not include_references:
            continue
        candidates.append(
            {
                "kind": blk.get("role", "paragraph"),
                "source_file": src_pdf,
                "page": blk["page"],
                "printed_page_label": blk.get("printed_page_label", str(blk["page"])),
                "column": blk.get("column", 1),
                "section_path": blk.get("section_path", ""),
                "block_id": blk["block_id"],
                "bbox": blk["bbox"],
                "text": blk["text"],
                "extraction_method": blk.get("extraction_method", "unknown"),
            }
        )

    if include_tables:
        for tab in parsed_doc.get("tables", []):
            grid_txt = "\n".join(" | ".join(r) for r in tab.get("grid", []))
            full_txt = f"{tab.get('caption', '')}\n{grid_txt}".strip()
            first_bbox = tab["bboxes"][0]["bbox"] if tab.get("bboxes") else [0, 0, 0, 0]
            first_page = tab["pages"][0] if tab.get("pages") else 1
            candidates.append(
                {
                    "kind": "table",
                    "source_file": src_pdf,
                    "page": first_page,
                    "pages": tab.get("pages", [first_page]),
                    "printed_page_label": str(first_page),
                    "column": 0,
                    "section_path": f"Table {tab.get('table_number')}",
                    "block_id": tab["table_id"],
                    "bbox": first_bbox,
                    "text": full_txt,
                    "extraction_method": tab.get("extraction_method", "table_grid"),
                }
            )

    if include_equations:
        for eq in parsed_doc.get("equations", []):
            candidates.append(
                {
                    "kind": "equation",
                    "source_file": src_pdf,
                    "page": eq["page"],
                    "printed_page_label": eq.get("printed_page_label", str(eq["page"])),
                    "column": eq.get("column", 1),
                    "section_path": eq.get("section_path", ""),
                    "block_id": eq["equation_id"],
                    "bbox": eq["bbox"],
                    "text": eq["expression"],
                    "extraction_method": "equation_extractor",
                }
            )

    if include_references:
        for ref in parsed_doc.get("references", []):
            candidates.append(
                {
                    "kind": "reference",
                    "source_file": src_pdf,
                    "page": ref["page"],
                    "printed_page_label": str(ref["page"]),
                    "column": 0,
                    "section_path": "References",
                    "block_id": ref["ref_id"],
                    "bbox": ref["bbox"],
                    "text": ref["raw_text"],
                    "extraction_method": "reference_parser",
                }
            )

    # Apply metadata filters
    if page_filter is not None:
        candidates = [c for c in candidates if c["page"] == page_filter or page_filter in c.get("pages", [])]
    if section_filter:
        sf = section_filter.lower()
        candidates = [c for c in candidates if sf in c.get("section_path", "").lower()]

    if not query and not regex_str:
        return candidates[:top_k]

    n_docs = max(1, len(candidates))
    df: Dict[str, int] = {}
    for c in candidates:
        for tok in set(tokenize(c["text"])):
            df[tok] = df.get(tok, 0) + 1
    idf = {tok: math.log(1.0 + (n_docs - freq + 0.5) / (freq + 0.5)) for tok, freq in df.items()}

    q_tokens = tokenize(query)
    rx = re.compile(regex_str, re.I) if regex_str else None

    scored: List[Dict[str, Any]] = []
    for c in candidates:
        sc = score_candidate(query, q_tokens, c["text"], idf, rx)
        if sc > 0:
            hit = dict(c)
            hit["score"] = sc
            hit["verbatim_quote"] = c["text"][:450]
            scored.append(hit)

    scored.sort(key=lambda x: (-x["score"], x["page"]))
    return scored[:top_k]


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve verbatim evidence anchors from parsed_document.json.")
    parser.add_argument("--parsed-json", required=True, help="Path to parsed_document.json.")
    parser.add_argument("--query", default="", help="Keywords or question text to search.")
    parser.add_argument("--regex", default=None, help="Optional regex pattern to match.")
    parser.add_argument("--section", default=None, help="Substring filter on section_path.")
    parser.add_argument("--page", type=int, default=None, help="Exact 1-based PDF page filter.")
    parser.add_argument("--include-tables", action="store_true", default=True)
    parser.add_argument("--include-equations", action="store_true", default=True)
    parser.add_argument("--include-references", action="store_true", default=False)
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    parsed_doc = json.loads(Path(args.parsed_json).read_text(encoding="utf-8"))
    hits = search_parsed_document(
        parsed_doc=parsed_doc,
        query=args.query,
        regex_str=args.regex,
        section_filter=args.section,
        page_filter=args.page,
        include_tables=args.include_tables,
        include_equations=args.include_equations,
        include_references=args.include_references,
        top_k=args.top_k,
    )
    print(
        json.dumps(
            {
                "doc_id": parsed_doc.get("doc_id"),
                "query": args.query,
                "hit_count": len(hits),
                "hits": hits,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
