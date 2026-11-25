# `scipdf-structured-notes` — 章节骨架对齐与前提绑定科研结构化笔记 Skill

本项目面向 **“阅读10｜文献总结与结构化笔记”** 赛题设计，针对基础 Agent 在文献总结中常见的“套用泛化模板脱离原文章节、丢掉结论成立前提、出处笼统无法回查、概念缩写重复堆砌难以复用”四大痛点，构建了一套可独立运行、机械核验且支持多篇文献并排对比的结构化笔记工作流。

---

## 1. 四维核心设计（对得上 · 留得住 · 找得回 · 用得上）

| 核心维度 | 基础 Agent 常见短板 | 本 Skill 的工程机制 |
|---|---|---|
| **对得上** | 无论什么类型论文都套用“背景-方法-结论”三段式，丢失原文章节递进逻辑 | `parse_and_index_paper.py` 先提取论文真实章节树 `SEC01..SECNN`（含双栏 XY-Cut 防串栏排序），强制逐节记录“本节目的 → 方法/论证 → 本节结论 → 下一节承接关系” |
| **留得住** | 只摘抄亮眼指标（如“选择性 96.1%”），丢失温度、流速或统计假设等成立前提 | `field_dictionary.json` 规定每条核心发现（`condition_bound_findings`）必须显式绑定非空的 `holding_preconditions`，并将局限性拆分为“作者明示局限”与“原文未报告边界” |
| **找得回** | 只写“见正文第3节”，甚至编造不存在的页码或数值 | 为全篇每个段落（`P0001`）、表格（`TAB01`）、图片（`FIG01`）、公式（`EQ01`）建立带 `page`、`column`、`bbox` 与原文的注册表；`compile_and_verify_notes.py` 自动解引用全部锚点并核对数值字面一致性 |
| **用得上** | 输出一段纯文本，缩写与全称重复建条，无法检索或跨文献横向比较 | 自动识别正文缩略语定义（如 `Turnover Frequency (TOF)`）归并同义知识点并检测矛盾；同时导出 YAML+Markdown 笔记、`knowledge_items.jsonl` 检索库、`paper_card.csv` 表格及多文献横向对比工具 `query_notes.py` |

---

## 2. 目录结构与字段字典说明

```text
.
├── SKILL.md                                   # Skill 主入口（含 YAML 元信息与四步标准工作流）
├── README.md                                  # 使用说明、字段字典、锚点规范与维护指南
├── GITHUB_URL.txt                             # 本作品 GitHub 公开仓库子目录链接
├── scripts/
│   ├── inspect_case.py                        # 案例输入与工具链探针，生成初始诚实检查点 result.json
│   ├── parse_and_index_paper.py               # 四级容错 PDF/文本解析，生成章节骨架与细粒度锚点库
│   ├── compile_and_verify_notes.py            # 锚点解引用校验、数值一致性核查、同义词归并与多格式导出
│   ├── query_notes.py                         # 知识点检索与多篇论文卡片横向对比矩阵生成工具
│   └── self_test.py                           # 纯 Python 动态生成双栏学术 PDF 端到端回归自检
├── resources/
│   ├── field_dictionary.json                  # 论文卡片/章节摘要/知识点字段字典（区分机器判定 vs 人工确认）
│   └── anchor_and_heading_rules.json          # 锚点语法规则（P/TAB/FIG/EQ）与双语前提/局限提示词库
├── templates/
│   ├── notes_input_schema.json                # 结构化笔记草稿输入 Schema 模板
│   └── result_template.json                   # 标准输出契约模板
└── examples/
    └── minimal_example.md                     # 最小可复现调用示例（明确标明为合成示例）
```

### 机器可判定字段 vs 需人工确认字段（详见 `resources/field_dictionary.json`）
- **机器可判定 (`machine_verifiable`)**：论文标题、作者年份、数据集/实验材料参数、控制条件与公式编号、定量发现数值与原文锚点匹配度、缩略语归并。
- **需人工确认 (`requires_human_judgment`)**：研究空白的高层学术定位、章节间论证承接归纳、未报告实验对照条件是否影响外推适用性（自动汇总至 `uncertainty.human_confirmation_fields`）。

---

## 3. 依赖环境与快速自检

- **基础依赖**：Python `>= 3.9`（纯标准库即可运行全部脚本并通过 `self_test.py`）。
- **增强依赖**（Harbor 镜像已内置；本地可按需安装）：`pip install pymupdf>=1.23.0 pdfplumber>=0.10.0`。

```bash
python scripts/self_test.py
```

期望输出 `"self_test": "PASSED"`，验证章节骨架对齐、前提条件绑定、锚点 100% 解引用、`TOF`↔`Turnover Frequency` 自动归并及横向对比矩阵生成全部通过。
