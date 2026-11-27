---
name: scipdf-citation-manager
description: >
  End-to-end academic reference and citation management Skill for scientific manuscripts
  (Markdown, plain text, LaTeX, DOCX) and multi-format bibliography libraries (BibTeX, RIS,
  EndNote .enw, CNKI exports, CSL-JSON, plain-text lists). Performs claim-level citation placement,
  explainable multi-tier deduplication (preprint vs published, bilingual titles, cross-database
  exports with anti-false-merge guards), deterministic in-text and bibliography synchronized
  renumbering (GB/T 7714-2015 numeric & author-year, APA 7th, IEEE, Vancouver), format conversion,
  and 6-gate source verification & claim-to-reference alignment auditing.
---

# Academic Reference & Citation Management Skill (`scipdf-citation-manager`)

## 1. Purpose, Core Guarantees & Triggering Boundary

Use this Skill whenever the task requires inserting/relocating citations, synchronizing/renumbering in-text references and bibliography lists after edits, deduplicating/merging multi-source reference libraries, converting reference formats (BibTeX / RIS / EndNote / CNKI ↔ GB/T 7714—2015 / APA 7th / IEEE / Vancouver), or verifying citation authenticity and claim-to-reference alignment.

This Skill enforces four non-negotiable guarantees:
1. **挂得对 (Claim-Level Placement)**: Place citations directly at the specific claim clause or sentence requiring attribution—never blindly dump all citations at paragraph end or attach them to figure/table captions unless specifically citing the figure source. Support multi-citation compression (`[1-3]` / `[1–3]`), multi-sentence shared citations, sequential numeric, author-year, and footnote formats.
2. **编得顺 (Synchronized Renumbering & Cross-References)**: After insertion, deletion, reordering, or section merging, deterministically renumber every in-text marker (main body, captions, footnotes, appendices) AND the final reference list in exact synchronized order—zero gaps, zero duplicate numbers, consistent bracket/superscript formatting.
3. **收得净 (Explainable Deduplication with Anti-False-Merge Guard)**: Merge duplicate records across BibTeX/RIS/EndNote/CNKI exports, bilingual titles, and preprint (`arXiv`/`bioRxiv`) vs. published versions with explicit merge rationale and field provenance—while **never** false-merging distinct works by the same author in the same year or entries with distinct valid DOIs.
4. **对得上 (Source Verification & Claim-to-Reference Honesty)**: Never fabricate DOIs, page numbers, authors, or bibliographic records. Ground every citation in the actual input library/files; flag orphan citations, uncited entries, missing required metadata fields, fabricated/unverifiable references, and claim-to-reference mismatches or insufficient metadata evidence transparently.

---

## 2. Runtime Contract & Output Layout (Harbor / Local)

- **Skill root**: `/skills/weekly-skill` (or local `submissions/product-arena-03-429cc14d`).
- **Inputs**: `/input/case.json`, `/input/task.md`, manuscript files (`.md`, `.txt`, `.tex`, `.docx`, `.json`), and bibliography files (`.bib`, `.ris`, `.enw`, `.txt`, `.json`, `.csv`). Always inspect the actual files first.
- **Working directory**: `/app/work` (normalized library, dedup report, citation edit plan).
- **Final deliverables**: `/app/output/`:
  - `manuscript_updated.<ext>` (editable manuscript with synchronized citations)
  - `references_formatted.md` (clean formatted reference list in the requested target style)
  - `references_clean.bib` and `references_clean.ris` (deduplicated, field-enriched portable libraries)
  - `dedup_and_merge_report.json` and `dedup_and_merge_report.md` (explainable merge decisions + `kept_separate_audit` + field conflicts)
  - `citation_audit_report.json` and `citation_audit_report.md` (6-gate audit, old→new renumbering map, claim-to-reference verification matrix, missing fields & anomalies)
  - `result.json` (preserves `case_id` and **ALL** original `output_contract` fields from `/input/case.json`, plus honest `status`, `sources`, `uncertainty`, and `change_records`)

---

## 3. Standard Execution Pipeline (4 Steps)

