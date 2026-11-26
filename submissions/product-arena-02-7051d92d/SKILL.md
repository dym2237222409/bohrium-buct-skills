---
name: scipdf-academic-polisher
description: >
  Evidence-grounded academic language polishing Skill for Chinese and English scientific manuscripts
  (journal articles, theses, grant proposals, rebuttals, abstracts; Markdown, plain text, LaTeX, DOCX).
  Performs minimal-edit sentence-level polishing with glossary-driven cross-section terminology consistency,
  automated 7-gate semantic/fact preservation (numbers, units, citations, cross-refs, LaTeX math,
  claim-strength/hedge conservation, causality vs correlation, negation scope), and auditable
  original-to-revised change logs. Do NOT trigger on pure Q&A, literature summarization, or tasks that
  do not request polishing, proofreading, or linguistic revision.
---

# Academic Language Polishing Skill (`scipdf-academic-polisher`)

## 1. Purpose, Triggering & Non-Triggering Boundary

Use this Skill **only** when the task asks to polish, proofread, copy-edit, tighten academic register, fix grammar/Chinglish, or unify terminology/abbreviations in an academic text or manuscript draft.
**Do NOT modify source text** when the user only asks factual questions, literature summaries, or conceptual explanations without requesting text editing.

This Skill enforces four non-negotiable academic polishing guarantees:
1. **改得对 (Accurate Correction)**: Fix genuine grammar, syntax, agreement, article/preposition, tense/voice, collocation, punctuation, and Chinglish/literal-translation errors rather than masking them with vague paraphrasing.
2. **说得像 (Register & Terminology Consistency)**: Match the target academic genre (`journal_article`, `thesis`, `grant_proposal`, `rebuttal`, `abstract`), eliminate colloquial/promotional/absolutist tone, enforce a unified glossary across all sections, and standardize first-mention abbreviations (`Full Term (ABBR)`).
3. **没改坏 (Zero Semantic/Fact Drift — Minimal Edit)**: Preserve **100%** of numerical values, uncertainties (`±`), units, p-values, citations (`[1]`, `(Author, 2020)`, `\cite{...}`), cross-references (`Fig. 1`, `Table 2`, `Eq. (3)`, `\ref{...}`), LaTeX math (`$...$`, `\[...\]`), negation scope, causality vs. correlation, and **claim/hedge strength** (`may/suggests/indicates` must NEVER be upgraded to `proves/demonstrates/causes/clearly`).
4. **讲得清 (Auditable Sentence-Level Traceability)**: Deliver BOTH the complete ready-to-use polished manuscript AND an itemized sentence-level revision table (`original`, `revised`, `edit_type`, `reason`, `risk_level`, `needs_human_confirmation`) backed by automated 7-gate verification.

---

## 2. Runtime Contract & Workspace Layout (Harbor / Local)

- **Skill root**: `/skills/weekly-skill` (or local `submissions/product-arena-02-7051d92d`).
- **Inputs**: `/input/case.json`, `/input/task.md`, and actual manuscript/glossary/style files referenced therein. **Always read the actual files**—never assume content from folder names or domain labels.
- **Working directory**: `/app/work` (intermediate sentence index, draft edits JSON, audit reports).
- **Final deliverables**: `/app/output/`:
  - `polished_manuscript.<ext>` (`.md` / `.tex` / `.txt` matching source; plus `.docx` when requested/applicable)
  - `revision_log.md` and `revision_log.jsonl` (sentence-level diff with type, reason, and gate status)
  - `glossary_and_abbreviations.json` and `glossary_and_abbreviations.md`
  - `qa_audit_report.json` and `qa_audit_report.md` (7-gate audit + human confirmation list)
  - `result.json` (must preserve `case_id` and **ALL** original `output_contract` fields from `/input/case.json`, plus honest `status`, `sources`, `uncertainty`, and `change_records`)

---

## 3. Standard Execution Pipeline (4 Steps)

### Step 1: Inspect Case, Extract Constraints & Segment Manuscript
Run `scripts/inspect_and_segment.py` against `/input/case.json` and `/input/task.md` (or `--input <file>`):

