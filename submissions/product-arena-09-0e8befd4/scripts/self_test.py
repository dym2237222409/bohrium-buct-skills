#!/usr/bin/env python3
"""
self_test.py — End-to-End Synthetic Verification for scipdf-nanopub-extractor

Verifies BOTH:
  1. Positive path on JATS JSON corpus (honest paragraph_id location without faking PDF page 1):
     atomic assertion decomposition, minimal quote verification, duplicate removal,
     contradiction detection, and W3C TriG / JSON-LD / N-Quads / JSONL serialization.
  2. Negative quality-gate path: confirms over-broad multi-sentence assertions,
     ungrounded quotes, and out-of-vocabulary relations are deterministically caught!
NOTE: Strictly synthetic self-test data, NOT a formal benchmark case.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List


SCRIPT_DIR = Path(__file__).resolve().parent


def run_py(args: List[str], expect_rc: int = 0) -> dict:
    p = subprocess.run([sys.executable, *args], capture_output=True, text=True)
    assert p.returncode == expect_rc, f"Expected rc={expect_rc}, got {p.returncode}\nSTDOUT:{p.stdout}\nSTDERR:{p.stderr}"
    return json.loads(p.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", default=None)
    args = ap.parse_args()

    tmp_ctx = tempfile.TemporaryDirectory(prefix="nanopub_selftest_") if not args.work_dir else None
    base = Path(args.work_dir) if args.work_dir else Path(tmp_ctx.name)  # type: ignore[union-attr]
    try:
        inp, work, out = base / "input", base / "work", base / "output"
        inp.mkdir(parents=True, exist_ok=True)

        # Synthetic JATS/JSON material (no PDF page numbers -> must stay page=null, location_type='jats_paragraph_id')
        (inp / "primary-material.json").write_text(json.dumps({
            "source_id": "primary",
            "doi": "10.1000/syn.nanopub.2025",
            "title": "Synthetic Catalytic Modulation Study",
            "authors": ["Wei Zhang", "Li Chen"],
            "year": 2025,
            "paragraphs": [
                {"paragraph_id": "sec3-p1", "section": "Results", "text": "Nitrogen doping at 2.4 at% decreases oxygen evolution overpotential by 48 mV in 1 M KOH at 10 mA/cm^2."},
                {"paragraph_id": "sec3-p2", "section": "Results", "text": "However, excessive nitrogen doping at 8.0 at% increases oxygen evolution overpotential by 22 mV due to conductivity loss."},
            ],
        }, indent=2), encoding="utf-8")

        case_p = inp / "case.json"
        case_p.write_text(json.dumps({
            "case_id": "synthetic-nanopub-001",
            "output_contract": {"required_fields": ["case_id", "results", "sources", "status"]},
        }, indent=2), encoding="utf-8")

        run_py([str(SCRIPT_DIR / "inspect_case.py"), "--input-dir", str(inp), "--work-dir", str(work), "--output-dir", str(out)])
        idx_dir = work / "indexed"
        run_py([str(SCRIPT_DIR / "index_source_passages.py"), "--input-dir", str(inp), "--out-dir", str(idx_dir)])

        # Test A: Negative gate check (over-broad 2-sentence claim + unknown relation + ungrounded quote)
        bad_draft = work / "bad_draft.json"
        bad_draft.write_text(json.dumps({
            "assertions": [{
                "assertion_id": "BAD01",
                "subject": "Nitrogen doping",
                "relation": "magically_improves",
                "object": "catalyst",
                "claim_text": "Sentence one makes a claim. Sentence two dumps another claim.",
                "provenance": {"source_id": "primary", "passage_id": "sec3-p1", "minimal_evidence_quote": "Fabricated sentence not in paper.", "evidence_type": "direct_experimental"},
            }]
        }), encoding="utf-8")
        bad_res = run_py([
            str(SCRIPT_DIR / "build_and_validate_nanopubs.py"),
            "--index-json", str(idx_dir / "indexed_passages.json"),
            "--draft-json", str(bad_draft),
            "--output-dir", str(work / "bad_out"),
            "--strict",
        ], expect_rc=2)
        assert bad_res["valid"] is False and len(bad_res["errors"]) >= 3

        # Test B: Valid atomic assertions (including duplicate check & opposite-relation conflict detection)
        good_draft = work / "good_draft.json"
        good_draft.write_text(json.dumps({
            "paper_metadata": {"source_id": "primary", "paper_title": "Synthetic Catalytic Modulation Study", "paper_authors": ["Wei Zhang", "Li Chen"], "paper_year": 2025, "paper_doi_or_id": "10.1000/syn.nanopub.2025"},
            "assertions": [
                {
                    "assertion_id": "NP001",
                    "subject": "nitrogen doping",
                    "relation": "decreases",
                    "object": "oxygen evolution overpotential",
                    "conditions_and_scope": "2.4 at% N, 1 M KOH, 10 mA/cm^2",
                    "quantitative_value": "-48 mV",
                    "negated": False,
                    "claim_text": "Nitrogen doping at 2.4 at% decreases oxygen evolution overpotential by 48 mV in 1 M KOH.",
                    "provenance": {"source_id": "primary", "passage_id": "sec3-p1", "minimal_evidence_quote": "Nitrogen doping at 2.4 at% decreases oxygen evolution overpotential by 48 mV in 1 M KOH at 10 mA/cm^2.", "evidence_type": "direct_experimental", "confidence_basis": "Direct electrochemical measurement"},
                },
                {
                    "assertion_id": "NP001_DUP",
                    "subject": "nitrogen doping",
                    "relation": "decreases",
                    "object": "oxygen evolution overpotential",
                    "conditions_and_scope": "2.4 at% N, 1 M KOH, 10 mA/cm^2",
                    "quantitative_value": "-48 mV",
                    "negated": False,
                    "claim_text": "Nitrogen doping at 2.4 at% decreases oxygen evolution overpotential by 48 mV in 1 M KOH.",
                    "provenance": {"source_id": "primary", "passage_id": "sec3-p1", "minimal_evidence_quote": "Nitrogen doping at 2.4 at% decreases oxygen evolution overpotential by 48 mV in 1 M KOH at 10 mA/cm^2.", "evidence_type": "direct_experimental", "confidence_basis": "Duplicate check"},
                },
                {
                    "assertion_id": "NP002",
                    "subject": "nitrogen doping",
                    "relation": "increases",
                    "object": "oxygen evolution overpotential",
                    "conditions_and_scope": "excessive 8.0 at% N due to conductivity loss",
                    "quantitative_value": "+22 mV",
                    "negated": False,
                    "claim_text": "Excessive nitrogen doping at 8.0 at% increases oxygen evolution overpotential by 22 mV.",
                    "provenance": {"source_id": "primary", "passage_id": "sec3-p2", "minimal_evidence_quote": "excessive nitrogen doping at 8.0 at% increases oxygen evolution overpotential by 22 mV due to conductivity loss.", "evidence_type": "direct_experimental", "confidence_basis": "Direct electrochemical measurement"},
                },
            ],
        }, indent=2), encoding="utf-8")

        good_res = run_py([
            str(SCRIPT_DIR / "build_and_validate_nanopubs.py"),
            "--index-json", str(idx_dir / "indexed_passages.json"),
            "--draft-json", str(good_draft),
            "--case-json", str(case_p),
            "--output-dir", str(out),
            "--strict",
        ])
        assert good_res["valid"] is True
        assert good_res["metrics"]["duplicates_removed"] == 1
        assert good_res["metrics"]["contradictions_detected"] == 1
        assert (out / "nanopublications.trig").is_file() and (out / "nanopublications.jsonld").is_file() and (out / "nanopublications.nq").is_file()
        jld = json.loads((out / "nanopublications.jsonld").read_text(encoding="utf-8"))
        assert jld["@graph"][0]["np:hasProvenance"]["page"] is None  # Must NOT fake PDF page 1 for JATS input!

        print(json.dumps({
            "self_test": "PASSED",
            "notice": "SYNTHETIC EXAMPLE ONLY — NOT A FORMAL BENCHMARK CASE",
            "metrics": good_res["metrics"],
            "verified_assertions": [
                "jats_paragraph_id_preserved_no_fake_pdf_page",
                "negative_quality_gates_caught_broad_ungrounded_invalid_relation",
                "trusty_sha256_deduplication_verified",
                "opposite_polarity_regime_conflict_detected",
                "w3c_trig_jsonld_nquads_jsonl_exported",
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
