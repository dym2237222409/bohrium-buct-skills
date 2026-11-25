---
name: scipdf-structured-notes
description: Section-aligned scientific literature summarization and structured note-taking workflow featuring premise-bound Paper Cards, acronym/synonym-deduplicated searchable knowledge items, fine-grained page/bbox anchor verification, and multi-paper comparison exports.
---

# Scientific Structured Literature Notes (`scipdf-structured-notes`)

## 1. Purpose & Trigger Conditions

Use this Skill whenever a task requires **summarizing a scientific paper, building structured reading notes, extracting comparable Paper Cards, cataloging concepts/variables/methods/findings, or producing citation-ready literature syntheses** with strict provenance back to the source PDF or manuscript.

This Skill directly solves the four failure modes of generic LLM summarization:
1. **对得上 (Section & Argument Skeleton Alignment)**: Parses the paper's **actual hierarchical section tree** (`SEC01..SECNN`) before summarizing, preserving original section order and inter-section logical bridges instead of flattening into a generic boilerplate summary.
2. **留得住 (Premise-Bound Paper Card)**: Records `research_problem_and_gap`, `datasets_materials_or_subjects`, `methodology_and_conditions`, `condition_bound_findings`, and `limitations_and_open_questions`—requiring **every main finding to carry explicit `holding_preconditions`** (operating regimes, statistical assumptions, sample bounds) and separating author-stated limitations from unreported controls.
3. **找得回 (Fine-Grained Dereferenceable Anchors)**: Indexes every paragraph (`P0001..PNNNN`), table (`TAB01..TABNN`), figure (`FIG01..FIGNN`), and equation (`EQ01..EQNN`) with `(page, printed_page_label, column, section_path, bbox, verbatim_text)`, mechanically verifying every cited anchor and quantitative metric.
4. **用得上 (Deduplicated Searchable Knowledge & Multi-Paper Reuse)**: Automatically merges abbreviated/synonymous entries (e.g., `Turnover Frequency` ↔ `TOF`), checks for contradictory definitions, distinguishes `machine_verifiable` vs `requires_human_judgment` fields, and exports reusable `structured_notes.md`, `knowledge_items.jsonl`, and `paper_card.csv`.

---

## 2. Skill Path Resolution & Bundled Resources

- **Harbor Evaluator Path**: `/skills/weekly-skill`
- **Local Standalone Path**: Directory containing this `SKILL.md`

```bash
if [ -d "/skills/weekly-skill/scripts" ]; then
  SKILL_DIR="/skills/weekly-skill"
else
  SKILL_DIR="$(pwd)"
fi
```

Bundled UTF-8 text resources:
- `scripts/inspect_case.py`: Discovers `/input/case.json`, `task.md`, and source papers; initializes an early honest partial `/app/output/result.json`.
- `scripts/parse_and_index_paper.py`: 4-tier XY-Cut multi-column PDF/MD/TEX parser generating `section_skeleton.json`, `anchor_registry.json`, and `abbreviation_and_symbol_candidates.json`.
- `scripts/compile_and_verify_notes.py`: Dereferences all `[A:...]` anchors, checks numerical claim parity, deduplicates acronym/synonym knowledge items, and exports Markdown, JSONL, CSV, verification report, and contract-compliant `result.json`.
- `scripts/query_notes.py`: Downstream CLI to search `knowledge_items.jsonl` or build side-by-side comparison tables across multiple Paper Cards.
- `scripts/self_test.py`: Synthetic 2-column scientific PDF generator & end-to-end self-test.
- `resources/field_dictionary.json` & `resources/anchor_and_heading_rules.json`: Field specifications (`machine_verifiable` vs `requires_human_judgment`) and anchor/cue grammar.
- `templates/notes_input_schema.json`, `templates/result_template.json` & `examples/minimal_example.md`.

---

## 3. Standard Execution Workflow (Harbor `/input` → `/app/work` → `/app/output`)

> **Execution Budget Discipline**: Operate cleanly within 64 model requests / 900s / 16384 tokens. Run Steps 1–2 immediately to save an honest early checkpoint and build the exact anchor index before authoring notes.

### Step 1 — Inspect Case Requirements & Save Early Honest Checkpoint

Read `/input/case.json` and `task.md` first (never assume paper contents from titles or domain tags):

