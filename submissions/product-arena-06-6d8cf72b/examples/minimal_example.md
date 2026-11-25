# 最小调用示例（Minimal Reproducible Example）

> **重要声明（示例标注）**：以下展示的输入文档、调用命令、论文卡片、章节摘要与知识点清单均为**合成最小示例（Synthetic Example）**，仅用于说明 `scipdf-structured-notes` Skill 的接口规范与锚点回链机制，**不代表、也不冒充任何平台正式评测案例结果**。

---

## 1. 一键运行内置合成双栏论文自检

无需任何外部私有 PDF，直接执行 `scripts/self_test.py`，即可动态生成两篇可并排比较的合成双栏科研文献 PDF，完整运行“版面解析与锚点注册 → 章节对齐笔记与前提绑定卡片编译 → 同义词/缩略语知识点归并 → 细粒度锚点与数值核验 → 跨文献检索与卡片对比矩阵导出”：

```bash
python /skills/weekly-skill/scripts/self_test.py --work-dir /tmp/notes_demo
```

---

## 2. Harbor 评测环境标准调用步骤

```bash
# 步骤 1：探查 /input/case.json、task.md 与源文献，初始化诚实检查点 /app/output/result.json
python /skills/weekly-skill/scripts/inspect_case.py \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output

# 步骤 2：执行双栏防串栏版面解析，生成章节骨架 section_skeleton.json 与细粒度锚点库 anchor_registry.json
python /skills/weekly-skill/scripts/parse_and_index_paper.py \
  --source /app/work/target_paper.pdf \
  --out-dir /app/work/indexed

# 步骤 3：（Agent 依据 section_skeleton.json 与 anchor_registry.json 撰写 structured_notes_draft.json 后）
# 执行锚点回链解引用校验、数值一致性核查、缩略语/同义知识点归并，并导出 Markdown/JSONL/CSV 成果
python /skills/weekly-skill/scripts/compile_and_verify_notes.py \
  --index-dir /app/work/indexed \
  --draft-json /app/work/structured_notes_draft.json \
  --case-json /input/case.json \
  --output-dir /app/output \
  --strict

# 步骤 4（可选复用）：按类型/关键词检索知识点，或将多篇论文卡片生成横向对比表
python /skills/weekly-skill/scripts/query_notes.py \
  --jsonl /app/output/knowledge_items.jsonl \
  --query "TOF"
```

---

## 3. 示意输出片段（【示例数据 / EXAMPLE ONLY】）

### 3.1 前提条件绑定结论（`condition_bound_findings` 示例）

```json
{
  "_example_only": true,
  "finding_id": "F01",
  "claim": "Micro-C reactor achieves 96.1% product selectivity with isothermal stability.",
  "quantitative_metrics": ["96.1%", "260 C"],
  "holding_preconditions": "Valid only at wall temperature 260 C, residence time <= 2.5 s, and laminar flow (12.5 mL/min); selectivity drops above 290 C due to side cracking.",
  "anchors": ["P0008", "TAB01"],
  "resolved_provenance": [
    {"anchor_id": "P0008", "page": 2, "section": "3 Results and Discussion", "bbox": [54.0, 178.0, 286.0, 214.0]},
    {"anchor_id": "TAB01", "page": 1, "section": "Table 1", "bbox": [326.0, 286.0, 552.0, 336.0]}
  ]
}
```

### 3.2 去重归并后的可检索知识点条目（`knowledge_items.jsonl` 单行示例）

```json
{"_example_only": true, "kid": "K002", "type": "variable_or_symbol", "canonical_name": "Turnover Frequency", "aliases": ["TOF", " catalytic turnover frequency"], "definition_or_statement": "Moles of product formed per active site per unit time (38.4 h^-1 for Micro-C).", "conditions_or_units": "h^-1 at 260 C, 1 atm", "verification_tier": "machine_verifiable", "anchors": ["P0007", "TAB01"]}
```
