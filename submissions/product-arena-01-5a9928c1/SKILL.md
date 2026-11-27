---
name: scipdf-faithful-translator
description: Terminology-locked, paragraph-aligned scientific literature translation workflow with XY-Cut multi-column segmentation, context-carrying chunked translation, protected number/unit/formula/citation verification, negation & scope polarity auditing, and bilingual provenance reporting.
---

# Scientific Literature Faithful Translator (`scipdf-faithful-translator`)

## 1. Purpose & Trigger Conditions

Use this Skill whenever a task involves **translating scientific papers, technical sections, figure/table captions, or structured excerpts** where **terminology consistency, 1-to-1 paragraph alignment, numerical/formula/citation preservation, logical polarity (negation/hedging/conditions), and verbatim source traceability** are evaluated.

Instead of relying on prompt-only reminders, this Skill enforces quality mechanically across four pillars:
1. **术语稳 (Programmatic Terminology Locking)**: Extracts domain terms and paper-defined abbreviations, merges case-supplied glossaries, locks a single canonical target translation per term in `locked_glossary.json`, and **programmatically audits/backfills every occurrence** across Title, Abstract, Body, Captions, and Conclusion.
2. **段落全 (1:1 Paragraph & Sentence Completeness)**: Uses XY-Cut multi-column layout ordering + cross-column/cross-page paragraph stitching to assign deterministic IDs (`S0001..SNNNN` and sentence IDs `S0001.1..S0001.k`), guaranteeing zero omitted, duplicated, or misaligned paragraphs across long documents.
3. **关系准 (Relational, Numerical & Symbol Fidelity)**: Extracts protected tokens per segment (numbers, SI units, `[N]` / author-year citations, `Fig./Table/Eq.` cross-references, inline formulas) and runs deterministic post-translation checks on **negation triggers, epistemic hedging (`may/might`), necessary/sufficient conditions (`only when/unless`), quantitative bounds (`at least/no more than`), and concessive contrasts (`although/whereas`)**.
4. **可核验 (Source Provenance & Honest Review Checklist)**: Outputs paragraph-by-paragraph bilingual alignment with `(page, column, section_path, block_id, bbox, translation_rationale)` and generates an explicit **Human Confirmation Checklist** for polysemous terms, ambiguous pronouns, garbled fonts, or image-only regions rather than smoothing over uncertainty.

---

## 2. Skill Path Resolution & Bundled Resources

- **Harbor Evaluator Path**: `/skills/weekly-skill`
- **Standalone Local Path**: Directory containing this `SKILL.md`

```bash
if [ -d "/skills/weekly-skill/scripts" ]; then
  SKILL_DIR="/skills/weekly-skill"
else
  SKILL_DIR="$(pwd)"
fi
```

Bundled UTF-8 text resources:
- `scripts/inspect_case.py`: Discovers `/input/case.json`, `task.md`, source documents, and case glossaries; writes an early honest partial `/app/output/result.json`.
- `scripts/segment_and_extract_terms.py`: Multi-backend PDF/MD/TXT/TEX layout segmenter + protected-token & term-candidate extractor + context-windowed batch planner (`translation_batches.json`).
- `scripts/build_and_lock_glossary.py`: Priority-ordered glossary builder & conflict resolver (`case glossary` > `agent curated terms` > `seed scientific glossary`).
- `scripts/verify_and_export_bilingual.py`: Deterministic completeness, glossary consistency, numerical/citation parity, and negation/condition polarity auditor + bilingual Markdown/JSON exporter.
- `scripts/self_test.py`: Synthetic 2-column scientific PDF self-test (zero binary files stored).
- `resources/seed_scientific_glossary.json` & `resources/fidelity_rules.json`: Cross-disciplinary terminology/false-friends dictionary and polarity verification rules.
- `templates/glossary_schema.json`, `templates/result_template.json` & `examples/minimal_example.md`.

---

## 3. Standard Execution Workflow (Harbor `/input` → `/app/work` → `/app/output`)

> **Execution Budget Discipline**: Operate well within the 64-request / 900s / 16384-token budget. Save the initial honest checkpoint in Step 1 immediately, translate batch-by-batch using `translation_batches.json` so responses never hit token truncation, and run mechanical verification in Step 5 before finishing.

### Step 1 — Inspect Case Inputs, Scope & Glossary Constraints

Read the actual `/input/case.json` and `task.md` first (never assume content from file names):

```bash
python "${SKILL_DIR}/scripts/inspect_case.py" \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output
```

Inspect `/app/work/case_manifest.json` (and `/input/case.json` + `/input/task.md` directly) to confirm:
- Exact `case_id` and all required fields/shapes in `output_contract`.
- Source document path(s), target language (default `zh`, or `en` if ZH→EN), and any specified page/section/paragraph scope.
- Any mandatory terminology table supplied in `case.json` or `/input/` (`glossaries_found`).

### Step 2 — Layout-Aware Segmentation & Candidate Term Extraction