```bash
python /skills/weekly-skill/scripts/inspect_and_segment.py \
  --case /input/case.json \
  --task /input/task.md \
  --out /app/work/segmented_case.json
```

What this script produces deterministically:
- Detected target register (`journal_article` / `thesis` / `grant_proposal` / `rebuttal` / `abstract` or `unspecified_default_journal_article` flagged in `uncertainty`), target language (`en` / `zh` / `bilingual`), and `output_contract` fields.
- Stable sentence units (`S001`, `S002`, ...) with `section`, `paragraph_id`, `source_file`, and `line_range`.
- Pre-extracted **protected anchors** per sentence: numbers/units/stats, citations, cross-refs, LaTeX math/commands, URLs, and baseline `claim_strength_tier`, `causality_markers`, `negation_markers`.
- Parsed glossary entries + **automatic glossary conflict detection** (e.g., same source term mapped to conflicting target translations) and mixed-encoding/mixed-script/ambiguous-genre anomaly warnings.

### Step 2: Plan Glossary, Abbreviation Table & Sentence-Level Minimal Edits
Read `/app/work/segmented_case.json` plus `resources/style_profiles.json`, `resources/hedge_causality_lexicon.json`, and `resources/edit_taxonomy.json`.

Construct `/app/work/edits_draft.json` conforming to `templates/edits_draft_schema.json`:
1. **Glossary & Abbreviation Locking**:
   - Honor any user-supplied glossary first. If the supplied glossary has internal conflicts, **do NOT silently pick one**—flag the conflict in `glossary_conflicts` and `human_review_items`, choose the most standard domain form provisionally, and mark `needs_human_confirmation: true`.
   - Build a unified terminology table for all recurring technical terms and acronyms across sections.
   - Ensure every abbreviation is defined at its **first occurrence** in the manuscript (`Full Name (ABBR)`) and used consistently as `ABBR` thereafter (unless section-specific rules such as Abstract + Main Text require independent first-mention definitions—record that rule explicitly).
2. **Sentence-by-Sentence Minimal Editing**:
   - Process **every** sentence in every requested section in order (`S001`..`Snnn`). Unchanged sentences must still be recorded with `changed: false` so full coverage is provable.
   - Prefer minimal surgical edits over wholesale paragraph rewrites. Only restructure a sentence substantially when fixing severe Chinglish, broken syntax, or literal translation (`edit_type: "chinglish_restructure"` or `"syntax_clarity"`), and verify every propositional claim survives intact.
   - **Never** invent missing data, citations, experimental conditions, or stronger claims. If the source sentence contains an ambiguity, factual contradiction, broken reference, or missing unit, keep the literal meaning untouched or minimally clarify grammar while setting `needs_human_confirmation: true` with an explicit note.
   - For every modified sentence, provide exact `edit_types` (from `resources/edit_taxonomy.json`) and a concrete, third-party-verifiable `reason` (e.g., `"Fixed subject-verb disagreement ('data shows' -> 'data show' / plural noun agreement) and replaced colloquial 'a lot of' with 'substantial' while preserving exact p-value and tentative hedge 'suggest'."`).
3. **Save Partial Deliverables Early**:
   - For very long manuscripts (>150 sentences), process section-by-section, run the compiler after each major section, and merge—ensuring honest partial deliverables exist well within the 64-request / 900s execution budget.

### Step 3: Run Automated 7-Gate Audit & Compile All Deliverables
Run `scripts/audit_and_compile_polish.py`:

```bash
python /skills/weekly-skill/scripts/audit_and_compile_polish.py \
  --segmented /app/work/segmented_case.json \
  --edits /app/work/edits_draft.json \
  --resources /skills/weekly-skill/resources \
  --out-dir /app/output
```

