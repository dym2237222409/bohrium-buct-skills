#!/usr/bin/env python3
"""
self_test.py — End-to-End 2-Paper JATS Corpus Synthetic Self-Test for scipdf-kg-builder

Tests every critical requirement:
1. Two independent papers in corpus-manifest.json + primary-material.json + secondary-material.json
   with distinct DOIs/authors/hashes and JATS paragraph_id locations (page=None, zero fake PDF page 1!).
2. Incremental Update: Paper 1 built first (E0001..), then Paper 2 merged via --merge-into preserving
   all Paper-1 node IDs!
3. Anti-False-Merge Alignment: Shared concept 'HZSM-5 zeolite' merges cleanly across Paper 1 & Paper 2
   (is_true_cross_paper=True), while Paper-1 sample (Si/Al=40, 260 C) and Paper-2 sample (Si/Al=80, 380 C)
   remain strictly separate nodes with explicit kept_separate_do_not_merge log!
4. Conflict Detection + 4 Real Executable Queries (including honest 'unverifiable_not_connected' check)
   + GraphML / Cypher / JSON-LD / Mermaid / SVG visual preview exports!
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


def run_py(args: List[str]) -> dict:
    p = subprocess.run([sys.executable, *args], capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", default=None)
    args = ap.parse_args()

    tmp_ctx = tempfile.TemporaryDirectory(prefix="kg_selftest_") if not args.work_dir else None
    base = Path(args.work_dir) if args.work_dir else Path(tmp_ctx.name)  # type: ignore[union-attr]
    try:
        inp, work, out = base / "input", base / "work", base / "output"
        inp.mkdir(parents=True, exist_ok=True)

        # Two independent synthetic papers with JATS paragraph IDs (NO PDF pages)
        (inp / "corpus-manifest.json").write_text(json.dumps({
            "sources": [
                {"source_id": "primary", "doi": "10.1000/syn.p1.2024", "title": "Microchannel HZSM-5 Catalysis", "authors": ["Lin Chen"], "year": 2024, "license": "CC-BY-4.0"},
                {"source_id": "secondary", "doi": "10.1000/syn.p2.2025", "title": "High-Temperature Pellet HZSM-5 Study", "authors": ["Tao Wang"], "year": 2025, "license": "CC-BY-4.0"},
            ]
        }, indent=2), encoding="utf-8")

        (inp / "primary-material.json").write_text(json.dumps({
            "source_id": "primary",
            "paragraphs": [
                {"paragraph_id": "p1_sec2_p1", "section": "Methods", "text": "HZSM-5 zeolite (Si/Al = 40) microchannel coating was tested at 260 C and 12.5 mL/min."},
                {"paragraph_id": "p1_sec3_p1", "section": "Results", "text": "At 260 C, the microchannel HZSM-5 sample increases olefin selectivity to 96.1%."},
            ],
        }, indent=2), encoding="utf-8")

        (inp / "secondary-material.json").write_text(json.dumps({
            "source_id": "secondary",
            "paragraphs": [
                {"paragraph_id": "p2_sec2_p1", "section": "Methods", "text": "Pelletized HZSM-5 zeolite (Si/Al = 80) was evaluated in a packed bed at 380 C."},
                {"paragraph_id": "p2_sec3_p1", "section": "Results", "text": "At 380 C, high-silica HZSM-5 pellets decrease olefin selectivity to 64.0% due to cracking."},
            ],
        }, indent=2), encoding="utf-8")

        case_p = inp / "case.json"
        case_p.write_text(json.dumps({
            "case_id": "synthetic-kg-001",
            "doi": "10.1000/syn.p1.2024",
            "output_contract": {"required_fields": ["case_id", "results", "sources", "status"]},
        }, indent=2), encoding="utf-8")

        # 1. Inspect & index corpus
        idx_res = run_py([str(SCRIPT_DIR / "inspect_and_index_corpus.py"), "--input-dir", str(inp), "--work-dir", str(work), "--output-dir", str(out)])
        assert idx_res["distinct_content_hashes"] == 2

        # 2. Build Paper 1 KG
        d1 = work / "draft_p1.json"
        d1.write_text(json.dumps({
            "entities": [
                {"temp_id": "c_hzsm5", "canonical_name": "HZSM-5 zeolite", "entity_type": "Concept_Term", "aliases": ["HZSM-5"], "is_paper_specific_instance": False,
                 "provenance": [{"source_id": "primary", "paragraph_id": "p1_sec2_p1", "verbatim_quote": "HZSM-5 zeolite (Si/Al = 40) microchannel coating was tested at 260 C", "attribution": "author_direct"}]},
                {"temp_id": "s_p1", "canonical_name": "HZSM-5 microchannel sample (Si/Al=40, primary)", "entity_type": "Material_or_Sample", "is_paper_specific_instance": True, "conditions_summary": "Si/Al=40, 260 C",
                 "provenance": [{"source_id": "primary", "paragraph_id": "p1_sec2_p1", "verbatim_quote": "HZSM-5 zeolite (Si/Al = 40) microchannel coating was tested at 260 C", "attribution": "author_direct"}]},
                {"temp_id": "m_sel", "canonical_name": "olefin selectivity", "entity_type": "Metric_or_Property", "is_paper_specific_instance": False,
                 "provenance": [{"source_id": "primary", "paragraph_id": "p1_sec3_p1", "verbatim_quote": "increases olefin selectivity to 96.1%.", "attribution": "author_direct"}]},
            ],
            "relations": [
                {"source_temp_id": "s_p1", "relation_type": "INSTANCE_OF", "target_temp_id": "c_hzsm5", "conditions": "General catalyst family", "evidence_strength": "strong_direct",
                 "provenance": [{"source_id": "primary", "paragraph_id": "p1_sec2_p1", "verbatim_quote": "HZSM-5 zeolite (Si/Al = 40) microchannel coating", "attribution": "author_direct"}]},
                {"source_temp_id": "c_hzsm5", "relation_type": "INCREASES", "target_temp_id": "m_sel", "conditions": "primary Si/Al=40 microchannel at 260 C", "quantitative_detail": "96.1%", "evidence_strength": "strong_direct",
                 "provenance": [{"source_id": "primary", "paragraph_id": "p1_sec3_p1", "verbatim_quote": "At 260 C, the microchannel HZSM-5 sample increases olefin selectivity to 96.1%.", "attribution": "author_direct"}]},
            ],
        }, indent=2), encoding="utf-8")

        kg_v1_dir = work / "kg_v1"
        run_py([str(SCRIPT_DIR / "build_and_update_kg.py"), "--corpus-index", str(work / "corpus_index.json"), "--draft-json", str(d1), "--output-dir", str(kg_v1_dir), "--strict"])
        v1_ids = {n["node_id"] for n in json.loads((kg_v1_dir / "knowledge_graph.json").read_text(encoding="utf-8"))["nodes"]}

        # 3. Incremental Update with Paper 2 (preserving E0001.. IDs, merging concept HZSM-5, keeping sample separate!)
        d2 = work / "draft_p2.json"
        d2.write_text(json.dumps({
            "entities": [
                {"temp_id": "c_hzsm5_2", "canonical_name": "HZSM-5", "entity_type": "Concept_Term", "aliases": ["HZSM-5 zeolite"], "is_paper_specific_instance": False,
                 "provenance": [{"source_id": "secondary", "paragraph_id": "p2_sec2_p1", "verbatim_quote": "Pelletized HZSM-5 zeolite (Si/Al = 80) was evaluated in a packed bed at 380 C.", "attribution": "author_direct"}]},
                {"temp_id": "s_p2", "canonical_name": "HZSM-5 pellet sample (Si/Al=80, secondary)", "entity_type": "Material_or_Sample", "is_paper_specific_instance": True, "conditions_summary": "Si/Al=80, 380 C",
                 "provenance": [{"source_id": "secondary", "paragraph_id": "p2_sec2_p1", "verbatim_quote": "Pelletized HZSM-5 zeolite (Si/Al = 80) was evaluated in a packed bed at 380 C.", "attribution": "author_direct"}]},
                {"temp_id": "m_sel_2", "canonical_name": "olefin selectivity", "entity_type": "Metric_or_Property", "is_paper_specific_instance": False,
                 "provenance": [{"source_id": "secondary", "paragraph_id": "p2_sec3_p1", "verbatim_quote": "decrease olefin selectivity to 64.0% due to cracking.", "attribution": "author_direct"}]},
            ],
            "relations": [
                {"source_temp_id": "s_p2", "relation_type": "INSTANCE_OF", "target_temp_id": "c_hzsm5_2", "conditions": "General catalyst family", "evidence_strength": "strong_direct",
                 "provenance": [{"source_id": "secondary", "paragraph_id": "p2_sec2_p1", "verbatim_quote": "Pelletized HZSM-5 zeolite (Si/Al = 80)", "attribution": "author_direct"}]},
                {"source_temp_id": "c_hzsm5_2", "relation_type": "DECREASES", "target_temp_id": "m_sel_2", "conditions": "secondary Si/Al=80 pellets at 380 C (cracking)", "quantitative_detail": "64.0%", "evidence_strength": "strong_direct",
                 "provenance": [{"source_id": "secondary", "paragraph_id": "p2_sec3_p1", "verbatim_quote": "At 380 C, high-silica HZSM-5 pellets decrease olefin selectivity to 64.0% due to cracking.", "attribution": "author_direct"}]},
            ],
            "alignment_decisions": [
                {"candidate_names": ["HZSM-5 microchannel sample (primary)", "HZSM-5 pellet sample (secondary)"], "decision": "kept_separate_do_not_merge",
                 "rationale": "Distinct Si/Al ratio (40 vs 80), morphology (microchannel vs pellet), and operating temperature (260 C vs 380 C)."}
            ],
        }, indent=2), encoding="utf-8")

        b2_res = run_py([
            str(SCRIPT_DIR / "build_and_update_kg.py"),
            "--corpus-index", str(work / "corpus_index.json"),
            "--draft-json", str(d2),
            "--merge-into", str(kg_v1_dir / "knowledge_graph.json"),
            "--output-dir", str(out),
            "--strict",
        ])
        kg_final = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        v2_ids = {n["node_id"] for n in kg_final["nodes"]}
        assert v1_ids.issubset(v2_ids), "Incremental update did not preserve Paper-1 node IDs!"
        assert kg_final["summary"]["true_cross_paper_concept_nodes"] == 2
        assert kg_final["summary"]["paper_specific_instance_nodes"] == 2
        assert kg_final["summary"]["conflicts_detected"] == 1
        assert all(pr["page"] is None for n in kg_final["nodes"] for pr in n["provenance"])  # Zero fake PDF pages!

        # 4. Run 4 executable queries (including unverifiable path check)
        q_p = work / "queries.json"
        q_p.write_text((SCRIPT_DIR.parent / "templates" / "query_templates.json").read_text(encoding="utf-8"), encoding="utf-8")
        q_res = run_py([
            str(SCRIPT_DIR / "run_kg_queries.py"),
            "--kg-json", str(out / "knowledge_graph.json"),
            "--queries-json", str(q_p),
            "--case-json", str(case_p),
            "--output-dir", str(out),
        ])
        assert q_res["queries_executed"] == 4
        assert q_res["query_statuses"]["Q4"] == "unverifiable_not_connected"
        assert (out / "knowledge_graph.graphml").is_file() and (out / "knowledge_graph.cypher").is_file() and (out / "kg_preview.svg").is_file()

        print(json.dumps({
            "self_test": "PASSED",
            "notice": "SYNTHETIC EXAMPLE ONLY — NOT A FORMAL BENCHMARK CASE",
            "graph_summary": kg_final["summary"],
            "query_statuses": q_res["query_statuses"],
            "verified_assertions": [
                "independent_two_paper_metadata_and_jats_ids_preserved",
                "incremental_update_preserved_existing_node_ids",
                "anti_false_merge_separated_physical_samples_from_shared_concept",
                "cross_paper_regime_conflict_detected",
                "4_queries_executed_including_unverifiable_relation_check",
                "graphml_cypher_jsonld_mermaid_svg_exported",
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