Run `segment_and_extract_terms.py` (supports `.pdf`, structured `.json`/JATS, `.xml`, `.docx`, `.md`, `.txt`, `.tex`; pass `--pages` if `case.json` restricts translation to specific pages):

```bash
python "${SKILL_DIR}/scripts/segment_and_extract_terms.py" \
  --source "<ACTUAL_SOURCE_PATH>" \
  --out-dir /app/work/segmented \
  --batch-chars 1800
```

**CRITICAL — Satisfy `output_contract` & Preserve Honest Locations**:
- Check `/input/case.json`'s `output_contract` and `/input/task.md` verbatim before writing outputs; deliver every requested custom filename and top-level `result.json` key.
- For JATS/JSON/XML/DOCX sources without PDF pages, keep `page: null` and cite true `block_id` / `paragraph_id`—never fabricate `page: 1`.
- Review `/app/work/segmented/term_candidates.json` (`unassigned_frequent_phrases`, `paper_abbreviation_definitions`, `polysemy_and_false_friend_alerts`) and write `/app/work/agent_terms.json` with domain-grounded translations and `polysemy_decisions`.

### Step 3 — Build & Lock the Canonical Terminology Table

```bash
python "${SKILL_DIR}/scripts/build_and_lock_glossary.py" \
  --segments-json /app/work/segmented/source_segments.json \
  --candidates-json /app/work/segmented/term_candidates.json \
  --agent-terms-json /app/work/agent_terms.json \
  --out-glossary /app/work/locked_glossary.json
```

(If Step 1 discovered any case-provided glossary file, pass `--custom-glossary <PATH>` so case terms take Priority 1.)

### Step 4 — Context-Carrying Batch Translation

Read `/app/work/locked_glossary.json` and `/app/work/segmented/translation_batches.json`. Translate every translatable segment `S0001..SNNNN` in order, obeying four strict rules:
1. **Mandatory Glossary Compliance**: Whenever a segment appears in a locked term's `occurrence_segments`, you **must** use its exact `target_term` verbatim across Title, Abstract, Headings, Body, Captions, Table Headers, and Conclusion.
2. **Protected Token Preservation**: Keep all numbers, decimal precisions, SI units, inequality signs (`≤, ≥, ±`), inline formulas/variables, figure/table numbers, and citation brackets (`[1]`, `[2–4]`, `(Author et al., 2024)`) exact.
3. **Syntactic & Logical Precision**: Resolve pronoun antecedents (`it`, `this`, `the latter`, `which`) against `prev_context_tail`; preserve negation (`not`, `without`, `fail to`, `independent of`), hedging (`may`, `suggest`), conditions (`only when`, `unless`), and contrasts (`although`, `whereas`).
4. **Honest Uncertainty Annotation**: If a source span is ambiguous, corrupted, mixed-language, or references an unreadable image formula/table, record it in that segment's `uncertain_spans` list and explain in `rationale` rather than guessing.

Save all segment translations to `/app/work/translated_segments.json` as:
```json
{
  "segments": [
    {
      "seg_id": "S0001",
      "target_text": "...",
      "rationale": "...",
      "uncertain_spans": []
    }
  ]
}
```

### Step 5 — Programmatic Verification, Repair & Deliverable Export

Run the deterministic verifier and exporter:

```bash
python "${SKILL_DIR}/scripts/verify_and_export_bilingual.py" \
  --segments-json /app/work/segmented/source_segments.json \
  --translations-json /app/work/translated_segments.json \
  --glossary-json /app/work/locked_glossary.json \
  --case-json /input/case.json \
  --output-dir /app/output \
  --strict
```

- If `--strict` reports any `glossary_violations`, `missing_segments`, or `fidelity_issues` (number/citation mismatch or negation flip), fix those exact `seg_id`s in `/app/work/translated_segments.json` and re-run Step 5 until `valid: true`.
- Finally, verify that `/app/output/result.json` satisfies every custom field name/type in `/input/case.json`'s `output_contract` alongside `/app/output/bilingual_translation.md`, `/app/output/bilingual_segments.json`, `/app/output/locked_glossary.json`, and `/app/output/translation_qa_report.md`.

---

## 4. Exception Handling Rules

- **Scanned / Missing Text Layer PDF**: `segment_and_extract_terms.py` logs affected pages in `untranslatable_notices`; if `tesseract` OCR is installed in the container, OCR text may be extracted with explicit OCR confidence caveats; otherwise set `status: "partial"` or `"unverifiable"` and identify exact pages for human takeover.
- **Image-Based Formulas or Tables**: Translate text captions/headers normally, keep formula identifiers (`Eq. (1)`) intact, and note image-embedded equations/tables in `uncertainty.unverifiable_or_untranslated_spans`.
- **Encrypted / Corrupted Source**: Report `is_encrypted: true` (`status: "unverifiable"`) without inventing translations.
- **Unparseable or Foreign-Script References**: Keep bibliography entries in original scholarly citation form (`translatable: false`) and link inline `[N]` citations cleanly.

---

## 5. Self-Check

```bash
python "${SKILL_DIR}/scripts/self_test.py"
```
