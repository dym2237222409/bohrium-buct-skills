# `scipdf-nanopub-extractor` — 科研原子断言拆解、最小证据绑定与 Nanopublication 四图校验 Skill

本项目面向 **“阅读14｜Nanopublication”** 赛题设计，提供从真实科研论文（支持 PDF 与 JATS/JSON 语料）抽取**可独立判真假的原子科研断言**、绑定**最小充分原文证据与完整来源元数据**、执行 **6 项机械质量门控**并导出标准 W3C **TriG / JSON-LD / N-Quads / JSONL** 序列化的完整可复用工作流。

---

## 1. 四大核心要求实现机制（拆得细 · 对得上 · 追得回 · 读得懂）

| 维度 | 核心问题 | 本 Skill 的工程机制 |
|---|---|---|
| **拆得细** | 避免把整段摘要塞进单条断言，保留条件与限定范围 | `build_and_validate_nanopubs.py` 强制检查单句原子性（长度 ≤ 280 字符、句数 ≤ 1、复合分句告警），要求每条断言独立声明 `(subject, relation, object, conditions_and_scope, quantitative_value, negated)` |
| **对得上** | 每条断言找到最小充分原文证据并区分直接证据与间接推论 | `index_source_passages.py` 兼容 JATS/JSON（严格保留真实 `source_id` 与 `paragraph_id`，不伪造 PDF 页码）与 PDF（记录 `page` 与 `bbox`）；校验器核验 `minimal_evidence_quote` 为原文精确子串且长度精简，强制标注 6 类 `evidence_type` |
| **追得回** | 断言、证据、论文、作者、年份、版本与标识符链路完整 | 按标准 Nanopublication 四命名图结构（`Head` / `Assertion` / `Provenance` / `Pubinfo`）构建，计算基于内容规范化哈希的 **Trusty SHA-256 标识符（`urn:nanopub:RA...`）** 实现自动去重与溯源 |
| **读得懂** | 导出结构符合机器解析标准、无需人工清洗 | 同步输出 W3C `nanopublications.trig`（若容器安装 `rdflib` 自动做语法解析验真）、`nanopublications.jsonld`、`nanopublications.nq`、`nanopublications.jsonl` 与可读审计报告 `nanopub_audit_report.md` |

---

## 2. 目录结构与生态复用资源

```text
.
├── SKILL.md                               # Skill 入口（含 YAML 元信息与四步执行协议）
├── README.md                              # 完整文档、四图规范、质量门控说明与使用边界
├── GITHUB_URL.txt                         # 本作品公开 GitHub 子目录链接
├── resources/
│   ├── controlled_relations.json          # 17 个科研受控关系词表（映射 RO/SIO/SEPIO IRI 及反义冲突对）
│   └── field_dictionary.json              # Nanopublication 四图字段字典与质量门控阈值
├── templates/
│   ├── assertions_draft_schema.json       # 原子断言草稿输入模板
│   └── result_template.json               # 标准结果输出模板
├── examples/
│   ├── minimal_example.md                 # 最小可复现调用与 TriG 示意（明确标注为合成示例）
│   └── sample_20_assertions.json         # 20 条跨学科合成断言样例（覆盖催化、材料、生医、AI、物理、气候、阴性结果与引用区分）
└── scripts/
    ├── inspect_case.py                    # 案例与材料探针 + 初始诚实检查点生成
    ├── index_source_passages.py           # 多格式原文索引器（JATS/JSON + PDF + MD/TXT）
    ├── build_and_validate_nanopubs.py     # 6 项质量门控、去重、矛盾检测与多格式序列化器
    └── self_test.py                       # 正向抽取 + 负向门控拦截端到端自检脚本
```

---

## 3. 依赖与快速自检

- **必需依赖**：Python `>= 3.9`（纯标准库即可运行并导出合规 TriG / JSON-LD / N-Quads / JSONL）。
- **可选增强**：`pymupdf`（高精度 PDF 解析）、`rdflib`（额外执行第三方 TriG RDF 图语法校验）。

```bash
python scripts/self_test.py
```