The script automatically executes **7 hard verification gates** on every sentence:
1. **Gate 1 — Number / Unit / Stat Invariance**: Flags any dropped, altered, or newly invented number, percentage, tolerance (`±`), unit, or `p`-value.
2. **Gate 2 — Citation & Cross-Ref Integrity**: Flags any dropped, renumbered, or corrupted citation (`[1]`, `[1-3]`, `\cite{...}`, `(Author et al., 2023)`) or figure/table/equation reference (`Fig. 1`, `Table 2`, `Eq. (3)`, `\ref{...}`).
3. **Gate 3 — LaTeX / Math / Markup Preservation**: Flags broken `$...$`, `\(...\)`, `\[...\]`, `\begin{...}` blocks, or altered Markdown heading levels.
4. **Gate 4 — Claim-Strength, Hedge, Causality & Negation Conservation**: Uses `resources/hedge_causality_lexicon.json` to detect **claim inflation** (upgrading hedged/correlational statements into causal/definitive/absolutist claims), unauthorized claim weakening, or flipped negation polarity.
5. **Gate 5 — Glossary & Cross-Section Abbreviation Consistency**: Verifies canonical preferred terms are applied consistently across sections, flags deprecated variants, detects undefined abbreviations or redundant repeated definitions, and surfaces glossary conflicts.
6. **Gate 6 — Target Register & Anti-Promotional / Chinglish Lint**: Flags surviving promotional hyperbole, colloquialisms, or Chinglish clichés in the polished text.
7. **Gate 7 — Minimal-Edit Ratio, Coverage & Explainability Check**: Verifies 100% sentence coverage of requested sections, flags over-rewriting without justification, and ensures every changed sentence has valid `edit_types` and `reason`.

### Step 4: Inspect Gate Failures, Repair & Finalize
- Inspect `/app/output/qa_audit_report.json`.
- If ANY **hard failure** (`ERROR`) occurs in Gates 1–5 (dropped number, dropped citation, broken math, claim inflation, negation flip, glossary violation):
  - Fix the offending sentence in `/app/work/edits_draft.json` and re-run `audit_and_compile_polish.py` until **0 unresolved hard errors** remain (or explicitly document why an exception is intentional and mark `needs_human_confirmation: true`).
- If input was a `.docx` or Office file and LibreOffice/Pandoc is available, generate the updated editable `.docx` and render a real PDF/PNG preview; if unavailable, state the environment limit honestly in `uncertainty` without fabricating rendered previews.
- Verify `/app/output/result.json` contains `case_id`, all original `output_contract` fields, accurate `status` (`completed` or `partial`), `sources`, `uncertainty`, and `change_records`.

---

## 4. Handling Edge Cases & Anomalies

| Anomaly / Edge Case | Required Action |
| :--- | :--- |
| **Pure Q&A / non-editing prompt** | Do NOT polish or rewrite the text; answer the question directly and log `status: "not_triggered_non_polishing_request"`. |
| **Ambiguous or missing target register** | Default to `journal_article` (neutral, formal academic register), record assumption in `uncertainty`, and flag for human confirmation. |
| **Glossary internal conflict** | Keep both conflicting definitions in `glossary_conflicts`, apply the domain-standard form provisionally, mark affected sentences `needs_human_confirmation: true`. |
| **Mixed LaTeX / Markdown / CJK-EN-JP text** | Protect math/commands/code spans strictly; preserve original markup syntax; flag unexpected third-language tokens rather than silently deleting them. |
| **Garbled encoding / truncated input / missing source material** | Detect mojibake or missing files at Step 1; repair encoding only if lossless, otherwise leave corrupted spans intact and mark `needs_human_confirmation: true` with `status: "partial"`. |
| **Source factual error or internal contradiction** | Do NOT silently alter numbers or scientific claims during language polishing; preserve the original claim and add an explicit `human_review_items` warning. |

---

## 5. Self-Test & Offline Verification

Run the bundled zero-dependency end-to-end self-test (uses synthetic bilingual data only):

```bash
python /skills/weekly-skill/scripts/self_test.py
```

It validates both **negative gate rejection** (catches claim inflation, dropped numbers/citations, negation flip, glossary drift, and glossary conflicts) and **positive end-to-end polishing compilation** (segments manuscript, verifies all 7 gates pass, and emits valid `polished_manuscript.md`, `revision_log.md`, `glossary_and_abbreviations.json`, `qa_audit_report.json`, and `result.json`).
