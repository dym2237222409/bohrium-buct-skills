#!/usr/bin/env python3
"""
self_test.py — End-to-End Synthetic Verification for scipdf-structured-notes

Generates a synthetic 2-column scientific PDF in pure Python (zero binary files in repo),
runs inspect_case -> parse_and_index_paper -> compile_and_verify_notes -> query_notes,
and verifies:
  1. Section skeleton alignment ('对得上')
  2. Premise-bound findings & limitations in Paper Card ('留得住')
  3. 100% fine-grained anchor resolution & numeric verification ('找得回')
  4. Acronym/synonym knowledge deduplication + multi-paper comparison matrix ('用得上')
NOTE: Strictly synthetic self-test data, NOT a formal benchmark case.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent


def _esc(s: str) -> bytes:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("utf-8")


def build_synthetic_notes_pdf(pdf_path: Path) -> None:
    ops: List[Tuple[str, float, float, float, str]] = [
        ("F1", 8.5, 54.0, 760.0, "Synthetic Catalysis Letters - Vol. 9 (EXAMPLE)"),
        ("F1", 8.5, 296.0, 28.0, "201"),
        ("F2", 14.5, 54.0, 720.0, "Turnover Frequency Scaling in Zeolite Microreactors"),
        ("F1", 10.0, 54.0, 696.0, "Hao Lin, Yuxuan Zhou (2025)"),
        ("F2", 11.0, 54.0, 668.0, "Abstract"),
        ("F1", 10.0, 54.0, 650.0, "We study Turnover Frequency (TOF) in zeolite microreactors under laminar flow."),
        # Two-column interleaved body
        ("F2", 11.5, 54.0, 610.0, "1 Introduction"),
        ("F2", 11.5, 326.0, 610.0, "2 Methods"),
        ("F1", 10.0, 54.0, 588.0, "Conventional packed beds suffer hot spots"),
        ("F1", 10.0, 326.0, 588.0, "HZSM-5 catalyst (Si/Al = 40) was tested at"),
        ("F1", 10.0, 54.0, 574.0, "above 300 C, limiting selectivity."),
        ("F1", 10.0, 326.0, 574.0, "260 C and 12.5 mL/min flow rate."),
        ("F1", 10.0, 54.0, 540.0, "TOF = n_prod / (n_site * t) (1)"),
        ("F2", 11.5, 326.0, 540.0, "3 Results and Limitations"),
        ("F2", 10.0, 54.0, 504.0, "Table 1: Reactor Metrics (EXAMPLE)"),
        ("F1", 9.5, 54.0, 488.0, "| Reactor | Temp (C) | TOF (h^-1) | Selectivity (%) |"),
        ("F1", 9.5, 54.0, 474.0, "| Zeo-Micro | 260 | 38.4 | 96.1 |"),
        ("F1", 10.0, 326.0, 518.0, "Zeo-Micro reaches 96.1% selectivity and 38.4 h^-1"),
        ("F1", 10.0, 326.0, 504.0, "only when temperature stays at 260 C. However,"),
        ("F1", 10.0, 326.0, 490.0, "coking deactivation beyond 48 h remains a limitation."),
    ]
    stream = b"\n".join(
        b"BT /" + f.encode("ascii") + f" {sz:.1f} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm (".encode("ascii") + _esc(t) + b") Tj ET"
        for f, sz, x, y, t in ops
    )
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offs = [0]
    for idx, body in enumerate(objs, start=1):
        offs.append(len(buf))
        buf.extend(f"{idx} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xp = len(buf)
    buf.extend(f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode("ascii"))
    for o in offs[1:]:
        buf.extend(f"{o:010d} 00000 n \n".encode("ascii"))
    buf.extend(f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xp}\n%%EOF\n".encode("ascii"))
    pdf_path.write_bytes(bytes(buf))


def run_py(args: List[str]) -> dict:
    p = subprocess.run([sys.executable, *args], capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", default=None)
    args = ap.parse_args()

    tmp_ctx = tempfile.TemporaryDirectory(prefix="notes_selftest_") if not args.work_dir else None
    base = Path(args.work_dir) if args.work_dir else Path(tmp_ctx.name)  # type: ignore[union-attr]
    try:
        inp, work, out = base / "input", base / "work", base / "output"
        inp.mkdir(parents=True, exist_ok=True)
        pdf_p = inp / "synthetic_zeolite_paper.pdf"
        build_synthetic_notes_pdf(pdf_p)

        case_p = inp / "case.json"
        case_p.write_text(json.dumps({
            "case_id": "synthetic-notes-001",
            "pdf_path": str(pdf_p),
            "output_contract": {"required_fields": ["case_id", "results", "sources", "status"]},
        }, indent=2), encoding="utf-8")

        # 1. inspect_case
        run_py([str(SCRIPT_DIR / "inspect_case.py"), "--input-dir", str(inp), "--work-dir", str(work), "--output-dir", str(out)])

        # 2. parse_and_index_paper
        idx_dir = work / "indexed"
        idx_res = run_py([str(SCRIPT_DIR / "parse_and_index_paper.py"), "--source", str(pdf_p), "--out-dir", str(idx_dir)])
        skel = json.loads((idx_dir / "section_skeleton.json").read_text(encoding="utf-8"))
        reg = json.loads((idx_dir / "anchor_registry.json").read_text(encoding="utf-8"))["anchors"]

        # Map anchors dynamically from parsed skeleton
        sec_map = {s["heading"]: s for s in skel["sections"]}
        tab_aid = next(iter(k for k, v in reg.items() if v["kind"] == "table"))
        eq_aid = next(iter(k for k, v in reg.items() if v["kind"] == "equation"))
        res_p_aid = sec_map["3 Results and Limitations"]["paragraph_anchors"][0]
        intro_p_aid = sec_map["1 Introduction"]["paragraph_anchors"][0]
        meth_p_aid = sec_map["2 Methods"]["paragraph_anchors"][0]

        # 3. Draft structured notes (deliberately adding both 'Turnover Frequency' and 'TOF' to test acronym merge!)
        draft = {
            "paper_card": {
                "title": skel["title_detected"],
                "authors_and_year": "Hao Lin, Yuxuan Zhou (2025)",
                "research_problem_and_gap": {"summary": "Hot spots above 300 C in packed beds degrade catalytic selectivity.", "anchors": [intro_p_aid]},
                "datasets_materials_or_subjects": {"summary": "HZSM-5 zeolite catalyst (Si/Al = 40) in Zeo-Micro reactor.", "anchors": [meth_p_aid, tab_aid]},
                "methodology_and_conditions": {"summary": "Isothermal laminar-flow test at 260 C and 12.5 mL/min; rate evaluated via Eq. (1).", "anchors": [meth_p_aid, eq_aid]},
                "condition_bound_findings": [
                    {
                        "finding_id": "F01",
                        "claim": "Zeo-Micro achieves 96.1% selectivity and TOF of 38.4 h^-1.",
                        "quantitative_metrics": ["96.1%", "38.4"],
                        "holding_preconditions": "Only when operating temperature stays at 260 C under 12.5 mL/min flow.",
                        "anchors": [res_p_aid, tab_aid],
                    }
                ],
                "limitations_and_open_questions": {
                    "author_stated_limitations": [{"point": "Coking deactivation beyond 48 h time-on-stream.", "anchors": [res_p_aid]}],
                    "unreported_or_missing_controls": [{"field": "pressure_drop_measurement", "checked_scope": "All sections p.1"}],
                },
            },
            "section_summaries": [
                {
                    "section_id": s["section_id"],
                    "heading": s["heading"],
                    "section_role_and_purpose": f"Establishes {s['heading']} stage of the study.",
                    "methods_or_arguments": "Synthesized from indexed section blocks.",
                    "findings_and_conditions": "Anchored to verified block IDs.",
                    "logical_bridge_to_next": "Motivates subsequent experimental evaluation.",
                    "evidence_anchors": (s["paragraph_anchors"] + s["table_anchors"] + s["equation_anchors"])[:2],
                }
                for s in skel["sections"] if s["heading"] != "Front Matter"
            ],
            "knowledge_items": [
                {
                    "type": "variable_or_symbol",
                    "canonical_name": "Turnover Frequency",
                    "aliases": [],
                    "definition_or_statement": "Defined by TOF = n_prod / (n_site * t).",
                    "conditions_or_units": "h^-1",
                    "tags": ["kinetics"],
                    "verification_tier": "machine_verifiable",
                    "anchors": [eq_aid],
                },
                {
                    "type": "variable_or_symbol",
                    "canonical_name": "TOF",
                    "aliases": [],
                    "definition_or_statement": "Reaches 38.4 h^-1 in Zeo-Micro at 260 C.",
                    "conditions_or_units": "h^-1",
                    "tags": ["results"],
                    "verification_tier": "machine_verifiable",
                    "anchors": [tab_aid, res_p_aid],
                },
            ],
        }
        draft_p = work / "structured_notes_draft.json"
        draft_p.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")

        comp_res = run_py([
            str(SCRIPT_DIR / "compile_and_verify_notes.py"),
            "--index-dir", str(idx_dir),
            "--draft-json", str(draft_p),
            "--case-json", str(case_p),
            "--output-dir", str(out),
            "--strict",
        ])
        assert comp_res["valid"] is True, f"Compile failed: {comp_res}"
        assert comp_res["metrics"]["synonym_or_abbreviation_merges"] == 1
        assert comp_res["metrics"]["anchors_resolved_ratio"] == 1.0
        assert comp_res["metrics"]["numeric_claims_verified_ratio"] == 1.0

        # 4. query_notes search & card comparison
        q_res = run_py([
            str(SCRIPT_DIR / "query_notes.py"),
            "--jsonl", str(out / "knowledge_items.jsonl"),
            "--query", "tof",
            "--compare-cards", str(out / "result.json"),
        ])
        assert q_res["query_hit_count"] == 1 and q_res["comparison_papers"] == 1

        print(json.dumps({
            "self_test": "PASSED",
            "notice": "SYNTHETIC EXAMPLE ONLY — NOT A FORMAL BENCHMARK CASE",
            "metrics": comp_res["metrics"],
            "verified_assertions": [
                "section_skeleton_1_to_1_alignment",
                "premise_bound_findings_and_limitations_recorded",
                "fine_grained_anchors_100pct_dereferenced",
                "abbreviation_tof_turnover_frequency_merged",
                "searchable_jsonl_and_comparison_matrix_verified",
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
