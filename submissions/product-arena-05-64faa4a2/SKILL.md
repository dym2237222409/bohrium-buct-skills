---
name: scipdf-methods-experiments-writer
description: Fact-grounded Methods and Experiments section writing and verification skill. Extracts hyperparameters, dataset versions/splits, hardware/software environments, random seeds, CLI commands, and baseline controls directly from configs, code, and logs; detects cross-file conflicts; aligns prose with parameter tables; enforces zero fabricated values; and emits structured reproducibility checklists and author-confirmation queries for missing details.
---

# `scipdf-methods-experiments-writer` — Config/Log-Grounded Methods & Experiments Skill

## 1. Mission & Triggers

Invoke this skill whenever the user or evaluation harness asks to:
- Draft, complete, align, or audit a paper's **Methods** and/or **Experiments** section (`写方法与实验章节`, `整理实验设置`, `对齐配置与参数表`, `补全复现清单`, `核对数据集与对照实验`).
- Synthesize scattered research materials—YAML/JSON/INI/TOML configs, Python/Shell scripts, training/evaluation logs, CSV result tables, and lab notes—into an executable, fully reproducible scientific narrative with line-level provenance.

### Four Core Quality Pillars
1. **写得全 (Six-Element Completeness)**: Cover all six mandatory dimensions: (a) ordered executable method steps (input -> operation -> output -> stopping/decision rule), (b) train/val/test experimental setup (hardware, software versions, seeds, repeat count), (c) parameter table with units, targets, and `file:line` provenance, (d) dataset version/size/preprocessing/split strategy & subset roles, (e) controlled baseline comparisons (version, same-budget check, metrics, statistical tests), and (f) reproducibility checklist & concrete failure-mode limitations.
2. **写得准 (Config/Log/Code Exact Fidelity)**: Every hyperparameter, dataset split, repeat count, hardware spec, and metric must match actual files in `/input/`—ZERO numbers invented for prose smoothness. Cross-file value conflicts (e.g., config vs. actual run log) must be explicitly surfaced and reconciled or escalated.
3. **做得着 (Executable Reproducibility)**: Provide concrete step-by-step execution logic, environment versions, seeds, and runnable command templates so another researcher can reproduce the experiment.
4. **说得住 (Explicit Gaps & Concrete Limitations)**: Explicitly mark every missing detail as `[MISSING_FROM_MATERIALS]` / `[NEEDS_AUTHOR_CONFIRMATION]` in a dedicated author-question list; state limitations in terms of concrete failure regimes and applicable boundaries rather than generic boilerplate.

---

## 2. Harbor Runtime & I/O Contract

When executed in an automated evaluation container (installed at `/skills/weekly-skill`):
1. **Inspect actual files first**: Always read `/input/case.json`, `/input/task.md`, and every config/code/log/table file in `/input/`. Never infer hyperparameters or dataset versions from file/task labels alone.
2. **Work directory**: Store extracted facts and intermediate plans in `/app/work/`.
3. **Deliverables under `/app/output/`**:
   - `result.json`: MUST preserve `case_id` and **every original field** required by `output_contract`, plus honest `status` (`completed` | `partial` | `blocked`), `sources`, `uncertainty`, and `change_records`.
   - `methods_and_experiments.md` (and `methods_and_experiments.tex` when LaTeX is requested): Editable publication-ready Methods & Experiments manuscript.
   - `parameter_table.md`: Complete parameter-to-source alignment table (`name | value | unit | target | source_file:line | status`).
   - `reproducibility_and_questions.md`: Reproducibility checklist, cross-file conflict resolution log, and structured Author Confirmation Questions.
   - `fact_provenance.json`: Machine-readable mapping from every parameter, dataset field, baseline, and method step back to raw input files.
4. **Budget & honesty**: Within the 64-request / 900s budget, run the extractor immediately, generate an audited draft early, and iterate on any failing gate. Never fabricate missing seeds, GPU models, dataset versions, or statistical p-values.

---

## 3. End-to-End Execution Workflow

### Step 1 — Extract Line-Anchored Experimental Facts & Cross-File Conflicts
Resolve `SKILL_DIR` (`/skills/weekly-skill` inside Harbor, or local skill directory) and run `scripts/extract_experiment_facts.py`:
```bash
SKILL_DIR="/skills/weekly-skill"
[ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(pwd)"
python3 "${SKILL_DIR}/scripts/extract_experiment_facts.py" \
  --input-dir /input \
  --out /app/work/experiment_facts.json
```
Read `/input/case.json` (`output_contract`) and `/input/task.md` verbatim immediately and ensure every requested custom output filename and top-level `result.json` field is fulfilled. This deterministic script parses `.yaml`/`.yml`/`.json`/`.toml`/`.ini`/`.cfg`/`.py`/`.sh`/`.ipynb`/`.log`/`.txt`/`.csv`/`.tsv`/`.md`/`.tex`/`.docx`/`.pdf` files and exports both `parameter_table.md` and booktabs `parameter_table.tex`.
- `parameter_facts`: Every key/value pair with normalized canonical name, raw value, unit (if any), and exact `source_file:line`.
- `cross_file_conflicts`: Automatic detection when the same canonical parameter (e.g., `learning_rate`, `batch_size`, `epochs`, `seed`) holds differing values across files (e.g., config default vs. executed CLI/log).
- `environment_and_hardware`: Detected GPU/CPU, Python/framework versions, random seeds, repeat counts, and CLI commands.
- `dataset_and_split_hints`: Detected dataset names, versions, sample counts, split ratios, and preprocessing clues.
- `missing_element_hints`: Early detection of which of the 6 elements are absent from `/input/`.

