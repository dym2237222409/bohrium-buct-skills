---
name: scipdf-latex-builder-auditor
description: End-to-end LaTeX authoring, template adaptation, multi-pass compilation, root-cause error diagnostics, semantic/math/table/reference auditing, real PDF/PNG preview rendering, and reproducible build manifest generation skill.
---

# `scipdf-latex-builder-auditor` — LaTeX Authoring, Repair & Reproducible Build Skill

## 1. Mission & Triggers

Invoke this skill whenever the user or evaluation harness asks to:
- Author, repair, debug, adapt to a journal/conference/thesis template, or compile a **LaTeX project** (`LaTeX 写作`, `修复编译报错`, `适配期刊模板`, `整理公式与三线表`, `修交叉引用与 BibTeX`, `生成可复现构建记录`).
- Turn raw manuscripts, broken `.tex` trees, `.bib` files, and template `.cls`/`.sty` assets into clean, editable, compilable `.tex` sources, real compiled `.pdf` + rendered `.png` previews (when TeX/PDF tools exist in the container), and a deterministic build manifest.

### Four Core Quality Pillars
1. **编译得通 (Multi-Pass Convergence & Root-Cause Diagnostics)**: Discover the full `.tex` project tree (`\input`/`\include`/`.cls`/`.sty`/`.bib`/graphics), select the right TeX engine (`xelatex`/`lualatex` for CJK or `fontspec`, `pdflatex` otherwise), run multi-pass convergence (`engine -> bibtex/biber -> engine -> engine` until rerun warnings disappear), and parse the **entire** `.log`/`.blg` into deduplicated root-cause errors with exact `file:line`—never stopping at the first cascade symptom.
2. **改得对 (Math, Table, Reference & Template Fidelity)**: Audit math delimiter/environment balance, variable/unit conventions, tabular `&` column alignment vs. column specs & `Overfull \hbox` overflow (never silently masking overflow with blind `\scalebox`!), `\cite`/`\ref`/`\eqref`/`\label` closure against `.bib` and `.tex`, package load-order conflicts (e.g., `hyperref` vs. `cleveref`), and template required macros (`\title`, `\author`, `\begin{abstract}`).
3. **交得可用 (Clean Editable Source + Real Rendered Previews)**: Deliver clean, modular, editable `.tex` / `.bib` source under `/app/output/`, real compiled `.pdf`, and real rendered page `.png` previews (`pdftoppm` / `pymupdf` / `gs` when available) plus unified `changes.diff`—never passing off a broken snippet or screenshot as source, and never fabricating PDFs/PNGs if TeX is unavailable.
4. **复现得了 (Reproducible Build Manifest)**: Record exact TeX engine version, detected classes/packages/fonts, exact compile pass commands, convergence status, SHA-256 checksums, `build_manifest.json`, and `reproduce_build.sh`.

---

## 2. Harbor Runtime & I/O Contract

When executed in an automated evaluation container (installed at `/skills/weekly-skill`):
1. **Inspect actual project & environment first**: Read `/input/case.json`, `/input/task.md`, and all `.tex`/`.bib`/`.cls`/`.sty`/asset files under `/input/`. Probe actual installed TeX binaries (`pdflatex`, `xelatex`, `lualatex`, `bibtex`, `biber`, `kpsewhich`, `pdftoppm`, `gs`).
2. **Work directory**: Copy the mutable TeX project into `/app/work/project/` before editing/compiling so `/input/` remains pristine for exact diff computation.
3. **Deliverables under `/app/output/`**:
   - Clean editable source files (`.tex`, `.bib`, and needed local assets/templates).
   - Compiled `.pdf` and rendered `previews/page-*.png` (whenever TeX compilation and PDF rendering succeed in the environment; if a binary is missing, honestly report `status: "partial"` and exact environment limitation in `uncertainty`).
   - `changes.diff`: Unified diff between original `/input/` sources and repaired `/app/output/` sources.
   - `build_manifest.json` & `reproduce_build.sh`: Exact reproducible build record (engine version, packages, pass commands, SHA-256 hashes).
   - `audit_report.md`: Static + compile-log diagnostics, reference/citation closure, math/table hygiene, and change log.
   - `result.json`: MUST preserve `case_id` and **every original field** in `output_contract`, plus honest `status`, `sources`, `uncertainty`, and `change_records`.
4. **Budget & incremental saves**: Within 64 requests / 900s, run inspection immediately, fix root causes in batches rather than one error per compile, and save clean deliverables early.

---

## 3. End-to-End Execution Workflow

### Step 1 — Inspect Project Tree, Static Issues & TeX Environment
Resolve `SKILL_DIR` (`/skills/weekly-skill` inside Harbor, or local skill directory) and read `/input/case.json` (`output_contract`) + `/input/task.md` verbatim first so every requested output filename and `result.json` key is honored:
```bash
SKILL_DIR="/skills/weekly-skill"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(pwd)"
python3 "${SKILL_DIR}/scripts/inspect_latex_project.py" \
  --input-dir /input \
  --out /app/work/project_inspection.json
```
After compiling in Step 3, `compile_and_audit_latex.py` automatically verifies rendered PDF text (`pdf_text_audit`) for zero leftover `??` / `[?]` markers and probes `installed_cjk_fonts` via `fc-list` whenever CJK text is detected, so font or reference bugs can be fixed in one deterministic pass.
This deterministic stdlib script:
- Identifies the main `.tex` entry file (scoring `\documentclass`, `\begin{document}`, `case.json` hints), recursively resolves `\input`/`\include`, and inventories `.bib`, `.cls`, `.sty`, and graphic assets.
- Probes available TeX binaries (`pdflatex`, `xelatex`, `lualatex`, `bibtex`, `biber`, `latexmk`, `kpsewhich`) and PDF previewers (`pdftoppm`, `fitz`/PyMuPDF, `gs`).
- Recommends the right TeX engine (`xelatex` if `ctex`/`xeCJK`/`fontspec`/CJK characters detected; `lualatex` if `luacode`; else `pdflatex`) and bibliography backend (`biber` if `biblatex` loaded; else `bibtex`).
- Runs **pre-compile static checks**: environment/brace balance, math delimiters, tabular column count (`&` vs. column spec), `\cite` vs. `.bib` keys, `\ref`/`\eqref` vs. `\label` (plus duplicate labels), missing graphic files, package load-order conflicts (`hyperref` loaded before `amsmath` or after `cleveref`), and missing template macros.

