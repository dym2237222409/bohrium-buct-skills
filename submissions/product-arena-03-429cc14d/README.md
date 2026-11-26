# `scipdf-citation-manager` — 参考文献与引用管理 Skill（写作04）

面向 Bohrium Product Arena（`product-arena-03-429cc14d`）的参考文献与引用管理 Skill。支持多格式文献库（BibTeX / RIS / EndNote `.enw` / CNKI 导出 / CSL-JSON / 纯文本列表）与多格式稿件（Markdown / 纯文本 / LaTeX / DOCX），聚焦**挂得对、编得顺、收得净、对得上**四项核心任务，实现论断级引用挂载、可解释四级去重（含防误合并护栏）、正文与文末列表确定性双向同步重排、多规范格式转换与 6 项引用真实性/匹配度审计。

## 目录结构

```text
product-arena-03-429cc14d/
├── SKILL.md                             # Agent 调用入口、工作流与异常处理规范
├── README.md                            # 本说明文档
├── GITHUB_URL.txt                       # 公开 GitHub 目录与入口声明
├── resources/
│   ├── citation_styles.json             # GB/T 7714-2015（顺序编码/著者年）、APA 7th、IEEE、Vancouver 规则
│   └── dedup_rules.json                 # 四级去重判据、预印本优先规则、防误合并护栏与字段冲突策略
├── templates/
│   ├── citation_plan_schema.json        # 论断级引用挂载与匹配度核验计划 Schema
│   └── result_template.json             # Harbor result.json 输出模板
├── examples/
│   └── minimal_example.md               # 合成最小调用与输出示例（非正式测试答案）
└── scripts/
    ├── parse_bib_and_manuscript.py      # 稿件分句/已有引用解析 + BibTeX/RIS/EndNote/CNKI/JSON/文本文献库解析
    ├── deduplicate_refs.py              # 四级可解释文献去重、字段补全、防误合并护栏与冲突台账
    ├── sync_format_and_verify.py        # 正文与文末双向同步重排、多规范导出与 6 道审计门禁
    └── self_test.py                     # 零外部依赖端到端正/反向自检脚本
```

## 运行环境与依赖

- **Python**: `>= 3.9`（纯 Python 标准库，零强制第三方依赖）。

## 快速使用流程

```bash
# 1. 解析稿件与多来源文献库（自动识别 BibTeX / RIS / EndNote / CNKI / JSON / 纯文本）
python scripts/parse_bib_and_manuscript.py \
  --case /input/case.json \
  --task /input/task.md \
  --out /app/work/parsed_case.json

# 2. 执行可解释多级去重、字段互补合并与防误合并审计
python scripts/deduplicate_refs.py \
  --parsed /app/work/parsed_case.json \
  --rules resources/dedup_rules.json \
  --out /app/work/deduped_refs.json

# 3. Agent 生成论断级挂载与匹配核验计划 /app/work/citation_plan.json

# 4. 同步重排正文与文末编号、导出目标格式并执行 6 道审计门禁
python scripts/sync_format_and_verify.py \
  --parsed /app/work/parsed_case.json \
  --deduped /app/work/deduped_refs.json \
  --plan /app/work/citation_plan.json \
  --styles resources/citation_styles.json \
  --out-dir /app/output
```

## 本地自检

```bash
python scripts/self_test.py
```

自检完整覆盖：BibTeX + RIS + CNKI 多源解析、预印本与期刊正式版自动归并、同作者同年不同论文防误合并、`[1-3]` 连续编号同步重排与旧→新映射、GB/T 7714—2015 与 APA 7th 格式化导出、虚假/孤立引用拦截及论断-文献不匹配告警。
