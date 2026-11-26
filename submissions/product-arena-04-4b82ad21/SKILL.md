---
name: scipdf-intro-related-writer
description: Evidence-grounded Introduction and Related Work writing and verification skill. Organizes background without empty clichés, groups related work by problem/method/evaluation dimensions rather than chronological paper dumps, derives research gaps directly from cited limitations/conflicts, aligns every contribution 1:1 with the author's actual methods/figures/tables/results, and audits 100% citation and provenance traceability.
---

# `scipdf-intro-related-writer` — Evidence-Grounded Introduction & Related Work Skill

## 1. Mission & Triggers

Invoke this skill whenever the user or evaluation harness asks to:
- Draft, revise, restructure, or audit a paper's **Introduction** and/or **Related Work** section (`写引言`, `整理相关工作`, `推导研究空白`, `提炼贡献`, `补引用`, `核对引用覆盖`).
- Turn raw research materials (author draft/outline, experiment tables/figures, literature notes, BibTeX/RIS/JSON bibliographies, PDFs, or case instructions) into publication-ready prose with full claim-to-source traceability.

### Four Core Quality Pillars
1. **立得住 (Grounded Research Background)**: Anchor motivation in concrete research lineage, practical/scientific bottlenecks, and technical constraints—never empty openers ("近年来受到广泛关注", "With the rapid development of...").
2. **分得清 (Thematic Related Work Comparison)**: Group prior works by **problem setting, methodological route, or evaluation paradigm** (`>= 2` explicit thematic groups) and compare conclusions, applicable conditions, and tradeoffs on unified axes—never sequential abstract concatenation.
3. **说得实 (Derived Research Gaps)**: Derive every research gap from explicit limitations, uncovered regimes, or conflicting findings of *cited* works (`cited_ids -> shared_limitation_or_conflict -> gap -> how_this_work_fills`). Distinguish evidence-backed statements from explicit `[AUTHOR_JUDGMENT]` or `[NEEDS_EVIDENCE]`.
4. **对得上 (1:1 Contribution Alignment & Verified Citations)**: Map every contribution bullet directly to concrete methods, tables, figures, or quantitative results present in the author's materials; verify every in-text citation against real input bibliography/materials with zero fabricated citations or ungrounded scope inflation.

---

## 2. Harbor Runtime & I/O Contract

When executed in an automated evaluation container (installed at `/skills/weekly-skill`):
1. **Read real inputs first**: Always inspect `/input/case.json`, `/input/task.md`, and every attached file under `/input/`. Never assume paper content from folder/case labels.
2. **Work directory**: Place intermediate manifests, writing plans, and audit logs under `/app/work/`.
3. **Deliverables under `/app/output/`**:
   - `result.json`: MUST preserve `case_id` and **every original field** required by `output_contract` in `/input/case.json` or `/input/task.md`, plus honest `status` (`completed` | `partial` | `blocked`), `sources`, `uncertainty`, and `change_records`.
   - Editable manuscript: `introduction_and_related_work.md` (and `introduction_and_related_work.tex` if LaTeX output is requested).
   - Structured comparison & traceability artifacts: `comparison_matrix.md`, `evidence_traceability.json`, `review_checklist.md`.
4. **Save early & iterate**: Within the execution budget (64 requests / 900s), produce an audited partial/complete deliverable early, then refine any failed audit gates rather than risking a timeout.
5. **Strict honesty**: Never invent citations, author names, venues, years, or experimental numbers. Missing evidence must be explicitly tagged (`[NEEDS_EVIDENCE]` / `[NEEDS_AUTHOR_CONFIRMATION]`) and logged in `uncertainty`.

---

## 3. End-to-End Execution Workflow

### Step 1 — Inspect & Index All Input Materials (Deterministic Script)
Run `scripts/inspect_materials.py` before drafting a single sentence:
```bash
python3 /skills/weekly-skill/scripts/inspect_materials.py \
  --input-dir /input \
  --out /app/work/material_manifest.json
```
This script:
- Parses `/input/case.json` and `/input/task.md` for `case_id`, target language, citation format (`numeric`, `author-year`, `latex-cite`), audience, word/length bounds, and `output_contract` keys.
- Indexes all bibliography/literature sources (`.bib`, `.json`, `.jsonl`, `.md`, `.txt`, `.csv`, `.tex`, and `.pdf` via stdlib/optional extractors) into `literature_inventory` with canonical `ref_id`, `authors`, `year`, `title`, `venue`, and `source_file`.
- Extracts author-paper facts (method names, datasets, metrics, tables, figures, numbers) into `author_evidence_index`.
- Flags input anomalies (empty bibliography, missing source text, ambiguous citation style, conflicting numbers) in `input_warnings`.

