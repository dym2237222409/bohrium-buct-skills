---
name: scipdf-nanopub-extractor
description: Atomic scientific assertion decomposition, minimal sufficient evidence binding, 4-graph Nanopublication (Head/Assertion/Provenance/Pubinfo) generation, 6-gate mechanical quality auditing, and W3C TriG/JSON-LD/N-Quads/JSONL serialization.
---

# Scientific Nanopublication Extractor & Validator (`scipdf-nanopub-extractor`)

## 1. Purpose & Trigger Conditions

Use this Skill whenever a task requires **converting scientific literature claims into atomic, falsifiable, machine-readable Nanopublications** with verifiable evidence snippets, provenance chains, controlled relation IRIs, and W3C RDF serializations (`TriG`, `JSON-LD`, `N-Quads`, `JSONL`).

This Skill enforces the four Nanopublication quality pillars mechanically:
1. **拆得细 (Atomic Falsifiable Decomposition)**: Breaks multi-clause findings, causal chains, and contrasts into single-sentence, individually falsifiable assertions with explicit `conditions_and_scope` and `negated` flags—mechanically rejecting multi-sentence summary dumps.
2. **对得上 (Minimal Sufficient Evidence Binding)**: Binds every assertion to the minimal verbatim source quote and true location (`jats_paragraph_id` for JATS/JSON corpora—never inventing PDF page 1—or `pdf_page_bbox` for actual PDFs), explicitly distinguishing `direct_experimental` / `direct_computational` / `direct_tabular` from `indirect_inference` and `cited_prior_work`.
3. **追得回 (Complete 4-Graph Provenance & Trusty Content Hash)**: Generates distinct `Head`, `Assertion`, `Provenance`, and `Pubinfo` graphs with deterministic SHA-256 Trusty-style identifiers (`urn:nanopub:RA...`), paper DOI/authors/year/hash, and conflict/duplicate tracking.
4. **读得懂 (Standards-Compliant Machine Serializations)**: Outputs parseable W3C `nanopublications.trig`, `nanopublications.jsonld`, `nanopublications.nq`, `nanopublications.jsonl`, `nanopub_audit_report.md`, and contract-compliant `/app/output/result.json`.

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
- `scripts/inspect_case.py`: Case/material discovery + early honest partial `/app/output/result.json`.
- `scripts/index_source_passages.py`: Universal indexer for JATS/JSON corpora (`corpus-manifest.json`, `primary-material.json`, `secondary-material.json`, `*.xml`), PDFs, and Markdown/text.
- `scripts/build_and_validate_nanopubs.py`: 6-gate mechanical validator + deduplicator + conflict detector + TriG/JSON-LD/N-Quads/JSONL serializer.
- `scripts/self_test.py`: Positive & negative quality-gate self-test.
- `resources/controlled_relations.json`: 17 controlled scientific relations mapped to OBO RO/SIO/SEPIO IRIs + 6 `evidence_types`.
- `resources/field_dictionary.json`, `templates/assertions_draft_schema.json`, `templates/result_template.json`, `examples/minimal_example.md`, and `examples/sample_20_assertions.json` (20 reusable synthetic examples).

---

## 3. Standard Execution Workflow (Harbor `/input` → `/app/work` → `/app/output`)

### Step 1 — Inspect Case Inputs & Save Early Honest Checkpoint

```bash
python "${SKILL_DIR}/scripts/inspect_case.py" \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output
```

Read `/app/work/case_manifest.json`, `/input/case.json`, and `/input/task.md` to confirm `case_id`, all `output_contract` fields, source files, and target extraction scope.

### Step 2 — Index Source Passages (Preserving True Location Type)

```bash
python "${SKILL_DIR}/scripts/index_source_passages.py" \
  --input-dir /input \
  --out-dir /app/work/indexed
```

Read `/app/work/indexed/indexed_passages.json`. Note whether each source uses `jats_paragraph_id` (`page: null`, do NOT fabricate PDF page numbers) or `pdf_page_bbox` (`page` + `bbox`), and record each paper's own independent metadata (`source_id`, `doi`, `title`, `authors`, `year`).

### Step 3 — Decompose Claims into Comprehensive Atomic Assertions (`/app/work/assertions_draft.json`)

**CRITICAL — Satisfy `output_contract` & Aim for High Recall + Precision**:
- First inspect `/input/case.json`'s `output_contract` and `/input/task.md` verbatim. Ensure every requested output file path and top-level `result.json` key is satisfied.
- Do **not** stop at 3–5 toy assertions when the paper contains 15–30 distinct empirical/computational/limitation findings! Cover every major Results, Quantitative Comparison, Mechanism, Control/Null Result, and Limitation claim across **all** provided papers while keeping each assertion strictly atomic and falsifiable.

Following `templates/assertions_draft_schema.json` and `resources/controlled_relations.json`:
1. Split compound conclusions (e.g., *"X increases A by 10% while decreasing B by 5%"*) into **two separate atomic assertions** (`NP001`, `NP002`).
2. Select `relation` strictly from `resources/controlled_relations.json` (17 IRIs) and `evidence_type` strictly from the 6 allowed categories (never mark cited background literature as `direct_experimental`).
3. Keep `claim_text` to **1 concise falsifiable sentence** (≤ 280 chars) and keep `minimal_evidence_quote` to the **exact verbatim substring** (18–480 chars) from `indexed_passages.json` that minimally suffices to verify the claim (ellipses `...` are allowed to trim irrelevant parenthetical clauses while preserving exact verbatim segments).
4. Include null/negative findings (`no_significant_effect_on`, `independent_of`, `negated: true`) and explicit boundary conditions (`conditions_and_scope`) whenever stated in the paper.
5. Put unsubstantiated or unreported candidate questions in `unreported_or_rejected_claims` rather than fabricating assertions.

### Step 4 — Run 6-Gate Quality Audit & Multi-Format Export

```bash
python "${SKILL_DIR}/scripts/build_and_validate_nanopubs.py" \
  --index-json /app/work/indexed/indexed_passages.json \
  --draft-json /app/work/assertions_draft.json \
  --case-json /input/case.json \
  --output-dir /app/output \
  --strict
```

Fix any mechanical gate error (`assertion_too_broad`, `evidence_not_in_source`, `relation_not_in_vocabulary`, `subject_object_confusion`, `provenance_missing_passage`) and re-run until `valid: true`. Confirm `/app/output/result.json` satisfies every original `output_contract` field in `/input/case.json`.

---

## 4. Exception & Edge-Case Handling

- **Scanned / Encrypted PDF or Unreachable Source**: Log in `uncertainty.unverifiable_items` and set `status: "partial"` or `"unverifiable"` without fabricating assertions.
- **Evidence Spanning Figure/Table**: Cite the caption/table passage ID; if numerical data only exists in unextracted image pixels, disclose explicitly rather than reading off guessed pixel values.
- **Conflicting Assertions Across Regimes or Papers**: Keep both assertions with their distinct `conditions_and_scope` and preserve the auto-detected entry in `uncertainty.material_conflicts`.

---

## 5. Self-Check

```bash
python "${SKILL_DIR}/scripts/self_test.py"
```
