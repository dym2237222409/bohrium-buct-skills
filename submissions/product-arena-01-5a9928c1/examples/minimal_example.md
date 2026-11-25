# 最小调用示例（Minimal Reproducible Example）

> **重要声明（示例标注）**：本文件展示的输入文本、术语表、命令行调用与双语译文输出均为**合成最小示例（Synthetic Example）**，仅用于演示 `scipdf-faithful-translator` Skill 的完整调用流程与数据结构，**不代表、也不冒充任何平台正式评测案例的结果**。

---

## 1. 一键运行端到端合成自检示例

无需任何外部私有数据，直接执行 `scripts/self_test.py`。脚本会自动合成一篇跨页双栏英文科研短文 PDF（包含标题、摘要、引言、实验表格与题注、带编号公式 `[Eq. 1]`、文献引用 `[1, 2]`、否定与限定句式以及故意设置的易混淆多义学术词 `substrate` / `solution` / `significant`），并完整验证分段、术语表锁定、术语漂移拦截与双语对齐导出：

```bash
python /skills/weekly-skill/scripts/self_test.py --work-dir /tmp/trans_demo
```

---

## 2. Harbor 评测环境标准四步调用

```bash
# 步骤 1：探查 /input/case.json 与原文材料，生成初始诚实检查点 /app/output/result.json
python /skills/weekly-skill/scripts/inspect_case.py \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output

# 步骤 2：版面感知分段（跨栏/跨页接续，保留段号 S0001..SNNNN 与句子边界）并抽取候选术语与受保护符号
python /skills/weekly-skill/scripts/segment_and_extract_terms.py \
  --source /app/work/source_input.pdf \
  --out-dir /app/work/segmented

# 步骤 3：融合题目给定术语表、内置学科种子词表与全文高频术语，生成强制锁定的 locked_glossary.json
python /skills/weekly-skill/scripts/build_and_lock_glossary.py \
  --segments-json /app/work/segmented/source_segments.json \
  --candidates-json /app/work/segmented/term_candidates.json \
  --out-glossary /app/work/locked_glossary.json

# 步骤 4：（Agent 按 translation_batches.json 与 locked_glossary.json 完成逐段翻译并写入 translated_segments.json 后）
# 执行程序化术语一致性强制核验、数字/单位/公式/引用/否定与限定关系自检，并生成双语对照交付物与审计报告
python /skills/weekly-skill/scripts/verify_and_export_bilingual.py \
  --segments-json /app/work/segmented/source_segments.json \
  --translations-json /app/work/translated_segments.json \
  --glossary-json /app/work/locked_glossary.json \
  --case-json /input/case.json \
  --output-dir /app/output
```

---

## 3. 示意输出片段（【示例数据 / EXAMPLE ONLY】）

### 3.1 双语段落级对照 Markdown 示意（`bilingual_translation.md` 节选，示例）

```markdown
### [S0004] 1 Introduction (Page 1, Col 1, Block `p001_b005`, BBox `[54.0, 202.0, 286.0, 248.0]`)

- **原文 (Source)**:
  > Microchannel reactors improve catalyst selectivity by 16.4% only when the residence time does not exceed 2.5 s [1, 2].
- **译文 (Target)**:
  > 仅当停留时间不超过 2.5 s 时，微通道反应器才能将催化剂选择性提高 16.4% [1, 2]。
- **译法与核验依据 (Rationale & Checks)**:
  - **锁定术语命中**: `microchannel reactor` → `微通道反应器` (`T001`)；`catalyst selectivity` → `催化剂选择性` (`T002`)；`residence time` → `停留时间` (`T003`)。
  - **逻辑与数值保真**: 保留限定条件 `only when`（`仅当…时`）、否定上界 `does not exceed 2.5 s`（`不超过 2.5 s`）、数值 `16.4%` 及引用标记 `[1, 2]`。
```

### 3.2 译文自检与人工复核清单示意（`translation_qa_report.md` 节选，示例）

```json
{
  "_example_only": true,
  "segment_completeness": "8/8 (100.0%)",
  "glossary_consistency": "12/12 occurrences matched locked target_term (100.0%)",
  "number_and_unit_parity": "9/9 preserved (100.0%)",
  "citation_and_formula_parity": "4/4 preserved (100.0%)",
  "negation_and_condition_checks": "PASS (0 polarity flips detected)",
  "human_confirmation_checklist": [
    {
      "seg_id": "S0006",
      "page": 1,
      "span": "substrate",
      "chosen_translation": "反应底物",
      "reason": "[EXAMPLE] Polysemous term ('反应底物' vs '催化剂基底'); disambiguated from kinetic rate equation context, flagged for domain confirmation."
    }
  ]
}
```
