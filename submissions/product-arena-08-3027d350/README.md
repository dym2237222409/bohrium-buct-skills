# `scipdf-evidence-parser` — 科研文献文档解析与原位证据核验 Skill

本项目面向 **“阅读01｜文献文档解析”** 任务设计，提供一套可被 Agent（含平台官方 DeepSeek Harness 及通用本地 Agent）零配置调用的**版面感知科研 PDF 解析与证据核验工具链**。围绕 **“提得全、排得对、拆得清、核得回”** 四大核心痛点，将原始学术 PDF 解析为带页码、分栏、区块编号与 `bbox` 坐标定位的结构化数据，并通过机械化契约校验杜绝页码幻觉与未报告数据编造。

---

## 1. 核心能力与技术改进

### 1.1 四维核心问题针对性设计

| 维度 | 传统基础 Agent 常见缺陷 | 本 Skill 的改进机制 |
|---|---|---|
| **提得全** | 页眉期刊名、页脚页码混入正文句子中间；页底脚注丢失或串入左栏末尾 | 跨页上下边缘带（Top/Bottom Margin Band）重复签名聚类 + 独立页码识别 + 字号/前导标记脚注分离，分别归档至 `headers_footers` |
| **排得对** | 双栏/三栏论文按同高度 Y 行横跨拼接导致严重串栏；跨栏/跨页断句被拆碎 | **XY-Cut 通栏切分 + 中心空白沟槽（Gutter Histogram）投影分栏算法**，精准区分通栏标题/摘要/宽表与分栏正文，并自动缝合跨栏/跨页连字符断段（保留多段 `provenance_spans`） |
| **拆得清** | 章节层级扁平化；跨页续表断裂；连字 `ﬁ/ﬂ`、Symbol 字体希腊字母 `α/β/Δ` 及上下标 `10⁻⁴` 失真 | 正文字号众数直方图 + 中英双语编号语法树重建多级 `sections`；几何/管道网格提取并自动合并 `Table X (continued)` / `续表`（同步导出 JSON / Markdown / HTML）；基线偏移量上下标还原与 `unicode_math_map.json` 符号保真 |
| **核得回** | 结论无页码或编造超界页码；扫描件、图片型表格、加密文件或乱码字体靠猜测补全 | 每个段落、表格、公式、图表题注与参考文献均绑定 `(page, printed_page_label, column, section_path, block_id, bbox)`；`validate_output.py` 强制核验页码范围与原文子串匹配，并对未报告、冲突与乱码区块诚实披露 |

### 1.2 四级多后端自动容错链路（零手工配置）

`scripts/parse_scientific_pdf.py` 按以下优先级自动探测并降级，保证在不同容器与本地环境中持续稳定运行：
1. **Tier 1 — `PyMuPDF` (`fitz`)**：高精度 `dict` 级 Span 字体大小、粗斜体标志、基线偏移上下标还原及原生表格检测（自动联动 `pdfplumber` 补充复杂几何表格）。
2. **Tier 2 — `pdfplumber`**：词级坐标聚类与几何线段表格结构重建。
3. **Tier 3 — `Poppler` CLI (`pdftohtml -xml`)**：基于 Python 标准库 `xml.etree.ElementTree` 的坐标与字号解析（无需任何第三方 Python 包）。
4. **Tier 4 — `stdlib_pdf_stream`**：纯 Python 标准库 PDF 内容流（`zlib` `/FlateDecode` + `BT..ET` `Tm/Td/Tf/Tj/TJ` 矩阵坐标解析器），即使在完全无第三方库与外部二进制的最小 Python 环境中仍可执行分栏、表格与公式解析。
5. **自适应扫描件 OCR 增强 (`--ocr-mode auto`)**：逐页诊断文字层密度与 `(cid:N)` 字体乱码；对无文字层扫描页自动执行 300 DPI 渲染 + 对比度增强 + `tesseract` TSV 词级置信度识别，低置信度词自动写入不确定性清单。

---

## 2. 目录结构与文件说明

