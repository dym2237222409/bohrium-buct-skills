---
name: scipdf-kg-builder
description: Multi-paper scientific Literature Knowledge Graph builder with independent per-paper provenance, anti-false-merge entity alignment (concept vs physical sample/instance separation), ID-preserving incremental updates, executable subgraph queries, and GraphML/Cypher/JSON-LD/SVG visual exports.
---

# Scientific Literature Knowledge Graph Builder (`scipdf-kg-builder`)

## 1. Purpose & Trigger Conditions

Use this Skill whenever a task requires **building, incrementally updating, aligning, auditing, or querying a Literature Knowledge Graph across one or multiple scientific papers** (supporting JATS/JSON corpora such as `corpus-manifest.json` + `primary-material.json` + `secondary-material.json` + `*.xml`, as well as PDFs).

This Skill mechanically enforces the four KG integrity pillars:
1. **抽得准 (Strict Grounded Extraction)**: Every node and directed edge must cite an actual `(source_id, paragraph_id)` and verbatim source substring verified against `corpus_index.json`—never hallucinating unmentioned concepts or fabricating PDF page numbers (`page=1`) for JATS corpora.
2. **连得对 (Typed, Condition-Scoped & Attribution-Tagged Relations)**: Distinguishes `author_direct` from `cited_prior_work`, tags `evidence_strength`, binds `conditions` and `quantitative_detail` to every edge, and detects opposing/conflicting relations across papers or regimes without collapsing them into one conclusion.
3. **对得齐 (Anti-False-Merge Cross-Paper Alignment & ID-Preserving Incremental Updates)**:
   - Strictly isolates each paper's own DOI/title/authors/hash (never copying Paper 1's root metadata onto Paper 2).
   - Verifies true cross-paper corroboration requires distinct `source_id` **and** distinct content hashes (rejecting same-paper JSON+Markdown duplicates).
   - Merges shared conceptual terms (`Concept_Term`, `is_paper_specific_instance=false`) while keeping paper-specific physical samples, model checkpoints, datasets, and numeric metrics (`is_paper_specific_instance=true`) **strictly separate**, linked via `INSTANCE_OF`, and documents every merged and non-merged decision in `entity_alignment_log`.
   - Supports `--merge-into` incremental updates preserving all existing node IDs (`E0001..`).
4. **查得到、回得去 (Executable Queries & Multi-Format Visual Exports)**: Runs >= 3 real queries (neighborhood subgraph, cross-paper comparison, conflict/condition audit, shortest path / honest `unverifiable_not_connected` check) and exports `knowledge_graph.json`, `JSON-LD`, `GraphML`, `Cypher`, `Mermaid`, `kg_preview.svg` (+ `kg_preview.png` when `matplotlib` is present), and `/app/output/result.json`.

---

## 2. Skill Path Resolution & Bundled Resources

```bash
if [ -d "/skills/weekly-skill/scripts" ]; then
  SKILL_DIR="/skills/weekly-skill"
else
  SKILL_DIR="$(pwd)"
fi
```

- `scripts/inspect_and_index_corpus.py`: Reads `corpus-manifest.json`, `primary/secondary-material.json`, JATS XML, and PDFs; isolates independent per-paper metadata and saves an honest early `/app/output/result.json`.
- `scripts/build_and_update_kg.py`: Validates grounding, runs anti-false-merge alignment and ID-preserving incremental updates, detects conflicts, and exports JSON/JSON-LD/GraphML/Cypher/Mermaid/SVG(+PNG).
- `scripts/run_kg_queries.py`: Executes >= 3 real queries with result subgraphs + verbatim provenance and finalizes `/app/output/result.json`.
- `scripts/self_test.py`: 2-paper JATS corpus end-to-end self-test.
- `resources/kg_schema.json`, `resources/alignment_policy.json`, `templates/kg_draft_schema.json`, `templates/query_templates.json`, `templates/result_template.json`, `examples/minimal_example.md`.

---

## 3. Standard Execution Workflow (Harbor `/input` → `/app/work` → `/app/output`)

### Step 1 — Inspect Corpus Manifest & Index All Independent Papers

```bash
python "${SKILL_DIR}/scripts/inspect_and_index_corpus.py" \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output
```

Read `/app/work/corpus_index.json`, `/input/case.json`, and `/input/task.md`:
- Verify every independent paper's `source_id`, `doi`, `title`, `authors`, `license`, and `location_type` (`jats_paragraph_id` vs `pdf_page_bbox`).
- **CRITICAL**: Never apply root `case.json` DOI/title/authors to a secondary paper, and never invent `page: 1` when `location_type == "jats_paragraph_id"`. If `secondary-publisher.xml` is provided for inline formula/subscript cross-checking, inspect it directly when reading formula-heavy passages.

### Step 2 — Extract Paper 1 KG & Incrementally Merge Paper 2+

Author `/app/work/kg_primary_draft.json` (and `/app/work/kg_secondary_draft.json` for Paper 2, following `templates/kg_draft_schema.json` and `resources/alignment_policy.json`):
- Keep general concepts (`Concept_Term`, `is_paper_specific_instance: false`) cleanly separated from paper-specific physical samples, experimental runs, model checkpoints, or datasets (`is_paper_specific_instance: true`), connecting instances to concepts via `INSTANCE_OF`.
- Explicitly document BOTH merged concepts AND **do-not-merge** decisions (distinct conditions, samples, homonyms, conflicting outcomes) in `alignment_decisions`.

Build Paper 1 first, then incrementally merge Paper 2 while preserving Paper-1 node IDs:

```bash
python "${SKILL_DIR}/scripts/build_and_update_kg.py" \
  --corpus-index /app/work/corpus_index.json \
  --draft-json /app/work/kg_primary_draft.json \
  --output-dir /app/work/kg_v1 \
  --strict

python "${SKILL_DIR}/scripts/build_and_update_kg.py" \
  --corpus-index /app/work/corpus_index.json \
  --draft-json /app/work/kg_secondary_draft.json \
  --merge-into /app/work/kg_v1/knowledge_graph.json \
  --output-dir /app/output \
  --strict
```

(If the case provides only 1 paper, run `build_and_update_kg.py` directly into `/app/output`.)

### Step 3 — Execute >= 3 Real Graph Queries & Finalize Contract Output

Write `/app/work/queries.json` (based on `templates/query_templates.json`, tailored to the actual entities/questions in the corpus, including at least 3 substantive queries such as neighborhood lookup, cross-paper comparison, conflict/condition audit, and an unsupported/disconnected relation boundary check):

```bash
python "${SKILL_DIR}/scripts/run_kg_queries.py" \
  --kg-json /app/output/knowledge_graph.json \
  --queries-json /app/work/queries.json \
  --case-json /input/case.json \
  --output-dir /app/output
```

Inspect `/app/output/kg_queries_report.md`, `/app/output/kg_audit_report.md`, and `/app/output/result.json` to ensure every original `output_contract` field in `/input/case.json` is populated honestly.

---

## 4. Exception & Boundary Handling

- **Missing Pixels / Supplementary Files / Raw Clinical Data**: Never infer numbers from absent figures or attachments; log honestly in `uncertainty.unreported_in_source`.
- **Encrypted / Unparseable PDF**: Disclose in `encrypted_pdfs` and `uncertainty.human_takeover_locations`.
- **Disconnected or Unreported Query Target**: Return `status: "unverifiable_not_connected"` or `"not_found_in_corpus"` instead of fabricating a relationship.

---

## 5. Self-Check

```bash
python "${SKILL_DIR}/scripts/self_test.py"
```
