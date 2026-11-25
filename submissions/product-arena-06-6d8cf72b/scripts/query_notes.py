#!/usr/bin/env python3
"""
query_notes.py — Downstream Search & Multi-Paper Side-by-Side Card Comparison Tool

Makes structured notes directly actionable ('用得上') for follow-up reading and writing:
1. Search atomic knowledge_items.jsonl by keyword, alias/abbreviation, item type, or tag.
2. Compare multiple compiled result.json / paper_card JSON files side-by-side into a unified
   Markdown comparison matrix (Problem | Materials/Data | Method & Conditions | Findings + Preconditions | Limitations).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def search_knowledge_jsonl(paths: List[Path], query: str = "", item_type: str = "", tag: str = "") -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    q = query.strip().lower()
    t_filter = item_type.strip().lower()
    tag_filter = tag.strip().lower()

    for p in paths:
        if not p.is_file():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            item = json.loads(ln)
            if t_filter and str(item.get("type", "")).lower() != t_filter:
                continue
            if tag_filter and tag_filter not in [str(x).lower() for x in item.get("tags", [])]:
                continue
            if q:
                haystack = " ".join([
                    str(item.get("canonical_name", "")),
                    " ".join(item.get("aliases", [])),
                    str(item.get("definition_or_statement", "")),
                    str(item.get("conditions_or_units", "")),
                ]).lower()
                if q not in haystack:
                    continue
            hits.append(item)
    return hits


def render_comparison_matrix(card_paths: List[Path]) -> str:
    rows = []
    for cp in card_paths:
        data = json.loads(cp.read_text(encoding="utf-8"))
        card = data.get("results", {}).get("paper_card") or data.get("paper_card") or data
        prob = (card.get("research_problem_and_gap") or {}).get("summary", "-")
        mat = (card.get("datasets_materials_or_subjects") or {}).get("summary", "-")
        meth = (card.get("methodology_and_conditions") or {}).get("summary", "-")
        finds = "; ".join(
            f"{f.get('claim')} (Precondition: {f.get('holding_preconditions')})"
            for f in card.get("condition_bound_findings", [])
        ) or "-"
        lims_obj = card.get("limitations_and_open_questions") or {}
        lims = "; ".join(
            [x.get("point", "") for x in lims_obj.get("author_stated_limitations", [])]
            + [f"Unreported: {x.get('field', '')}" for x in lims_obj.get("unreported_or_missing_controls", [])]
        ) or "-"
        rows.append([
            str(card.get("title", cp.stem)).replace("|", "\\|"),
            str(card.get("authors_and_year", "-")).replace("|", "\\|"),
            str(prob).replace("|", "\\|"),
            str(mat).replace("|", "\\|"),
            str(meth).replace("|", "\\|"),
            str(finds).replace("|", "\\|"),
            str(lims).replace("|", "\\|"),
        ])

    lines = [
        "# Multi-Paper Side-by-Side Comparison Matrix",
        "",
        "| Paper Title | Authors & Year | Problem & Gap | Materials / Data | Method & Conditions | Findings (with Preconditions) | Limitations & Gaps |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Query structured knowledge items or compare multiple Paper Cards.")
    ap.add_argument("--jsonl", action="append", default=[], help="Path(s) to knowledge_items.jsonl.")
    ap.add_argument("--query", default="")
    ap.add_argument("--type", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--compare-cards", action="append", default=[], help="Paths to result.json or paper_card JSON files.")
    ap.add_argument("--out-comparison-md", default=None)
    args = ap.parse_args()

    output: Dict[str, Any] = {}
    if args.jsonl:
        hits = search_knowledge_jsonl([Path(p) for p in args.jsonl], args.query, args.type, args.tag)
        output["query_hit_count"] = len(hits)
        output["hits"] = hits

    if args.compare_cards:
        matrix_md = render_comparison_matrix([Path(p) for p in args.compare_cards])
        output["comparison_papers"] = len(args.compare_cards)
        if args.out_comparison_md:
            Path(args.out_comparison_md).write_text(matrix_md, encoding="utf-8")
            output["comparison_md_path"] = args.out_comparison_md
        else:
            output["comparison_markdown"] = matrix_md

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
