# Minimal Synthetic Invocation Example (`_synthetic_example: true`)

> **Note**: The `.tex`, `.bib`, and log snippets below are **synthetic demonstration data** illustrating `scipdf-latex-builder-auditor`. They are not formal test cases.

## 1. Synthetic Broken Project (`/tmp/demo_latex_in/`)

- `main.tex` contains 4 intentional defects:
  1. `cleveref` loaded before `hyperref` (package order conflict).
  2. `tabular` spec `{lcc}` (3 columns) containing a row with 4 cells (`A & 1.2 & 3.4 & 5.6 \\`).
  3. Unclosed `\begin{equation}` missing `\end{equation}`.
  4. Citation `\cite{ghost2025}` absent from `refs.bib` and duplicate `\label{eq:loss}`.

## 2. Commands

```bash
# Step 1: Discover project, probe TeX toolchain, run pre-compile static audit
python3 scripts/inspect_latex_project.py \
  --input-dir /tmp/demo_latex_in \
  --out /tmp/demo_work/project_inspection.json

# Step 2: Patch root causes in /tmp/demo_work/project, then run multi-pass compile + audit
python3 scripts/compile_and_audit_latex.py \
  --input-dir /tmp/demo_latex_in \
  --work-project /tmp/demo_work/project \
  --inspection /tmp/demo_work/project_inspection.json \
  --output-dir /tmp/demo_output \
  --engine auto
```

## 3. Illustrative Audited Output Excerpt (Synthetic)

- **Static Audit Before Repair**: Caught `package_order_conflict` (`cleveref` before `hyperref`), `table_column_mismatch` (row has 4 cells vs 3-column spec), `unclosed_environment` (`equation`), `undefined_citation` (`ghost2025`), and `duplicate_label` (`eq:loss`).
- **After Repair**: `static_errors = 0`, clean unified `changes.diff`, reproducible `build_manifest.json` + `reproduce_build.sh`, plus real compiled `main.pdf` and `previews/page-1.png` when `pdflatex`/`xelatex` and `pdftoppm` are installed (or honest `status: "partial"` with exact tool availability recorded in `uncertainty` if TeX is absent).
