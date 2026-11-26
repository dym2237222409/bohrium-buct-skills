# 最小调用示例（Minimal Reproducible Example）

> **重要声明（示例标注）**：以下展示的两篇合成论文语料、实体对齐决策、增量更新日志与 4 个图谱查询结果均为**合成最小示例（Synthetic Example）**，仅用于演示 `scipdf-kg-builder` Skill 的工作流与防错误合并机制，**不代表任何平台正式评测案例结果**。

---

## 1. 一键运行双篇语料端到端自检（含增量更新、防误合并与 4 项真实查询）

```bash
python /skills/weekly-skill/scripts/self_test.py --work-dir /tmp/kg_demo
```

---

## 2. Harbor 评测环境标准四步调用

```bash
# 步骤 1：读取 corpus-manifest.json、primary/secondary-material.json、JATS XML 或 PDF，
# 为每篇论文建立独立元数据与真实段落索引（JATS 段落 ID 不伪造 PDF 页码），写入初始诚实检查点 result.json
python /skills/weekly-skill/scripts/inspect_and_index_corpus.py \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output

# 步骤 2：先基于第一篇论文构建基础图谱，再增量合入第二篇论文（保留已有节点编号 E0001..，校验原文短引并执行防误合并审计）
python /skills/weekly-skill/scripts/build_and_update_kg.py \
  --corpus-index /app/work/corpus_index.json \
  --draft-json /app/work/kg_primary_draft.json \
  --output-dir /app/work/kg_v1 \
  --strict

python /skills/weekly-skill/scripts/build_and_update_kg.py \
  --corpus-index /app/work/corpus_index.json \
  --draft-json /app/work/kg_secondary_draft.json \
  --merge-into /app/work/kg_v1/knowledge_graph.json \
  --output-dir /app/output \
  --strict

# 步骤 3：执行 >= 3 个真实图谱查询（邻域子图、跨篇概念对比、冲突与条件审计、不可证实路径查询）并更新 /app/output/result.json
python /skills/weekly-skill/scripts/run_kg_queries.py \
  --kg-json /app/output/knowledge_graph.json \
  --queries-json /app/work/queries.json \
  --case-json /input/case.json \
  --output-dir /app/output
```

---

## 3. 防错误合并对齐记录示意（【示例数据 / EXAMPLE ONLY】）

```json
{
  "_example_only": true,
  "alignment_decisions": [
    {
      "candidate_names": ["HZSM-5 (primary)", "HZSM-5 (secondary)"],
      "decision": "kept_separate_do_not_merge",
      "shared_concept": "HZSM-5 zeolite",
      "rationale": "[EXAMPLE] Both papers share conceptual catalyst family 'HZSM-5 zeolite', but primary uses Si/Al=40 microchannel coating at 260 C while secondary uses Si/Al=80 packed pellets at 380 C; physical sample nodes and selectivity values remain strictly separated via INSTANCE_OF."
    }
  ]
}
```
