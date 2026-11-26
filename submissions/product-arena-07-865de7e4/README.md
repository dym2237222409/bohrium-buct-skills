# `scipdf-latex-builder-auditor` — LaTeX 写作、查错修复与可复现编译 Skill

面向学术论文 **LaTeX 源码编写、模板适配、多遍编译排错、公式/表格/引用审计与可复现构建**的可复用 Agent Skill，围绕四项核心要求设计：**编译得通（多遍收敛 + 全量根因定位）**、**改得对（公式/表格/引用/宏包顺序/模板必填项保真）**、**交得可用（清晰可编辑源码 + 真实 PDF/PNG 预览 + Diff）**、**复现得了（构建清单与一键复现脚本）**。

## 1. 目录结构

```text
product-arena-07-865de7e4/
├── SKILL.md                                    # Agent 执行主规范（YAML frontmatter + 编译-修复闭环）
├── README.md                                   # 本说明文档
├── GITHUB_URL.txt                              # 公开仓库地址声明
├── resources/
│   ├── latex_error_taxonomy.json               # LaTeX/BibTeX/Biber 报错分类、根因定位与级联识别规则
│   └── template_and_package_rules.json         # 宏包加载顺序、引擎选择与模板/公式/三线表规范
├── templates/
│   ├── build_report_schema.json                # 可复现构建清单（build_manifest.json）契约
│   └── result_template.json                    # Harbor 评测 result.json 输出模板
├── examples/
│   └── minimal_example.md                      # 显式标注的合成最小调用与输出示例
└── scripts/
    ├── inspect_latex_project.py                # 工程树发现、TeX 环境探测与编译前静态审计器
    ├── compile_and_audit_latex.py              # 多遍编译收敛、全量日志根因解析、PNG 预览与 Diff 编译器
    └── self_test.py                            # 正向 + 负向端到端合成自检脚本
```

## 2. 环境依赖

- **脚本必需依赖**：Python 3.8+（100% 纯标准库，无第三方 Python 包强制依赖）。
- **实际编译与视觉预览工具（自动探测）**：
  - TeX 引擎：`pdflatex` / `xelatex` / `lualatex` + `bibtex` / `biber`（TeX Live / MiKTeX）。
  - PDF 渲染预览：`pdftoppm`（Poppler）或 `PyMuPDF (fitz)` 或 `gs`（Ghostscript）。
  - 若运行环境未预装 TeX 引擎或渲染器，脚本不会伪造 `.pdf` / `.png`，而是完成全量静态审计、源码修复、统一 Diff 与 `reproduce_build.sh`，并在 `result.json` 中诚实标注 `status: "partial"` 与环境限制。

## 3. 快速调用步骤

```bash
# 1. 探测工程结构、可用 TeX 工具链与静态语法/表格/引用/宏包顺序隐患
python3 scripts/inspect_latex_project.py \
  --input-dir /input \
  --out /app/work/project_inspection.json

# 2. 在 /app/work/project 中对源码做最小化根因修复，然后执行多遍编译、全量日志诊断、预览渲染与产物归档
python3 scripts/compile_and_audit_latex.py \
  --input-dir /input \
  --work-project /app/work/project \
  --inspection /app/work/project_inspection.json \
  --output-dir /app/output \
  --engine auto
```

### 输出产物清单（`/app/output/`）

| 文件 | 用途 |
|---|---|
| `*.tex` / `*.bib` | 结构清晰、可直接并入稿件并继续编辑的修复后源码 |
| `*.pdf` & `previews/page-*.png` | 真实编译得到的 PDF 及逐页渲染 PNG 预览（环境支持时生成） |
| `changes.diff` | 与 `/input/` 原始源码逐行对比的统一差异补丁 |
| `build_manifest.json` | 引擎版本、宏包与类文件、编译轮次、日志诊断、SHA-256 校验与收敛状态 |
| `reproduce_build.sh` | 可在他人环境中一键复现相同编译轮次的构建脚本 |
| `audit_report.md` | 静态审计 + 编译日志根因 + 表格溢出/引用闭合综合报告 |
| `result.json` | 完整保留 `case_id` 与原始 `output_contract` 字段的结构化汇总结果 |

## 4. 核心方法与创新点

1. **静态预检 + 多遍收敛双闭环**：在耗时的 TeX 编译前，先通过纯 Python 静态分析拦截环境不匹配、表格列数错位、未定义 `\cite`/`\ref`、重复 `\label`、缺失图片与 `hyperref`/`cleveref` 宏包加载顺序冲突。
2. **全量日志解析与级联去噪**：解析整个 `.log`/`.blg` 文件（不停在第一条报错），按 `file:line` 归类根因并标记常见下游级联症状，大幅减少反复试错轮次。
3. **诚实可复现交付**：自动固化编译命令、轮次、引擎版本与产物哈希；环境缺宏包/缺字体/缺 TeX 发行版时如实汇报，绝不伪造编译通过或视觉截图。

## 5. 端到端自检

```bash
python3 scripts/self_test.py
```

自检构造了包含表格列数错位、未闭合 `equation` 环境、未定义引用 `\cite{ghost2025}`、重复 `\label`、`hyperref`/`cleveref` 加载倒置的合成 LaTeX 工程及多错误级联 `.log`，验证静态审计、全量日志根因解析、修复后通过与诚实降级逻辑全部正常。