```text
.
├── SKILL.md                          # Agent 加载入口（含合法 YAML 元信息与四步标准执行协议）
├── README.md                         # 完整使用说明、方法原理、依赖、限制与自查指南
├── scripts/
│   ├── inspect_case.py               # 案例输入与环境探针，生成初始诚实检查点 /app/output/result.json
│   ├── parse_scientific_pdf.py       # 多后端版面感知解析核心引擎（XY-Cut、表格合并、公式保真、OCR）
│   ├── query_evidence.py             # 基于 BM25/正则/章节的原位证据检索器，输出带坐标逐字引文
│   ├── validate_output.py            # 输出契约完整性、页码反幻觉与原文引文匹配校验器 + 审计报告生成
│   └── self_test.py                  # 纯 Python 动态生成双栏学术 PDF 并执行端到端回归与对照自检
├── resources/
│   ├── unicode_math_map.json         # PDF 连字、私有区 Symbol 希腊字母、数学算子与乱码正则字典
│   └── section_patterns.json         # 中英双语学术章节层级、图表题注、公式编号与参考文献语法规则
├── templates/
│   └── result_template.json          # 标准结构化结果与证据/边界披露字段模板
└── examples/
    └── minimal_example.md            # 最小可复现调用示例（所有示意输出均明确标注为合成示例）
```

> 全部交付材料共 **10 个纯 UTF-8 文本文件**，无符号链接、无二进制文件、无预制正式测试答案，总大小约 85 KB（严格满足 ≤50 文件、单文件 ≤500 KB、总量 ≤2 MB 的 Harbor 技术提交约定）。

---

## 3. 环境依赖与安装方式

- **必需基础环境**：Python `>= 3.9`（仅使用 Python 标准库即可通过 `Tier 3/4` 运行全部脚本并通过 `self_test.py`）。
- **推荐增强依赖**（Harbor `scientific-python` / `pdf-text-tool` / `ocr-tool` 镜像已预装；本地独立使用可按需安装）：
  ```bash
  pip install pymupdf>=1.23.0 pdfplumber>=0.10.0 pillow>=10.0.0
  # 可选系统工具（用于 Poppler XML 回退与扫描件 OCR）：poppler-utils, tesseract-ocr (eng, chi_sim)
  ```

---

## 4. 快速开始与调用步骤

### 4.1 一键运行内置自检（推荐首次使用时执行）

```bash
python scripts/self_test.py
```

期望输出 `"self_test": "PASSED"`，验证双栏防串栏、页眉页脚隔离、跨页续表合并、公式与参考文献提取及反幻觉校验器全部正常。

### 4.2 解析单篇科研 PDF 并检索证据

```bash
# 1. 解析 PDF（输出 parsed_document.json、structured_fulltext.md、tables/*.md|html、parse_diagnostics.json）
python scripts/parse_scientific_pdf.py --pdf /path/to/paper.pdf --out-dir ./work/parsed --ocr-mode auto

# 2. 按问题关键词检索带页码与 bbox 的原文证据
python scripts/query_evidence.py --parsed-json ./work/parsed/parsed_document.json --query "selectivity" --top-k 5
```

### 4.3 Harbor 评测环境完整调用流程

详见 [SKILL.md](SKILL.md) 第 3 节（`inspect_case.py` → `parse_scientific_pdf.py` → `query_evidence.py` → `validate_output.py`）。

---

## 5. 适用范围、已知限制与失败处理

1. **适用范围**：单栏、双栏、混合通栏排版的英文与中文科研论文 PDF、补充材料（SI）、实验数据表、带编号数学公式及常见扫描版文献。
2. **已知限制与诚实边界**：
   - **手写批注或极低分辨率扫描件（<120 DPI）**：OCR 词级置信度可能低于阈值，系统不会猜测补字，而是将对应页码与低置信度片段记录在 `uncertainty.garbled_or_low_confidence_spans`。
   - **纯矢量绘图/位图内嵌表格且无文字层**：自动标记 `table_type: "image_or_unstructured"` 并输出人工接管定位（`human_takeover_locations`），不伪造单元格数值。
   - **强密码加密 PDF**：立即识别 `is_encrypted: true` 并返回 `status: "unverifiable"` 及明确原因。
   - **复杂多行矩阵积分的完整 LaTeX AST 还原**：在仅含字形流而无源 TeX 的 PDF 中，保留 Unicode 数学符号、希腊字母、上下标与原编号对照，不强行编造未出现的公式项。

---

## 6. 维护与复用信息

- **组件化复用**：`parse_scientific_pdf.py`、`query_evidence.py` 与 `validate_output.py` 既可作为 CLI 独立运行，也可在文献翻译、结构化笔记、Nanopublication 抽取及知识图谱构建等下游科研工作流中直接 `import` 复用。