### Step 2 — Construct Structured Writing Plan (`/app/work/writing_plan.json`)
Following `templates/writing_plan_schema.json` and `resources/comparison_taxonomy.json`, author a structured JSON plan *before* final compilation:
1. **`background_claims`**: Context -> specific technical bottleneck -> why now. Each item carries `claim_id`, `text`, `evidence_type` (`cited_fact` | `author_evidence` | `author_judgment`), and `source_ids`.
2. **`related_work_groups`**: At least 2 thematic groups (`group_id`, `group_title`, `grouping_axis`, `comparison_dimensions`, `works[]` with `ref_id`, `core_method`, `finding`, `applicable_condition`, `limitation`, `source_locator`, plus `group_synthesis` comparing tradeoffs across works).
3. **`gap_derivations`**: Each gap links `derived_from_refs` (verified `ref_id`s), `limitation_or_conflict`, `gap_statement`, `how_this_work_addresses`, and `evidence_status` (`verified` | `partial_material` | `author_judgment`).
4. **`contributions`**: Each item links `contrib_id`, `statement`, `supporting_evidence` (specific method/table/figure/metric from `author_evidence_index`), `numeric_claims[]`, `scope_boundary`, and `verification_status`.
5. **`prose_sections`**: Cohesive, publication-grade prose for `background`, `related_work`, `research_gap`, and `contributions` (matching requested language, venue register, and citation style), with citations anchored to specific claims.
6. **`uncertainties` & `change_records`**: Explicit log of material gaps, unverified citations, scope boundaries, and revisions.

### Step 3 — Run 7-Gate Audit & Compile Final Deliverables
```bash
python3 /skills/weekly-skill/scripts/audit_and_compile_intro.py \
  --manifest /app/work/material_manifest.json \
  --plan /app/work/writing_plan.json \
  --output-dir /app/output
```
The auditor enforces **7 deterministic gates**:
1. **Four-Part Structural Completeness**: Background, Related Work, Research Gap, Contributions all non-empty and logically linked.
2. **Cliché & Empty-Opener Lint**: Rejects generic filler openers (`resources/cliche_and_overclaim_lexicon.json`) in Background.
3. **Thematic Grouping & Anti-Listing Check**: Requires `>= 2` thematic groups with explicit comparison dimensions and cross-work synthesis; flags chronological paper-by-paper abstract dumps.
4. **Gap Derivation Chain Verification**: Every gap must cite `>= 1` verified `ref_id` from `literature_inventory` (unless bibliography is genuinely empty, in which case `partial` + `uncertainty` is mandatory) and specify both limitation/conflict and how the current work addresses it.
5. **Contribution ↔ Evidence 1:1 Alignment**: Every contribution must reference verified author-material evidence (or explicitly mark `needs_author_confirmation`) and pass numeric grounding against `author_evidence_index`.
6. **Citation Integrity & Coverage**: 100% of in-text citation keys/tags must resolve to `literature_inventory` (zero fabricated refs); reports coverage ratio of key/recent literature and flags missing metadata fields (`author`/`year`/`venue`).
7. **Traceability & Uncertainty Honesty**: Verifies claim-level source provenance, checks overclaim verbs lacking evidence, and preserves all `output_contract` fields in `/app/output/result.json`.

### Step 4 — Iterative Repair Loop
If `audit_report.overall_pass` is `false` or any `error`-level violation exists:
- Inspect `/app/work/audit_report.json` (or `/app/output/review_checklist.md`).
- Fix every violation in `/app/work/writing_plan.json` (remove clichés, replace fabricated/mismatched keys with real `ref_id`s, tighten overclaims, bind contributions to real tables/figures, or explicitly mark genuine input gaps).
- Re-run `audit_and_compile_intro.py` until all error gates pass (or only genuine, honestly documented input-material gaps remain).

---

## 4. Edge Cases & Exception Handling

| Scenario | Required Action |
|---|---|
| **Empty or missing bibliography** | Do NOT fabricate citations. Mark `status: "partial"`, insert `[CITATION_NEEDED: <topic>]` placeholders, list required literature slots in `uncertainty` and `review_checklist.md`. |
| **Citation mentioned in draft but absent from input files** | Flag as `unverified_citation` in `uncertainty`; do not invent DOI/year/venue; mark `[UNVERIFIED_REF]` for author confirmation. |
| **Author results/tables incomplete** | Keep contribution claims strictly within available facts; tag missing quantitative support with `[NEEDS_AUTHOR_CONFIRMATION]` and list exact needed metrics. |
| **Conflicting findings across cited papers** | Treat as a **first-class basis for the Research Gap** rather than smoothing it over; cite both sides with their differing experimental conditions. |
| **Unknown target journal/citation format** | Default to standard academic markdown + explicit author-year or `[ref_id]` keys, record the assumption in `uncertainty`, and keep structure trivially convertible to LaTeX/BibTeX. |
| **Bilingual / language switch requested** | Follow `/input/task.md` language specification strictly; preserve canonical technical terms and original author/venue metadata without mistranslation. |

---

## 5. Bundled Resources & Verification

- `scripts/inspect_materials.py` — Stdlib-only Python 3.8+ input & bibliography indexer.
- `scripts/audit_and_compile_intro.py` — Stdlib-only 7-gate auditor and multi-artifact compiler.
- `scripts/self_test.py` — End-to-end positive + negative synthetic verification suite.
- `resources/cliche_and_overclaim_lexicon.json` — Bilingual cliché, sequential-dump, and overclaim patterns.
- `resources/comparison_taxonomy.json` — Reusable grouping axes, comparison dimensions, and citation style specs.
- `templates/writing_plan_schema.json` — Schema for `/app/work/writing_plan.json`.
- `templates/result_template.json` — Reference Harbor `result.json` output structure.
- `examples/minimal_example.md` — Synthetic end-to-end invocation & output example.