### Step 2 — Author Structured Methods Plan (`/app/work/methods_plan.json`)
Follow `templates/methods_plan_schema.json` and `resources/six_elements_checklist.json` to populate:
1. `method_steps`: Ordered steps (`step_id`, `name`, `inputs`, `operation`, `outputs`, `decision_or_stop_rule`, `source_locator`).
2. `experimental_setup`: Separate `training`, `validation`, `testing` procedures, plus `hardware`, `software_versions`, `random_seeds`, `repeat_count`, and `source_locators`.
3. `parameter_table`: Every parameter with `name`, `value`, `unit`, `target_component`, `source_locator`, and `verification_status` (`verified` | `conflict_resolved` | `needs_author_confirmation`).
4. `datasets`: Each dataset's `name`, `version`, `size`, `preprocessing`, `split_strategy`, `subset_roles`, `source_locator`.
5. `baselines`: Each baseline's `name`, `version`, `same_budget_verified`, `metrics`, `statistical_test`, `source_locator`.
6. `reproducibility_checklist`: Code version/commit, environment spec, exact CLI commands, seeds, data access, plus explicit gaps.
7. `limitations`: Concrete `failure_regime`, `trigger_condition`, `applicable_scope`, and `source_basis`.
8. `conflict_resolutions` & `author_questions`: Explicit resolution for every conflict found in `experiment_facts.json` and concrete questions for every missing element.
9. `prose_sections`: Publication-grade prose (`method_steps`, `experimental_setup`, `datasets_and_splits`, `baselines_and_evaluation`, `reproducibility_and_limitations`) aligned verbatim with the parameter table.

### Step 3 — Run 7-Gate Audit & Compile Final Deliverables
```bash
python3 /skills/weekly-skill/scripts/audit_and_compile_methods.py \
  --facts /app/work/experiment_facts.json \
  --plan /app/work/methods_plan.json \
  --output-dir /app/output
```
The auditor verifies **7 deterministic gates**:
1. **Six-Element Coverage Gate**: All 6 categories covered or explicitly documented in `author_questions`.
2. **Parameter ↔ Source Grounding Gate**: Every `verified` parameter value exists in `experiment_facts.json` (catching fabricated numbers!).
3. **Prose ↔ Table Numeric Consistency Gate**: Parameter values written in prose match `parameter_table` without silent drift.
4. **Cross-File Conflict Disclosure Gate**: Every cross-file conflict discovered in `experiment_facts.json` is explicitly addressed in `conflict_resolutions` or `author_questions`.
5. **Train/Val/Test Isolation & Control Fairness Gate**: Train/val/test descriptions are distinct and baselines state budget/metric/statistical comparison or flag missing controls.
6. **Step Executability & Reproducibility Gate**: Every method step specifies inputs, operation, and outputs; reproducibility checklist covers version/environment/seed/command (or marks explicit missing items).
7. **Concrete Limitation Lint**: Rejects generic boilerplate limitations lacking concrete failure regimes or boundary conditions.

### Step 4 — Iterative Repair Loop
If any error-level gate fails, inspect `/app/output/reproducibility_and_questions.md`, correct `/app/work/methods_plan.json` (never fabricating values—use `needs_author_confirmation` for genuinely absent facts), and re-run Step 3 until clean.

---

## 4. Edge Cases & Conflict Precedence Rules

- **Config vs. Runtime Log Conflict**: Runtime logs/executed CLI commands generally reflect what actually ran, whereas static config files may hold defaults. Always report **both** values with exact `file:line`, explain which value is adopted (or mark pending author confirmation if ambiguous), and never silently drop the conflicting file.
- **Missing Seed / Hardware / Dataset Version**: Write `[MISSING_FROM_MATERIALS: <field>]` in the checklist and add an actionable question in `author_questions`.
- **Unequal Baseline Budget**: Explicitly flag `same_budget_verified: false` and qualify any comparison claim as non-equivalent budget.
- **No Code or Config Provided (Notes Only)**: Extract facts from prose/tables, downgrade provenance cleanly to `author_notes`, and note missing executable commands in `uncertainty`.

---

## 5. Bundled Resources & Verification

- `scripts/extract_experiment_facts.py` — Stdlib multi-format config/code/log/table fact & conflict extractor.
- `scripts/audit_and_compile_methods.py` — Stdlib 7-gate auditor and multi-artifact compiler.
- `scripts/self_test.py` — End-to-end positive + negative synthetic verification suite.
- `resources/six_elements_checklist.json` — Six-element requirements and boilerplate limitation patterns.
- `resources/config_extraction_patterns.json` — Canonical hyperparameter, hardware, seed, and split extraction rules.
- `templates/methods_plan_schema.json` — Schema for `/app/work/methods_plan.json`.
- `templates/result_template.json` — Harbor `result.json` output structure.
- `examples/minimal_example.md` — Synthetic end-to-end example.
