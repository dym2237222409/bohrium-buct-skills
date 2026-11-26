# 最小调用示例（Synthetic Example Only — 非正式测试案例）

> **声明**：以下所有文本、术语表与输出结果均为人工构造的合成示例（`_synthetic_example: true`），仅用于演示 `scipdf-academic-polisher` 的输入输出接口、7 道保真门禁与修改说明格式，不代表任何正式评测案例结果。

## 1. 示例输入（合成英文摘要片段）

- **目标体裁**：`journal_article`（同行评审期刊论文）
- **给定术语表**：`turnover frequency (TOF)`（禁止混用 `turn-over frequency` / `TOF rate`）
- **原稿段落**：
  - `S001`: `With the rapid development of heterogeneous catalysis, a lot of researchers studies single-atom catalysts [1,2].`
  - `S002`: `In this work, our turn-over frequency measurement results at 350 °C suggests that Fe-N4 sites may be associated with higher CO2 conversion (p < 0.05, Fig. 2).`

## 2. 调用命令

```bash
python scripts/inspect_and_segment.py --input work/sample_draft.md --register journal_article --out work/segmented.json
python scripts/audit_and_compile_polish.py --segmented work/segmented.json --edits work/edits_draft.json --resources resources --out-dir work/output
```

## 3. 示意输出（`_synthetic_example: true`）

### 3.1 润色后完整文本
> Recent advances in heterogeneous catalysis have prompted extensive investigation of single-atom catalysts [1,2]. In this work, turnover frequency (TOF) measurements at 350 °C suggest that Fe-N4 sites may be associated with higher CO2 conversion (p < 0.05, Fig. 2).

### 3.2 逐句修改说明对照

| 句号 | 原文 | 改文 | 修改类型 | 修改理由 | 门禁校验 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `S001` | With the rapid development of heterogeneous catalysis, a lot of researchers studies single-atom catalysts [1,2]. | Recent advances in heterogeneous catalysis have prompted extensive investigation of single-atom catalysts [1,2]. | `chinglish_restructure`, `register_tone`, `grammar_agreement` | 消除中式英语套话 `With the rapid development of` 与口语化 `a lot of`，修正主谓一致错误（`researchers studies`），同时完整保留引用 `[1,2]`。 | PASS (7/7) |
| `S002` | In this work, our turn-over frequency measurement results at 350 °C suggests that Fe-N4 sites may be associated with higher CO2 conversion (p < 0.05, Fig. 2). | In this work, turnover frequency (TOF) measurements at 350 °C suggest that Fe-N4 sites may be associated with higher CO2 conversion (p < 0.05, Fig. 2). | `terminology_consistency`, `abbreviation_standardization`, `grammar_agreement` | 按术语表将 `turn-over frequency` 统一为 `turnover frequency (TOF)` 并完成首现定义；修正主谓一致（`measurements ... suggest`）；严格保留 `350 °C`、`p < 0.05`、`Fig. 2` 及弱限定/相关性表述 `may be associated with`，未将结论拔高为因果或确证。 | PASS (7/7) |
