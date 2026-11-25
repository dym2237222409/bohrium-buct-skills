# 最小调用示例（Minimal Reproducible Example）

> **重要声明（示例标注）**：本文件展示的所有输入、命令行调用与输出内容均为**合成最小示例（Synthetic Example）**，仅用于演示 `scipdf-evidence-parser` Skill 的接口约定、目录结构与字段格式，**不代表、也不冒充任何平台正式评测案例的结果**。

---

## 1. 场景一：独立命令行自检与最小示例运行

无需准备外部私有文件，直接运行内置自检脚本 `scripts/self_test.py`。该脚本会在临时目录动态生成一份包含**双栏排版、跨栏阅读顺序、页眉页脚、合并表头实验表格、希腊字母数学公式、页脚注释与参考文献**的合成学术 PDF（`synthetic_two_column_demo.pdf`），并完整走通解析、检索与校验流程：

```bash
python /skills/weekly-skill/scripts/self_test.py --work-dir /tmp/scipdf_demo
```

（若在本地仓库根目录运行，将 `/skills/weekly-skill` 替换为当前 Skill 所在目录 `.` 即可。）

---

## 2. 场景二：Harbor 评测环境标准四步调用

当平台评测环境提供 `/input/case.json` 与 `/input/task.md`（或工作目录下的 `task.md`）时：

```bash
# 步骤 1：探查输入材料、工具链与 output_contract，并在 /app/output/result.json 生成诚实初始检查点
python /skills/weekly-skill/scripts/inspect_case.py \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output

# 步骤 2：对发现的真实 PDF 执行版面感知解析（多栏 XY-Cut 排序、页眉页脚剥离、表格与公式提取、扫描件自适应 OCR）
python /skills/weekly-skill/scripts/parse_scientific_pdf.py \
  --pdf /app/work/discovered_primary.pdf \
  --out-dir /app/work/parsed \
  --ocr-mode auto

# 步骤 3：按任务问题或字段检索原文逐字证据与页码/区块坐标定位
python /skills/weekly-skill/scripts/query_evidence.py \
  --parsed-json /app/work/parsed/parsed_document.json \
  --query "catalyst selectivity" \
  --section "Results" \
  --include-tables \
  --top-k 5

# 步骤 4：写入 /app/output/result.json 后执行契约完整性与反幻觉页码核验，并导出复核报告
python /skills/weekly-skill/scripts/validate_output.py \
  --case-json /input/case.json \
  --parsed-dir /app/work/parsed \
  --result-json /app/output/result.json \
  --report-out /app/output/parsing_report.md
```

---

## 3. 示意输出片段（【示例数据 / EXAMPLE ONLY】）

### 3.1 `parsed_document.json` 结构化区块示意（节选，示例）

```json
{
  "_example_only": true,
  "doc_id": "synthetic_two_column_demo",
  "source_pdf": "/tmp/scipdf_demo/synthetic_two_column_demo.pdf",
  "num_pages": 2,
  "backend_used": "pymupdf_xycut",
  "document_mode": "digital_clean",
  "metadata": {
    "title": "Selective Catalytic Conversion in Microchannel Reactors: A Synthetic Layout Benchmark",
    "authors": ["Lin Chen", "Mingze Wu"],
    "abstract": "We present a synthetic two-column benchmark manuscript to verify reading-order reconstruction, table structure recovery, and symbol fidelity."
  },
  "reading_order_blocks": [
    {
      "block_id": "p001_b003",
      "page": 1,
      "printed_page_label": "101",
      "column": 1,
      "ColumnCountOnPage": 2,
      "role": "section_heading",
      "section_path": "1 Introduction",
      "bbox": [54.0, 210.0, 288.0, 224.0],
      "text": "1 Introduction"
    },
    {
      "block_id": "p001_b004",
      "page": 1,
      "printed_page_label": "101",
      "column": 1,
      "role": "paragraph",
      "section_path": "1 Introduction",
      "bbox": [54.0, 230.0, 288.0, 312.0],
      "text": "Microchannel reactors improve heat transfer coefficient α by 3.2× compared with batch vessels at ΔT ≤ 15 °C (1)."
    }
  ],
  "headers_footers": {
    "headers": [
      {"page": 1, "bbox": [54.0, 28.0, 540.0, 40.0], "text": "Journal of Synthetic Benchmarks · Vol. 12 (EXAMPLE)"}
    ],
    "footers": [
      {"page": 1, "bbox": [280.0, 752.0, 315.0, 764.0], "text": "101"}
    ],
    "footnotes": [
      {"page": 1, "bbox": [54.0, 720.0, 288.0, 738.0], "marker": "*", "text": "* Corresponding synthetic author note (EXAMPLE)."}
    ]
  }
}
```

### 3.2 `/app/output/result.json` 最终交付示意（示例）

```json
{
  "_example_only": true,
  "case_id": "demo-synthetic-case-001",
  "status": "success",
  "status_reason": "[EXAMPLE] Completed layout-aware parsing and verified all anchors against synthetic_two_column_demo.pdf.",
  "results": {
    "reaction_temperature_limit": "ΔT ≤ 15 °C",
    "unreported_pressure_drop": null
  },
  "sources": [
    {
      "item_id": "reaction_temperature_limit",
      "source_file": "/tmp/scipdf_demo/synthetic_two_column_demo.pdf",
      "page": 1,
      "column": 1,
      "section_path": "1 Introduction",
      "block_id": "p001_b004",
      "bbox": [54.0, 230.0, 288.0, 312.0],
      "verbatim_quote": "improve heat transfer coefficient α by 3.2× compared with batch vessels at ΔT ≤ 15 °C",
      "extraction_method": "pymupdf_xycut"
    }
  ],
  "uncertainty": {
    "unreported_in_source": [
      {
        "field": "unreported_pressure_drop",
        "note": "[EXAMPLE] Not reported anywhere in pages 1–2 of the source PDF; kept as null rather than guessed."
      }
    ],
    "unverifiable_items": [],
    "material_conflicts": [],
    "garbled_or_low_confidence_spans": [],
    "human_takeover_locations": []
  },
  "change_records": [
    {
      "step": "inspect_case",
      "timestamp": "2026-11-25T08:00:00Z",
      "summary": "[EXAMPLE] Initialized output skeleton from case_id=demo-synthetic-case-001."
    },
    {
      "step": "validate_output",
      "timestamp": "2026-11-25T08:00:05Z",
      "summary": "[EXAMPLE] Verified 1/1 cited page bounds and verbatim quote substrings."
    }
  ]
}
```
