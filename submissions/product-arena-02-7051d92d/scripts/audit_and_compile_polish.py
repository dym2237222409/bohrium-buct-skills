#!/usr/bin/env python3
"""
audit_and_compile_polish.py — Automated 7-gate semantic/fact preservation auditor
and multi-artifact compiler for scipdf-academic-polisher.
Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from inspect_and_segment import (  # noqa: E402
    DEFAULT_RESOURCES,
    RE_ABBR_DEF,
    extract_claim_signals,
    extract_protected_anchors,
    load_lexicon,
)


def normalize_num_token(tok: str) -> str:
    return re.sub(r"\s+", "", tok).replace("℃", "°C").replace("μ", "u").lower()


def normalize_cite_token(tok: str) -> str:
    return re.sub(r"\s+", "", tok).replace("–", "-").replace("—", "-")


def compute_edit_ratio(orig: str, rev: str) -> float:
    if not orig and not rev:
        return 0.0
    sm = difflib.SequenceMatcher(None, orig.split() if " " in orig else list(orig), rev.split() if " " in rev else list(rev))
    return round(1.0 - sm.ratio(), 4)


def check_unbalanced_math_or_brackets(text: str) -> List[str]:
    issues: List[str] = []
    # Count unescaped single $ outside $$
    no_display = re.sub(r"\$\$[\s\S]+?\$\$", "", text)
    singles = re.findall(r"(?<!\\)\$", no_display)
    if len(singles) % 2 != 0:
        issues.append("Unbalanced inline LaTeX '$' delimiter.")
    if text.count("\\(") != text.count("\\)"):
        issues.append("Unbalanced '\\(' and '\\)' math delimiters.")
    if text.count("\\[") != text.count("\\]"):
        issues.append("Unbalanced '\\[' and '\\]' math delimiters.")
    return issues


def audit_sentence(
    orig_item: Dict[str, Any],
    edit_item: Dict[str, Any],
    glossary: List[Dict[str, Any]],
    lexicon: Dict[str, Any],
    style_profile: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    orig = orig_item["text"]
    rev = edit_item["revised"]
    sid = orig_item["sentence_id"]
    changed = bool(edit_item.get("changed", orig != rev))
    edit_types = edit_item.get("edit_types", [])
    reason = (edit_item.get("reason") or "").strip()
    issues: List[Dict[str, Any]] = []

    orig_anchors = orig_item.get("anchors") or extract_protected_anchors(orig)
    rev_anchors = extract_protected_anchors(rev)

    # Gate 1: Numbers, units, statistical markers
    if not edit_item.get("allow_numeric_format_change", False):
        orig_nums = [normalize_num_token(x) for x in orig_anchors.get("numbers_and_units", [])]
        rev_nums = [normalize_num_token(x) for x in rev_anchors.get("numbers_and_units", [])]
        missing_nums = [x for x in orig_nums if x not in rev_nums]
        added_nums = [x for x in rev_nums if x not in orig_nums]
        if missing_nums:
            issues.append({
                "gate": "Gate1_NumbersAndUnits",
                "severity": "ERROR",
                "sentence_id": sid,
                "message": f"Numeric/unit tokens altered or dropped: {missing_nums}",
            })
        if added_nums:
            issues.append({
                "gate": "Gate1_NumbersAndUnits",
                "severity": "ERROR",
                "sentence_id": sid,
                "message": f"Unverified new numeric/unit tokens introduced: {added_nums}",
            })

    # Gate 2: Citations & Cross-References
    orig_cites = [normalize_cite_token(x) for x in orig_anchors.get("citations", [])]
    rev_cites = [normalize_cite_token(x) for x in rev_anchors.get("citations", [])]
    if sorted(orig_cites) != sorted(rev_cites):
        issues.append({
            "gate": "Gate2_CitationsAndRefs",
            "severity": "ERROR",
            "sentence_id": sid,
            "message": f"Citation mismatch: original={orig_cites}, revised={rev_cites}",
        })

    orig_refs = [normalize_cite_token(x).lower() for x in orig_anchors.get("cross_refs", [])]
    rev_refs = [normalize_cite_token(x).lower() for x in rev_anchors.get("cross_refs", [])]
    if sorted(orig_refs) != sorted(rev_refs):
        issues.append({
            "gate": "Gate2_CitationsAndRefs",
            "severity": "ERROR",
            "sentence_id": sid,
            "message": f"Figure/Table/Eq cross-reference mismatch: original={orig_refs}, revised={rev_refs}",
        })

    # Gate 3: LaTeX / Math / Markup Preservation
    orig_math = orig_anchors.get("math_blocks", [])
    rev_math = rev_anchors.get("math_blocks", [])
    if orig_math != rev_math:
        issues.append({
            "gate": "Gate3_MathAndMarkup",
            "severity": "ERROR",
            "sentence_id": sid,
            "message": f"LaTeX math block altered: original={orig_math}, revised={rev_math}",
        })
    for m_err in check_unbalanced_math_or_brackets(rev):
        issues.append({
            "gate": "Gate3_MathAndMarkup",
            "severity": "ERROR",
            "sentence_id": sid,
            "message": m_err,
        })
    if orig_item.get("is_heading"):
        orig_lvl = len(re.match(r"^(#*)", orig).group(1))
        rev_lvl = len(re.match(r"^(#*)", rev).group(1))
        if orig_lvl != rev_lvl:
            issues.append({
                "gate": "Gate3_MathAndMarkup",
                "severity": "ERROR",
                "sentence_id": sid,
                "message": f"Heading level altered from H{orig_lvl} to H{rev_lvl}.",
            })

    # Gate 4: Claim-Strength (Hedge), Causality vs Correlation, and Negation Conservation
    orig_sig = orig_item.get("claim_signals") or extract_claim_signals(orig, lexicon)
    rev_sig = extract_claim_signals(rev, lexicon)

    orig_max = orig_sig.get("max_strength_tier")
    rev_max = rev_sig.get("max_strength_tier")
    if rev_max is not None:
        if (orig_max is None and rev_max >= 2) or (orig_max is not None and rev_max > orig_max and rev_max >= 2):
            issues.append({
                "gate": "Gate4_ClaimStrengthAndPolarity",
                "severity": "ERROR",
                "sentence_id": sid,
                "message": (
                    f"Claim strength inflated from tier {orig_max} ({orig_sig.get('strength_markers')}) "
                    f"to tier {rev_max} ({rev_sig.get('strength_markers')})."
                ),
            })
        elif rev_max == 3 and (orig_max is None or orig_max < 3):
            issues.append({
                "gate": "Gate4_ClaimStrengthAndPolarity",
                "severity": "ERROR",
                "sentence_id": sid,
                "message": f"Absolutist/promotional language introduced: {rev_sig.get('strength_markers')}.",
            })

    # Correlation upgraded to causation without prior causality marker
    if orig_sig.get("correlation_markers") and not orig_sig.get("causality_markers") and rev_sig.get("causality_markers"):
        issues.append({
            "gate": "Gate4_ClaimStrengthAndPolarity",
            "severity": "ERROR",
            "sentence_id": sid,
            "message": (
                f"Correlation ({orig_sig['correlation_markers']}) improperly upgraded to "
                f"causality ({rev_sig['causality_markers']})."
            ),
        })

    # Negation polarity flip check
    orig_has_neg = bool(orig_sig.get("negation_markers"))
    rev_has_neg = bool(rev_sig.get("negation_markers"))
    if orig_has_neg != rev_has_neg:
        issues.append({
            "gate": "Gate4_ClaimStrengthAndPolarity",
            "severity": "ERROR",
            "sentence_id": sid,
            "message": (
                f"Negation/scope polarity shifted: original={orig_sig.get('negation_markers')}, "
                f"revised={rev_sig.get('negation_markers')}."
            ),
        })

    # Gate 5: Glossary Consistency Sweep (check banned/deprecated variants)
    rev_lower = rev.lower()
    for term_entry in glossary:
        canonical = (term_entry.get("canonical") or "").strip()
        for var in term_entry.get("variants", []):
            var_s = var.strip()
            if not var_s or var_s.lower() == canonical.lower():
                continue
            # Avoid false positive if variant is a substring of canonical
            if var_s.lower() in canonical.lower():
                continue
            if re.search(r"(?<![A-Za-z0-9\-])" + re.escape(var_s.lower()) + r"(?![A-Za-z0-9\-])", rev_lower):
                issues.append({
                    "gate": "Gate5_GlossaryConsistency",
                    "severity": "ERROR",
                    "sentence_id": sid,
                    "message": f"Deprecated glossary variant '{var_s}' remains in revised text; canonical term is '{canonical}'.",
                })

    # Gate 6: Register & Anti-Promotional / Chinglish Lint
    for rule in lexicon.get("chinglish_and_colloquial_patterns", []):
        if re.search(rule["pattern"], rev, flags=re.IGNORECASE):
            issues.append({
                "gate": "Gate6_RegisterAndChinglishLint",
                "severity": "WARNING",
                "sentence_id": sid,
                "message": f"Pattern {rule['id']} remains in revised text: {rule['suggestion']}",
            })

    # Gate 7: Minimal-Edit Ratio & Explainability
    edit_ratio = compute_edit_ratio(orig, rev)
    max_ratio = float(style_profile.get("max_recommended_edit_ratio", 0.65))
    restructure_allowed = bool(set(edit_types) & {"chinglish_restructure", "syntax_clarity"})
    if changed and edit_ratio > max_ratio and not restructure_allowed:
        issues.append({
            "gate": "Gate7_MinimalEditAndExplainability",
            "severity": "WARNING",
            "sentence_id": sid,
            "message": f"High edit ratio ({edit_ratio:.2f} > {max_ratio:.2f}) without 'chinglish_restructure'/'syntax_clarity' justification.",
        })
    if changed and (not edit_types or not reason):
        issues.append({
            "gate": "Gate7_MinimalEditAndExplainability",
            "severity": "ERROR",
            "sentence_id": sid,
            "message": "Modified sentence is missing required 'edit_types' or 'reason'.",
        })

    metrics = {
        "edit_ratio": edit_ratio,
        "protected_tokens_verified": not any(i["severity"] == "ERROR" and i["gate"].startswith(("Gate1", "Gate2", "Gate3")) for i in issues),
        "claim_strength_verified": not any(i["severity"] == "ERROR" and i["gate"] == "Gate4_ClaimStrengthAndPolarity" for i in issues),
        "glossary_verified": not any(i["severity"] == "ERROR" and i["gate"] == "Gate5_GlossaryConsistency" for i in issues),
    }
    return issues, metrics


def audit_abbreviations_cross_document(
    ordered_sentences: List[Dict[str, Any]],
    declared_abbrs: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    issues: List[Dict[str, Any]] = []
    tracker: List[Dict[str, Any]] = []
    abbr_list = {a["abbr"]: a.get("full_form", "") for a in declared_abbrs if a.get("abbr")}

    # Also discover abbreviations defined in revised text
    first_def: Dict[str, str] = {}
    def_counts: Dict[str, List[str]] = {}
    first_use: Dict[str, str] = {}

    for s in ordered_sentences:
        sid = s["sentence_id"]
        text = s["revised"]
        for m in RE_ABBR_DEF.finditer(text):
            abbr = m.group(2).strip()
            full = m.group(1).strip()
            abbr_list.setdefault(abbr, full)
            def_counts.setdefault(abbr, []).append(sid)
            if abbr not in first_def:
                first_def[abbr] = sid

    for abbr in sorted(abbr_list.keys()):
        pat = re.compile(r"\b" + re.escape(abbr) + r"\b")
        for s in ordered_sentences:
            if pat.search(s["revised"]):
                first_use[abbr] = s["sentence_id"]
                break

        defs = def_counts.get(abbr, [])
        fu = first_use.get(abbr)
        fd = first_def.get(abbr)
        status = "ok"
        if fu and not fd:
            status = "undefined_abbreviation"
            issues.append({
                "gate": "Gate5_GlossaryConsistency",
                "severity": "WARNING",
                "sentence_id": fu,
                "message": f"Abbreviation '{abbr}' is used at {fu} without a first-mention full definition.",
            })
        elif fu and fd and fu != fd:
            status = "defined_after_first_use"
            issues.append({
                "gate": "Gate5_GlossaryConsistency",
                "severity": "WARNING",
                "sentence_id": fu,
                "message": f"Abbreviation '{abbr}' first appears at {fu} before its definition at {fd}.",
            })
        elif len(defs) > 1:
            status = "redefined_multiple_times"
            issues.append({
                "gate": "Gate5_GlossaryConsistency",
                "severity": "WARNING",
                "sentence_id": defs[1],
                "message": f"Abbreviation '{abbr}' is redefined multiple times at {defs}.",
            })

        tracker.append({
            "abbr": abbr,
            "full_form": abbr_list[abbr],
            "first_use_sentence_id": fu,
            "first_definition_sentence_id": fd,
            "definition_count": len(defs),
            "status": status,
        })
    return issues, tracker


def reconstruct_manuscript(ordered_sentences: List[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    current_pid: str | None = None
    current_parts: List[str] = []

    def flush() -> None:
        nonlocal current_parts
        if current_parts:
            # Join CJK sentences without extra spaces, Latin sentences with single space
            joined = ""
            for part in current_parts:
                if not joined:
                    joined = part
                elif re.search(r"[\u4e00-\u9fff。！？]$", joined) and re.match(r"^[\u4e00-\u9fff]", part):
                    joined += part
                else:
                    joined += " " + part
            blocks.append(joined)
            current_parts = []

    for s in ordered_sentences:
        pid = s.get("paragraph_id")
        if s.get("is_structural_block") or pid != current_pid:
            flush()
            current_pid = pid
        current_parts.append(s["revised"])
        if s.get("is_structural_block"):
            flush()
            current_pid = None

    flush()
    return "\n\n".join(blocks) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit sentence-level academic edits and compile deliverables.")
    parser.add_argument("--segmented", type=Path, required=True, help="Path to segmented_case.json")
    parser.add_argument("--edits", type=Path, required=True, help="Path to edits_draft.json")
    parser.add_argument("--resources", type=Path, default=DEFAULT_RESOURCES, help="Path to resources dir")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory (e.g. /app/output)")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any hard gate ERROR exists")
    args = parser.parse_args()

    segmented = json.loads(args.segmented.read_text(encoding="utf-8-sig"))
    edits = json.loads(args.edits.read_text(encoding="utf-8-sig"))
    lexicon = load_lexicon(args.resources)
    profiles = json.loads((args.resources / "style_profiles.json").read_text(encoding="utf-8")).get("profiles", {})

    target_register = edits.get("target_register") or segmented.get("target_register") or "journal_article"
    style_profile = profiles.get(target_register, profiles.get("journal_article", {}))
    glossary = edits.get("glossary", [])
    declared_abbrs = edits.get("abbreviations", [])

    orig_sentences: List[Dict[str, Any]] = segmented.get("sentences", [])
    edits_by_id: Dict[str, Dict[str, Any]] = {e["sentence_id"]: e for e in edits.get("sentences", [])}

    all_issues: List[Dict[str, Any]] = []
    compiled_sentences: List[Dict[str, Any]] = []
    human_review_items: List[Dict[str, Any]] = []

    # Surface glossary conflicts from Step 1
    for conf in segmented.get("glossary_conflicts", []):
        human_review_items.append({
            "type": "glossary_conflict",
            "sentence_id": None,
            "detail": conf,
        })

    # Audit every sentence in deterministic order
    for orig_item in orig_sentences:
        sid = orig_item["sentence_id"]
        if sid not in edits_by_id:
            all_issues.append({
                "gate": "Gate7_MinimalEditAndExplainability",
                "severity": "ERROR",
                "sentence_id": sid,
                "message": f"Sentence {sid} missing from edits_draft.json; retained original text.",
            })
            edit_item = {
                "sentence_id": sid,
                "original": orig_item["text"],
                "revised": orig_item["text"],
                "changed": False,
                "edit_types": ["no_change"],
                "reason": "Missing in draft; preserved original verbatim.",
                "risk_level": "high",
                "needs_human_confirmation": True,
                "confirmation_note": "Sentence was omitted from edits draft.",
            }
        else:
            edit_item = edits_by_id[sid]

        sent_issues, sent_metrics = audit_sentence(orig_item, edit_item, glossary, lexicon, style_profile)
        all_issues.extend(sent_issues)

        record = {
            "sentence_id": sid,
            "section": orig_item.get("section", "Body"),
            "paragraph_id": orig_item.get("paragraph_id", "P001"),
            "source_file": orig_item.get("source_file", ""),
            "is_structural_block": bool(orig_item.get("is_structural_block", False)),
            "original": orig_item["text"],
            "revised": edit_item["revised"],
            "changed": bool(edit_item.get("changed", orig_item["text"] != edit_item["revised"])),
            "edit_types": edit_item.get("edit_types", ["no_change"]),
            "reason": edit_item.get("reason", ""),
            "risk_level": edit_item.get("risk_level", "low"),
            "needs_human_confirmation": bool(edit_item.get("needs_human_confirmation", False)),
            "confirmation_note": edit_item.get("confirmation_note", ""),
            "gate_status": "FAIL" if any(i["severity"] == "ERROR" for i in sent_issues) else ("WARN" if sent_issues else "PASS"),
            "gate_issues": sent_issues,
            **sent_metrics,
        }
        compiled_sentences.append(record)
        if record["needs_human_confirmation"] or record["gate_status"] == "FAIL":
            human_review_items.append({
                "type": "sentence_review",
                "sentence_id": sid,
                "section": record["section"],
                "risk_level": record["risk_level"],
                "note": record["confirmation_note"] or "; ".join(i["message"] for i in sent_issues),
            })

    abbr_issues, abbr_tracker = audit_abbreviations_cross_document(compiled_sentences, declared_abbrs)
    all_issues.extend(abbr_issues)

    hard_errors = [i for i in all_issues if i["severity"] == "ERROR"]
    warnings = [i for i in all_issues if i["severity"] == "WARNING"]

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Polished manuscript
    manuscript_text = reconstruct_manuscript(compiled_sentences)
    src_formats = [s.get("format") for s in segmented.get("sources", [])]
    ext = ".tex" if src_formats == ["tex"] else ".md"
    polished_filename = f"polished_manuscript{ext}"
    (out_dir / polished_filename).write_text(manuscript_text, encoding="utf-8")

    # 2. Revision log (Markdown + JSONL)
    md_lines = [
        f"# 学术语言润色逐句修改台账（{segmented.get('case_id', 'local-case')}）",
        "",
        f"- 目标体裁：`{target_register}` | 目标语言：`{edits.get('target_language', segmented.get('target_language'))}`",
        f"- 总句数：{len(compiled_sentences)} | 修改句数：{sum(1 for r in compiled_sentences if r['changed'])} | 硬门禁错误：{len(hard_errors)} | 警告：{len(warnings)}",
        "",
        "| 句号 | 章节 | 原文 | 改文 | 修改类型 | 修改理由 | 风险 | 门禁 | 人工确认 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    jsonl_lines: List[str] = []
    for r in compiled_sentences:
        jsonl_lines.append(json.dumps(r, ensure_ascii=False))
        def esc(s: str) -> str:
            return s.replace("|", "\\|").replace("\n", "<br>")
        md_lines.append(
            f"| `{r['sentence_id']}` | {esc(r['section'])} | {esc(r['original'])} | {esc(r['revised'])} | "
            f"`{', '.join(r['edit_types'])}` | {esc(r['reason'])} | {r['risk_level']} | **{r['gate_status']}** | "
            f"{'是' if r['needs_human_confirmation'] else '否'} |"
        )
    (out_dir / "revision_log.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    (out_dir / "revision_log.jsonl").write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

    # 3. Glossary & Abbreviations
    glossary_bundle = {
        "glossary": glossary,
        "glossary_conflicts": segmented.get("glossary_conflicts", []),
        "abbreviations": abbr_tracker,
    }
    (out_dir / "glossary_and_abbreviations.json").write_text(json.dumps(glossary_bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    g_md = ["# 术语表与缩略语首现跟踪表", "", "## 统一术语表", "", "| 规范表达 | 中文 | 英文 | 已消除变体 | 来源 |", "| :--- | :--- | :--- | :--- | :--- |"]
    for g in glossary:
        g_md.append(f"| {g.get('canonical', '')} | {g.get('zh', '')} | {g.get('en', '')} | {', '.join(g.get('variants', []))} | {g.get('source', '')} |")
    g_md.extend(["", "## 缩略语首现检查", "", "| 缩写 | 全称 | 首次出现 | 首次定义 | 定义次数 | 状态 |", "| :--- | :--- | :--- | :--- | :--- | :--- |"])
    for a in abbr_tracker:
        g_md.append(f"| `{a['abbr']}` | {a['full_form']} | `{a['first_use_sentence_id']}` | `{a['first_definition_sentence_id']}` | {a['definition_count']} | {a['status']} |")
    (out_dir / "glossary_and_abbreviations.md").write_text("\n".join(g_md) + "\n", encoding="utf-8")

    # 4. QA Audit Report
    qa_report = {
        "case_id": segmented.get("case_id", "local-case"),
        "passed_all_hard_gates": len(hard_errors) == 0,
        "hard_error_count": len(hard_errors),
        "warning_count": len(warnings),
        "issues": all_issues,
        "human_review_items": human_review_items,
    }
    (out_dir / "qa_audit_report.json").write_text(json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8")
    qa_md = [
        "# 7 道自动化保真门禁核验报告",
        "",
        f"- 全部硬门禁通过：`{qa_report['passed_all_hard_gates']}`",
        f"- 硬门禁错误数：`{len(hard_errors)}` | 提醒/警告数：`{len(warnings)}` | 需人工确认项：`{len(human_review_items)}`",
        "",
        "## 问题清单",
        "",
    ]
    if not all_issues:
        qa_md.append("- 无门禁异常。")
    else:
        for iss in all_issues:
            qa_md.append(f"- [{iss['severity']}] `{iss['gate']}` (`{iss['sentence_id']}`): {iss['message']}")
    (out_dir / "qa_audit_report.md").write_text("\n".join(qa_md) + "\n", encoding="utf-8")

    # 5. Harbor result.json preserving case_id & ALL original output_contract fields
    uncertainty = list(segmented.get("anomalies_and_warnings", [])) + list(edits.get("uncertainty", []))
    result_payload: Dict[str, Any] = {}
    orig_contract = segmented.get("output_contract")
    if isinstance(orig_contract, dict):
        for k, v in orig_contract.items():
            result_payload[k] = v

    result_payload.update({
        "case_id": segmented.get("case_id", "local-case"),
        "status": "completed" if len(hard_errors) == 0 else "partial",
        "target_register": target_register,
        "target_language": edits.get("target_language", segmented.get("target_language", "en")),
        "polished_text": manuscript_text,
        "deliverables": {
            "polished_manuscript": polished_filename,
            "revision_log_md": "revision_log.md",
            "revision_log_jsonl": "revision_log.jsonl",
            "glossary_json": "glossary_and_abbreviations.json",
            "glossary_md": "glossary_and_abbreviations.md",
            "qa_audit_report": "qa_audit_report.json",
            "qa_audit_report_md": "qa_audit_report.md",
        },
        "summary_metrics": {
            "total_sentences": len(compiled_sentences),
            "changed_sentences": sum(1 for r in compiled_sentences if r["changed"]),
            "unchanged_sentences": sum(1 for r in compiled_sentences if not r["changed"]),
            "hard_gate_errors": len(hard_errors),
            "warnings": len(warnings),
            "human_confirmation_count": len(human_review_items),
        },
        "sources": segmented.get("sources", []),
        "glossary": glossary,
        "abbreviations": abbr_tracker,
        "change_records": [
            {
                "sentence_id": r["sentence_id"],
                "section": r["section"],
                "original": r["original"],
                "revised": r["revised"],
                "changed": r["changed"],
                "edit_types": r["edit_types"],
                "reason": r["reason"],
                "risk_level": r["risk_level"],
                "gate_status": r["gate_status"],
                "needs_human_confirmation": r["needs_human_confirmation"],
            }
            for r in compiled_sentences
        ],
        "human_review_items": human_review_items,
        "uncertainty": uncertainty,
    })
    (out_dir / "result.json").write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": result_payload["status"],
        "case_id": result_payload["case_id"],
        "total_sentences": len(compiled_sentences),
        "hard_gate_errors": len(hard_errors),
        "warnings": len(warnings),
        "out_dir": str(out_dir),
    }, ensure_ascii=False))
    if args.strict and hard_errors:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
