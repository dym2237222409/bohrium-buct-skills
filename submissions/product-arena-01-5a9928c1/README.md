# `scipdf-faithful-translator` — 术语强制锁定与双语段落对齐科研文献翻译 Skill

本项目面向 **“阅读03｜文献翻译”** 赛题设计，针对基础 Agent 在长篇科研论文翻译中最常见的四大痛点——**术语前后漂移、双栏跨页漏段错位、否定/限定/数值/引用失真、译法不可复核**——提供一套“版面感知分段 → 术语表优先级锁定 → 上下文接续翻译 → 程序化回填与极性自检”的完整可复用工具链。

---

## 1. 四大核心难点解决机制

| 核心指标 | 常见问题 | 本 Skill 的工程实现 |
|---|---|---|
| **术语稳** | 术语表只写在 Prompt 中，标题译“微通道”、结论漂移成“微槽” | `build_and_lock_glossary.py` 建立全篇强制锁定的 `locked_glossary.json`（融合题目词表 + 论文缩略语定义 + 内置学科词库）；`verify_and_export_bilingual.py` 逐段扫描命中位置，自动修复已知漂移别名并拦截任何未对齐译名 |
| **段落全** | 双栏串行、跨页断句导致漏译、重译或段号错位 | `segment_and_extract_terms.py` 内置四级 PDF 坐标解析与 XY-Cut 分栏算法，剥离页眉页脚、缝合跨栏/跨页断段，生成唯一段号 `S0001..SNNNN` 与句级边界 `S0001.1..S0001.k`，强制校验 100% 1:1 段落覆盖率 |
| **关系准** | 漏掉 `not` / `only when` / `at most`、误把 `may` 译为“必然”、弄丢小数或 `[1, 2]` 引用 | 自动提取每段受保护科学符号（数值、单位、引用标记、图表公式编号），并基于 `fidelity_rules.json` 对否定词、情态/推测词、充要条件、上下界限定与让步转折关系执行双向极性核查 |
| **可核验** | 只给通顺意译，找不到原文页码，多义词或模糊处含混带过 | 导出段落级双语对照 `bilingual_translation.md` 与 `bilingual_segments.json`（附 `page`、`column`、`bbox` 与 `translation_rationale`），并单独生成 `translation_qa_report.md` 人工确认清单（含多义词消歧记录与存疑片段标注） |

---

## 2. 目录结构

```text
.
├── SKILL.md                             # Skill 主入口（含 YAML 元信息与五步标准执行流程）
├── README.md                            # 完整方法说明、术语表规范、对齐约定与异常处理指南
├── GITHUB_URL.txt                       # 本作品 GitHub 公开仓库目录链接
├── scripts/
│   ├── inspect_case.py                  # 探查输入材料与给定术语表，初始化诚实检查点 result.json
│   ├── segment_and_extract_terms.py     # 多后端双栏 PDF/文本分段、受保护符号提取与长文批次规划
│   ├── build_and_lock_glossary.py       # 多源术语优先级融合、冲突消解与全篇段落分布锁定
│   ├── verify_and_export_bilingual.py   # 术语一致性强制检查、数值/引用/否定极性核验与双语导出
│   └── self_test.py                     # 纯 Python 合成双栏英文论文 PDF 端到端自检脚本
├── resources/
│   ├── seed_scientific_glossary.json    # 跨学科核心术语与高频学术“假朋友”多义词消歧词典
│   └── fidelity_rules.json              # 数值/引用/公式保护正则及否定、推测、限定、让步核验规则
├── templates/
│   ├── glossary_schema.json             # 可复用术语表 JSON Schema 模板
│   └── result_template.json             # 标准输出结构模板
└── examples/
    └── minimal_example.md               # 最小调用示例（所有示意输出均明确标明为合成示例）
```

---

## 3. 环境依赖与使用方式

- **必需环境**：Python `>= 3.9`（纯标准库即可运行全部脚本并通过 `self_test.py`）。
- **可选增强库**（容器已预装；本地可按需安装以处理复杂版式 PDF）：`pip install pymupdf>=1.23.0 pdfplumber>=0.10.0`。

### 一键自检
```bash
python scripts/self_test.py
```

### 标准翻译核验闭环
完整命令行参数与分步示例详见 [SKILL.md](SKILL.md) 与 [examples/minimal_example.md](examples/minimal_example.md)。

---

## 4. 段号对齐约定、未译标注规则与适用限制

1. **段号与句号约定**：全篇按阅读顺序分配连续段号 `S0001..SNNNN`，段内按学术分句规则（保护 `et al.`、`Fig. 1`、`e.g.`、小数点）分配 `S0001.1..S0001.k`。参考文献条目默认标记 `translatable: false` 并保留原始引文格式，以防译名破坏文献检索。
2. **未译与不确定标注规则**：遇到无文字层扫描页、`(cid:N)` 字体乱码或图片内嵌复杂公式时，严禁凭空猜测；统一标记 `[UNTRANSLATED_SOURCE: <具体原因>]` 并写入 `uncertainty.unverifiable_or_untranslated_spans` 与 `translation_qa_report.md` 人工复核清单。
3. **跨学科复用**：只需提供新领域的 CSV/TSV/JSON 术语表传给 `--custom-glossary`，即可无缝迁移至化学化工、材料、生物医学、物理或计算机文献翻译工作流。
