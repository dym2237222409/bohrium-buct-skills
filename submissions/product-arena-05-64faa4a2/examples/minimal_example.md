# Minimal Synthetic Invocation Example (`_synthetic_example: true`)

> **Note**: All configs, logs, scripts, and outputs below are **synthetic demonstration data** created purely to illustrate `scipdf-methods-experiments-writer`. They do not represent real experiments or formal test cases.

## 1. Synthetic Input Directory (`/tmp/demo_methods_in/`)

- `case.json`: Declares `case_id: "synthetic-methods-001"`, `language: "zh"`.
- `config.yaml`: Sets default `lr: 1e-4`, `batch_size: 32`, `epochs: 50`, `optimizer: AdamW`, `seed: 42`.
- `run_train.log`: Actual execution log showing `NVIDIA A100 80GB`, `PyTorch 2.2.1`, `CUDA 12.1`, actual CLI `python train.py --lr 3e-4 --batch_size 32 --epochs 50 --seeds 42,43,44` (note the **cross-file conflict** on `learning_rate`: `1e-4` in `config.yaml:2` vs `3e-4` in `run_train.log:4`).
- `data_notes.md`: Describes `CatYield-10k` (`10,240` reactions, stratified 80/10/10 split), intentionally omitting the exact dataset version number.

## 2. Commands

```bash
python3 scripts/extract_experiment_facts.py \
  --input-dir /tmp/demo_methods_in \
  --out /tmp/demo_work/experiment_facts.json

python3 scripts/audit_and_compile_methods.py \
  --facts /tmp/demo_work/experiment_facts.json \
  --plan /tmp/demo_work/methods_plan.json \
  --output-dir /tmp/demo_output
```

## 3. Illustrative Audited Output Excerpt (Synthetic)

- **Cross-file conflict automatically caught**: `learning_rate` (`config.yaml:2 = 1e-4` vs `run_train.log:4 = 3e-4`) -> disclosed in `reproducibility_and_questions.md`, adopting runtime log value `3e-4` while flagging author confirmation.
- **Missing field explicitly flagged**: Dataset version marked `[MISSING_FROM_MATERIALS: dataset_version]` and added to `author_questions` instead of fabricating `v1.0`.
- **7-gate audit**: All 7 gates `PASS` (`parameter_grounding`, `prose_table_consistency`, `conflict_disclosure`, `train_val_test_isolation`, `executability`, `concrete_limitations`).
