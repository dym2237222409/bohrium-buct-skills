#!/usr/bin/env python3
"""End-to-end synthetic self-test (static bugs + cascading log parser + repaired build) for scipdf-latex-builder-auditor."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from compile_and_audit_latex import parse_compile_logs  # noqa: E402


INSPECT_SCRIPT = SKILL_ROOT / "scripts" / "inspect_latex_project.py"
COMPILE_SCRIPT = SKILL_ROOT / "scripts" / "compile_and_audit_latex.py"


def run_py(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


BROKEN_TEX = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{booktabs}
\usepackage{cleveref}
\usepackage{hyperref}
\title{Synthetic Catalyst Optimization Study}
\author{Test Author}
\begin{document}
\maketitle
\section{Method}
As shown in \ref{eq:missing} and \cite{ghost2025}, our loss is:
\begin{equation}
  L = \alpha x + \beta
  \label{eq:loss}
% Intentionally unclosed equation above!
\begin{equation}
  J = L^2
  \label{eq:loss}
\end{equation}
\begin{table}[htbp]
\centering
\begin{tabular}{lcc}
\toprule
Method & MAE & R2 \\
\midrule
Ours & 0.12 & 0.96 & 99.9 \\
\bottomrule
\end{tabular}
\end{table}
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""

FIXED_TEX = r"""\documentclass{article}
\usepackage{amsmath}
\usepackage{booktabs}
\usepackage{hyperref}
\usepackage{cleveref}
\title{Synthetic Catalyst Optimization Study}
\author{Test Author}
\begin{document}
\maketitle
\section{Method}
As shown in \eqref{eq:loss} and \cite{smith2024cat}, our loss is:
\begin{equation}
  L = \alpha x + \beta
  \label{eq:loss}
\end{equation}
\begin{equation}
  J = L^2
  \label{eq:obj}
\end{equation}
\begin{table}[htbp]
\centering
\begin{tabular}{lcc}
\toprule
Method & MAE & R2 \\
\midrule
Ours & 0.12 & 0.96 \\
\bottomrule
\end{tabular}
\end{table}
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""

REFS_BIB = r"""@article{smith2024cat,
  author = {Smith, Jane},
  title = {Synthetic catalyst screening},
  journal = {Journal of Catalysis},
  year = {2024}
}
"""

SYNTHETIC_LOG = r"""This is pdfTeX, Version 3.141592653
./main.tex:12: Undefined control sequence.
./main.tex:25: Extra alignment tab has been changed to \cr.
LaTeX Warning: Citation `ghost2025' on page 1 undefined on input line 10.
LaTeX Warning: Reference `eq:missing' on page 1 undefined on input line 10.
Overfull \hbox (18.42pt too wide) in paragraph at lines 20--28
LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.
"""


def main() -> int:
    # 1. Test full multi-error log parser directly (ensures we don't stop at first error)
    taxonomy = json.loads((SKILL_ROOT / "resources" / "latex_error_taxonomy.json").read_text(encoding="utf-8"))
    parsed_log = parse_compile_logs(SYNTHETIC_LOG, 'Warning--I didn\'t find a database entry for "ghost2025"\n', taxonomy)
    assert parsed_log["fatal_error_count"] == 2, f"Expected 2 fatal errors, got {parsed_log['fatal_error_count']}"
    assert parsed_log["unresolved_citations"] == 1
    assert parsed_log["unresolved_references"] == 1
    assert parsed_log["overfull_hbox_over_10pt"] == 1
    assert parsed_log["rerun_needed"] is True
    cats = {d["category"] for d in parsed_log["diagnostics"]}
    assert "undefined_control_sequence" in cats
    assert "table_column_or_overflow" in cats

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        in_dir = root / "input"
        work_proj = root / "work_proj"
        out_dir = root / "output"
        in_dir.mkdir()

        (in_dir / "case.json").write_text(
            json.dumps(
                {
                    "case_id": "synthetic-latex-test-01",
                    "output_contract": {"required_fields": ["case_id", "status", "deliverables", "sources", "uncertainty"]},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (in_dir / "main.tex").write_text(BROKEN_TEX, encoding="utf-8")
        (in_dir / "refs.bib").write_text(REFS_BIB, encoding="utf-8")

        # 2. Negative static audit on broken project
        insp_path = root / "inspection.json"
        p1 = run_py(INSPECT_SCRIPT, "--input-dir", str(in_dir), "--out", str(insp_path))
        assert p1.returncode == 0, f"inspect failed: {p1.stderr}"
        insp = json.loads(insp_path.read_text(encoding="utf-8"))
        issue_cats = {it["category"] for it in insp["project"]["static_issues"]}
        assert "package_order_conflict" in issue_cats, f"Missed package order conflict: {issue_cats}"
        assert "environment_mismatch" in issue_cats, f"Missed unclosed equation: {issue_cats}"
        assert "table_column_or_overflow" in issue_cats, f"Missed table column mismatch: {issue_cats}"
        assert "citation_or_bib_error" in issue_cats, f"Missed undefined cite: {issue_cats}"
        assert "undefined_reference_or_duplicate_label" in issue_cats, f"Missed undef ref / dup label: {issue_cats}"

        # 3. Positive repair + compile/audit pass (gracefully handles containers with or without pdflatex)
        shutil.copytree(in_dir, work_proj)
        (work_proj / "main.tex").write_text(FIXED_TEX, encoding="utf-8")
        p2 = run_py(
            COMPILE_SCRIPT,
            "--input-dir",
            str(in_dir),
            "--work-project",
            str(work_proj),
            "--inspection",
            str(insp_path),
            "--output-dir",
            str(out_dir),
            "--engine",
            "auto",
        )
        assert p2.returncode == 0, f"repaired audit failed: {p2.stdout} {p2.stderr}"
        res = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
        assert res["summary"]["static_errors"] == 0
        assert res["summary"]["compile_fatal_errors"] == 0
        assert (out_dir / "main.tex").exists()
        assert (out_dir / "changes.diff").exists()
        assert (out_dir / "build_manifest.json").exists()
        assert (out_dir / "reproduce_build.sh").exists()
        assert (out_dir / "audit_report.md").exists()
        # If no TeX engine is installed, verify honest partial status (never fabricated!)
        if res["summary"]["engine_used"] is None:
            assert res["status"] == "partial"
            assert res["deliverables"]["compiled_pdf"] is None

    print("SELF_TEST_PASSED: scipdf-latex-builder-auditor (static bugs + multi-error log parser + repaired build verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
