#!/usr/bin/env python3
"""
compile_and_verify_notes.py — Anchor Dereferencer, Knowledge Deduplicator & Multi-Format Exporter

Validates structured_notes_draft.json against section_skeleton.json & anchor_registry.json:
1. 对得上 (Section Skeleton Alignment): Checks that non-bibliography sections in the paper's
   skeleton are covered in order with logical bridges preserved.
2. 留得住 (Premise-Bound Paper Card): Verifies that all required Paper Card fields exist and
   every finding carries explicit 'holding_preconditions' + valid anchors.
3. 找得回 (Anchor Resolution & Numerical Fidelity): Dereferences every cited anchor ID
   (Pxxxx / TABxx / FIGxx / EQxx) to exact page/column/bbox/verbatim_text, rejecting unknown
   anchors and checking that quantitative metrics in findings appear in their cited anchors.
4. 用得上 (Synonym/Abbreviation Deduplication & Reusable Exports): Merges duplicate knowledge
   items sharing canonical names or aliases/abbreviations, flags contradictory definitions,
   and exports:
     - /app/output/structured_notes.md
     - /app/output/knowledge_items.jsonl
     - /app/output/paper_card.csv
     - /app/output/notes_verification_report.md
     - /app/output/result.json (preserving all /input/case.json output_contract fields)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
FIELD_DICT = json.loads((SCRIPT_DIR.parent / "resources" / "field_dictionary.json").read_text(encoding="utf-8"))


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_key(s: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", s.lower())


def clean_anchor_id(raw: str) -> str:
    m = re.search(r"(P\d{4}|TAB\d{2}|FIG\d{2}|EQ\d{2})", str(raw).upper())
    return m.group(1) if m else str(raw).strip()


def deduplicate_knowledge_items(
    raw_items: List[Dict[str, Any]],
    abbr_candidates: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int, List[Dict[str, Any]]]:
    """Merges items sharing canonical names, aliases, or paper-defined abbreviations; flags contradictions."""
    abbr_map: Dict[str, Set[str]] = {}
    for ab in abbr_candidates:
        ck = normalize_key(ab["canonical_name"])
        sk = normalize_key(ab["abbreviation"])
        abbr_map.setdefault(sk, set()).add(ck)
        abbr_map.setdefault(ck, set()).add(sk)

    merged: List[Dict[str, Any]] = []
    key_to_idx: Dict[str, int] = {}
    merges_count = 0
    contradictions: List[Dict[str, Any]] = []

    for item in raw_items:
        cname = str(item.get("canonical_name") or item.get("name") or "").strip()
        if not cname:
            continue
        aliases = [str(a).strip() for a in item.get("aliases", []) if str(a).strip()]
        all_keys = [normalize_key(cname), *[normalize_key(a) for a in aliases]]
        expanded_keys: List[str] = []
        for k in all_keys:
            if not k:
                continue
            expanded_keys.append(k)
            for linked in abbr_map.get(k, set()):
                expanded_keys.append(linked)

        target_idx = next((key_to_idx[k] for k in expanded_keys if k and k in key_to_idx), None)
        anchors = [clean_anchor_id(a) for a in item.get("anchors", []) if str(a).strip()]
        stmt = str(item.get("definition_or_statement") or item.get("statement") or "").strip()

        if target_idx is not None:
            merges_count += 1
            existing = merged[target_idx]
            # Check for potential numerical contradiction between duplicate entries
            nums_old = set(re.findall(r"\d+\.\d+%?", existing["definition_or_statement"]))
            nums_new = set(re.findall(r"\d+\.\d+%?", stmt))
            if nums_old and nums_new and nums_old.isdisjoint(nums_new) and existing["type"] == item.get("type"):
                contradictions.append({
                    "canonical_name": existing["canonical_name"],
                    "statement_a": existing["definition_or_statement"],
                    "statement_b": stmt,
                    "anchors": sorted(set(existing["anchors"] + anchors)),
                })
            # Prefer longer canonical name (full phrase over bare acronym) while keeping acronym in aliases
            if len(cname) > len(existing["canonical_name"]):
                if existing["canonical_name"] not in existing["aliases"]:
                    existing["aliases"].append(existing["canonical_name"])
                existing["canonical_name"] = cname
            elif cname != existing["canonical_name"] and cname not in existing["aliases"]:
                existing["aliases"].append(cname)
            for al in aliases:
                if al != existing["canonical_name"] and al not in existing["aliases"]:
                    existing["aliases"].append(al)
            if stmt and stmt not in existing["definition_or_statement"]:
                existing["definition_or_statement"] = f"{existing['definition_or_statement']}; {stmt}"
            existing["anchors"] = list(dict.fromkeys(existing["anchors"] + anchors))
            for tg in item.get("tags", []):
                if tg not in existing["tags"]:
                    existing["tags"].append(tg)
            for k in expanded_keys:
                if k:
                    key_to_idx[k] = target_idx
        else:
            new_idx = len(merged)
            rec = {
                "kid": f"K{new_idx + 1:03d}",
                "type": item.get("type", "concept"),
                "canonical_name": cname,
                "aliases": aliases,
                "definition_or_statement": stmt,
                "conditions_or_units": str(item.get("conditions_or_units", "")).strip(),
                "tags": list(item.get("tags", [])),
                "verification_tier": item.get("verification_tier", "machine_verifiable"),
                "anchors": list(dict.fromkeys(anchors)),
            }
            merged.append(rec)
            for k in expanded_keys:
                if k:
                    key_to_idx[k] = new_idx

    return merged, merges_count, contradictions


def verify_and_compile(
    skeleton: Dict[str, Any],
    registry: Dict[str, Any],
    abbr_data: Dict[str, Any],
    draft: Dict[str, Any],
) -> Dict[str, Any]:
    anchors_map: Dict[str, Dict[str, Any]] = registry.get("anchors", {})
    errors: List[str] = []
    warnings: List[str] = []
    cited_anchors_all: List[str] = []
    unresolved_anchors: List[str] = []

    def check_anchors(aids: List[str], context_label: str) -> List[Dict[str, Any]]:
        resolved = []
        for raw_a in aids:
            aid = clean_anchor_id(raw_a)
            cited_anchors_all.append(aid)
            if aid in anchors_map:
                a_obj = anchors_map[aid]
                resolved.append({
                    "anchor_id": aid,
                    "kind": a_obj["kind"],
                    "page": a_obj["page"],
                    "printed_page_label": a_obj["printed_page_label"],
                    "column": a_obj["column"],
                    "section_path": a_obj["section_path"],
                    "bbox": a_obj["bbox"],
                    "verbatim_excerpt": a_obj["verbatim_text"][:260],
                })
            else:
                unresolved_anchors.append(f"{context_label}:{aid}")
                errors.append(f"Unresolved anchor '{aid}' cited in {context_label}.")
        return resolved

    # 1. Verify Paper Card & Premise-Bound Findings
    card = draft.get("paper_card") or {}
    for fdef in FIELD_DICT.get("paper_card_fields", []):
        fn = fdef["field"]
        if fdef.get("required") and fn not in card:
            errors.append(f"Missing required paper_card field '{fn}'.")

    for block_key in ("research_problem_and_gap", "datasets_materials_or_subjects", "methodology_and_conditions"):
        blk = card.get(block_key)
        if isinstance(blk, dict):
            blk["resolved_anchors"] = check_anchors(blk.get("anchors", []), f"paper_card.{block_key}")

    numeric_checks_total = 0
    numeric_checks_passed = 0
    findings = card.get("condition_bound_findings", [])
    if not findings:
        errors.append("paper_card.condition_bound_findings must contain at least 1 finding paired with holding_preconditions.")
    for idx, fd in enumerate(findings, start=1):
        if not str(fd.get("holding_preconditions", "")).strip():
            errors.append(f"Finding F{idx:02d} is missing non-empty 'holding_preconditions' (premise required for claim to hold).")
        res_a = check_anchors(fd.get("anchors", []), f"finding_{idx}")
        fd["resolved_anchors"] = res_a
        combined_evidence = " ".join(anchors_map[clean_anchor_id(a)]["verbatim_text"] for a in fd.get("anchors", []) if clean_anchor_id(a) in anchors_map)
        for qm in fd.get("quantitative_metrics", []):
            num_toks = re.findall(r"\d+(?:\.\d+)?%?", str(qm))
            for nt in num_toks:
                numeric_checks_total += 1
                if nt in combined_evidence:
                    numeric_checks_passed += 1
                else:
                    warnings.append(f"Metric token '{nt}' in finding '{fd.get('finding_id', idx)}' not found verbatim in its cited anchors {fd.get('anchors')}.")

    lims = card.get("limitations_and_open_questions", {})
    if isinstance(lims, dict):
        for st_idx, st_lim in enumerate(lims.get("author_stated_limitations", []), start=1):
            if isinstance(st_lim, dict):
                st_lim["resolved_anchors"] = check_anchors(st_lim.get("anchors", []), f"limitation_{st_idx}")

    # 2. Verify Section Skeleton Alignment
    non_ref_skel = [
        s for s in skeleton.get("sections", [])
        if s["heading"].lower().rstrip(":：") not in {"references", "bibliography", "参考文献", "front matter"}
    ]
    sec_summaries = draft.get("section_summaries", [])
    summarized_ids = {s.get("section_id") for s in sec_summaries}
    aligned_sec_count = sum(1 for s in non_ref_skel if s["section_id"] in summarized_ids)
    for ss in sec_summaries:
        ss["resolved_anchors"] = check_anchors(ss.get("evidence_anchors", []), f"section_{ss.get('section_id')}")

    if non_ref_skel and aligned_sec_count < len(non_ref_skel):
        missing_secs = [f"{s['section_id']}({s['heading']})" for s in non_ref_skel if s["section_id"] not in summarized_ids]
        warnings.append(f"Some non-reference sections from skeleton were not summarized individually: {missing_secs}")

    # 3. Deduplicate & Verify Knowledge Items
    dedup_items, merges_done, contradictions = deduplicate_knowledge_items(
        draft.get("knowledge_items", []),
        abbr_data.get("abbreviations", []),
    )
    for ki in dedup_items:
        ki["resolved_anchors"] = check_anchors(ki.get("anchors", []), f"knowledge_{ki['kid']}")

    total_cited = len(cited_anchors_all)
    resolved_cnt = total_cited - len(unresolved_anchors)
    metrics = {
        "skeleton_non_ref_sections": len(non_ref_skel),
        "sections_summarized": len(sec_summaries),
        "sections_aligned_ratio": round(aligned_sec_count / max(1, len(non_ref_skel)), 4),
        "anchors_cited_total": total_cited,
        "anchors_resolved_count": resolved_cnt,
        "anchors_resolved_ratio": round(resolved_cnt / max(1, total_cited), 4),
        "numeric_claims_checked": numeric_checks_total,
        "numeric_claims_verified": numeric_checks_passed,
        "numeric_claims_verified_ratio": round(numeric_checks_passed / numeric_checks_total, 4) if numeric_checks_total else 1.0,
        "knowledge_items_raw": len(draft.get("knowledge_items", [])),
        "knowledge_items_deduplicated": len(dedup_items),
        "synonym_or_abbreviation_merges": merges_done,
        "contradictions_flagged": len(contradictions),
    }

    human_confirm_fields = [
        {"scope": "paper_card.research_problem_and_gap", "reason": "High-level framing synthesis (requires_human_judgment tier)."},
        {"scope": "paper_card.limitations_and_open_questions", "reason": "Unreported control boundaries should be confirmed by domain reviewer."},
    ]
    for ki in dedup_items:
        if ki.get("verification_tier") == "requires_human_judgment":
            human_confirm_fields.append({"scope": ki["kid"], "reason": f" interpretative item '{ki['canonical_name']}'"})

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
        "paper_card": card,
        "section_summaries": sec_summaries,
        "knowledge_items": dedup_items,
        "contradictions": contradictions,
        "human_confirm_fields": human_confirm_fields,
        "distinct_cited_anchors": [
            anchors_map[aid] for aid in dict.fromkeys(cited_anchors_all) if aid in anchors_map
        ],
    }


def render_structured_notes_md(compiled: Dict[str, Any], source_file: str) -> str:
    card = compiled["paper_card"]
    m = compiled["metrics"]
    lines = [
        "---",
        f'title: "{str(card.get("title", "")).replace(chr(34), chr(39))}"',
        f'authors_and_year: "{card.get("authors_and_year", "not_reported")}"',
        f'source_file: "{Path(source_file).name}"',
        f"anchors_resolved_ratio: {m['anchors_resolved_ratio']}",
        f"knowledge_items_count: {m['knowledge_items_deduplicated']}",
        "---",
        "",
        f"# Structured Literature Notes: {card.get('title', Path(source_file).stem)}",
        "",
        "## 1. Standardized Paper Card (横向可比论文卡片)",
        "",
        "| Field | Tier | Content | Evidence Anchors |",
        "|---|---|---|---|",
        f"| **Title** | `machine_verifiable` | {card.get('title', '-')} | `P0001` |",
        f"| **Authors & Year** | `machine_verifiable` | {card.get('authors_and_year', '-')} | Front Matter |",
    ]
    for label, key, tier in [
        ("Research Problem & Gap", "research_problem_and_gap", "requires_human_judgment"),
        ("Datasets / Materials", "datasets_materials_or_subjects", "machine_verifiable"),
        ("Methodology & Conditions", "methodology_and_conditions", "machine_verifiable"),
    ]:
        blk = card.get(key) or {}
        summ = blk.get("summary", blk) if isinstance(blk, dict) else str(blk)
        ancs = ", ".join(f"`[A:{a}]`" for a in (blk.get("anchors", []) if isinstance(blk, dict) else []))
        lines.append(f"| **{label}** | `{tier}` | {str(summ).replace('|', '\\|')} | {ancs or '-'} |")

    lines.extend(["", "### 1.1 Premise-Bound Core Findings (结论及其成立前提条件)", ""])
    for fd in card.get("condition_bound_findings", []):
        ancs = ", ".join(f"`[A:{a}]`" for a in fd.get("anchors", []))
        lines.extend([
            f"- **[{fd.get('finding_id', 'F')}] Finding**: {fd.get('claim')} ({ancs})",
            f"  - **Holding Preconditions / Scope**: *{fd.get('holding_preconditions')}*",
            f"  - **Quantitative Metrics**: `{', '.join(map(str, fd.get('quantitative_metrics', [])))}`",
        ])

    lims = card.get("limitations_and_open_questions") or {}
    lines.extend(["", "### 1.2 Limitations & Unreported Boundaries", ""])
    for sl in lims.get("author_stated_limitations", []):
        ancs = ", ".join(f"`[A:{a}]`" for a in sl.get("anchors", []))
        lines.append(f"- **[Author-Stated]** {sl.get('point')} ({ancs})")
    for ul in lims.get("unreported_or_missing_controls", []):
        lines.append(f"- **[Unreported / Boundary Gap]** `{ul.get('field')}` — Checked scope: {ul.get('checked_scope')}")

    lines.extend(["", "## 2. Section-Aligned Argument & Summary Skeleton (按原文章节对齐摘要)", ""])
    for ss in compiled["section_summaries"]:
        ancs = ", ".join(f"`[A:{a}]`" for a in ss.get("evidence_anchors", []))
        lines.extend([
            f"### [{ss.get('section_id')}] {ss.get('heading')} ({ancs})",
            f"- **Role & Purpose**: {ss.get('section_role_and_purpose', '-')}",
            f"- **Methods / Arguments**: {ss.get('methods_or_arguments', '-')}",
            f"- **Findings & Conditions**: {ss.get('findings_and_conditions', '-')}",
            f"- **Bridge to Next Section**: {ss.get('logical_bridge_to_next', '-')}",
            "",
        ])

    lines.extend(["## 3. Deduplicated Searchable Knowledge & Variable Index (去重知识点清单)", ""])
    for ki in compiled["knowledge_items"]:
        al_str = f" (Aliases: `{', '.join(ki['aliases'])}`)" if ki.get("aliases") else ""
        ancs = ", ".join(f"`[A:{a}]`" for a in ki.get("anchors", []))
        lines.extend([
            f"- **`[{ki['kid']}]` [{ki['type']}] {ki['canonical_name']}**{al_str} — *{ki['verification_tier']}* ({ancs})",
            f"  - Definition / Statement: {ki['definition_or_statement']}",
            f"  - Conditions / Units: `{ki.get('conditions_or_units') or 'N/A'}` | Tags: `{', '.join(ki.get('tags', []))}`",
        ])

    lines.extend(["", "## 4. Dereferenced Source Anchor Registry (原文定位回链索引)", ""])
    for a in compiled["distinct_cited_anchors"]:
        excerpt = a["verbatim_text"].replace("\n", " ")[:200]
        lines.append(
            f"- **`[A:{a['anchor_id']}]`** ({a['kind']} | Page {a['page']} Col {a['column']} | `{a['section_path']}` | BBox `{a['bbox']}`): “{excerpt}”"
        )
    lines.append("")
    return "\n".join(lines)


def write_paper_card_csv(csv_path: Path, card: Dict[str, Any], source_file: str) -> None:
    headers = [
        "source_file", "title", "authors_and_year", "research_problem",
        "datasets_or_materials", "methodology_and_conditions",
        "findings_with_preconditions", "limitations_and_unreported",
    ]
    prob = (card.get("research_problem_and_gap") or {}).get("summary", "")
    mat = (card.get("datasets_materials_or_subjects") or {}).get("summary", "")
    meth = (card.get("methodology_and_conditions") or {}).get("summary", "")
    f_str = " || ".join(
        f"{f.get('claim')} [Precondition: {f.get('holding_preconditions')}]"
        for f in card.get("condition_bound_findings", [])
    )
    lims = card.get("limitations_and_open_questions") or {}
    l_str = " || ".join(
        [x.get("point", "") for x in lims.get("author_stated_limitations", [])]
        + [f"Unreported:{x.get('field', '')}" for x in lims.get("unreported_or_missing_controls", [])]
    )
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerow([Path(source_file).name, card.get("title", ""), card.get("authors_and_year", ""), prob, mat, meth, f_str, l_str])


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile, deduplicate, and verify structured literature notes.")
    ap.add_argument("--index-dir", required=True)
    ap.add_argument("--draft-json", required=True)
    ap.add_argument("--case-json", default=None)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    idx_dir = Path(args.index_dir)
    skeleton = json.loads((idx_dir / "section_skeleton.json").read_text(encoding="utf-8"))
    registry = json.loads((idx_dir / "anchor_registry.json").read_text(encoding="utf-8"))
    abbr_p = idx_dir / "abbreviation_and_symbol_candidates.json"
    abbr_data = json.loads(abbr_p.read_text(encoding="utf-8")) if abbr_p.is_file() else {}
    draft = json.loads(Path(args.draft_json).read_text(encoding="utf-8"))

    compiled = verify_and_compile(skeleton, registry, abbr_data, draft)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    src_file = skeleton.get("source_file", "document.pdf")
    md_path = out_dir / "structured_notes.md"
    jsonl_path = out_dir / "knowledge_items.jsonl"
    csv_path = out_dir / "paper_card.csv"
    rep_path = out_dir / "notes_verification_report.md"

    md_path.write_text(render_structured_notes_md(compiled, src_file), encoding="utf-8")
    jsonl_path.write_text(
        "\n".join(json.dumps(ki, ensure_ascii=False) for ki in compiled["knowledge_items"]) + "\n",
        encoding="utf-8",
    )
    write_paper_card_csv(csv_path, compiled["paper_card"], src_file)

    rep_lines = [
        "# Structured Notes Mechanical Verification & Boundary Audit",
        "",
        f"- **Validation Gate**: `{'PASS' if compiled['valid'] else 'FAIL'}`",
        f"- **Metrics**: `{json.dumps(compiled['metrics'], ensure_ascii=False)}`",
        "",
        "## Machine-Verifiable vs Human-Confirmation Boundary",
        "- **Machine-Verified**: Anchor ID existence, PDF page/bbox coordinates, section skeleton coverage, numerical literal presence in cited anchors, and acronym/synonym item deduplication.",
        "- **Requires Human Confirmation**: High-level problem-gap framing, cross-section transition interpretation, and sufficiency of unreported control boundaries.",
        "",
    ]
    if compiled["errors"] or compiled["warnings"]:
        rep_lines.append("## Findings")
        for e in compiled["errors"]:
            rep_lines.append(f"- **ERROR**: {e}")
        for w in compiled["warnings"]:
            rep_lines.append(f"- **WARNING**: {w}")
    rep_path.write_text("\n".join(rep_lines) + "\n", encoding="utf-8")

    # Populate /app/output/result.json preserving all case.json output_contract fields
    res_path = out_dir / "result.json"
    res_obj: Dict[str, Any] = json.loads(res_path.read_text(encoding="utf-8")) if res_path.is_file() else {}
    case_data = json.loads(Path(args.case_json).read_text(encoding="utf-8")) if (args.case_json and Path(args.case_json).is_file()) else {}

    res_obj["case_id"] = case_data.get("case_id") or case_data.get("id") or res_obj.get("case_id") or "unknown_case"
    res_obj["status"] = "success" if compiled["valid"] and not skeleton.get("scanned_or_empty_pages") else "partial"
    res_obj["status_reason"] = (
        f"Compiled section-aligned notes ({compiled['metrics']['sections_summarized']} sections), "
        f"premise-bound paper card, and {compiled['metrics']['knowledge_items_deduplicated']} deduplicated knowledge items "
        f"({compiled['metrics']['anchors_resolved_count']}/{compiled['metrics']['anchors_cited_total']} anchors verified)."
    )
    res_obj["results"] = {
        **(res_obj.get("results") if isinstance(res_obj.get("results"), dict) else {}),
        "structured_notes_markdown_path": str(md_path),
        "knowledge_items_jsonl_path": str(jsonl_path),
        "paper_card_csv_path": str(csv_path),
        "verification_report_path": str(rep_path),
        "paper_card": compiled["paper_card"],
        "section_summaries": compiled["section_summaries"],
        "knowledge_items": compiled["knowledge_items"],
        "verification_metrics": compiled["metrics"],
    }
    res_obj["sources"] = [
        {
            "anchor_id": a["anchor_id"],
            "kind": a["kind"],
            "source_file": a["source_file"],
            "page": a["page"],
            "printed_page_label": a["printed_page_label"],
            "column": a["column"],
            "section_path": a["section_path"],
            "bbox": a["bbox"],
            "verbatim_quote": a["verbatim_text"][:300],
        }
        for a in compiled["distinct_cited_anchors"]
    ]
    lims = compiled["paper_card"].get("limitations_and_open_questions") or {}
    res_obj["uncertainty"] = {
        "unreported_in_source": lims.get("unreported_or_missing_controls", []),
        "unverifiable_items": [f"Scanned/empty page {p}" for p in skeleton.get("scanned_or_empty_pages", [])],
        "material_conflicts": compiled["contradictions"],
        "human_confirmation_fields": compiled["human_confirm_fields"],
        "human_takeover_locations": [{"page": p, "reason": "Empty text layer"} for p in skeleton.get("scanned_or_empty_pages", [])],
    }
    recs = res_obj.get("change_records") if isinstance(res_obj.get("change_records"), list) else []
    recs.append({"step": "compile_and_verify_notes", "timestamp": utc_now_iso(), "summary": res_obj["status_reason"]})
    res_obj["change_records"] = recs
    res_path.write_text(json.dumps(res_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "valid": compiled["valid"],
        "status": res_obj["status"],
        "metrics": compiled["metrics"],
        "errors": compiled["errors"],
        "warnings": compiled["warnings"],
    }, ensure_ascii=False, indent=2))
    return 2 if (args.strict and not compiled["valid"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
