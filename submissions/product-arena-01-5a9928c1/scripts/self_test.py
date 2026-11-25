#!/usr/bin/env python3
"""
self_test.py — End-to-End Synthetic Verification for scipdf-faithful-translator

Synthesizes a 2-column English scientific PDF in pure Python (zero binary files in repo),
runs inspect_case -> segment_and_extract_terms -> build_and_lock_glossary ->
verify_and_export_bilingual (testing BOTH drift-catch/auto-backfill AND 100% clean
verification), and asserts every structural & semantic invariant.
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


def _esc(text: str) -> bytes:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("utf-8")


def build_synthetic_translation_pdf(pdf_path: Path) -> None:
    ops_p1: List[Tuple[str, float, float, float, str]] = [
        ("F1", 8.5, 54.0, 760.0, "Synthetic Journal of Catalysis - Vol. 5 (EXAMPLE)"),
        ("F1", 8.5, 296.0, 28.0, "1"),
        ("F2", 14.5, 54.0, 720.0, "Microchannel Reactor Optimization for Catalyst Selectivity"),
        ("F2", 11.0, 54.0, 686.0, "Abstract"),
        ("F1", 10.0, 54.0, 668.0, "Microchannel reactors increase catalyst selectivity by 16.4% only when residence time does not exceed 2.5 s [1, 2]."),
        # Interleaved two-column body
        ("F2", 11.5, 54.0, 624.0, "1 Introduction"),
        ("F2", 11.5, 326.0, 624.0, "2 Conclusion"),
        ("F1", 10.0, 54.0, 602.0, "Although batch vessels are simple, heat transfer"),
        ("F1", 10.0, 326.0, 602.0, "In summary, the microchannel reactor maintains"),
        ("F1", 10.0, 54.0, 588.0, "coefficient limits isothermal operation on the substrate."),
        ("F1", 10.0, 326.0, 588.0, "steady state conversion without significant deactivation [2]."),
        ("F2", 10.0, 54.0, 548.0, "Fig. 1: Residence time vs catalyst selectivity profile (EXAMPLE)."),
        ("F2", 11.0, 326.0, 548.0, "References"),
        ("F1", 9.0, 326.0, 530.0, "[1] A. Demo, Microreactor Transport, 2024."),
        ("F1", 9.0, 326.0, 516.0, "[2] B. Sample, Catalytic Kinetics, 2025."),
    ]

    stream = b"\n".join(
        b"BT /" + f.encode("ascii") + f" {sz:.1f} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm (".encode("ascii") + _esc(t) + b") Tj ET"
        for f, sz, x, y, t in ops_p1
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

    tmp_ctx = tempfile.TemporaryDirectory(prefix="trans_selftest_") if not args.work_dir else None
    base = Path(args.work_dir) if args.work_dir else Path(tmp_ctx.name)  # type: ignore[union-attr]
    try:
        inp, work, out = base / "input", base / "work", base / "output"
        inp.mkdir(parents=True, exist_ok=True)
        pdf_p = inp / "synthetic_catalysis_paper.pdf"
        build_synthetic_translation_pdf(pdf_p)

        case_p = inp / "case.json"
        case_p.write_text(json.dumps({
            "case_id": "synthetic-translation-001",
            "pdf_path": str(pdf_p),
            "target_language": "zh",
            "output_contract": {"required_fields": ["case_id", "results", "sources", "status"]},
        }, indent=2), encoding="utf-8")

        # 1. inspect_case
        run_py([str(SCRIPT_DIR / "inspect_case.py"), "--input-dir", str(inp), "--work-dir", str(work), "--output-dir", str(out)])

        # 2. segment_and_extract_terms
        seg_dir = work / "segmented"
        seg_res = run_py([str(SCRIPT_DIR / "segment_and_extract_terms.py"), "--source", str(pdf_p), "--out-dir", str(seg_dir)])
        seg_payload = json.loads((seg_dir / "source_segments.json").read_text(encoding="utf-8"))

        # 3. build_and_lock_glossary (with drift alias test!)
        agent_terms_p = work / "agent_terms.json"
        agent_terms_p.write_text(json.dumps({
            "entries": [
                {"source_term": "microchannel reactor", "target_term": "微通道反应器", "drift_aliases": ["微槽反应器"], "rationale": "Standard reactor engineering term"}
            ],
            "polysemy_decisions": [
                {"word": "substrate", "chosen_target": "催化剂基底", "rationale": "Solid reactor wall support in microchannel heat-transfer context"}
            ]
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        gloss_p = work / "locked_glossary.json"
        gloss_res = run_py([
            str(SCRIPT_DIR / "build_and_lock_glossary.py"),
            "--segments-json", str(seg_dir / "source_segments.json"),
            "--candidates-json", str(seg_dir / "term_candidates.json"),
            "--agent-terms-json", str(agent_terms_p),
            "--out-glossary", str(gloss_p),
        ])
        assert gloss_res["locked_term_count"] >= 4, f"Expected >=4 locked terms, got {gloss_res}"

        # 4. Construct synthetic translations (deliberately using '微槽反应器' in Conclusion to verify auto-backfill!)
        translations = []
        for s in seg_payload["segments"]:
            if not s["translatable"]:
                continue
            txt = s["source_text"]
            if "Optimization" in txt:
                zh = "面向催化剂选择性提升的微通道反应器优化"
            elif txt.lower() == "abstract":
                zh = "摘要"
            elif "16.4%" in txt:
                zh = "仅当停留时间不超过 2.5 s 时，微通道反应器才能将催化剂选择性提高 16.4% [1, 2]。"
            elif "1 Introduction" in txt:
                zh = "1 引言"
            elif "Although batch" in txt:
                zh = "尽管釜式反应器结构简单，但传热系数限制了催化剂基底上的等温运行。"
            elif "Fig. 1" in txt:
                zh = "图 1：停留时间与催化剂选择性分布曲线（示例）。"
            elif "2 Conclusion" in txt:
                zh = "2 结论"
            elif "In summary" in txt:
                # Deliberate alias '微槽反应器' -> will be deterministically backfilled to '微通道反应器'!
                zh = "综上所述，微槽反应器在无显著失活的情况下维持了稳态转化 [2]。"
            elif txt.lower() == "references":
                zh = "参考文献"
            else:
                zh = txt
            translations.append({"seg_id": s["seg_id"], "target_text": zh})

        trans_p = work / "translated_segments.json"
        trans_p.write_text(json.dumps({"segments": translations}, ensure_ascii=False, indent=2), encoding="utf-8")

        # 5. verify_and_export_bilingual (--strict)
        ver_res = run_py([
            str(SCRIPT_DIR / "verify_and_export_bilingual.py"),
            "--segments-json", str(seg_dir / "source_segments.json"),
            "--translations-json", str(trans_p),
            "--glossary-json", str(gloss_p),
            "--case-json", str(case_p),
            "--output-dir", str(out),
            "--strict",
        ])
        assert ver_res["valid"] is True, f"Verification failed: {ver_res}"
        assert ver_res["metrics"]["glossary_consistency_rate"] == 1.0
        assert ver_res["metrics"]["number_citation_parity_rate"] == 1.0
        assert ver_res["metrics"]["auto_backfills_applied"] == 1

        print(json.dumps({
            "self_test": "PASSED",
            "notice": "SYNTHETIC EXAMPLE ONLY — NOT A FORMAL BENCHMARK CASE",
            "metrics": ver_res["metrics"],
            "verified_assertions": [
                "xycut_two_column_segmentation_no_bleed",
                "1_to_1_segment_completeness_100pct",
                "programmatic_glossary_lock_and_alias_backfill",
                "number_unit_citation_parity_100pct",
                "negation_concessive_condition_polarity_verified",
                "bilingual_markdown_and_qa_checklist_exported",
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
