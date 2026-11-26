# 最小调用示例（Synthetic Example Only — 非正式测试案例）

> **声明**：以下稿件、文献库与输出均为人工构造的合成示例（`_synthetic_example: true`），仅用于演示 `scipdf-citation-manager` 的多源去重、同步重排、格式转换与核验审计流程，不代表任何正式测试案例结果。

## 1. 示例输入（合成多源文献库 + 稿件）

- **文献库 A（BibTeX）**：含 `Zhang2023_journal`（DOI `10.1016/j.apcatb.2023.122001`）、`Zhang2023_second`（同作者同年另一篇，DOI `10.1021/acscatal.2023.99999`）
- **文献库 B（RIS）**：含 `Zhang2022_arxiv`（同题预印本 `arXiv:2211.01234`，应与 `Zhang2023_journal` 归并，保留正式版为主记录）
- **原稿句**：`单原子铁催化剂在酸性介质中表现出较高的氧还原活性[2]，而双原子位点可进一步降低过电位[1]。`（编号顺序倒置，且需同步插入第 3 篇综述引用并转为 GB/T 7714—2015 顺序编码制）

## 2. 调用命令

```bash
python scripts/parse_bib_and_manuscript.py --case work/case.json --out work/parsed.json
python scripts/deduplicate_refs.py --parsed work/parsed.json --rules resources/dedup_rules.json --out work/deduped.json
python scripts/sync_format_and_verify.py --parsed work/parsed.json --deduped work/deduped.json --plan work/citation_plan.json --styles resources/citation_styles.json --out-dir work/output
```

## 3. 示意输出（`_synthetic_example: true`）

- **去重结果**：
  - `REF001` 合并 `Zhang2023_journal` + `Zhang2022_arxiv`（依据：`Tier2_PreprintPublishedMatch`，标题相似度 `1.00`，优先保留期刊正式版并记录 `arXiv:2211.01234`）。
  - `REF001` 与 `REF002`（`Zhang2023_second`）记入 `kept_separate_audit`（依据：`distinct_valid_doi` + 同作者同年不同论文，严禁误合并）。
- **同步重排后的正文（GB/T 7714—2015）**：
  > 单原子铁催化剂在酸性介质中表现出较高的氧还原活性[1]，而双原子位点可进一步降低过电位[2]。近期综述系统总结了上述两类位点的稳定性边界[1-3]。
- **同步生成的文末参考文献列表（GB/T 7714—2015）**：
  1. `[1] ZHANG W, LI M. Single-atom Fe-N4 catalysts for acidic oxygen reduction[J]. Applied Catalysis B: Environmental, 2023, 325: 122001. DOI: 10.1016/j.apcatb.2023.122001.`
  2. `[2] ZHANG W, CHEN Y. Dual-atom Fe-Co sites lowering ORR overpotential[J]. ACS Catalysis, 2023, 13(8): 5120-5132. DOI: 10.1021/acscatal.2023.99999.`
  3. `[3] 王明, 李华. 酸性氧还原单原子催化剂稳定性研究进展[J]. 催化学报, 2024, 45(2): 110-125.`
