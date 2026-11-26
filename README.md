# Bohrium Product Arena · BUCT Campus Challenge Skills

公开收录北京化工大学分站赛（第一期：AI 文献阅读与学术写作）各赛题的可复用 Agent Skills、多后端解析/核验脚本、规则词表与最小可复现自检示例。所有作品均遵循 Harbor 技术提交约定（纯 UTF-8 文本、无预制正式测试答案、包含端到端合成自检脚本）。

## 作品目录索引（文献阅读赛道 5/5 全部完成 · 学术写作赛道推进中）

| 赛题编号 | 赛道与名称 | 作品目录 | 核心功能 |
|---|---|---|---|
| `product-arena-08-3027d350` | 阅读01｜文献文档解析 | [`submissions/product-arena-08-3027d350`](./submissions/product-arena-08-3027d350) | `scipdf-evidence-parser`：四级多后端容错、XY-Cut 双栏防串栏、页眉页脚与脚注隔离、跨页续表合并、公式符号保真、自适应扫描件 OCR 与原位 `bbox` 反幻觉核验 |
| `product-arena-01-5a9928c1` | 阅读03｜文献翻译 | [`submissions/product-arena-01-5a9928c1`](./submissions/product-arena-01-5a9928c1) | `scipdf-faithful-translator`：版面感知 1:1 分段对齐、多源术语表强制锁定与自动回填、数值/单位/引用/否定与限定极性核验、双语对照与人工复核清单生成 |
| `product-arena-06-6d8cf72b` | 阅读10｜文献总结与结构化笔记 | [`submissions/product-arena-06-6d8cf72b`](./submissions/product-arena-06-6d8cf72b) | `scipdf-structured-notes`：真实章节骨架对齐、成立前提条件绑定的可比论文卡片、细粒度 `P/TAB/FIG/EQ` 锚点解引用与数值核验、缩略语/同义知识点归并及多文献横向对比导出 |
| `product-arena-09-0e8befd4` | 阅读14｜Nanopublication | [`submissions/product-arena-09-0e8befd4`](./submissions/product-arena-09-0e8befd4) | `scipdf-nanopub-extractor`：原子可判真假断言拆解、最小充分原文证据与直接/间接类型绑定、6 项质量门控、Trusty SHA-256 去重与 W3C TriG/JSON-LD/N-Quads/JSONL 导出 |
| `product-arena-10-760d1a0b` | 阅读15｜文献知识图谱构建 | [`submissions/product-arena-10-760d1a0b`](./submissions/product-arena-10-760d1a0b) | `scipdf-kg-builder`：多篇独立文献元数据与 JATS 段落 ID 隔离、概念与物理样品防误合并对齐、保号增量更新、≥3 项真实子图查询与 GraphML/Cypher/JSON-LD/SVG 可视化导出 |
| `product-arena-02-7051d92d` | 写作02｜学术语言润色 | [`submissions/product-arena-02-7051d92d`](./submissions/product-arena-02-7051d92d) | `scipdf-academic-polisher`：最小编辑分句对齐、术语表冲突检测与缩略语首现追踪、7 项保真门禁（数值/单位/引用/公式/限定强度/因果相关/否定极性）与可编辑成品及逐句审校台账编译 |
| `product-arena-03-429cc14d` | 写作04｜参考文献与引用管理 | [`submissions/product-arena-03-429cc14d`](./submissions/product-arena-03-429cc14d) | `scipdf-citation-manager`：BibTeX/RIS/EndNote/CNKI 多源解析、四级可解释去重与同作者同年防误合并护栏、GB/T 7714-2015 与 APA 7th 双向同步重排及 6 道论断匹配/真实性审计门禁 |

## 快速验证任意 Skill

每个赛题目录均内置零外部私有数据依赖的端到端自检脚本：

```bash
python submissions/product-arena-08-3027d350/scripts/self_test.py
python submissions/product-arena-01-5a9928c1/scripts/self_test.py
python submissions/product-arena-06-6d8cf72b/scripts/self_test.py
python submissions/product-arena-09-0e8befd4/scripts/self_test.py
python submissions/product-arena-10-760d1a0b/scripts/self_test.py
python submissions/product-arena-02-7051d92d/scripts/self_test.py
python submissions/product-arena-03-429cc14d/scripts/self_test.py
```
