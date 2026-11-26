# Minimal Synthetic Invocation Example (`_synthetic_example: true`)

> **Note**: All materials, bibliography entries, and outputs below are **synthetic demonstration data** created solely to illustrate the interface and workflow of `scipdf-intro-related-writer`. They are not formal test cases or real published papers.

## 1. Synthetic Input Directory (`/tmp/demo_input/`)

- `case.json`:
  ```json
  {
    "case_id": "synthetic-intro-001",
    "language": "zh",
    "citation_style": "pandoc-key",
    "output_contract": {
      "required_fields": ["case_id", "status", "deliverables", "sources", "uncertainty", "change_records"]
    }
  }
  ```
- `references.bib`: Contains 4 synthetic references (`zhang2022mem`, `li2023zeolite`, `wang2024hybrid`, `chen2025scale`) covering two technical routes (polymeric membranes vs. zeolite-filled mixed-matrix membranes).
- `author_notes.md`: Summarizes the author's synthetic method (`Dual-Channel Interface Crosslinking`, Table 2 selectivity `48.6`, permeance `112 GPU` at `35 °C`, Figure 4 200-hour stability test).

## 2. Commands

```bash
# Step 1: Index materials and bibliography
python3 scripts/inspect_materials.py \
  --input-dir /tmp/demo_input \
  --out /tmp/demo_work/material_manifest.json

# Step 2: Audit writing plan & compile deliverables
python3 scripts/audit_and_compile_intro.py \
  --manifest /tmp/demo_work/material_manifest.json \
  --plan /tmp/demo_work/writing_plan.json \
  --output-dir /tmp/demo_output
```

## 3. Illustrative Audited Output Excerpt (Synthetic)

### `introduction_and_related_work.md` (excerpt)
- **研究背景**：直接切入 CO2/N2 分离膜在高湿烟气工况下的“渗透率-选择性权衡（Robeson 上限）”与界面微缺陷老化约束，不含“近年来受到广泛关注”类空泛铺垫。
- **相关工作**：按“聚合物本体改性路线（`[@zhang2022mem]`, `[@chen2025scale]`）”与“分子筛杂化基质路线（`[@li2023zeolite]`, `[@wang2024hybrid]`）”两组横向比较渗透率、选择性、耐湿工况与界面缺陷局限。
- **研究空白**：由 `[@li2023zeolite]` 的高负载团聚与 `[@wang2024hybrid]` 在 70% RH 下长时衰减推出“高通量与高湿界面稳定性难以兼得”的明确空白，并说明本文双通道界面交联策略的针对性填补。
- **主要贡献**：逐条绑定 `Table 2`（选择性 `48.6`、渗透通量 `112 GPU`）与 `Figure 4`（200 h 稳定性），且显式限定适用温度 `35 °C`。

### `review_checklist.md` (audit gates summary)
- Gate 1 Structural Completeness: `PASS`
- Gate 2 Cliché & Empty-Opener Lint: `PASS`
- Gate 3 Thematic Grouping & Anti-Listing: `PASS` (`2` groups, comparative synthesis verified)
- Gate 4 Gap Derivation Chain: `PASS` (`1` gap grounded in `2` verified refs)
- Gate 5 Contribution ↔ Evidence Alignment: `PASS` (`2/2` aligned, numeric check passed)
- Gate 6 Citation Integrity & Coverage: `PASS` (`4/4` cited keys verified, `0` fabricated)
- Gate 7 Traceability & Uncertainty Honesty: `PASS`
