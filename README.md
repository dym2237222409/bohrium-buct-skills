# Bohrium Product Arena · BUCT Campus Challenge Skills

公开收录北京化工大学分站赛（第一期：AI 文献阅读与学术写作）各赛题的可复用 Agent Skills、多后端解析/核验脚本、规则词表与最小可复现自检示例。所有作品均遵循 Harbor 技术提交约定（纯 UTF-8 文本、无预制正式测试答案、包含端到端合成自检脚本）。

## 作品目录索引

| 赛题编号 | 赛道与名称 | 作品目录 | 核心功能 |
|---|---|---|---|
| `product-arena-08-3027d350` | 阅读01｜文献文档解析 | [`submissions/product-arena-08-3027d350`](./submissions/product-arena-08-3027d350) | `scipdf-evidence-parser`：四级多后端容错、XY-Cut 双栏防串栏、页眉页脚与脚注隔离、跨页续表合并、公式符号保真、自适应扫描件 OCR 与原位 `bbox` 反幻觉核验 |
| `product-arena-01-5a9928c1` | 阅读03｜文献翻译 | [`submissions/product-arena-01-5a9928c1`](./submissions/product-arena-01-5a9928c1) | `scipdf-faithful-translator`：版面感知 1:1 分段对齐、多源术语表强制锁定与自动回填、数值/单位/引用/否定与限定极性核验、双语对照与人工复核清单生成 |
| `product-arena-06-6d8cf72b` | 阅读10｜文献总结与结构化笔记 | [`submissions/product-arena-06-6d8cf72b`](./submissions/product-arena-06-6d8cf72b) | `scipdf-structured-notes`：真实章节骨架对齐、成立前提条件绑定的可比论文卡片、细粒度 `P/TAB/FIG/EQ` 锚点解引用与数值核验、缩略语/同义知识点归并及多文献横向对比导出 |

## 快速验证任意 Skill

每个赛题目录均内置零外部私有数据依赖的端到端自检脚本：

```bash
python submissions/product-arena-08-3027d350/scripts/self_test.py
python submissions/product-arena-01-5a9928c1/scripts/self_test.py
python submissions/product-arena-06-6d8cf72b/scripts/self_test.py
```