### Step 2 — Edit Cleanly in `/app/work/project/`
Copy `/input/` (excluding `case.json`/`task.md`) into `/app/work/project/` and apply targeted, minimal fixes guided by `project_inspection.json` and `resources/latex_error_taxonomy.json`:
- Fix root causes first (missing `\end{...}`, missing packages/macro definitions, engine/CJK mismatch, broken `\begin{tabular}` column counts, undefined `\cite`/`\ref`)—never blindly silence errors with `\batchmode` or shrink overflowing wide tables with illegible `\resizebox`.
- For wide tables, prefer `booktabs` + `tabularx` (`X` columns), `p{...}` wrapping, `longtable` (for multi-page tables), or `siunitx` decimal alignment.
- Preserve every scientific number, formula symbol, equation label, and citation intent verbatim.

### Step 3 — Run Multi-Pass Compilation, Log Root-Cause Parser, Preview Renderer & Final Audit
```bash
python3 /skills/weekly-skill/scripts/compile_and_audit_latex.py \
  --input-dir /input \
  --work-project /app/work/project \
  --inspection /app/work/project_inspection.json \
  --output-dir /app/output \
  --engine auto
```
This script:
1. Executes real multi-pass compilation (`-interaction=nonstopmode -file-line-error`) with automatic `bibtex`/`biber` execution and rerun convergence loop (up to 4 passes).
2. Parses the **entire** `.log` and `.blg` into deduplicated root-cause diagnostics (`file`, `line`, `category`, `message`, `fix_hint`, `is_cascade`) using `resources/latex_error_taxonomy.json`, plus `Overfull \hbox` warnings and undefined reference/citation counts.
3. Re-runs static checks on the repaired sources to verify zero broken refs/cites/tables/environments.
4. When PDF is produced, renders real page previews to `/app/output/previews/page-*.png` via `pdftoppm`, `pymupdf`, or `gs` (and honestly logs if no renderer exists).
5. Computes unified `changes.diff` against `/input/`, copies clean `.tex`/`.bib`/`.pdf` to `/app/output/`, and writes `build_manifest.json`, `reproduce_build.sh`, `audit_report.md`, and `result.json`.

### Step 4 — Iterative Fix Loop (Compile -> Diagnose -> Patch -> Converge)
If compilation reports fatal errors, undefined references/citations, or severe `Overfull \hbox (> 10pt)`:
- Read `audit_report.md` / `build_manifest.json` root-cause diagnostics (`is_cascade: false` items first!).
- Patch `/app/work/project/` and re-run `compile_and_audit_latex.py` until exit code `0` (zero fatal errors, zero unresolved refs/cites, converged passes).

---

## 4. Edge Cases & Graceful Degradation

| Failure Mode | Required Action |
|---|---|
| **Missing `.cls` / `.sty` or TeX package** | Check with `kpsewhich`; if absent in container, do NOT fabricate compilation success—document exact missing file/package in `uncertainty` and propose clean standard fallback only if allowed by `task.md`. |
| **Missing CJK font under `xelatex`** | Inspect installed fonts (`fc-list` if available) and switch to an installed system CJK font or `ctex` default; log exact font mapping in `build_manifest.json`. |
| **TeX engine not installed in container** | Still execute complete static audit, source repairs, `changes.diff`, `reproduce_build.sh`, and set `status: "partial"` with explicit `uncertainty` noting TeX binary absence—never fabricate `.pdf` or `.png`. |
| **Error cascade (1 typo causing 20 log errors)** | Group downstream symptoms (`is_cascade: true`) under the earliest root cause (`file:line`) so the Agent fixes the actual bug in one edit. |
| **Missing figure file referenced by `\includegraphics`** | Flag in `audit_report.md` and `uncertainty`; never invent fake experimental plots; use `\usepackage[draft]{graphicx}` only if explicitly requested for layout proofing and mark `partial`. |

---

## 5. Bundled Resources & Verification

- `scripts/inspect_latex_project.py` — Stdlib project discovery, environment probe, and static LaTeX auditor.
- `scripts/compile_and_audit_latex.py` — Stdlib multi-pass compiler, full `.log`/`.blg` root-cause parser, PNG previewer, diff & manifest generator.
- `scripts/self_test.py` — End-to-end positive + negative synthetic verification suite (static bugs + cascading log parser + graceful compile check).
- `resources/latex_error_taxonomy.json` — Structured root-cause diagnostic taxonomy and fix strategies.
- `resources/template_and_package_rules.json` — Package load-order, engine selection, and template hygiene rules.
- `templates/build_report_schema.json` — Schema for `build_manifest.json`.
- `templates/result_template.json` — Harbor `result.json` output structure.
- `examples/minimal_example.md` — Synthetic end-to-end example.
