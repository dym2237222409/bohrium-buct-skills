#!/usr/bin/env python3
"""7-gate deterministic auditor and multi-artifact compiler for Introduction & Related Work."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


SKILL_ROOT = Path(__file__).resolve().parent.parent


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_citations_from_text(text: str) -> Dict[str, List[str]]:
    """Extract citation tokens across LaTeX, Pandoc, numeric, and author-year conventions."""
    keys: List[str] = []
    # 1. LaTeX \cite{a, b}
    for m in re.finditer(r"\\cite[a-zA-Z*]*\{([^}]+)\}", text):
        for k in m.group(1).split(","):
            tok = k.strip()
            if tok:
                keys.append(tok)
    # 2. Pandoc [@key1; @key2]
    for m in re.finditer(r"\[([^\]]*@[^\]]+)\]", text):
        for km in re.finditer(r"@([A-Za-z0-9_.:-]+)", m.group(1)):
            keys.append(km.group(1))
    # 3. Numeric [1], [1, 3], [1-3]
    numeric_ids: List[str] = []
    for m in re.finditer(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]", text):
        raw = m.group(1)
        for part in re.split(r"\s*,\s*", raw):
            if "-" in part or "–" in part:
                bounds = re.split(r"[-–]", part)
                if len(bounds) == 2 and bounds[0].strip().isdigit() and bounds[1].strip().isdigit():
                    lo, hi = int(bounds[0].strip()), int(bounds[1].strip())
                    if 0 < hi - lo <= 30:
                        numeric_ids.extend(str(i) for i in range(lo, hi + 1))
            elif part.strip().isdigit():
                numeric_ids.append(part.strip())
    # 4. Author-year (Smith et al., 2023)
    author_years: List[str] = []
    for m in re.finditer(r"\(([^()]*?(?:19|20)\d{2}[a-z]?[^()]*?)\)", text):
        inner = m.group(1)
        for seg in inner.split(";"):
            if re.search(r"(?:19|20)\d{2}", seg) and re.search(r"[A-Za-z\u4e00-\u9fa5]", seg):
                author_years.append(seg.strip())

    return {
        "keys": keys,
        "numeric_ids": numeric_ids,
        "author_years": author_years,
    }


def author_year_matches_inventory(ay_token: str, inventory: List[Dict[str, Any]]) -> bool:
    ym = re.search(r"((?:19|20)\d{2})", ay_token)
    if not ym:
        return False
    year = ym.group(1)
    surname_candidates = re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fa5]{1,3}", ay_token.split(year)[0])
    surname_candidates = [s.lower() for s in surname_candidates if s.lower() not in {"et", "al", "and", "等", "人"}]
    for ref in inventory:
        if str(ref.get("year", "")).strip() != year:
            continue
        hay = f"{ref.get('authors', '')} {ref.get('ref_id', '')}".lower()
        if any(s in hay for s in surname_candidates):
            return True
    return False


def extract_numbers(text: str) -> List[str]:
    return re.findall(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?", text)


def run_audit(
    manifest: Dict[str, Any],
    plan: Dict[str, Any],
    lexicon: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    issues: List[Dict[str, Any]] = []
    gates: Dict[str, Dict[str, Any]] = {}

    inventory = manifest.get("literature_inventory", [])
    ref_map = {str(r["ref_id"]): r for r in inventory}
    author_idx = manifest.get("author_evidence_index", {})
    allowed_numbers: Set[str] = set()
    for tok in author_idx.get("all_numeric_tokens", []):
        for n in extract_numbers(tok):
            allowed_numbers.add(n)
    # Also allow years from verified bibliography
    for r in inventory:
        if r.get("year"):
            allowed_numbers.add(str(r["year"]).strip())

    prose = plan.get("prose_sections", {})
    bg_text = str(prose.get("background", "")).strip()
    rw_text = str(prose.get("related_work", "")).strip()
    gap_text = str(prose.get("research_gap", "")).strip()
    contrib_text = str(prose.get("contributions", "")).strip()
    full_prose = "\n\n".join([bg_text, rw_text, gap_text, contrib_text])

    # --- Gate 1: Four-Part Structural Completeness ---
    g1_errors: List[str] = []
    for sec_name, sec_val in [
        ("background", bg_text),
        ("related_work", rw_text),
        ("research_gap", gap_text),
        ("contributions", contrib_text),
    ]:
        if len(sec_val) < 30:
            g1_errors.append(f"Section '{sec_name}' is missing or too short ({len(sec_val)} chars).")
    if not plan.get("background_claims"):
        g1_errors.append("background_claims list is empty.")
    if not plan.get("gap_derivations"):
        g1_errors.append("gap_derivations list is empty.")
    if not plan.get("contributions"):
        g1_errors.append("contributions list is empty.")
    gates["gate1_structural_completeness"] = {"passed": not g1_errors, "errors": g1_errors}
    for e in g1_errors:
        issues.append({"gate": "gate1_structural_completeness", "severity": "error", "message": e})

    # --- Gate 2: Cliché & Empty-Opener Lint ---
    g2_errors: List[str] = []
    for lang_key in ("zh", "en"):
        for pat in lexicon.get("empty_background_cliches", {}).get(lang_key, []):
            m = re.search(pat, bg_text, re.IGNORECASE)
            if m:
                g2_errors.append(f"Empty background cliché matched: '{m.group(0)}'")
    gates["gate2_cliche_and_opener_lint"] = {"passed": not g2_errors, "errors": g2_errors}
    for e in g2_errors:
        issues.append({"gate": "gate2_cliche_and_opener_lint", "severity": "error", "message": e})

    # --- Gate 3: Thematic Grouping & Anti-Listing Check ---
    g3_errors: List[str] = []
    groups = plan.get("related_work_groups", [])
    if len(groups) < 2 and len(inventory) >= 2:
        g3_errors.append(f"Related work must organize literature into >= 2 thematic groups (found {len(groups)}).")
    for g in groups:
        dims = g.get("comparison_dimensions") or []
        if len(dims) < 2:
            g3_errors.append(f"Group '{g.get('group_id')}' specifies fewer than 2 comparison dimensions.")
        if len(str(g.get("group_synthesis", "")).strip()) < 20:
            g3_errors.append(f"Group '{g.get('group_id')}' lacks comparative group_synthesis.")
        for w in g.get("works", []):
            for req_f in ("ref_id", "core_method", "key_finding", "applicable_condition", "limitation"):
                if not str(w.get(req_f, "")).strip():
                    g3_errors.append(f"Work '{w.get('ref_id', '?')}' in group '{g.get('group_id')}' missing '{req_f}'.")

    # Anti-listing chronological dump heuristic
    dump_hits = 0
    for lang_key in ("zh", "en"):
        for pat in lexicon.get("chronological_listing_markers", {}).get(lang_key, []):
            if re.search(pat, rw_text, re.IGNORECASE | re.DOTALL):
                dump_hits += 1
    comp_hits = 0
    for lang_key in ("zh", "en"):
        for pat in lexicon.get("comparative_synthesis_signals", {}).get(lang_key, []):
            if re.search(pat, rw_text, re.IGNORECASE):
                comp_hits += 1
    if dump_hits >= 2 and comp_hits == 0:
        g3_errors.append("Related work prose resembles a chronological paper-by-paper abstract dump without comparative synthesis.")
    gates["gate3_thematic_grouping_anti_listing"] = {
        "passed": not g3_errors,
        "group_count": len(groups),
        "comparative_signal_hits": comp_hits,
        "errors": g3_errors,
    }
    for e in g3_errors:
        issues.append({"gate": "gate3_thematic_grouping_anti_listing", "severity": "error", "message": e})

    # --- Gate 4: Gap Derivation Chain Check ---
    g4_errors: List[str] = []
    gaps = plan.get("gap_derivations", [])
    for gap in gaps:
        gid = gap.get("gap_id", "?")
        derived = gap.get("derived_from_refs") or []
        if inventory and not derived and gap.get("evidence_status") != "author_judgment":
            g4_errors.append(f"Gap '{gid}' has no derived_from_refs despite non-empty bibliography.")
        for rid in derived:
            if rid not in ref_map:
                g4_errors.append(f"Gap '{gid}' cites unknown ref_id '{rid}' not in literature_inventory.")
        if not str(gap.get("limitation_or_conflict", "")).strip():
            g4_errors.append(f"Gap '{gid}' missing explicit limitation_or_conflict.")
        if not str(gap.get("how_this_work_addresses", "")).strip():
            g4_errors.append(f"Gap '{gid}' missing how_this_work_addresses mapping.")
    gates["gate4_gap_derivation_chain"] = {"passed": not g4_errors, "gap_count": len(gaps), "errors": g4_errors}
    for e in g4_errors:
        issues.append({"gate": "gate4_gap_derivation_chain", "severity": "error", "message": e})

    # --- Gate 5: Contribution <-> Evidence 1:1 Alignment ---
    g5_errors: List[str] = []
    g5_warnings: List[str] = []
    contribs = plan.get("contributions", [])
    for c in contribs:
        cid = c.get("contrib_id", "?")
        supp = c.get("supporting_evidence") or []
        vstatus = c.get("verification_status", "verified")
        if vstatus == "verified" and not supp:
            g5_errors.append(f"Contribution '{cid}' marked verified but has empty supporting_evidence.")
        if not str(c.get("scope_boundary", "")).strip():
            g5_warnings.append(f"Contribution '{cid}' omits explicit scope_boundary.")
        # Check numeric claims in contribution against author_evidence_index
        c_nums = extract_numbers(str(c.get("statement", ""))) + [
            n for nc in (c.get("numeric_claims") or []) for n in extract_numbers(str(nc))
        ]
        # Exclude trivial ordinal digits 1..5 unless decimal/large
        for num in c_nums:
            if num in {"1", "2", "3", "4", "5"}:
                continue
            if allowed_numbers and num not in allowed_numbers and vstatus == "verified":
                g5_errors.append(
                    f"Contribution '{cid}' mentions ungrounded number '{num}' not found in author_evidence_index."
                )
    gates["gate5_contribution_evidence_alignment"] = {
        "passed": not g5_errors,
        "contribution_count": len(contribs),
        "errors": g5_errors,
        "warnings": g5_warnings,
    }
    for e in g5_errors:
        issues.append({"gate": "gate5_contribution_evidence_alignment", "severity": "error", "message": e})
    for w in g5_warnings:
        issues.append({"gate": "gate5_contribution_evidence_alignment", "severity": "warning", "message": w})

    # --- Gate 6: Citation Integrity & Coverage ---
    g6_errors: List[str] = []
    g6_warnings: List[str] = []
    cited_parsed = extract_citations_from_text(full_prose)
    used_verified_refs: Set[str] = set()

    # Check key-based citations (\cite{k} and [@k])
    for k in cited_parsed["keys"]:
        if k in ref_map:
            used_verified_refs.add(k)
        else:
            g6_errors.append(f"Fabricated or unknown in-text citation key: '{k}'.")
    # Check numeric citations against numeric ref_ids or 1..N
    for nid in cited_parsed["numeric_ids"]:
        if nid in ref_map:
            used_verified_refs.add(nid)
        elif inventory and 1 <= int(nid) <= len(inventory):
            used_verified_refs.add(str(inventory[int(nid) - 1]["ref_id"]))
        else:
            g6_errors.append(f"Numeric citation '[{nid}]' out of range or absent from bibliography.")
    # Check author-year citations
    for ay in cited_parsed["author_years"]:
        if not author_year_matches_inventory(ay, inventory):
            g6_errors.append(f"Author-year citation '({ay})' does not match any verified bibliography entry.")

    # Also verify ref_ids referenced in plan structures
    for g in groups:
        for w in g.get("works", []):
            rid = str(w.get("ref_id", "")).strip()
            if rid in ref_map:
                used_verified_refs.add(rid)
            elif rid:
                g6_errors.append(f"Related work group '{g.get('group_id')}' references unknown ref_id '{rid}'.")
    for bc in plan.get("background_claims", []):
        for sid in bc.get("source_ids", []):
            if sid in ref_map:
                used_verified_refs.add(sid)

    if inventory and not used_verified_refs:
        g6_errors.append("Input bibliography is non-empty, yet zero verified references are cited.")

    coverage_ratio = round(len(used_verified_refs) / len(inventory), 3) if inventory else 0.0
    if inventory and coverage_ratio < 0.5:
        g6_warnings.append(
            f"Only {len(used_verified_refs)}/{len(inventory)} ({coverage_ratio:.0%}) of input references were cited; check if key/recent works were missed."
        )
    missing_meta = [r["ref_id"] for r in inventory if r["ref_id"] in used_verified_refs and not r.get("metadata_complete")]
    if missing_meta:
        g6_warnings.append(f"Used references with incomplete source metadata: {', '.join(missing_meta)}.")

    gates["gate6_citation_integrity_coverage"] = {
        "passed": not g6_errors,
        "verified_refs_used": len(used_verified_refs),
        "total_input_refs": len(inventory),
        "coverage_ratio": coverage_ratio,
        "errors": g6_errors,
        "warnings": g6_warnings,
    }
    for e in g6_errors:
        issues.append({"gate": "gate6_citation_integrity_coverage", "severity": "error", "message": e})
    for w in g6_warnings:
        issues.append({"gate": "gate6_citation_integrity_coverage", "severity": "warning", "message": w})

    # --- Gate 7: Traceability, Overclaim & Uncertainty Honesty ---
    g7_errors: List[str] = []
    g7_warnings: List[str] = []
    for bc in plan.get("background_claims", []):
        if bc.get("evidence_type") == "cited_fact" and not (bc.get("source_ids") or []):
            g7_errors.append(f"Background claim '{bc.get('claim_id')}' marked cited_fact without source_ids.")
    for lang_key in ("zh", "en"):
        for pat in lexicon.get("overclaim_patterns", {}).get(lang_key, []):
            m = re.search(pat, full_prose, re.IGNORECASE)
            if m:
                g7_errors.append(f"Unsupported overclaim phrasing detected: '{m.group(0)}'")
    if not inventory and not (plan.get("uncertainties") or []):
        g7_errors.append("Bibliography is empty, so uncertainties must explicitly record missing references.")

    gates["gate7_traceability_and_honesty"] = {
        "passed": not g7_errors,
        "errors": g7_errors,
        "warnings": g7_warnings,
    }
    for e in g7_errors:
        issues.append({"gate": "gate7_traceability_and_honesty", "severity": "error", "message": e})
    for w in g7_warnings:
        issues.append({"gate": "gate7_traceability_and_honesty", "severity": "warning", "message": w})

    overall_pass = all(g["passed"] for g in gates.values())
    return {
        "overall_pass": overall_pass,
        "gates": gates,
        "verified_refs_used": sorted(used_verified_refs),
        "coverage_ratio": coverage_ratio,
    }, issues


def compile_markdown_manuscript(plan: Dict[str, Any]) -> str:
    lang = plan.get("language", "zh")
    prose = plan.get("prose_sections", {})
    if lang == "zh":
        h1, h2_bg, h2_rw, h2_gap, h2_co = (
            "# 引言与相关工作",
            "## 1. 引言与研究背景",
            "## 2. 相关工作",
            "## 3. 研究空白与问题聚焦",
            "## 4. 本文主要贡献",
        )
    else:
        h1, h2_bg, h2_rw, h2_gap, h2_co = (
            "# Introduction and Related Work",
            "## 1. Research Background and Motivation",
            "## 2. Related Work",
            "## 3. Research Gap",
            "## 4. Summary of Contributions",
        )
    parts = [
        h1,
        "",
        h2_bg,
        "",
        prose.get("background", "").strip(),
        "",
        h2_rw,
        "",
        prose.get("related_work", "").strip(),
        "",
        h2_gap,
        "",
        prose.get("research_gap", "").strip(),
        "",
        h2_co,
        "",
        prose.get("contributions", "").strip(),
        "",
    ]
    return "\n".join(parts)


def compile_comparison_matrix(plan: Dict[str, Any], ref_map: Dict[str, Dict[str, Any]]) -> str:
    lines = ["# Related Work Comparison Matrix", ""]
    for g in plan.get("related_work_groups", []):
        lines.append(f"## {g.get('group_title', g.get('group_id', 'Group'))}")
        lines.append(f"- **Grouping Axis**: `{g.get('grouping_axis', '')}`")
        lines.append(f"- **Comparison Dimensions**: {', '.join(g.get('comparison_dimensions', []))}")
        lines.append("")
        lines.append("| Ref ID | Author / Year / Venue | Core Method | Key Finding | Applicable Condition | Limitation | Source Locator |")
        lines.append("|---|---|---|---|---|---|---|")
        for w in g.get("works", []):
            rid = str(w.get("ref_id", ""))
            meta = ref_map.get(rid, {})
            meta_str = f"{meta.get('authors', 'N/A')} ({meta.get('year', 'N/A')}) {meta.get('venue', '')}".strip()
            row = [
                rid,
                meta_str.replace("|", "/"),
                str(w.get("core_method", "")).replace("|", "/"),
                str(w.get("key_finding", "")).replace("|", "/"),
                str(w.get("applicable_condition", "")).replace("|", "/"),
                str(w.get("limitation", "")).replace("|", "/"),
                str(w.get("source_locator", "")).replace("|", "/"),
            ]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
        lines.append(f"**Group Synthesis**: {g.get('group_synthesis', '')}")
        lines.append("")
    return "\n".join(lines)


def compile_review_checklist(audit: Dict[str, Any], issues: List[Dict[str, Any]], plan: Dict[str, Any]) -> str:
    lines = [
        "# Introduction & Related Work Audit Checklist",
        "",
        f"- **Overall Audit Pass**: `{'PASS' if audit['overall_pass'] else 'FAIL'}`",
        f"- **Verified Citations Used**: `{len(audit['verified_refs_used'])}` (Coverage: `{audit['coverage_ratio']:.1%}`)",
        "",
        "## Gate Summary",
        "",
        "| Gate | Status | Details |",
        "|---|---|---|",
    ]
    for gname, ginfo in audit["gates"].items():
        status = "PASS" if ginfo["passed"] else "FAIL"
        errs = ginfo.get("errors") or []
        warns = ginfo.get("warnings") or []
        detail = "; ".join(errs + warns) if (errs or warns) else "OK"
        lines.append(f"| `{gname}` | **{status}** | {detail.replace('|', '/')} |")

    lines.append("")
    lines.append("## Uncertainties & Items Requiring Author Confirmation")
    lines.append("")
    uncertainties = plan.get("uncertainties") or []
    if uncertainties:
        for u in uncertainties:
            lines.append(f"- **[{u.get('category', 'note')}]** {u.get('item', '')} -> *Action*: {u.get('action_needed', '')}")
    else:
        lines.append("- None flagged.")

    if issues:
        lines.append("")
        lines.append("## Detailed Audit Findings")
        lines.append("")
        for it in issues:
            lines.append(f"- `[{it['severity'].upper()}]` ({it['gate']}): {it['message']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit writing plan and compile Introduction & Related Work deliverables.")
    parser.add_argument("--manifest", required=True, help="Path to material_manifest.json.")
    parser.add_argument("--plan", required=True, help="Path to writing_plan.json.")
    parser.add_argument("--output-dir", required=True, help="Directory for final deliverables.")
    args = parser.parse_args()

    manifest = load_json(Path(args.manifest))
    plan = load_json(Path(args.plan))
    lexicon = load_json(SKILL_ROOT / "resources" / "cliche_and_overclaim_lexicon.json")

    audit, issues = run_audit(manifest, plan, lexicon)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ref_map = {str(r["ref_id"]): r for r in manifest.get("literature_inventory", [])}
    manuscript_md = compile_markdown_manuscript(plan)
    matrix_md = compile_comparison_matrix(plan, ref_map)
    checklist_md = compile_review_checklist(audit, issues, plan)

    (out_dir / "introduction_and_related_work.md").write_text(manuscript_md, encoding="utf-8")
    (out_dir / "comparison_matrix.md").write_text(matrix_md, encoding="utf-8")
    (out_dir / "review_checklist.md").write_text(checklist_md, encoding="utf-8")

    # Auto-export verified cited references list (.md + .bib) so the manuscript is drop-in publication-ready
    cited_objs = [ref_map[k] for k in audit["verified_refs_used"] if k in ref_map]
    ref_md_lines = ["# Verified Cited References", ""]
    bib_blocks: List[str] = []
    for idx_r, r in enumerate(cited_objs, start=1):
        ref_md_lines.append(
            f"[{idx_r}] {r.get('authors') or '[Author Missing]'} ({r.get('year') or 'n.d.'}). "
            f"{r.get('title') or '[Title Missing]'}. {r.get('venue') or ''}"
            + (f" DOI: {r['doi']}." if r.get("doi") else "")
            + f" (`{r['ref_id']}`)"
        )
        bib_lines = [f"@{r.get('entry_type') or 'article'}{{{r['ref_id']},"]
        for bf, rk in (("author", "authors"), ("title", "title"), ("journal", "venue"), ("year", "year"), ("doi", "doi")):
            if r.get(rk):
                bib_lines.append(f"  {bf} = {{{r[rk]}}},")
        bib_lines.append("}")
        bib_blocks.append("\n".join(bib_lines))
    (out_dir / "references_cited.md").write_text("\n\n".join(ref_md_lines) + "\n", encoding="utf-8")
    (out_dir / "references_cited.bib").write_text("\n\n".join(bib_blocks) + ("\n" if bib_blocks else ""), encoding="utf-8")

    manuscript_tex_name = None
    if plan.get("target_format") in {"latex", "both"} or manifest.get("citation_style") == "latex-cite":
        tex_body = (
            "% Auto-compiled editable LaTeX draft from scipdf-intro-related-writer\n"
            "\\section{Introduction}\n"
            + plan.get("prose_sections", {}).get("background", "").strip()
            + "\n\n\\subsection{Research Gap}\n"
            + plan.get("prose_sections", {}).get("research_gap", "").strip()
            + "\n\n\\subsection{Contributions}\n"
            + plan.get("prose_sections", {}).get("contributions", "").strip()
            + "\n\n\\section{Related Work}\n"
            + plan.get("prose_sections", {}).get("related_work", "").strip()
            + "\n"
        )
        manuscript_tex_name = "introduction_and_related_work.tex"
        (out_dir / manuscript_tex_name).write_text(tex_body, encoding="utf-8")

    traceability = {
        "case_id": manifest.get("case_id", plan.get("case_id", "unknown-case")),
        "background_claims": plan.get("background_claims", []),
        "related_work_groups": plan.get("related_work_groups", []),
        "gap_derivations": plan.get("gap_derivations", []),
        "contributions": plan.get("contributions", []),
        "verified_references_used": [ref_map[k] for k in audit["verified_refs_used"] if k in ref_map],
    }
    (out_dir / "evidence_traceability.json").write_text(
        json.dumps(traceability, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Determine honest status
    has_missing_material = bool(manifest.get("input_warnings")) and not manifest.get("literature_inventory")
    if audit["overall_pass"] and not has_missing_material:
        status = "completed"
    elif audit["overall_pass"] and has_missing_material:
        status = "partial"
    else:
        status = "partial"

    combined_uncertainty = list(plan.get("uncertainties") or [])
    for w in manifest.get("input_warnings", []):
        combined_uncertainty.append({"item": w, "category": "input_material", "action_needed": "Verify source materials"})
    for it in issues:
        if it["severity"] == "error":
            combined_uncertainty.append(
                {"item": it["message"], "category": it["gate"], "action_needed": "Revise draft before final submission"}
            )

    sources = [
        {
            "ref_id": ref["ref_id"],
            "title": ref.get("title", ""),
            "authors": ref.get("authors", ""),
            "year": ref.get("year", ""),
            "venue": ref.get("venue", ""),
            "source_file": ref.get("source_file", ""),
        }
        for ref in traceability["verified_references_used"]
    ]
    for f in manifest.get("files_indexed", []):
        sources.append({"source_file": f["path"], "type": "input_file"})

    result: Dict[str, Any] = {
        "case_id": manifest.get("case_id", plan.get("case_id", "unknown-case")),
        "status": status,
        "skill_name": "scipdf-intro-related-writer",
        "deliverables": {
            "manuscript_md": "introduction_and_related_work.md",
            "manuscript_tex": manuscript_tex_name,
            "comparison_matrix_md": "comparison_matrix.md",
            "evidence_traceability_json": "evidence_traceability.json",
            "review_checklist_md": "review_checklist.md",
        },
        "manuscript": manuscript_md,
        "summary": {
            "related_work_group_count": len(plan.get("related_work_groups", [])),
            "verified_citations_used": len(audit["verified_refs_used"]),
            "literature_coverage_ratio": audit["coverage_ratio"],
            "gaps_derived": len(plan.get("gap_derivations", [])),
            "contributions_aligned": len(plan.get("contributions", [])),
            "audit_passed": audit["overall_pass"],
        },
        "sources": sources,
        "uncertainty": combined_uncertainty,
        "change_records": plan.get("change_records", []),
        "audit_gates": audit["gates"],
    }

    # Preserve any extra required fields declared in output_contract
    contract = manifest.get("output_contract") or {}
    req_fields = contract.get("required_fields") if isinstance(contract, dict) else None
    if isinstance(req_fields, list):
        for field in req_fields:
            if isinstance(field, str) and field not in result:
                result[field] = plan.get(field)

    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": status,
                "overall_pass": audit["overall_pass"],
                "error_count": sum(1 for i in issues if i["severity"] == "error"),
                "warning_count": sum(1 for i in issues if i["severity"] == "warning"),
                "output_dir": str(out_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0 if audit["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
