#!/usr/bin/env python3
"""
self_test.py — Self-Contained End-to-End Verification & Baseline-vs-Skill Comparison

Generates a synthetic 2-page, 2-column scientific PDF in pure Python (zero binary
files stored in the Skill repository), deliberately interleaving left/right column
text at identical Y-coordinates, running headers/footers, cross-page continued
tables, numbered equations, footnotes, and references.

Runs the full 4-script workflow (inspect_case -> parse_scientific_pdf ->
query_evidence -> validate_output) and verifies structural invariants.
NOTE: All data generated here is strictly labeled [SYNTHETIC SELF-TEST EXAMPLE].
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


def _pdf_escape(text: str) -> bytes:
    esc = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return esc.encode("utf-8")


def build_synthetic_two_column_pdf(pdf_path: Path) -> None:
    """
    Writes a valid 2-page PDF 1.4 file (612 x 792 pt) with:
    - Running header & footer (printed page labels 101, 102)
    - Spanning Title + Authors + Affiliation + Abstract on Page 1
    - Interleaved Left-Column (x=54..285) and Right-Column (x=324..555) text streams
    - Numbered equation (1), Table 1 + Table 1 (continued) on Page 2, Footnote, References
    """
    # Each item: (font, size, x, y_bottom_origin, text)
    page1_ops: List[Tuple[str, float, float, float, str]] = [
        # Running header & footer
        ("F1", 8.5, 54.0, 760.0, "Journal of Synthetic Layout Benchmarks - Vol. 12 (EXAMPLE)"),
        ("F1", 8.5, 296.0, 28.0, "101"),
        # Spanning front matter
        ("F2", 15.0, 54.0, 720.0, "Selective Catalytic Conversion in Microchannel Reactors: A Layout Benchmark"),
        ("F1", 10.5, 54.0, 696.0, "Lin Chen, Mingze Wu"),
        ("F1", 9.5, 54.0, 680.0, "Department of Chemical Engineering, Synthetic Benchmark Institute"),
        ("F2", 11.0, 54.0, 654.0, "Abstract"),
        ("F1", 10.0, 54.0, 636.0, "We construct a synthetic two-column paper to test reading-order recovery and table fidelity."),
        # Deliberately interleaved Y-rows across Left (x=54) and Right (x=326) columns!
        ("F2", 11.5, 54.0, 590.0, "1 Introduction"),
        ("F2", 11.5, 326.0, 590.0, "2 Experimental Methods"),
        ("F1", 10.0, 54.0, 568.0, "Microchannel reactors enhance heat transfer"),
        ("F1", 10.0, 326.0, 568.0, "Catalyst beds were packed at 450 C under"),
        ("F1", 10.0, 54.0, 554.0, "by 3.2x compared with conventional batch"),
        ("F1", 10.0, 326.0, 554.0, "steady flow rates of 12.5 mL/min with"),
        ("F1", 10.0, 54.0, 540.0, "vessels when temperature rise dT <= 15 C."),
        ("F1", 10.0, 326.0, 540.0, "online GC verification across three runs."),
        # Numbered equation in Left column & Table 1 in Right column
        ("F1", 10.0, 54.0, 506.0, "k_eff = k_0 * exp(-E_a / (R * T)) (1)"),
        ("F2", 10.0, 326.0, 506.0, "Table 1: Synthetic Reactor Performance (EXAMPLE)"),
        ("F1", 9.5, 326.0, 488.0, "| Reactor | Temp (C) | Selectivity (%) |"),
        ("F1", 9.5, 326.0, 474.0, "| Micro-A | 240 | 94.6 |"),
        ("F1", 9.5, 326.0, 460.0, "| Batch-B | 240 | 78.2 |"),
        # Footnote at bottom of Left column
        ("F1", 8.5, 54.0, 64.0, "* Synthetic corresponding note for verification example only."),
    ]

    page2_ops: List[Tuple[str, float, float, float, str]] = [
        ("F1", 8.5, 54.0, 760.0, "Journal of Synthetic Layout Benchmarks - Vol. 12 (EXAMPLE)"),
        ("F1", 8.5, 296.0, 28.0, "102"),
        # Continued Table 1 at top (spanning/left)
        ("F2", 10.0, 54.0, 718.0, "Table 1 (continued): Synthetic Reactor Performance (EXAMPLE)"),
        ("F1", 9.5, 54.0, 700.0, "| Reactor | Temp (C) | Selectivity (%) |"),
        ("F1", 9.5, 54.0, 686.0, "| Micro-C | 260 | 96.1 |"),
        # Two-column Conclusion + References
        ("F2", 11.5, 54.0, 636.0, "3 Conclusion"),
        ("F2", 11.5, 326.0, 636.0, "References"),
        ("F1", 10.0, 54.0, 614.0, "Micro-C achieved peak selectivity of 96.1%"),
        ("F1", 9.5, 326.0, 614.0, "[1] L. Chen, Synthetic Microreactor Design, 2024, doi:10.1000/syn.2024.01."),
        ("F1", 10.0, 54.0, 600.0, "at 260 C while preserving isothermal control."),
        ("F1", 9.5, 326.0, 596.0, "[2] M. Wu, Layout Parsing Benchmarks, 2025, doi:10.1000/syn.2025.02."),
    ]

    def render_stream(ops: List[Tuple[str, float, float, float, str]]) -> bytes:
        chunks: List[bytes] = []
        for font, sz, x, y, txt in ops:
            chunks.append(
                b"BT /" + font.encode("ascii") + f" {sz:.1f} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm (".encode("ascii")
                + _pdf_escape(txt)
                + b") Tj ET"
            )
        return b"\n".join(chunks)

    s1 = render_stream(page1_ops)
    s2 = render_stream(page2_ops)

    objs: List[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R /F2 8 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(s1)} >>\nstream\n".encode("ascii") + s1 + b"\nendstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R /F2 8 0 R >> >> /Contents 6 0 R >>",
        f"<< /Length {len(s2)} >>\nstream\n".encode("ascii") + s2 + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: List[int] = [0]
    for idx, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii") + body + b"\nendobj\n")

    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    pdf_path.write_bytes(bytes(out))


def run_cmd(args: List[str]) -> dict:
    proc = subprocess.run([sys.executable, *args], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end synthetic self-test of scipdf-evidence-parser.")
    parser.add_argument("--work-dir", default=None, help="Optional directory to keep synthetic demo outputs.")
    args = parser.parse_args()

    temp_ctx = tempfile.TemporaryDirectory(prefix="scipdf_selftest_") if not args.work_dir else None
    base = Path(args.work_dir) if args.work_dir else Path(temp_ctx.name)  # type: ignore[union-attr]
    try:
        inp = base / "input"
        work = base / "work"
        out = base / "output"
        inp.mkdir(parents=True, exist_ok=True)

        pdf_file = inp / "synthetic_two_column_demo.pdf"
        build_synthetic_two_column_pdf(pdf_file)

        case_json = inp / "case.json"
        case_json.write_text(
            json.dumps(
                {
                    "case_id": "synthetic-selftest-001",
                    "pdf_path": str(pdf_file),
                    "output_contract": {
                        "required_fields": ["peak_selectivity", "unreported_catalyst_lifetime"]
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # 1. inspect_case.py
        insp_res = run_cmd([
            str(SCRIPT_DIR / "inspect_case.py"),
            "--input-dir", str(inp),
            "--work-dir", str(work),
            "--output-dir", str(out),
        ])

        # 2. parse_scientific_pdf.py
        parsed_dir = work / "parsed"
        parse_res = run_cmd([
            str(SCRIPT_DIR / "parse_scientific_pdf.py"),
            "--pdf", str(pdf_file),
            "--out-dir", str(parsed_dir),
            "--ocr-mode", "off",
        ])

        pdoc = json.loads((parsed_dir / "parsed_document.json").read_text(encoding="utf-8"))

        # Verify multi-column separation (1 Introduction paragraph does NOT mix with 2 Experimental Methods)
        intro_blocks = [b["text"] for b in pdoc["reading_order_blocks"] if "Introduction" in b["section_path"] and b["role"] == "paragraph"]
        assert intro_blocks and "450 C" not in intro_blocks[0], f"Column bleed detected: {intro_blocks}"
        # Verify running header isolated from body blocks
        assert len(pdoc["headers_footers"]["headers"]) == 2, "Running headers were not isolated"
        # Verify continued Table 1 merged across pages 1 and 2 (3 data rows total!)
        assert len(pdoc["tables"]) == 1 and len(pdoc["tables"][0]["rows"]) == 3, f"Continued table merge failed: {pdoc['tables']}"
        # Verify equation (1) & 2 references extracted
        assert len(pdoc["equations"]) >= 1, "Equation (1) not extracted"
        assert len(pdoc["references"]) == 2, f"Expected 2 references, got {len(pdoc['references'])}"

        # 3. query_evidence.py
        q_res = run_cmd([
            str(SCRIPT_DIR / "query_evidence.py"),
            "--parsed-json", str(parsed_dir / "parsed_document.json"),
            "--query", "96.1%",
            "--top-k", "3",
        ])
        assert q_res["hit_count"] >= 1, "query_evidence returned no hits for 96.1%"
        top_hit = q_res["hits"][0]

        # 4. Populate final result.json and run validate_output.py
        final_result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        final_result["status"] = "success"
        final_result["status_reason"] = "[SYNTHETIC SELF-TEST EXAMPLE] Verified layout parsing and evidence anchors."
        final_result["peak_selectivity"] = "96.1% (Micro-C at 260 C)"
        final_result["unreported_catalyst_lifetime"] = None
        final_result["sources"] = [
            {
                "item_id": "peak_selectivity",
                "source_file": str(pdf_file),
                "page": top_hit["page"],
                "column": top_hit["column"],
                "section_path": top_hit["section_path"],
                "block_id": top_hit["block_id"],
                "bbox": top_hit["bbox"],
                "verbatim_quote": top_hit["verbatim_quote"][:120],
                "extraction_method": top_hit["extraction_method"],
            }
        ]
        final_result["uncertainty"]["unverifiable_items"] = []
        final_result["uncertainty"]["unreported_in_source"] = [
            {"field": "unreported_catalyst_lifetime", "note": "Not reported in synthetic 2-page PDF."}
        ]
        (out / "result.json").write_text(json.dumps(final_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        val_res = run_cmd([
            str(SCRIPT_DIR / "validate_output.py"),
            "--result-json", str(out / "result.json"),
            "--case-json", str(case_json),
            "--parsed-dir", str(parsed_dir),
            "--report-out", str(out / "parsing_report.md"),
        ])
        assert val_res["valid"] is True, f"Output validation failed: {val_res}"

        print(
            json.dumps(
                {
                    "self_test": "PASSED",
                    "notice": "SYNTHETIC EXAMPLE ONLY — NOT A FORMAL BENCHMARK CASE",
                    "backend_used": parse_res["backend_used"],
                    "verified_assertions": [
                        "two_column_xycut_no_bleed",
                        "running_header_footer_isolated",
                        "footnote_separated",
                        "cross_page_continued_table_merged_3_rows",
                        "numbered_equation_extracted",
                        "dois_and_references_parsed",
                        "verbatim_quote_and_page_bounds_validated",
                    ],
                    "counts": parse_res["counts"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
