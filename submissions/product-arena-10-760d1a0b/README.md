# `scipdf-kg-builder` — 多篇文献防误合并知识图谱构建、增量更新与证据查询 Skill

本项目面向 **“阅读15｜文献知识图谱构建”** 赛题设计，全面遵循多篇独立文献修订约定，实现从双篇/多篇科研语料（原生支持 `corpus-manifest.json`、`primary-material.json`、`secondary-material.json`、JATS XML 及 PDF）构建**逐条回溯原文、严防跨篇错误合并、支持保号增量更新与真实子图查询**的可运行知识图谱工作流。

---

## 1. 四大核心问题与修订约束落实机制

| 维度 | 核心难点 | 本 Skill 的工程实现 |
|---|---|---|
| **抽得准** | 模型凭常识补造概念、混淆作者自述与转述、对 JATS 语料乱编 PDF 页码 | `inspect_and_index_corpus.py` 严格区分 `jats_paragraph_id`（`page=null`，绝不伪造 PDF 第 1 页）与 `pdf_page_bbox`；每篇独立绑定 `source_id`/`doi`/`title`/`authors`/`content_sha256`；校验器强制验证所有节点与边的原文短引真实存在 |
| **连得对** | 共现当因果、丢失实验条件与方向、把不同论文数值硬拼成一条结论 | `kg_schema.json` 定义 14 类有向受控关系、归因属性（`author_direct` vs `cited_prior_work`）与证据强度；每条边显式携带 `conditions` 与 `quantitative_detail`，自动检测跨篇或跨工况相反关系冲突 |
| **对得齐** | 同义缩写重复建点，或把不同论文的同名称样品/模型实例错误合并；同篇多格式当成跨篇 | `alignment_policy.json` 强制分离**共享通用概念（`Concept_Term`）**与**论文专属样品/模型/数据集实例（`is_paper_specific_instance=true`）**，通过 `INSTANCE_OF` 关联并在 `entity_alignment_log` 中同时记录“合并依据”与“不得合并依据”；校验跨篇证据必须满足独立 `source_id` 与独立文本哈希 |
| **查得到、回得去** | 图谱无法执行真实查询、更新时打乱旧节点 ID、缺少标准格式与可视化预览 | `--merge-into` 增量更新完整保留既有 `E0001..` 节点编号；`run_kg_queries.py` 执行 ≥3 项真实查询（邻域子图、跨篇对比、冲突审计、不可证实路径如实拒答）；同步导出 **JSON / JSON-LD / GraphML / Cypher / Mermaid / SVG（及可选 PNG）** |

---

## 2. 目录结构

```text
.
├── SKILL.md                               # Skill 主入口（含 YAML 元信息与三步构建/增量/查询流程）
├── README.md                              # 完整本体、对齐策略、查询说明与使用边界文档
├── GITHUB_URL.txt                         # 本作品公开 GitHub 子目录链接
├── resources/
│   ├── kg_schema.json                     # 9 类实体 + 14 类有向关系 + 归因与证据强度本体定义
│   └── alignment_policy.json              # 跨篇防错误合并与独立元数据隔离规范
├── templates/
│   ├── kg_draft_schema.json               # 单篇/增量图谱抽取草稿输入 Schema
│   ├── query_templates.json               # 4 类可复用图谱查询模板（含不可证实路径边界检查）
│   └── result_template.json               # 标准 /app/output/result.json 输出模板
├── examples/
│   └── minimal_example.md                 # 双篇 JATS 语料增量构建与查询示例（明确标明为合成示例）
└── scripts/
    ├── inspect_and_index_corpus.py        # 多篇独立语料清单与 JATS/PDF 索引器 + 初始诚实检查点
    ├── build_and_update_kg.py             # 原文短引校验、防误合并对齐、保号增量合并、冲突检测与多格式/SVG 导出
    ├── run_kg_queries.py                  # 可运行图谱子图查询引擎与契约结果汇总
    └── self_test.py                       # 双篇独立 JATS 论文端到端增量构建与 4 项查询自检脚本
```

---

## 3. 依赖与一键自检

- **必需环境**：Python `>= 3.9`（纯标准库即可运行全部构建、查询与 GraphML/Cypher/SVG 导出）。
- **可选增强**：`pymupdf`（PDF 语料解析）、`matplotlib`（同步渲染 `kg_preview.png` 位图预览）。

```bash
python scripts/self_test.py
```