### Step 1: Parse Manuscript & Multi-Format Reference Libraries
Run `scripts/parse_bib_and_manuscript.py`:

```bash
python /skills/weekly-skill/scripts/parse_bib_and_manuscript.py \
  --case /input/case.json \
  --task /input/task.md \
  --out /app/work/parsed_case.json
```

Deterministically parses:
- BibTeX (`.bib`), RIS (`.ris`), EndNote Tagged (`.enw`), CNKI exports (`SrcDatabase`/`Title`/`Author`/`Year`), CSL-JSON/JSON, and plain-text numbered or author-year reference lists.
- Manuscript sentences (`S001`, `S002`, ...) with section labels and existing citation markers (`[1]`, `[1,2]`, `[1-3]`, `\cite{...}`, `(Author et al., 2020)`), detecting potential paragraph-end dumps, caption-attached citations, orphan markers, and missing metadata fields.

### Step 2: Run Explainable Multi-Tier Deduplication & Field Enrichment
Run `scripts/deduplicate_refs.py`:

```bash
python /skills/weekly-skill/scripts/deduplicate_refs.py \
  --parsed /app/work/parsed_case.json \
  --rules /skills/weekly-skill/resources/dedup_rules.json \
  --out /app/work/deduped_refs.json
```

Applies 4 deterministic tiers:
- **Tier 1 (Persistent ID)**: Exact normalized DOI or arXiv ID match.
- **Tier 2 (Preprint ↔ Published)**: High title similarity (`>= 0.90`) + author surname overlap (`>= 0.50`) + year difference `<= 2`; promotes the peer-reviewed published record to canonical while preserving preprint ID/URL in `merged_versions`.
- **Tier 3 (Cross-Database / Bilingual)**: High title similarity (`>= 0.92`) + author overlap + year diff `<= 1`, or matched bilingual title pair (`title_zh`/`title_en`) + matching authors/year/DOI.
- **Anti-False-Merge Guard**: Distinct valid DOIs or same-author-same-year papers with distinct titles (`similarity < 0.75`) are explicitly recorded under `kept_separate_audit` and **never** merged.
- **Field Provenance & Conflicts**: Enriches complementary missing fields (e.g., DOI from BibTeX, abstract from RIS, Chinese title from CNKI) without inventing data; any conflicting field (e.g., different years or page ranges) is logged in `field_conflicts` for human confirmation.

### Step 3: Build Claim-Level Citation Plan (`citation_plan.json` — Supports Zero-Copy Patch Mode!)
Read `/app/work/parsed_case.json`, `/app/work/deduped_refs.json`, `/input/case.json` (`output_contract`), and `/input/task.md`.
Write `/app/work/citation_plan.json` following `templates/citation_plan_schema.json`:
1. **Satisfy `output_contract` verbatim**: Check exact requested output filenames (e.g., `.tex`, `.bib`, `.docx`, `.md`) and top-level `result.json` fields before finalizing.
2. Select the target citation style (`gbt7714_numeric`, `gbt7714_author_year`, `apa7`, `ieee`, `vancouver`, `nature`, `acs`, `chicago_author_year`, `latex_bibtex`) from the task specification (default to `gbt7714_numeric` if unspecified and log in `uncertainty`).
3. **Zero-Copy Patch Mode (CRITICAL for long manuscripts)**: You only need to list sentences whose citations are being inserted, relocated, replaced, renumbered, or deleted in `sentences` (or `sentence_patches`) using canonical `{{CITE:REF001}}` / `{{CITE:REF001,REF002}}` placeholders. `sync_format_and_verify.py` automatically merges your patches onto the complete `parsed_case.json` manuscript backbone so **zero untouched paragraphs are ever dropped**, cutting token usage by >60%! (For `.tex` manuscripts, `manuscript_updated.tex` + `references_clean.bib` are emitted natively.)
4. Record a **claim-to-reference verification entry** for every cited `(sentence_id, ref_id)` pair:
   - `alignment_status`: `supported` | `partial_support` | `mismatch` | `insufficient_evidence_in_metadata` | `fabricated_or_unverified`
   - `evidence_basis`: Concrete comparison between the manuscript claim and the reference's title/abstract/full-text snippet in the provided inputs. **Never** claim `supported` if only a bare title exists and the quantitative claim cannot be verified—mark `insufficient_evidence_in_metadata` honestly!

