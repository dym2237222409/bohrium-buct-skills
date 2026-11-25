---
name: scipdf-evidence-parser
description: Layout-aware, evidence-anchored scientific PDF parsing workflow for multi-column reading order recovery, header/footer/footnote separation, complex & cross-page table reconstruction, formula/symbol preservation, adaptive scan OCR, and contract-verified output generation with page/bbox provenance.
---

# Scientific PDF Evidence Parser (`scipdf-evidence-parser`)

## 1. Purpose & Trigger Conditions

Use this Skill whenever a task requires **reading, parsing, extracting structured knowledge, answering questions with citations, translating sections, or auditing claims from scientific PDF documents**—especially when exact wording, multi-column reading order, complex/continued tables, mathematical symbols, scanned-page handling, and page/block-level verifiability matter.

This Skill specifically solves the four core failure modes of naive Agent PDF reading:
1. **提得全 (Completeness)**: Separates running headers, running footers, and bottom footnotes into dedicated structures instead of dropping lines or contaminating body sentences; captures captions and bibliography entries.
2. **排得对 (Reading Order)**: Uses **XY-Cut spanning-band segmentation + vertical whitespace gutter projection** to reconstruct 1/2/3-column reading order and stitch cross-column / cross-page broken paragraphs without column bleed.
3. **拆得清 (Structural Fidelity)**: Reconstructs multi-level section hierarchies, merged/multi-header and cross-page continued tables (`JSON` + `Markdown` + `HTML`), numbered equations, superscripts/subscripts (`x^{2}`, `H_{2}O`), ligatures, and Symbol/PUA Greek glyphs (`α–ω`, `Δ`, `±`, `≤`, `≥`).
4. **核得回 (Verifiability & Honesty)**: Anchors every extracted paragraph, table, figure, equation, and answer to `(source_file, page, printed_page_label, column, section_path, block_id, bbox, verbatim_quote)`, while explicitly reporting unreported fields, material conflicts, CID-garbled fonts, image-only tables, encrypted files, or low-confidence OCR spans instead of guessing.

---

## 2. Skill Directory & Runtime Path Resolution

- **Harbor Evaluator Path**: `/skills/weekly-skill`
- **Local / Standalone Path**: Directory containing this `SKILL.md`

Before invoking scripts, resolve `SKILL_DIR` once in shell:

```bash
if [ -d "/skills/weekly-skill/scripts" ]; then
  SKILL_DIR="/skills/weekly-skill"
else
  SKILL_DIR="$(pwd)"
fi
```

Bundled resources (all pure UTF-8 text, zero external network dependencies):
- `scripts/inspect_case.py`: Reads `/input/case.json`, `task.md`, and `environment/resources.yaml`; probes installed Python/CLI PDF & OCR tools; writes `/app/work/case_manifest.json` and an **early honest partial `/app/output/result.json`**.
- `scripts/parse_scientific_pdf.py`: 4-tier multi-backend parser (`PyMuPDF` → `pdfplumber` → `Poppler pdftohtml -xml` → pure-Python stdlib stream parser) + adaptive `Tesseract` OCR fallback.
- `scripts/query_evidence.py`: Keyword/regex/section/page retriever over `parsed_document.json` returning verbatim quotes and exact `bbox` anchors.
- `scripts/validate_output.py`: Verifies `/app/output/result.json` against `case.json` (`case_id`, all `output_contract` keys), checks every cited `page` against `[1, num_pages]` and `verbatim_quote` against extracted page text, and writes `/app/output/parsing_report.md`.
- `scripts/self_test.py`: Synthetic 2-column PDF generator & end-to-end self-check.
- `resources/unicode_math_map.json` & `resources/section_patterns.json`: Symbol/ligature maps and bilingual (EN/ZH) academic structural grammar.
- `templates/result_template.json` & `examples/minimal_example.md`: Reference output schema and clearly labeled synthetic example.

---

## 3. Standard Execution Workflow (Harbor `/input` → `/app/work` → `/app/output`)

> **Budget Discipline**: Respect the runtime budget (64 model requests, 900s wall time, 16384 max output tokens). Complete mechanical discovery and PDF parsing in **Steps 1–2 within the first 60 seconds** so an honest checkpoint exists early, then refine task-specific answers and validate.

### Step 1 — Inspect Case Boundaries & Save Early Honest Checkpoint

Always read the **actual** `/input/case.json`, `task.md`, and `environment/resources.yaml` first. Never assume content from filenames or domain labels.

```bash
python "${SKILL_DIR}/scripts/inspect_case.py" \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output
```

Read `/app/work/case_manifest.json` (and `/input/case.json` + `/input/task.md` directly) to confirm:
1. Exact `case_id` and every required field/type in `output_contract`.
2. All input PDF paths (`discovered_pdfs`) and any supplementary files or page/evidence scope constraints.
3. Specific task action requested by the case (e.g., full structural parsing, specific parameter/table extraction, grounded Q&A, bilingual translation of target sections, or conflict/boundary verification).

### Step 2 — Run Layout-Aware Multi-Backend PDF Parsing

For each target PDF discovered in Step 1, run `parse_scientific_pdf.py` under `/app/work`:

```bash
python "${SKILL_DIR}/scripts/parse_scientific_pdf.py" \
  --pdf "<ACTUAL_PDF_PATH>" \
  --out-dir "/app/work/parsed/<DOC_ID>" \
  --backend auto \
  --ocr-mode auto
```

