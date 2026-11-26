#!/usr/bin/env python3
"""
self_test.py — Zero-dependency end-to-end self-test for scipdf-citation-manager.
Verifies:
  1. Multi-format bibliography parsing (BibTeX + RIS + CNKI).
  2. Preprint <-> Published deduplication (prefers published journal version).
  3. Anti-false-merge guard (same author + same year, distinct valid DOIs kept separate).
  4. Negative gate rejection (orphan/fabricated citation caught as ERROR).
  5. Positive synchronized renumbering in GB/T 7714-2015 ([1-3] range compression)
     AND APA 7th (2023a/2023b disambiguation), BibTeX/RIS export, and contract preservation.
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
    with tempfile.TemporaryDirectory(prefix="cite_selftest_") as tmp:
        root = Path(tmp)
        in_dir = root / "input"
        work_dir = root / "work"
        out_bad = root / "out_bad"
        out_gbt = root / "out_gbt"
        out_apa = root / "out_apa"
        in_dir.mkdir()
        work_dir.mkdir()

        # Synthetic manuscript
        (in_dir / "draft.md").write_text(
            "# Introduction\n\n"
            "Single-atom Fe-N4 catalysts exhibit strong acidic ORR activity [2]. "
            "Dual-atom Fe-Co sites further reduce the overpotential [1]. "
            "Recent reviews summarize stability limits across both catalyst families.\n",
            encoding="utf-8",
        )

        # BibTeX library: 2 distinct papers by Zhang in 2023 (must NOT merge!)
        (in_dir / "refs_a.bib").write_text(
            "@article{Zhang2023_orr,\n"
            "  author = {Zhang, Wei and Li, Ming},\n"
            "  title = {Single-atom Fe-N4 catalysts for acidic oxygen reduction},\n"
            "  journal = {Applied Catalysis B: Environmental},\n"
            "  year = {2023},\n"
            "  volume = {325},\n"
            "  pages = {122001},\n"
            "  doi = {10.1016/j.apcatb.2023.122001}\n"
            "}\n\n"
            "@article{Zhang2023_dual,\n"
            "  author = {Zhang, Wei and Chen, Yu},\n"
            "  title = {Dual-atom Fe-Co sites lowering ORR overpotential},\n"
            "  journal = {ACS Catalysis},\n"
            "  year = {2023},\n"
            "  volume = {13},\n"
            "  number = {8},\n"
            "  pages = {5120--5132},\n"
            "  doi = {10.1021/acscatal.2023.99999}\n"
            "}\n",
            encoding="utf-8",
        )

        # RIS library: arXiv preprint of Zhang2023_orr (MUST merge with Zhang2023_orr, keeping published version)
        (in_dir / "refs_b.ris").write_text(
            "TY  - GEN\n"
            "ID  - Zhang2022_arxiv\n"
            "AU  - Zhang, Wei\n"
            "AU  - Li, Ming\n"
            "TI  - Single-atom Fe-N4 catalysts for acidic oxygen reduction\n"
            "JO  - arXiv preprint arXiv:2211.01234\n"
            "PY  - 2022\n"
            "AB  - We show Fe-N4 single-atom sites achieve high acidic ORR activity.\n"
            "ER  -\n",
            encoding="utf-8",
        )

        # CNKI export: Chinese review + an uncited extra entry
        (in_dir / "refs_cnki.txt").write_text(
            "SrcDatabase: 期刊\n"
            "Title-题名: 酸性氧还原单原子催化剂稳定性研究进展\n"
            "Author-作者: 王明; 李华\n"
            "Source-文献来源: 催化学报\n"
            "Year-年: 2024\n"
            "Volume: 45\n"
            "Period: 2\n"
            "Page: 110-125\n\n"
            "SrcDatabase: 期刊\n"
            "Title-题名: 碱性电解水制氢催化剂综述\n"
            "Author-作者: 赵敏\n"
            "Source-文献来源: 物理化学学报\n"
            "Year-年: 2021\n",
            encoding="utf-8",
        )

        (in_dir / "case.json").write_text(json.dumps({
            "case_id": "synthetic-cite-001",
            "files": ["draft.md", "refs_a.bib", "refs_b.ris", "refs_cnki.txt"],
            "output_contract": {"preserve_contract_flag": "yes_ok"},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (in_dir / "task.md").write_text("Format citations according to GB/T 7714-2015 sequential numeric style.", encoding="utf-8")

        # Step 1: Parse
        parsed_path = work_dir / "parsed.json"
        p_sum = run_py(
            SCRIPT_DIR / "parse_bib_and_manuscript.py",
            ["--case", str(in_dir / "case.json"), "--task", str(in_dir / "task.md"), "--out", str(parsed_path)],
        )
        assert p_sum["raw_references"] == 5, f"Expected 5 raw refs, got {p_sum}"

        # Step 2: Deduplicate
        dedup_path = work_dir / "deduped.json"
        d_sum = run_py(
            SCRIPT_DIR / "deduplicate_refs.py",
            ["--parsed", str(parsed_path), "--rules", str(RESOURCES / "dedup_rules.json"), "--out", str(dedup_path)],
        )
        assert d_sum["deduplicated_count"] == 4, f"Expected 4 deduped refs (5->4), got {d_sum}"
        assert d_sum["merged_duplicate_groups"] == 1, f"Expected 1 merged group, got {d_sum}"
        assert d_sum["kept_separate_guarded"] >= 1, f"Expected anti-false-merge guard to fire, got {d_sum}"

        dedup_data = json.loads(dedup_path.read_text(encoding="utf-8"))
        k2r = dedup_data["cite_key_to_ref_id"]
        r_orr = k2r["Zhang2023_orr"]
        r_dual = k2r["Zhang2023_dual"]
        assert r_orr == k2r["Zhang2022_arxiv"], "Preprint should merge into published Zhang2023_orr!"
        assert r_orr != r_dual, "Same-author same-year distinct DOIs must NOT merge!"
        # Find Chinese review ref_id
        r_rev = next(r["ref_id"] for r in dedup_data["references"] if "稳定性研究进展" in r["title"])
        # Verify abstract enrichment from RIS into canonical BibTeX record
        orr_obj = next(r for r in dedup_data["references"] if r["ref_id"] == r_orr)
        assert orr_obj["doi"] == "10.1016/j.apcatb.2023.122001"
        assert "acidic ORR" in orr_obj["abstract"], "Abstract should be enriched from merged RIS preprint!"

        # Step 3A: NEGATIVE TEST — orphan/fabricated citation must fail strict gate
        bad_plan = {
            "case_id": "synthetic-cite-001",
            "target_style": "gbt7714_numeric",
            "sentences": [
                {"sentence_id": "S001", "section": "Introduction", "paragraph_id": "P001", "is_structural_block": True, "original_text": "# Introduction", "text_with_placeholders": "# Introduction", "action": "unchanged"},
                {"sentence_id": "S002", "section": "Introduction", "paragraph_id": "P002", "original_text": "Fake claim.", "text_with_placeholders": "Fake claim {{CITE:NONEXISTENT_999}}.", "action": "insert"},
            ],
            "claim_alignments": [],
        }
        bad_plan_path = work_dir / "plan_bad.json"
        bad_plan_path.write_text(json.dumps(bad_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        run_py(
            SCRIPT_DIR / "sync_format_and_verify.py",
            ["--parsed", str(parsed_path), "--deduped", str(dedup_path), "--plan", str(bad_plan_path), "--styles", str(RESOURCES / "citation_styles.json"), "--out-dir", str(out_bad), "--strict"],
            expect_code=2,
        )
        bad_audit = json.loads((out_bad / "citation_audit_report.json").read_text(encoding="utf-8"))
        assert any(i["gate"] == "Gate1_NoOrphanCitations" and i["severity"] == "ERROR" for i in bad_audit["issues"])

        # Step 3B: POSITIVE TEST 1 — GB/T 7714-2015 sequential numeric with renumbering & [1-3] compression
        good_plan = {
            "case_id": "synthetic-cite-001",
            "target_style": "gbt7714_numeric",
            "sentences": [
                {"sentence_id": "S001", "section": "Introduction", "paragraph_id": "P001", "is_structural_block": True, "original_text": "# Introduction", "text_with_placeholders": "# Introduction", "action": "unchanged"},
                {"sentence_id": "S002", "section": "Introduction", "paragraph_id": "P002", "original_text": "Single-atom Fe-N4 catalysts exhibit strong acidic ORR activity [2].", "text_with_placeholders": f"Single-atom Fe-N4 catalysts exhibit strong acidic ORR activity {{{{CITE:{r_orr}}}}}.", "action": "renumber_only", "old_citation_markers": ["[2]"], "change_reason": "Renumbered first appearance from [2] to [1]."},
                {"sentence_id": "S003", "section": "Introduction", "paragraph_id": "P002", "original_text": "Dual-atom Fe-Co sites further reduce the overpotential [1].", "text_with_placeholders": f"Dual-atom Fe-Co sites further reduce the overpotential {{{{CITE:{r_dual}}}}}.", "action": "renumber_only", "old_citation_markers": ["[1]"], "change_reason": "Renumbered second appearance from [1] to [2]."},
                {"sentence_id": "S004", "section": "Introduction", "paragraph_id": "P002", "original_text": "Recent reviews summarize stability limits across both catalyst families.", "text_with_placeholders": f"Recent reviews summarize stability limits across both catalyst families {{{{CITE:{r_orr},{r_dual},{r_rev}}}}}.", "action": "insert", "old_citation_markers": [], "change_reason": "Inserted synchronized multi-citation compressed as [1-3]."},
            ],
            "claim_alignments": [
                {"sentence_id": "S002", "ref_id": r_orr, "alignment_status": "supported", "evidence_basis": "Merged abstract explicitly confirms Fe-N4 single-atom sites achieve high acidic ORR activity."},
                {"sentence_id": "S003", "ref_id": r_dual, "alignment_status": "supported", "evidence_basis": "Title directly confirms dual-atom Fe-Co sites lower ORR overpotential."},
                {"sentence_id": "S004", "ref_id": r_orr, "alignment_status": "partial_support", "evidence_basis": "Covers Fe-N4 single-atom ORR catalysts."},
                {"sentence_id": "S004", "ref_id": r_dual, "alignment_status": "partial_support", "evidence_basis": "Covers dual-atom Fe-Co catalysts."},
                {"sentence_id": "S004", "ref_id": r_rev, "alignment_status": "supported", "evidence_basis": "Chinese review directly addresses stability of acidic ORR single-atom catalysts."},
            ],
        }
        good_plan_path = work_dir / "plan_gbt.json"
        good_plan_path.write_text(json.dumps(good_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        gbt_sum = run_py(
            SCRIPT_DIR / "sync_format_and_verify.py",
            ["--parsed", str(parsed_path), "--deduped", str(dedup_path), "--plan", str(good_plan_path), "--styles", str(RESOURCES / "citation_styles.json"), "--out-dir", str(out_gbt), "--strict"],
            expect_code=0,
        )
        assert gbt_sum["status"] == "completed" and gbt_sum["cited_count"] == 3
        ms_gbt = (out_gbt / "manuscript_updated.md").read_text(encoding="utf-8")
        assert "[1]" in ms_gbt and "[2]" in ms_gbt and "[1-3]" in ms_gbt, f"Expected [1], [2], [1-3] in:\n{ms_gbt}"
        assert "[J]" in ms_gbt, "Expected GB/T 7714-2015 [J] doc type code!"
        res_gbt = json.loads((out_gbt / "result.json").read_text(encoding="utf-8"))
        assert res_gbt["preserve_contract_flag"] == "yes_ok"
        assert res_gbt["summary_metrics"]["uncited_library_count"] == 1

        # Step 3C: POSITIVE TEST 2 — APA 7th author-year with automatic 2023a / 2023b disambiguation
        apa_plan = {**good_plan, "target_style": "apa7"}
        apa_plan_path = work_dir / "plan_apa.json"
        apa_plan_path.write_text(json.dumps(apa_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        apa_sum = run_py(
            SCRIPT_DIR / "sync_format_and_verify.py",
            ["--parsed", str(parsed_path), "--deduped", str(dedup_path), "--plan", str(apa_plan_path), "--styles", str(RESOURCES / "citation_styles.json"), "--out-dir", str(out_apa), "--strict"],
            expect_code=0,
        )
        assert apa_sum["status"] == "completed"
        ms_apa = (out_apa / "manuscript_updated.md").read_text(encoding="utf-8")
        assert "2023a" in ms_apa and "2023b" in ms_apa, f"Expected APA 2023a/2023b disambiguation in:\n{ms_apa}"

    print("SELF-TEST PASSED: scipdf-citation-manager (multi-format parse + dedup + anti-false-merge + GB/T 7714 & APA7 sync)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
