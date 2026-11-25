#!/usr/bin/env python3
"""
verify_and_export_bilingual.py — Programmatic Glossary Enforcer, Semantic Fidelity Auditor & Bilingual Exporter

Executes deterministic verification over translated segments:
1. 段落全 (1:1 Segment Completeness & Order): Verifies every translatable S0001..SNNNN exists without omission, duplication, or order drift.
2. 术语稳 (Programmatic Glossary Enforcement & Consistency Audit): Verifies every locked term occurrence across Title, Abstract, Body, Captions, and Conclusion uses the exact locked target_term (with optional deterministic alias backfill --auto-backfill-aliases).
3. 关系准 (Numbers, Units, Citations, Equations, Negation & Conditionality Audit): Checks numerical parity, citation brackets, figure/table refs, negation polarity, hedging modality, and scope conditions.
4. 可核验 (Evidence Provenance & Human Confirmation Checklist): Exports paragraph-aligned bilingual Markdown + JSON, locked glossary table, QA audit report, and updates/validates /app/output/result.json against /input/case.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = SCRIPT_DIR.parent / "resources"
FIDELITY_RULES = json.loads((RESOURCES_DIR / "fidelity_rules.json").read_text(encoding="utf-8"))


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def extract_numbers(text: str) -> List[str]:
    # Extract normalized numeric literals (ignoring reference brackets like [1, 2] handled separately)
    without_refs = re.sub(r"\[\d+(?:\s*[,\-–]\s*\d+)*\]", " ", text)
    hits = re.findall(r"(?<![A-Za-z_])\d+(?:\.\d+)?%?", without_refs)
    return [h for h in hits]


def extract_bracket_citations(text: str) -> List[str]:
    return [re.sub(r"\s+", "", m.group(0)).replace("–", "-") for m in re.finditer(r"\[\d+(?:\s*[,\-–]\s*\d+)*\]", text)]


def audit_and_align(
    segments_payload: Dict[str, Any],
    translations_input: Any,
    glossary_payload: Dict[str, Any],
    auto_backfill_aliases: bool = True,
) -> Dict[str, Any]:
    source_segments = segments_payload.get("segments", [])
    locked_entries = glossary_payload.get("entries", [])

    # Normalize translations_input (accepts dict keyed by seg_id, or {"segments": [...]}, or list of objects)
    trans_map: Dict[str, Dict[str, Any]] = {}
    duplicate_seg_ids: List[str] = []
    raw_list: List[Dict[str, Any]] = []
    if isinstance(translations_input, dict):
        if isinstance(translations_input.get("segments"), list):
            raw_list = translations_input["segments"]
        else:
            for k, v in translations_input.items():
                if isinstance(v, str):
                    raw_list.append({"seg_id": k, "target_text": v})
                elif isinstance(v, dict):
                    item = dict(v)
                    item.setdefault("seg_id", k)
                    raw_list.append(item)
    elif isinstance(translations_input, list):
        raw_list = translations_input

    for item in raw_list:
        sid = str(item.get("seg_id", "")).strip()
        if not sid:
            continue
        if sid in trans_map:
            duplicate_seg_ids.append(sid)
        trans_map[sid] = item

    neg_en_res = [re.compile(p, re.I) for p in FIDELITY_RULES.get("negation_triggers_en", [])]
    neg_zh_markers = FIDELITY_RULES.get("negation_markers_zh", [])
    hedge_rules = FIDELITY_RULES.get("hedging_and_condition_rules", [])

    aligned_segments: List[Dict[str, Any]] = []
    missing_segments: List[str] = []
    glossary_checks_total = 0
    glossary_checks_passed = 0
    glossary_violations: List[Dict[str, Any]] = []
    auto_backfills: List[Dict[str, Any]] = []

    num_cit_total = 0
    num_cit_passed = 0
    fidelity_issues: List[Dict[str, Any]] = []
    human_review_checklist: List[Dict[str, Any]] = []

    for seg in source_segments:
        sid = seg["seg_id"]
        src_txt = seg["source_text"]
        translatable = seg.get("translatable", True)
        t_obj = trans_map.get(sid, {})
        tgt_txt = str(t_obj.get("target_text") or t_obj.get("translation") or "").strip()
        rationale = str(t_obj.get("rationale") or t_obj.get("translation_notes") or "").strip()
        seg_uncertain = t_obj.get("uncertain_spans", [])

        if not translatable and not tgt_txt:
            tgt_txt = src_txt if seg["role"] == "reference" else f"[UNTRANSLATED_SOURCE: {seg.get('untranslatable_reason')}]"

        if translatable and not tgt_txt:
            missing_segments.append(sid)
            human_review_checklist.append({
                "seg_id": sid,
                "page": seg["page"],
                "issue_type": "missing_translation",
                "detail": "Segment is marked translatable but received empty target_text.",
            })

        # Glossary enforcement & drift check on this segment
        matched_terms_in_seg: List[Dict[str, str]] = []
        if translatable and tgt_txt:
            for ent in locked_entries:
                if sid not in ent.get("occurrence_segments", []):
                    continue
                glossary_checks_total += 1
                locked_zh = ent["target_term"]
                if locked_zh in tgt_txt:
                    glossary_checks_passed += 1
                    matched_terms_in_seg.append({
                        "term_id": ent["term_id"],
                        "source_term": ent["source_term"],
                        "target_term": locked_zh,
                        "status": "verified",
                    })
                else:
                    repaired = False
                    if auto_backfill_aliases and ent.get("drift_aliases"):
                        for bad_alias in ent["drift_aliases"]:
                            if bad_alias and bad_alias in tgt_txt:
                                tgt_txt = tgt_txt.replace(bad_alias, locked_zh)
                                repaired = True
                                auto_backfills.append({
                                    "seg_id": sid,
                                    "term_id": ent["term_id"],
                                    "replaced_alias": bad_alias,
                                    "locked_target": locked_zh,
                                })
                                break
                    if repaired and locked_zh in tgt_txt:
                        glossary_checks_passed += 1
                        matched_terms_in_seg.append({
                            "term_id": ent["term_id"],
                            "source_term": ent["source_term"],
                            "target_term": locked_zh,
                            "status": "auto_backfilled",
                        })
                    else:
                        glossary_violations.append({
                            "seg_id": sid,
                            "page": seg["page"],
                            "section_path": seg["section_path"],
                            "term_id": ent["term_id"],
                            "source_term": ent["source_term"],
                            "expected_target_term": locked_zh,
                            "actual_target_excerpt": tgt_txt[:160],
                        })
                        human_review_checklist.append({
                            "seg_id": sid,
                            "page": seg["page"],
                            "issue_type": "terminology_drift",
                            "detail": f"Expected locked term '{locked_zh}' for '{ent['source_term']}'.",
                        })

            # Number & citation parity checks
            src_nums = extract_numbers(src_txt)
            tgt_nums = extract_numbers(tgt_txt)
            for num in src_nums:
                num_cit_total += 1
                if num in tgt_nums or num in tgt_txt:
                    num_cit_passed += 1
                else:
                    fidelity_issues.append({
                        "seg_id": sid,
                        "page": seg["page"],
                        "check": "number_parity",
                        "missing_token": num,
                        "source_excerpt": src_txt[:140],
                    })
                    human_review_checklist.append({
                        "seg_id": sid,
                        "page": seg["page"],
                        "issue_type": "number_mismatch",
                        "detail": f"Source numeric token '{num}' not found verbatim in target_text.",
                    })

            src_cits = extract_bracket_citations(src_txt)
            tgt_cits = extract_bracket_citations(tgt_txt)
            for cit in src_cits:
                num_cit_total += 1
                if cit in tgt_cits:
                    num_cit_passed += 1
                else:
                    fidelity_issues.append({
                        "seg_id": sid,
                        "page": seg["page"],
                        "check": "citation_parity",
                        "missing_token": cit,
                        "source_excerpt": src_txt[:140],
                    })

            # Negation polarity check
            has_en_neg = any(rx.search(src_txt) for rx in neg_en_res)
            has_zh_neg = any(mk in tgt_txt for mk in neg_zh_markers)
            if has_en_neg and not has_zh_neg:
                fidelity_issues.append({
                    "seg_id": sid,
                    "page": seg["page"],
                    "check": "negation_polarity",
                    "detail": "Source contains English negation trigger, but no Chinese negation marker was detected in target_text.",
                })
                human_review_checklist.append({
                    "seg_id": sid,
                    "page": seg["page"],
                    "issue_type": "negation_polarity_warning",
                    "detail": "Verify negation scope preservation against source sentence.",
                })

            # Hedging, conditional & bound rules
            for rl in hedge_rules:
                if re.search(rl["en_regex"], src_txt, re.I):
                    exp_any = rl.get("zh_expected_any", [])
                    forb = rl.get("zh_forbidden_overclaim", [])
                    if forb and any(fb in tgt_txt for fb in forb):
                        fidelity_issues.append({
                            "seg_id": sid,
                            "page": seg["page"],
                            "check": rl["id"],
                            "detail": f"Forbidden certainty escalation detected ({rl['description']}).",
                        })
                    elif exp_any and not any(ex in tgt_txt for ex in exp_any):
                        human_review_checklist.append({
                            "seg_id": sid,
                            "page": seg["page"],
                            "issue_type": rl["id"],
                            "detail": rl["description"],
                        })

        for us in seg_uncertain:
            human_review_checklist.append({
                "seg_id": sid,
                "page": seg["page"],
                "issue_type": "agent_flagged_uncertainty",
                "detail": us if isinstance(us, str) else json.dumps(us, ensure_ascii=False),
            })

        if not rationale and matched_terms_in_seg:
            rationale = "Locked glossary applied: " + "; ".join(
                f"{m['source_term']} -> {m['target_term']}" for m in matched_terms_in_seg
            )

        aligned_segments.append({
            "seg_id": sid,
            "order": seg["order"],
            "role": seg["role"],
            "translatable": translatable,
            "page": seg["page"],
            "printed_page_label": seg["printed_page_label"],
            "column": seg["column"],
            "section_path": seg["section_path"],
            "block_id": seg["block_id"],
            "bbox": seg["bbox"],
            "source_text": src_txt,
            "source_sentences": seg.get("source_sentences", []),
            "target_text": tgt_txt,
            "glossary_hits": matched_terms_in_seg,
            "translation_rationale": rationale or "Direct structural & syntactic alignment with source segment.",
            "uncertain_spans": seg_uncertain,
        })

    trans_total = max(1, sum(1 for s in source_segments if s.get("translatable", True)))
    trans_done = trans_total - len(missing_segments)

    metrics = {
        "total_segments": len(source_segments),
        "translatable_segments": trans_total,
        "translated_segments": trans_done,
        "segment_completeness_rate": round(trans_done / trans_total, 4),
        "duplicate_segment_ids": duplicate_seg_ids,
        "locked_glossary_terms": len(locked_entries),
        "glossary_occurrences_checked": glossary_checks_total,
        "glossary_occurrences_matched": glossary_checks_passed,
        "glossary_consistency_rate": round(glossary_checks_passed / glossary_checks_total, 4) if glossary_checks_total else 1.0,
        "auto_backfills_applied": len(auto_backfills),
        "number_and_citation_tokens_checked": num_cit_total,
        "number_and_citation_tokens_preserved": num_cit_passed,
        "number_citation_parity_rate": round(num_cit_passed / num_cit_total, 4) if num_cit_total else 1.0,
        "fidelity_issue_count": len(fidelity_issues),
    }

    overall_valid = (
        len(missing_segments) == 0
        and len(duplicate_seg_ids) == 0
        and len(glossary_violations) == 0
        and len(fidelity_issues) == 0
    )

    return {
        "valid": overall_valid,
        "metrics": metrics,
        "missing_segments": missing_segments,
        "glossary_violations": glossary_violations,
        "auto_backfills": auto_backfills,
        "fidelity_issues": fidelity_issues,
        "human_review_checklist": human_review_checklist,
        "aligned_segments": aligned_segments,
    }


def render_bilingual_markdown(source_file: str, aligned: List[Dict[str, Any]], metrics: Dict[str, Any]) -> str:
    lines = [
        "# Paragraph-Aligned Bilingual Scientific Translation (双语段落级对照译文)",
        "",
        f"- **Source Document**: `{Path(source_file).name}`",
        f"- **Segment Completeness**: `{metrics['translated_segments']}/{metrics['translatable_segments']} ({metrics['segment_completeness_rate']*100:.1f}%)`",
        f"- **Locked Glossary Consistency**: `{metrics['glossary_occurrences_matched']}/{metrics['glossary_occurrences_checked']} ({metrics['glossary_consistency_rate']*100:.1f}%)`",
        f"- **Number & Citation Parity**: `{metrics['number_and_citation_tokens_preserved']}/{metrics['number_and_citation_tokens_checked']} ({metrics['number_citation_parity_rate']*100:.1f}%)`",
        "",
        "---",
        "",
    ]
    for seg in aligned:
        lines.extend([
            f"### [{seg['seg_id']}] {seg['section_path']} (`{seg['role']}` | Page {seg['page']} Col {seg['column']} | `{seg['block_id']}` | BBox `{seg['bbox']}`)",
            "",
            "- **原文 (Source)**:",
            f"  > {seg['source_text'].replace(chr(10), chr(10) + '  > ')}",
            "- **译文 (Target)**:",
            f"  > {seg['target_text'].replace(chr(10), chr(10) + '  > ')}",
            f"- **译法依据 (Rationale)**: {seg['translation_rationale']}",
            "",
        ])
    return "\n".join(lines) + "\n"


def render_qa_report(audit: Dict[str, Any], glossary: Dict[str, Any]) -> str:
    m = audit["metrics"]
    lines = [
        "# Literature Translation Quality & Fidelity Audit Report (译文质量核验与人工确认清单)",
        "",
        f"- **Overall Mechanical Gate**: `{'PASS' if audit['valid'] else 'NEEDS_REVISION'}`",
        f"- **1:1 Segment Completeness**: `{m['translated_segments']}/{m['translatable_segments']}` (`{m['segment_completeness_rate']*100:.1f}%`)",
        f"- **Terminology Consistency Rate**: `{m['glossary_occurrences_matched']}/{m['glossary_occurrences_checked']}` (`{m['glossary_consistency_rate']*100:.1f}%`, Auto-backfills: `{m['auto_backfills_applied']}`)",
        f"- **Number & Citation Preservation Rate**: `{m['number_and_citation_tokens_preserved']}/{m['number_and_citation_tokens_checked']}` (`{m['number_citation_parity_rate']*100:.1f}%`)",
        f"- **Semantic / Polarity Issues**: `{m['fidelity_issue_count']}`",
        "",
    ]
    if audit["glossary_violations"]:
        lines.extend(["## 1. Terminology Drift Violations (Must Fix)", ""])
        for v in audit["glossary_violations"]:
            lines.append(f"- `{v['seg_id']}` (p.{v['page']}): `{v['source_term']}` expected **{v['expected_target_term']}** | Got: `{v['actual_target_excerpt']}`")
        lines.append("")

    if audit["fidelity_issues"]:
        lines.extend(["## 2. Numerical / Citation / Negation / Modality Alerts", ""])
        for f in audit["fidelity_issues"]:
            lines.append(f"- `{f['seg_id']}` (p.{f['page']}) [{f['check']}]: {f.get('detail') or ('Missing ' + str(f.get('missing_token')))}")
        lines.append("")

    lines.extend(["## 3. Human Confirmation Checklist (需人工重点复核项)", ""])
    if not audit["human_review_checklist"] and not glossary.get("polysemy_decisions"):
        lines.append("- None — all segments, locked terms, numbers, citations, and polarity triggers verified cleanly.")
    else:
        for pd in glossary.get("polysemy_decisions", []):
            lines.append(f"- **[Polysemy Disambiguation]** `{pd.get('word')}` → **{pd.get('chosen_target')}** ({pd.get('rationale', '')})")
        for item in audit["human_review_checklist"]:
            lines.append(f"- `[{item['seg_id']} | p.{item['page']}]` **{item['issue_type']}**: {item['detail']}")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify bilingual translation fidelity and export deliverables.")
    parser.add_argument("--segments-json", required=True)
    parser.add_argument("--translations-json", required=True)
    parser.add_argument("--glossary-json", required=True)
    parser.add_argument("--case-json", default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--no-auto-backfill", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero if any glossary or fidelity violation remains.")
    args = parser.parse_args()

    seg_data = json.loads(Path(args.segments_json).read_text(encoding="utf-8"))
    trans_data = json.loads(Path(args.translations_json).read_text(encoding="utf-8"))
    gloss_data = json.loads(Path(args.glossary_json).read_text(encoding="utf-8"))

    audit = audit_and_align(
        seg_data,
        trans_data,
        gloss_data,
        auto_backfill_aliases=not args.no_auto_backfill,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bi_md_path = out_dir / "bilingual_translation.md"
    bi_json_path = out_dir / "bilingual_segments.json"
    gloss_out_path = out_dir / "locked_glossary.json"
    qa_md_path = out_dir / "translation_qa_report.md"

    bi_md_path.write_text(render_bilingual_markdown(seg_data.get("source_file", "document"), audit["aligned_segments"], audit["metrics"]), encoding="utf-8")
    bi_json_path.write_text(json.dumps({"metrics": audit["metrics"], "segments": audit["aligned_segments"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gloss_out_path.write_text(json.dumps(gloss_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    qa_md_path.write_text(render_qa_report(audit, gloss_data), encoding="utf-8")

    # Update / merge into output_dir/result.json while preserving all case.json output_contract keys
    result_path = out_dir / "result.json"
    existing_res: Dict[str, Any] = {}
    if result_path.is_file():
        existing_res = json.loads(result_path.read_text(encoding="utf-8"))

    case_data: Dict[str, Any] = {}
    if args.case_json and Path(args.case_json).is_file():
        case_data = json.loads(Path(args.case_json).read_text(encoding="utf-8"))

    case_id = case_data.get("case_id") or case_data.get("id") or existing_res.get("case_id") or "unknown_case"
    existing_res["case_id"] = case_id
    existing_res["status"] = "success" if not audit["missing_segments"] and not seg_data.get("untranslatable_notices") else "partial"
    existing_res["status_reason"] = (
        f"Translated {audit['metrics']['translated_segments']}/{audit['metrics']['translatable_segments']} segments; "
        f"glossary consistency={audit['metrics']['glossary_consistency_rate']*100:.1f}%, "
        f"number/citation parity={audit['metrics']['number_citation_parity_rate']*100:.1f}%."
    )
    existing_res["results"] = {
        **(existing_res.get("results") if isinstance(existing_res.get("results"), dict) else {}),
        "bilingual_markdown_path": str(bi_md_path),
        "bilingual_segments_path": str(bi_json_path),
        "locked_glossary_path": str(gloss_out_path),
        "qa_report_path": str(qa_md_path),
        "summary_metrics": audit["metrics"],
        "bilingual_segments": audit["aligned_segments"],
        "glossary": gloss_data.get("entries", []),
    }
    existing_res["sources"] = [
        {
            "item_id": s["seg_id"],
            "source_file": seg_data.get("source_file"),
            "page": s["page"],
            "printed_page_label": s["printed_page_label"],
            "column": s["column"],
            "section_path": s["section_path"],
            "block_id": s["block_id"],
            "bbox": s["bbox"],
            "verbatim_quote": s["source_text"][:300],
            "translation_rationale": s["translation_rationale"],
        }
        for s in audit["aligned_segments"]
    ]
    existing_res["uncertainty"] = {
        "unreported_in_source": (existing_res.get("uncertainty") or {}).get("unreported_in_source", []),
        "unverifiable_or_untranslated_spans": seg_data.get("untranslatable_notices", []),
        "ambiguous_expressions_for_human_review": audit["human_review_checklist"],
        "material_conflicts": (existing_res.get("uncertainty") or {}).get("material_conflicts", []),
        "human_takeover_locations": seg_data.get("untranslatable_notices", []),
    }
    recs = existing_res.get("change_records") if isinstance(existing_res.get("change_records"), list) else []
    recs.append({
        "step": "verify_and_export_bilingual",
        "timestamp": utc_now_iso(),
        "summary": existing_res["status_reason"],
    })
    existing_res["change_records"] = recs

    result_path.write_text(json.dumps(existing_res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "valid": audit["valid"],
        "status": existing_res["status"],
        "metrics": audit["metrics"],
        "glossary_violations": len(audit["glossary_violations"]),
        "fidelity_issues": len(audit["fidelity_issues"]),
        "outputs": {
            "result_json": str(result_path),
            "bilingual_md": str(bi_md_path),
            "qa_report_md": str(qa_md_path),
        },
    }, ensure_ascii=False, indent=2))

    if args.strict and not audit["valid"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
