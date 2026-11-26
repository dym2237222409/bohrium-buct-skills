# `scipdf-intro-related-writer` — 引言与相关工作循证写作与核验 Skill

面向学术论文**引言（Introduction）与相关工作（Related Work）**起草、重构与审校任务的可复用 Agent Skill，聚焦四项核心指标：**立得住（背景去空话）**、**分得清（相关工作按维度分组比较）**、**说得实（研究空白由已引文献缺口推导）**、**对得上（贡献与正文证据一一对应、引用 100% 可回溯）**。

## 1. 目录结构

```text
product-arena-04-4b82ad21/
├── SKILL.md                                   # Agent 执行主规范（YAML frontmatter + 7 道门禁闭环）
├── README.md                                  # 本说明文档
├── GITHUB_URL.txt                             # 公开仓库地址声明
├── resources/
│   ├── cliche_and_overclaim_lexicon.json      # 中英文空泛开场套话、流水账特征与过度断言词表
│   └── comparison_taxonomy.json               # 相关工作分组维度、比较矩阵规范与引用格式配置
├── templates/
│   ├── writing_plan_schema.json               # 结构化写作计划（writing_plan.json）契约
│   └── result_template.json                   # Harbor 评测 result.json 输出模板
├── examples/
│   └── minimal_example.md                     # 显式标注的合成最小调用与输出示例
└── scripts/
    ├── inspect_materials.py                   # 多源材料与文献库索引器（纯标准库，可选 pypdf/fitz）
    ├── audit_and_compile_intro.py             # 7 道确定性核验门禁与多产物编译器
    └── self_test.py                           # 正向 + 负向端到端合成自检脚本
```

## 2. 环境依赖

- **必需依赖**：Python 3.8+（仅使用标准库 `json`, `re`, `pathlib`, `argparse`, `difflib`, `collections`, `unicodedata`）。
- **可选增强**：若输入含原始 PDF 且环境预装 `pypdf` / `pymupdf (fitz)` / `pdftotext`，`inspect_materials.py` 会自动调用抽取文本；未安装时诚实降级并在 `input_warnings` 中记录，不阻塞其余文本/BibTeX/JSON 材料解析。

## 3. 快速调用步骤

```bash
# 1. 索引输入材料、文献元数据与作者自身实验证据
python3 scripts/inspect_materials.py \
  --input-dir /input \
  --out /app/work/material_manifest.json

# 2. Agent 基于 material_manifest.json 生成结构化写作计划 /app/work/writing_plan.json
#    （遵循 templates/writing_plan_schema.json）

# 3. 执行 7 道确定性审计门禁并编译最终产物
python3 scripts/audit_and_compile_intro.py \
  --manifest /app/work/material_manifest.json \
  --plan /app/work/writing_plan.json \
  --output-dir /app/output
```

### 输出产物清单（`/app/output/`）

| 文件 | 用途 |
|---|---|
| `introduction_and_related_work.md` | 可直接并入论文的引言与相关工作成稿（可选同步导出 `.tex`） |
| `comparison_matrix.md` | 按路线分组的相关工作对比矩阵（方法、结论、适用条件、局限、来源） |
| `evidence_traceability.json` | 逐条背景论断、相关工作转述、空白推导链与贡献证据对照表 |
| `review_checklist.md` | 7 道门禁审计报告、引用覆盖率与人工确认清单 |
| `result.json` | 完整保留 `case_id` 与原始 `output_contract` 字段的结构化汇总结果 |

## 4. 核心方法与创新点

1. **写作与核验分离（Plan-then-Audit Architecture）**：先生成显式携带 `source_ids`、`derived_from_refs`、`supporting_evidence` 的结构化计划，再由确定性脚本审计并编译为可读文本，避免大模型“边写边编引用”。
2. **反流水账与去空话检测**：自动识别背景中的泛化套话，强制要求相关工作具备 `>= 2` 个主题分组与横向对比维度，检测按时间顺序逐篇摘要堆砌的反模式。
3. **空白推导链闭环**：每条研究空白必须显式锚定至少一篇真实已引文献的具体局限或冲突，并对应本文的解决机制。
4. **贡献 1:1 数值与图表核对**：将贡献条目中的数值指标、方法名、图表编号与 `author_evidence_index` 逐项比对，拦截无证据的夸大断言。

## 5. 适用范围与限制

- **适用范围**：中英文理工科、计算机、材料、化工、生医等学科论文的引言、相关工作、研究空白与贡献章节起草与改写；支持 `numeric`、`author-year` 与 LaTeX `\cite{}` 格式。
- **已知限制**：不主动联网检索外部文献库；若输入材料本身缺少关键基线文献或实验数据，脚本会诚实标注 `[CITATION_NEEDED]` / `[NEEDS_AUTHOR_CONFIRMATION]` 并在 `uncertainty` 中说明，绝不编造元数据或实验结果。

## 6. 端到端自检

```bash
python3 scripts/self_test.py
```

自检包含合成正向用例（验证 7 道门禁全绿及产物完整性）与负向破坏用例（注入空泛开场、虚构引用、流水账堆砌、无证据贡献与过度断言，验证审计器 100% 拦截）。