Inspect `/app/work/parsed/<DOC_ID>/parse_diagnostics.json` immediately:
- Check `status`, `document_mode`, `counts` (`sections`, `blocks`, `tables`, `figures`, `equations`, `references`), and `uncertainty`.
- If `tables` were extracted, inspect `/app/work/parsed/<DOC_ID>/tables/table_*.md` (and `.html` when multi-row/merged headers matter).
- If any page is flagged in `human_takeover_locations` (e.g., image-only raster table or low-resolution figure/formula critical to a question) and vision/page-rendering tools (`pdftoppm` / `fitz`) are available, render only those specific pages to `/app/work/page_renders/` for targeted inspection; otherwise record the exact page and bbox honestly in `uncertainty`.

### Step 3 — Retrieve Verbatim Evidence & Construct Contract-Compliant Deliverables

Instead of dumping raw unindexed text into context, query `parsed_document.json` (and read `structured_fulltext.md` / `tables/*.md` for target sections) to gather exact evidence anchors:

```bash
python "${SKILL_DIR}/scripts/query_evidence.py" \
  --parsed-json "/app/work/parsed/<DOC_ID>/parsed_document.json" \
  --query "<KEYWORDS_FROM_CASE_QUESTION>" \
  --include-tables \
  --include-equations \
  --top-k 10
```

Populate `/app/output/result.json` (and any additional editable artifacts requested by `case.json` / `task.md`, such as structured Markdown, CSV/HTML tables, or rendered PDF/PNG previews if Office/diagram artifacts are requested):
1. **Preserve Contract Shape**: Keep `case_id` and **every** original key required by `/input/case.json`'s `output_contract`. Never rename or omit required contract fields.
2. **Ground Every Key Conclusion**: For each answer, extracted value, table cell, or structural claim, record an anchor in `sources` (and any contract-specific citation field) containing:
   - `source_file`, `page` (1-based physical PDF page), `printed_page_label`, `column`, `section_path`, `block_id`, `bbox`, `verbatim_quote`, and `extraction_method`.
3. **Strict Epistemic Honesty (Zero Fabrication)**:
   - **Not reported in PDF** → Set value to `null` / `"not_reported"` per contract type and list in `uncertainty.unreported_in_source` (state which sections/pages were checked).
   - **Unreadable / Scanned without OCR / Encrypted / Garbled CID** → Do **not** guess missing numbers or symbols from prior domain knowledge; record exact `page` and `bbox` in `uncertainty.unverifiable_items` and `uncertainty.human_takeover_locations`, and set `status` to `"partial"` or `"unverifiable"` as appropriate.
   - **Conflicting values within paper** (e.g., Abstract says `92%` while Table 2 says `91.4%`) → Report both values with their respective page/block anchors in `uncertainty.material_conflicts` instead of silently picking one.
4. **Copy Reusable Structured Artifacts to `/app/output/`**:
   - Copy or write clean deliverables requested or helpful for inspection (e.g., `/app/output/structured_document.md`, `/app/output/parsed_document.json`, `/app/output/tables/`) alongside `/app/output/result.json` without violating any file-layout constraints in `case.json`.

### Step 4 — Mechanical Validation & Audit Report Generation

Always run `validate_output.py` before finishing:

```bash
python "${SKILL_DIR}/scripts/validate_output.py" \
  --result-json /app/output/result.json \
  --case-json /input/case.json \
  --parsed-dir /app/work/parsed \
  --report-out /app/output/parsing_report.md
```

- Fix any reported `errors` (missing `output_contract` key, `case_id` mismatch, out-of-range page number) and review any `warnings` (unmatched `verbatim_quote`) before concluding.

---

## 4. Exception & Edge-Case Handling Matrix

| Condition | Detection Signal | Required Action & `status` |
|---|---|---|
| **Password-Encrypted PDF** | `parsed_document.json["is_encrypted"] == true` | Set `status: "unverifiable"`, report encryption in `status_reason` and `uncertainty.human_takeover_locations`. Never fabricate content. |
| **Zero-Text Scanned PDF** | `page_mode == "scanned_no_text_layer"` | `--ocr-mode auto` automatically runs 300-DPI render + `tesseract` TSV. If `tesseract` is unavailable, set `status: "unverifiable"` (or `"partial"` if hybrid) and list affected pages. |
| **Image / Raster Table** | `table_type == "image_or_unstructured"` | Crop/OCR table region if `tesseract` is present; if cells remain ambiguous, keep `grid: []` and disclose exact page + caption in `uncertainty.unverifiable_items`. |
| **Garbled CID / Custom Font** | `(cid:N)` or `\ufffd` in `garbled_or_low_confidence_spans` | Trigger `--ocr-mode force` on affected page or preserve raw context and log exact `block_id` + `bbox` in `uncertainty.garbled_or_low_confidence_spans`. |
| **Printed vs PDF Page Offset** | `printed_page_label != str(page)` | Record **both** 1-based PDF `page` and `printed_page_label` (e.g., PDF `page: 3`, `printed_page_label: "1027"`) so human and automated graders both match unambiguously. |

---

## 5. Self-Check & Minimal Example

To verify the toolkit in any environment without external PDFs:

```bash
python "${SKILL_DIR}/scripts/self_test.py"
```

See `examples/minimal_example.md` and `templates/result_template.json` for full input/output schemas (explicitly marked as synthetic examples).