```bash
python "${SKILL_DIR}/scripts/inspect_case.py" \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output
```

Read `/app/work/case_manifest.json` (and `/input/case.json` + `/input/task.md`) to confirm `case_id`, all `output_contract` fields, source paper path(s), and any task-specific focus questions or custom note fields.

### Step 2 — Parse Paper into Section Skeleton & Fine-Grained Anchor Registry

```bash
python "${SKILL_DIR}/scripts/parse_and_index_paper.py" \
  --source "<ACTUAL_PAPER_PATH>" \
  --out-dir /app/work/indexed
```

Inspect `/app/work/indexed/section_skeleton.json`, `/app/work/indexed/abbreviation_and_symbol_candidates.json`, and `/app/work/indexed/anchor_registry.json`:
- Note every `section_id` (`SEC01..SECNN`), its `heading`, and its `precondition_cue_anchors` / `limitation_cue_anchors`.
- Check `scanned_or_empty_pages` or `is_encrypted`: if any pages lack extractable text, render targeted pages via `pdftoppm`/`fitz` or `tesseract` if available, or disclose exact page numbers honestly in `uncertainty`.

### Step 3 — Author Grounded `structured_notes_draft.json`

Following `templates/notes_input_schema.json` and `resources/field_dictionary.json`, write `/app/work/structured_notes_draft.json`:
1. **`paper_card`**:
   - Populate `title`, `authors_and_year`, `research_problem_and_gap`, `datasets_materials_or_subjects`, and `methodology_and_conditions` with exact anchor IDs (`Pxxxx`, `TABxx`, `FIGxx`, `EQxx`).
   - For every entry in `condition_bound_findings`, explicitly fill **`holding_preconditions`** (the temperature/pressure/sample/statistical regime or theoretical assumptions under which the finding holds) and list exact `quantitative_metrics` verbatim from the anchor.
   - In `limitations_and_open_questions`, separate `author_stated_limitations` (with anchors) from `unreported_or_missing_controls` (stating which sections/pages were checked).
2. **`section_summaries`**:
   - Summarize **every** non-reference section from `section_skeleton.json` in its original order (`SEC01..SECNN`), recording `section_role_and_purpose`, `methods_or_arguments`, `findings_and_conditions`, `logical_bridge_to_next`, and `evidence_anchors`.
3. **`knowledge_items`**:
   - Extract atomic entries (`concept`, `variable_or_symbol`, `method_or_protocol`, `finding_with_condition`, `dataset_or_material`, `limitation`) with `canonical_name`, `aliases` (acronyms, symbols, synonyms), `definition_or_statement`, `conditions_or_units`, `verification_tier` (`machine_verifiable` vs `requires_human_judgment`), and `anchors`.

### Step 4 — Compile, Deduplicate, Dereference & Verify Deliverables

```bash
python "${SKILL_DIR}/scripts/compile_and_verify_notes.py" \
  --index-dir /app/work/indexed \
  --draft-json /app/work/structured_notes_draft.json \
  --case-json /input/case.json \
  --output-dir /app/output \
  --strict
```

- If `--strict` reports any unresolved anchor ID, missing Paper Card field, missing `holding_preconditions`, or unmatched numeric claim warning, fix `/app/work/structured_notes_draft.json` against `anchor_registry.json` and re-run Step 4 until `valid: true`.
- Verify that `/app/output/result.json` also populates any case-specific top-level keys required by `/input/case.json`'s `output_contract`.

---

## 4. Exception & Boundary Handling

- **Encrypted / Permission-Locked PDF**: `is_encrypted: true` → set `status: "unverifiable"` and report honestly without fabricating notes.
- **Scanned Pages / Image-Only Tables or Equations**: Record affected page numbers in `uncertainty.unverifiable_items` and `human_takeover_locations`; never guess unreadable parameter values.
- **Ultra-Long Manuscripts / Supplementary Information**: `section_skeleton.json` indexes section-by-section anchor IDs so you can read and summarize each `SECxx` incrementally without exceeding context limits.
- **Conflicting Values Across Paper** (e.g., Abstract vs Table discrepancy): Record both values and their anchors in `uncertainty.material_conflicts`.

---

## 5. Self-Check

```bash
python "${SKILL_DIR}/scripts/self_test.py"
```
