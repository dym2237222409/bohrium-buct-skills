#!/usr/bin/env python3
"""
self_test.py — Zero-dependency end-to-end self-test for scipdf-academic-polisher.
Tests both NEGATIVE gate rejection (claim inflation, dropped numbers/citations,
causality shift, negation flip, glossary drift, glossary conflict) and
POSITIVE end-to-end compilation on synthetic bilingual manuscript inputs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
RESOURCES = SKILL_ROOT / "resources"


def run_py(script: Path, args: list[str], expect_code: int = 0) -> dict:
    cmd = [sys.executable, str(script)] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != expect_code:
        raise AssertionError(
            f"Command failed: {cmd}\nExpected rc={expect_code}, got rc={proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="polish_selftest_") as tmp:
        root = Path(tmp)
        in_dir = root / "input"
        work_dir = root / "work"
        out_bad = root / "out_bad"
        out_good = root / "out_good"
        in_dir.mkdir()
        work_dir.mkdir()

        # Synthetic manuscript with numbers, units, citations, cross-refs, math, hedges, Chinglish, glossary drift
        manuscript = (
            "# Abstract\n\n"
            "With the rapid development of electrocatalysis, a lot of researchers studies single-atom catalysts [1,2]. "
            "Our turn-over frequency measurements at 350 °C suggests that Fe-N4 sites may be associated with higher CO2 conversion ($\\eta = 180\\text{ mV}$, p < 0.05, Fig. 2). "
            "However, the catalyst did not retain full stability after 100 h.\n\n"
            "# Results\n\n"
            "As shown in Table 1, the TOF remained at 12.4 ± 0.3 s⁻¹ under acidic conditions [3].\n"
        )
        (in_dir / "draft.md").write_text(manuscript, encoding="utf-8")
        # Glossary with an intentional conflict to test conflict detection
        (in_dir / "glossary.csv").write_text(
            "source,target\n"
            "转化频率,turnover frequency\n"
            "转化频率,turn-over frequency\n"
            "单原子催化剂,single-atom catalyst\n",
            encoding="utf-8",
        )
        (in_dir / "case.json").write_text(json.dumps({
            "case_id": "synthetic-polish-001",
            "files": ["draft.md", "glossary.csv"],
            "output_contract": {
                "expected_format": "markdown_plus_changelog",
                "preserve_me": "contract_ok",
            },
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (in_dir / "task.md").write_text(
            "Polish the journal article draft while preserving numbers, citations, and hedge strength.",
            encoding="utf-8",
        )

        # Step 1: Inspect & segment
        seg_path = work_dir / "segmented.json"
        seg_summary = run_py(
            SCRIPT_DIR / "inspect_and_segment.py",
            ["--case", str(in_dir / "case.json"), "--task", str(in_dir / "task.md"), "--out", str(seg_path)],
        )
        assert seg_summary["glossary_conflicts"] == 1, f"Expected 1 glossary conflict, got {seg_summary}"
        segmented = json.loads(seg_path.read_text(encoding="utf-8"))
        sents = segmented["sentences"]
        assert len(sents) == 6, f"Expected 6 units (2 headings + 4 sentences), got {len(sents)}"

        # Step 2A: NEGATIVE TEST — bad polish introducing claim inflation, dropped number, dropped cite, negation flip, glossary drift
        bad_edits = {
            "case_id": "synthetic-polish-001",
            "target_register": "journal_article",
            "target_language": "en",
            "glossary": [
                {"canonical": "turnover frequency", "zh": "转化频率", "en": "turnover frequency", "variants": ["turn-over frequency"]}
            ],
            "abbreviations": [{"abbr": "TOF", "full_form": "turnover frequency"}],
            "sentences": [
                {"sentence_id": "S001", "original": sents[0]["text"], "revised": sents[0]["text"], "changed": False, "edit_types": ["no_change"], "reason": "Heading", "risk_level": "low", "needs_human_confirmation": False},
                # Dropped citation [1,2]
                {"sentence_id": "S002", "original": sents[1]["text"], "revised": "Recent advances in electrocatalysis have prompted extensive studies of single-atom catalysts.", "changed": True, "edit_types": ["chinglish_restructure"], "reason": "Removed Chinglish.", "risk_level": "medium", "needs_human_confirmation": False},
                # Inflated hedge 'may be associated with' -> 'proves ... directly causes', kept banned variant 'turn-over frequency', dropped '350 °C'
                {"sentence_id": "S003", "original": sents[2]["text"], "revised": "Our turn-over frequency measurements prove that Fe-N4 sites directly cause higher CO2 conversion ($\\eta = 180\\text{ mV}$, p < 0.05, Fig. 2).", "changed": True, "edit_types": ["grammar_agreement"], "reason": "Fixed verb.", "risk_level": "low", "needs_human_confirmation": False},
                # Flipped negation ('did not retain' -> 'retained')
                {"sentence_id": "S004", "original": sents[3]["text"], "revised": "However, the catalyst retained full stability after 100 h.", "changed": True, "edit_types": ["syntax_clarity"], "reason": "Simplified.", "risk_level": "medium", "needs_human_confirmation": False},
                {"sentence_id": "S005", "original": sents[4]["text"], "revised": sents[4]["text"], "changed": False, "edit_types": ["no_change"], "reason": "Heading", "risk_level": "low", "needs_human_confirmation": False},
                {"sentence_id": "S006", "original": sents[5]["text"], "revised": sents[5]["text"], "changed": False, "edit_types": ["no_change"], "reason": "Unchanged", "risk_level": "low", "needs_human_confirmation": False},
            ],
        }
        bad_edits_path = work_dir / "edits_bad.json"
        bad_edits_path.write_text(json.dumps(bad_edits, ensure_ascii=False, indent=2), encoding="utf-8")
        bad_res = run_py(
            SCRIPT_DIR / "audit_and_compile_polish.py",
            ["--segmented", str(seg_path), "--edits", str(bad_edits_path), "--resources", str(RESOURCES), "--out-dir", str(out_bad), "--strict"],
            expect_code=2,
        )
        qa_bad = json.loads((out_bad / "qa_audit_report.json").read_text(encoding="utf-8"))
        fired_gates = {i["gate"] for i in qa_bad["issues"] if i["severity"] == "ERROR"}
        for expected_gate in (
            "Gate1_NumbersAndUnits",
            "Gate2_CitationsAndRefs",
            "Gate4_ClaimStrengthAndPolarity",
            "Gate5_GlossaryConsistency",
        ):
            assert expected_gate in fired_gates, f"Expected {expected_gate} to fire, got {fired_gates}"

        # Step 2B: POSITIVE TEST — faithful minimal polish passing all 7 gates
        good_edits = {
            "case_id": "synthetic-polish-001",
            "target_register": "journal_article",
            "target_language": "en",
            "glossary": [
                {"canonical": "turnover frequency", "zh": "转化频率", "en": "turnover frequency", "variants": ["turn-over frequency"], "source": "glossary.csv (conflict resolved provisionally)"}
            ],
            "abbreviations": [{"abbr": "TOF", "full_form": "turnover frequency", "first_mention_sentence_id": "S003"}],
            "sentences": [
                {"sentence_id": "S001", "original": sents[0]["text"], "revised": sents[0]["text"], "changed": False, "edit_types": ["no_change"], "reason": "Heading retained.", "risk_level": "low", "needs_human_confirmation": False},
                {"sentence_id": "S002", "original": sents[1]["text"], "revised": "Recent advances in electrocatalysis have prompted extensive investigation of single-atom catalysts [1,2].", "changed": True, "edit_types": ["chinglish_restructure", "register_tone", "grammar_agreement"], "reason": "Replaced Chinglish opening and colloquial quantifier, fixed subject-verb agreement, preserved [1,2].", "risk_level": "medium", "needs_human_confirmation": False},
                {"sentence_id": "S003", "original": sents[2]["text"], "revised": "Our turnover frequency (TOF) measurements at 350 °C suggest that Fe-N4 sites may be associated with higher CO2 conversion ($\\eta = 180\\text{ mV}$, p < 0.05, Fig. 2).", "changed": True, "edit_types": ["terminology_consistency", "abbreviation_standardization", "grammar_agreement"], "reason": "Unified 'turnover frequency (TOF)' with first-mention definition, fixed verb agreement, preserved exact temperature, math, p-value, Fig. 2, and tentative correlational hedge.", "risk_level": "low", "needs_human_confirmation": True, "confirmation_note": "Glossary had conflicting variants for '转化频率'; provisionally used 'turnover frequency'."},
                {"sentence_id": "S004", "original": sents[3]["text"], "revised": sents[3]["text"], "changed": False, "edit_types": ["no_change"], "reason": "Grammatically sound; negation and 100 h duration preserved verbatim.", "risk_level": "low", "needs_human_confirmation": False},
                {"sentence_id": "S005", "original": sents[4]["text"], "revised": sents[4]["text"], "changed": False, "edit_types": ["no_change"], "reason": "Heading retained.", "risk_level": "low", "needs_human_confirmation": False},
                {"sentence_id": "S006", "original": sents[5]["text"], "revised": sents[5]["text"], "changed": False, "edit_types": ["no_change"], "reason": "Accurate academic phrasing; Table 1, TOF, 12.4 ± 0.3 s⁻¹, and [3] preserved.", "risk_level": "low", "needs_human_confirmation": False},
            ],
        }
        good_edits_path = work_dir / "edits_good.json"
        good_edits_path.write_text(json.dumps(good_edits, ensure_ascii=False, indent=2), encoding="utf-8")
        good_res = run_py(
            SCRIPT_DIR / "audit_and_compile_polish.py",
            ["--segmented", str(seg_path), "--edits", str(good_edits_path), "--resources", str(RESOURCES), "--out-dir", str(out_good), "--strict"],
            expect_code=0,
        )
        assert good_res["status"] == "completed" and good_res["hard_gate_errors"] == 0, f"Unexpected good result: {good_res}"

        result_json = json.loads((out_good / "result.json").read_text(encoding="utf-8"))
        assert result_json["case_id"] == "synthetic-polish-001"
        assert result_json["preserve_me"] == "contract_ok", "Original output_contract field was not preserved!"
        assert (out_good / "polished_manuscript.md").exists()
        assert (out_good / "revision_log.md").exists()
        assert (out_good / "glossary_and_abbreviations.json").exists()

    print("SELF-TEST PASSED: scipdf-academic-polisher (negative 7-gate rejection + positive end-to-end compilation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