### Step 4: Synchronize Numbering, Convert Formats & Run 6-Gate Audit
Run `scripts/sync_format_and_verify.py`:

```bash
python /skills/weekly-skill/scripts/sync_format_and_verify.py \
  --parsed /app/work/parsed_case.json \
  --deduped /app/work/deduped_refs.json \
  --plan /app/work/citation_plan.json \
  --styles /skills/weekly-skill/resources/citation_styles.json \
  --out-dir /app/output
```

The script deterministically executes:
- **Order-of-First-Appearance Numbering** (for `gbt7714_numeric`, `ieee`, `vancouver`) with range compression (`[1-3]` / `[1–3]`) and old-to-new renumbering map (`old_to_new_map`), OR **Alphabetical Author-Year Formatting** (for `apa7`, `gbt7714_author_year`) with automatic `2023a / 2023b` disambiguation.
- **Multi-Format Export**: Formatted Markdown reference list, clean deduplicated `.bib` and `.ris`.
- **6 Automated Quality Gates**:
  1. `Gate1_NoOrphanCitations`: Every in-text citation resolves to a real deduplicated record (flags unknown/fabricated IDs as `ERROR`).
  2. `Gate2_BibliographySync`: Exact 1:1 correspondence between cited set and bibliography list (`uncited_library_entries` cleanly separated).
  3. `Gate3_ContiguousNumbering`: Strict `1..N` continuity with zero gaps/duplicates (for numeric styles) and valid disambiguation letters (for author-year).
  4. `Gate4_MetadataCompleteness`: Flags missing required fields per target style (`author`, `title`, `year`, `venue`, `volume/pages`, `doi`) and malformed DOIs without fabricating them.
  5. `Gate5_ClaimAlignmentCoverage`: Verifies every cited `(sentence_id, ref_id)` has an explicit claim-alignment record and surfaces `mismatch` / `fabricated_or_unverified` / `insufficient_evidence_in_metadata` items into `human_review_items`.
  6. `Gate6_PlacementHygiene`: Flags unreviewed caption-attached citations or suspicious single-point paragraph dumps (`>= 5` refs lumped only at paragraph end).

If any hard `ERROR` appears in `/app/output/citation_audit_report.json` (e.g., typo in a `ref_id`), fix `citation_plan.json` and re-run Step 4.

---

## 4. Handling Edge Cases & Anomalies

| Anomaly / Edge Case | Required Action |
| :--- | :--- |
| **Fabricated / non-existent citation in draft** | Never invent metadata to rescue it; mark `fabricated_or_unverified`, retain an explicit `[UNVERIFIED_CITE]` flag in audit report, and add to `human_review_items`. |
| **Missing DOI / volume / issue / pages** | Leave field empty or omit per target style rules; log in `missing_fields` and `human_review_items`—never guess page numbers or DOIs. |
| **Same author + same year, different papers** | Never merge; preserve both records, log in `kept_separate_audit`, and apply `2023a / 2023b` in author-year styles. |
| **Preprint + peer-reviewed version** | Merge into canonical published version, keep preprint ID in `merged_versions` and `merge_rationale`, and check for title/year drift. |
| **Bilingual (ZH/EN) mixed reference list** | Apply Chinese punctuation / `等` for CJK entries and English punctuation / `et al.` for Latin entries under GB/T 7714—2015. |
| **Corrupted or unsupported file format** | Log parse warning honestly in `uncertainty` and continue with all readable files, emitting `status: "partial"`. |

---

## 5. Self-Test & Offline Verification

```bash
python /skills/weekly-skill/scripts/self_test.py
```

Validates multi-format parsing (BibTeX + RIS + CNKI), preprint↔published deduplication, same-author-same-year non-merge guard, citation insertion/deletion/reordering with contiguous renumbering & `[1-3]` compression, GB/T 7714—2015 + APA 7th + BibTeX + RIS export, orphan/fabrication rejection, and claim-mismatch reporting.
