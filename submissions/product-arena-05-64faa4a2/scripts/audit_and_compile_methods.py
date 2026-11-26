#!/usr/bin/env python3
"""7-gate deterministic auditor and multi-artifact compiler for Methods & Experiments."""

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


def normalize_num(val: str) -> str:
    s = str(val).strip().strip("'\"` ,;)")
    try:
        if re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", s):
            num = float(s)
            if num.is_integer() and "e" not in s.lower() and "." not in s:
                return str(int(num))
            return f"{num:.10g}"
    except Exception:
        pass
    return s.lower()


def extract_numbers(text: str) -> List[str]:
    return re.findall(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", str(text))


def is_missing_marker(val: str, markers: List[str]) -> bool:
    s = str(val or "").strip()
    if not s:
        return True
    upper = s.upper()
    return any(m.upper() in upper for m in markers)


def run_audit(
    facts: Dict[str, Any],
    plan: Dict[str, Any],
    checklist: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    issues: List[Dict[str, Any]] = []
    gates: Dict[str, Dict[str, Any]] = {}
    missing_markers = checklist.get("missing_markers", [])

    # Build lookup of grounded values from experiment_facts
    fact_values_by_key: Dict[str, Set[str]] = {}
    all_fact_raw_values: Set[str] = set()
    for f in facts.get("parameter_facts", []):
        ck = str(f.get("canonical_key", "")).lower()
        nv = str(f.get("normalized_value", ""))
        rv = str(f.get("value", "")).lower()
        fact_values_by_key.setdefault(ck, set()).update({nv, rv})
        for n in extract_numbers(rv):
            fact_values_by_key[ck].add(normalize_num(n))
        all_fact_raw_values.update({nv, rv})
    allowed_nums: Set[str] = set(facts.get("all_numeric_tokens", []))

    prose = plan.get("prose_sections", {})
    full_prose = "\n\n".join(str(v) for v in prose.values())
    author_questions = plan.get("author_questions") or []

    # --- Gate 1: Six-Element Completeness ---
    g1_errors: List[str] = []
    for sec in ("method_steps", "experimental_setup", "datasets_and_splits", "baselines_and_evaluation", "reproducibility_and_limitations"):
        if len(str(prose.get(sec, "")).strip()) < 25:
            g1_errors.append(f"Prose section '{sec}' is missing or too short.")
    if not plan.get("method_steps"):
        g1_errors.append("method_steps list is empty.")
    if not plan.get("parameter_table"):
        g1_errors.append("parameter_table is empty.")
    if not plan.get("datasets"):
        g1_errors.append("datasets list is empty.")
    if not plan.get("baselines"):
        g1_errors.append("baselines list is empty.")
    if not plan.get("limitations"):
        g1_errors.append("limitations list is empty.")
    gates["gate1_six_element_coverage"] = {"passed": not g1_errors, "errors": g1_errors}
    for e in g1_errors:
        issues.append({"gate": "gate1_six_element_coverage", "severity": "error", "message": e})

    # --- Gate 2: Parameter <-> Source Grounding ---
    g2_errors: List[str] = []
    g2_warnings: List[str] = []
    grounded_count = 0
    for p in plan.get("parameter_table", []):
        pname = str(p.get("name", "?"))
        ck = str(p.get("canonical_key", pname)).lower()
        pval = str(p.get("value", "")).strip()
        vstatus = str(p.get("verification_status", "verified"))
        loc = str(p.get("source_locator", "")).strip()

        if vstatus == "needs_author_confirmation" or is_missing_marker(pval, missing_markers):
            if not any(pname.lower() in str(q).lower() or ck in str(q).lower() for q in author_questions):
                g2_warnings.append(f"Unconfirmed parameter '{pname}' should also be listed in author_questions.")
            continue

        if not loc:
            g2_errors.append(f"Verified parameter '{pname}' is missing source_locator.")
            continue

        norm_v = normalize_num(pval)
        key_pool = fact_values_by_key.get(ck, set())
        nums_in_val = [normalize_num(n) for n in extract_numbers(pval)]
        matched = (
            norm_v in key_pool
            or pval.lower() in key_pool
            or norm_v in all_fact_raw_values
            or (nums_in_val and all(n in allowed_nums for n in nums_in_val))
        )
        if matched:
            grounded_count += 1
        else:
            g2_errors.append(
                f"Parameter '{pname}' value '{pval}' (status={vstatus}) not found in extracted facts/configs/logs (possible fabrication)."
            )
    gates["gate2_parameter_source_grounding"] = {
        "passed": not g2_errors,
        "grounded_count": grounded_count,
        "total_params": len(plan.get("parameter_table", [])),
        "errors": g2_errors,
        "warnings": g2_warnings,
    }
    for e in g2_errors:
        issues.append({"gate": "gate2_parameter_source_grounding", "severity": "error", "message": e})
    for w in g2_warnings:
        issues.append({"gate": "gate2_parameter_source_grounding", "severity": "warning", "message": w})

    # --- Gate 3: Prose <-> Table Numeric Consistency ---
    g3_errors: List[str] = []
    # Check that each verified parameter's numeric value (if the parameter name appears in prose) is not contradicted in prose
    for p in plan.get("parameter_table", []):
        if p.get("verification_status") == "needs_author_confirmation":
            continue
        pname = str(p.get("name", "")).strip()
        ck = str(p.get("canonical_key", "")).strip()
        pval = str(p.get("value", "")).strip()
        table_nums = [normalize_num(n) for n in extract_numbers(pval)]
        if not table_nums:
            continue
        # Search for explicit assignment patterns like "learning rate = X" or "batch size of X" in prose
        aliases = [a for a in {pname, ck, ck.replace("_", " ")} if len(a) >= 3]
        for alias in aliases:
            pat = re.compile(
                re.escape(alias) + r"\s*(?:=|is|of|was|为|设为|取|采用|:)?\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
                re.IGNORECASE,
            )
            for m in pat.finditer(full_prose):
                prose_num = normalize_num(m.group(1))
                # If conflict was resolved, prose may mention the rejected default too; allow if in conflict_resolutions
                resolved_vals = {
                    normalize_num(cv)
                    for cr in plan.get("conflict_resolutions", [])
                    if cr.get("canonical_key") == ck
                    for cv in cr.get("conflicting_values", [])
                }
                if prose_num not in table_nums and prose_num not in resolved_vals:
                    g3_errors.append(
                        f"Prose-table mismatch for '{pname}': prose states '{m.group(1)}' but parameter_table specifies '{pval}'."
                    )
    gates["gate3_prose_table_consistency"] = {"passed": not g3_errors, "errors": g3_errors}
    for e in g3_errors:
        issues.append({"gate": "gate3_prose_table_consistency", "severity": "error", "message": e})

    # --- Gate 4: Cross-File Conflict Disclosure ---
    g4_errors: List[str] = []
    detected_conflicts = facts.get("cross_file_conflicts", [])
    resolved_keys = {str(c.get("canonical_key", "")).lower() for c in plan.get("conflict_resolutions", [])}
    question_blob = json.dumps(author_questions, ensure_ascii=False).lower()
    disclosed = 0
    for conf in detected_conflicts:
        ck = str(conf.get("canonical_key", "")).lower()
        if ck in resolved_keys or ck in question_blob:
            disclosed += 1
        else:
            g4_errors.append(
                f"Cross-file conflict on '{ck}' was detected in inputs but NOT disclosed in conflict_resolutions or author_questions."
            )
    gates["gate4_cross_file_conflict_disclosure"] = {
        "passed": not g4_errors,
        "detected": len(detected_conflicts),
        "disclosed": disclosed,
        "errors": g4_errors,
    }
    for e in g4_errors:
        issues.append({"gate": "gate4_cross_file_conflict_disclosure", "severity": "error", "message": e})

    # --- Gate 5: Train/Val/Test Isolation & Control Fairness ---
    g5_errors: List[str] = []
    setup = plan.get("experimental_setup", {})
    for phase in ("training", "validation", "testing"):
        if not str(setup.get(phase, "")).strip():
            g5_errors.append(f"experimental_setup.{phase} is empty; train/val/test protocols must be described separately.")
    for bl in plan.get("baselines", []):
        bname = bl.get("name", "?")
        if not str(bl.get("metrics", "")).strip():
            g5_errors.append(f"Baseline '{bname}' is missing evaluation metrics.")
        if not str(bl.get("same_budget_verified", "")).strip():
            g5_errors.append(f"Baseline '{bname}' must explicitly state same_budget_verified (true/false/unknown).")
    gates["gate5_isolation_and_control_fairness"] = {"passed": not g5_errors, "errors": g5_errors}
    for e in g5_errors:
        issues.append({"gate": "gate5_isolation_and_control_fairness", "severity": "error", "message": e})

    # --- Gate 6: Step Executability & Reproducibility Checklist ---
    g6_errors: List[str] = []
    for st in plan.get("method_steps", []):
        sid = st.get("step_id", "?")
        for req_f in ("inputs", "operation", "outputs"):
            if not str(st.get(req_f, "")).strip():
                g6_errors.append(f"Method step '{sid}' missing '{req_f}'.")
    rep = plan.get("reproducibility_checklist", {})
    for rf in ("code_version", "environment", "seeds", "data_access"):
        if not str(rep.get(rf, "")).strip():
            g6_errors.append(f"reproducibility_checklist.{rf} is empty (use [MISSING_FROM_MATERIALS] if absent).")
    if not rep.get("commands"):
        g6_errors.append("reproducibility_checklist.commands is empty (include command or explicit missing marker).")
    gates["gate6_executability_and_reproducibility"] = {"passed": not g6_errors, "errors": g6_errors}
    for e in g6_errors:
        issues.append({"gate": "gate6_executability_and_reproducibility", "severity": "error", "message": e})

    # --- Gate 7: Concrete Limitation Lint ---
    g7_errors: List[str] = []
    for lim in plan.get("limitations", []):
        regime = str(lim.get("failure_regime", "")).strip()
        trigger = str(lim.get("trigger_condition", "")).strip()
        scope = str(lim.get("applicable_scope", "")).strip()
        if not regime or not trigger or not scope:
            g7_errors.append("Each limitation must specify failure_regime, trigger_condition, and applicable_scope.")
        combined_lim = f"{regime} {trigger} {scope}"
        for lang_k in ("zh", "en"):
            for pat in checklist.get("boilerplate_limitation_patterns", {}).get(lang_k, []):
                m = re.search(pat, combined_lim, re.IGNORECASE)
                if m:
                    g7_errors.append(f"Generic boilerplate limitation detected: '{m.group(0)}'.")
    # Also scan reproducibility_and_limitations prose
    lim_prose = str(prose.get("reproducibility_and_limitations", ""))
    for lang_k in ("zh", "en"):
        for pat in checklist.get("boilerplate_limitation_patterns", {}).get(lang_k, []):
            m = re.search(pat, lim_prose, re.IGNORECASE)
            if m:
                g7_errors.append(f"Generic boilerplate phrase in limitations prose: '{m.group(0)}'.")
    gates["gate7_concrete_limitation_lint"] = {"passed": not g7_errors, "errors": g7_errors}
    for e in g7_errors:
        issues.append({"gate": "gate7_concrete_limitation_lint", "severity": "error", "message": e})

    overall_pass = all(g["passed"] for g in gates.values())
    return {
        "overall_pass": overall_pass,
        "gates": gates,
        "grounded_count": grounded_count,
        "conflicts_detected": len(detected_conflicts),
        "conflicts_disclosed": disclosed,
    }, issues


def compile_manuscript(plan: Dict[str, Any]) -> str:
    lang = plan.get("language", "zh")
    prose = plan.get("prose_sections", {})
    if lang == "zh":
        titles = (
            "# 方法与实验",
            "## 1. 方法步骤",
            "## 2. 实验设置与运行环境",
            "## 3. 数据集与划分策略",
            "## 4. 对照实验与评价指标",
            "## 5. 可复现性与限制条件",
        )
    else:
        titles = (
            "# Methods and Experiments",
            "## 1. Method Pipeline",
            "## 2. Experimental Setup and Environment",
            "## 3. Datasets and Splits",
            "## 4. Baselines and Evaluation Protocol",
            "## 5. Reproducibility and Limitations",
        )
    sections = [
        titles[0], "",
        titles[1], "", prose.get("method_steps", "").strip(), "",
        titles[2], "", prose.get("experimental_setup", "").strip(), "",
        titles[3], "", prose.get("datasets_and_splits", "").strip(), "",
        titles[4], "", prose.get("baselines_and_evaluation", "").strip(), "",
        titles[5], "", prose.get("reproducibility_and_limitations", "").strip(), "",
    ]
    return "\n".join(sections)


def compile_parameter_table(plan: Dict[str, Any]) -> str:
    lines = [
        "# Parameter Table with Source Provenance",
        "",
        "| Parameter | Canonical Key | Value | Unit | Target Component | Source (`file:line`) | Verification Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in plan.get("parameter_table", []):
        row = [
            str(p.get("name", "")),
            str(p.get("canonical_key", "")),
            str(p.get("value", "")),
            str(p.get("unit", "-")),
            str(p.get("target_component", "")),
            str(p.get("source_locator", "")),
            str(p.get("verification_status", "")),
        ]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in row) + " |")
    lines.append("")
    return "\n".join(lines)


def compile_repro_and_questions(plan: Dict[str, Any], audit: Dict[str, Any], issues: List[Dict[str, Any]]) -> str:
    rep = plan.get("reproducibility_checklist", {})
    lines = [
        "# Reproducibility Checklist, Conflicts & Author Confirmation Questions",
        "",
        f"- **Overall 7-Gate Audit**: `{'PASS' if audit['overall_pass'] else 'FAIL'}`",
        "",
        "## 1. Reproducibility Checklist",
        f"- **Code Version**: {rep.get('code_version', '')}",
        f"- **Environment**: {rep.get('environment', '')}",
        f"- **Random Seeds**: {rep.get('seeds', '')}",
        f"- **Data Access**: {rep.get('data_access', '')}",
        "- **Commands**:",
    ]
    for cmd in rep.get("commands", []):
        lines.append(f"  - `{cmd}`")

    lines.extend(["", "## 2. Cross-File Conflicts Disclosed"])
    if plan.get("conflict_resolutions"):
        for c in plan["conflict_resolutions"]:
            lines.append(
                f"- **`{c.get('canonical_key')}`**: conflicting values `{c.get('conflicting_values')}` -> adopted `{c.get('adopted_value')}` ({c.get('resolution')})"
            )
    else:
        lines.append("- No cross-file conflicts recorded.")

    lines.extend(["", "## 3. Author Confirmation Questions"])
    if plan.get("author_questions"):
        for idx, q in enumerate(plan["author_questions"], 1):
            lines.append(f"{idx}. **[{q.get('element')}]** ({q.get('gap_or_conflict')}): {q.get('question')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## 4. 7-Gate Audit Summary", "", "| Gate | Status | Details |", "|---|---|---|"])
    for gname, ginfo in audit["gates"].items():
        st = "PASS" if ginfo["passed"] else "FAIL"
        msgs = (ginfo.get("errors") or []) + (ginfo.get("warnings") or [])
        lines.append(f"| `{gname}` | **{st}** | {('; '.join(msgs) if msgs else 'OK').replace('|', '/')} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Methods & Experiments plan and compile deliverables.")
    parser.add_argument("--facts", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    facts = load_json(Path(args.facts))
    plan = load_json(Path(args.plan))
    checklist = load_json(SKILL_ROOT / "resources" / "six_elements_checklist.json")

    audit, issues = run_audit(facts, plan, checklist)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manuscript_md = compile_manuscript(plan)
    param_md = compile_parameter_table(plan)
    repro_md = compile_repro_and_questions(plan, audit, issues)

    (out_dir / "methods_and_experiments.md").write_text(manuscript_md, encoding="utf-8")
    (out_dir / "parameter_table.md").write_text(param_md, encoding="utf-8")
    (out_dir / "reproducibility_and_questions.md").write_text(repro_md, encoding="utf-8")

    tex_name = None
    if plan.get("target_format") in {"latex", "both"}:
        tex_name = "methods_and_experiments.tex"
        prose = plan.get("prose_sections", {})
        tex_content = (
            "% Auto-compiled editable LaTeX draft from scipdf-methods-experiments-writer\n"
            "\\section{Methods}\n" + prose.get("method_steps", "").strip()
            + "\n\n\\section{Experiments}\n\\subsection{Experimental Setup}\n" + prose.get("experimental_setup", "").strip()
            + "\n\n\\subsection{Datasets and Splits}\n" + prose.get("datasets_and_splits", "").strip()
            + "\n\n\\subsection{Baselines and Evaluation}\n" + prose.get("baselines_and_evaluation", "").strip()
            + "\n\n\\subsection{Reproducibility and Limitations}\n" + prose.get("reproducibility_and_limitations", "").strip()
            + "\n"
        )
        (out_dir / tex_name).write_text(tex_content, encoding="utf-8")

    provenance = {
        "case_id": facts.get("case_id", plan.get("case_id", "unknown-case")),
        "method_steps": plan.get("method_steps", []),
        "parameter_table": plan.get("parameter_table", []),
        "datasets": plan.get("datasets", []),
        "baselines": plan.get("baselines", []),
        "conflict_resolutions": plan.get("conflict_resolutions", []),
        "limitations": plan.get("limitations", []),
    }
    (out_dir / "fact_provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")

    has_unresolved_gaps = bool(plan.get("author_questions"))
    status = "completed" if audit["overall_pass"] else "partial"

    uncertainty = [
        {"item": f"{q.get('element')}: {q.get('gap_or_conflict')}", "action_needed": q.get("question", "")}
        for q in plan.get("author_questions", [])
    ]
    for it in issues:
        if it["severity"] == "error":
            uncertainty.append({"item": it["message"], "action_needed": f"Fix {it['gate']}"})

    sources = [{"source_file": f["path"], "ext": f["ext"]} for f in facts.get("files_indexed", [])]

    result: Dict[str, Any] = {
        "case_id": facts.get("case_id", plan.get("case_id", "unknown-case")),
        "status": status,
        "skill_name": "scipdf-methods-experiments-writer",
        "deliverables": {
            "manuscript_md": "methods_and_experiments.md",
            "manuscript_tex": tex_name,
            "parameter_table_md": "parameter_table.md",
            "reproducibility_and_questions_md": "reproducibility_and_questions.md",
            "fact_provenance_json": "fact_provenance.json",
        },
        "manuscript": manuscript_md,
        "summary": {
            "method_steps_count": len(plan.get("method_steps", [])),
            "parameters_grounded": audit["grounded_count"],
            "conflicts_detected": audit["conflicts_detected"],
            "conflicts_disclosed": audit["conflicts_disclosed"],
            "author_questions_count": len(plan.get("author_questions", [])),
            "has_author_confirmation_items": has_unresolved_gaps,
            "audit_passed": audit["overall_pass"],
        },
        "sources": sources,
        "uncertainty": uncertainty,
        "change_records": plan.get("change_records", []),
        "audit_gates": audit["gates"],
    }

    contract = facts.get("output_contract") or {}
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
                "output_dir": str(out_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0 if audit["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
