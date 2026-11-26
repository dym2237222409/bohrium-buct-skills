# `scipdf-academic-polisher` — 学术语言润色 Skill（写作02）

面向 Bohrium Product Arena（`product-arena-02-7051d92d`）的科研论文与学术文本语言润色 Skill。支持中/英/双语稿件（Markdown、纯文本、LaTeX、DOCX），围绕**改得对、说得像、没改坏、讲得清**四项核心标准，采用「结构分句锚定 → 最小编辑与术语锁表 → 7 道自动化保真门禁核验 → 可编辑成品与逐句审校台账编译」双通道流程。

## 目录结构

```text
product-arena-02-7051d92d/
├── SKILL.md                              # Agent 调用入口、工作流与异常处理规范
├── README.md                             # 本说明文档
├── GITHUB_URL.txt                        # 公开 GitHub 目录与入口声明
├── resources/
│   ├── hedge_causality_lexicon.json      # 中英限定强度/因果-相关/否定/宣传词词表
│   ├── style_profiles.json               # 5 类学术体裁语域规则与中式英语检测规则
│   └── edit_taxonomy.json                # 标准化修改类型与风险等级定义
├── templates/
│   ├── edits_draft_schema.json           # 逐句润色草稿 JSON Schema
│   └── result_template.json              # Harbor result.json 输出模板
├── examples/
│   └── minimal_example.md                # 合成最小调用与输出示例（非正式测试答案）
└── scripts/
    ├── inspect_and_segment.py            # 案例解析、分句定位、锚点抽取与异常/术语冲突检测
    ├── audit_and_compile_polish.py       # 7 项保真与一致性门禁核验 + 全套可编辑产物编译
    └── self_test.py                      # 零外部依赖端到端正/反向自检脚本
```

## 运行环境与依赖

- **Python**: `>= 3.9`（纯 Python 标准库，零强制第三方依赖）。
- **可选依赖**: 若输入为 `.docx` 文件，脚本优先尝试标准库 `zipfile + xml.etree.ElementTree` 无依赖读取段落；若环境已安装 `python-docx`，亦可无缝扩展写出 `.docx`。

## 快速使用流程

```bash
# 1. 解析案例/稿件，提取稳定句号与受保护锚点（数字/单位/引用/公式/限定词）
python scripts/inspect_and_segment.py \
  --case /input/case.json \
  --task /input/task.md \
  --out /app/work/segmented_case.json

# 2. Agent 按 templates/edits_draft_schema.json 生成逐句润色草稿 edits_draft.json

# 3. 执行 7 道自动化核验门禁并编译全部可编辑产物与 result.json
python scripts/audit_and_compile_polish.py \
  --segmented /app/work/segmented_case.json \
  --edits /app/work/edits_draft.json \
  --resources resources \
  --out-dir /app/output
```

## 本地自检

```bash
python scripts/self_test.py
```

自检同时验证：
1. **反向门禁拦截**：能够精准拦截结论强度夸大（`suggests` → `proves`）、相关改因果、否定反转、数值/单位丢失、引用丢失、LaTeX 公式损坏、术语表漂移与术语表内部冲突；
2. **正向全流程编译**：对合成双语稿件完成分句、术语锁表、7 项门禁全绿并通过 `result.json` 契约完整性检查。

## 适用边界与限制

- 本 Skill 严格遵守“最小编辑、不篡改科学主张”原则：若原文存在科学事实疑问、数据缺失或术语表内部冲突，脚本会将其标记至 `human_review_items` 交由作者确认，绝不擅自编造数据或强行覆盖作者原意。
- 语法/语义润色由调用本 Skill 的 LLM 结合本 Skill 的规则与词表完成；Python 脚本负责确定性的分句、锚点提取、7 项保真门禁核验、术语一致性回扫与多格式成品编译。
