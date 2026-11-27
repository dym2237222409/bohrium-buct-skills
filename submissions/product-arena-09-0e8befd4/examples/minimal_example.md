# 最小调用示例（Minimal Reproducible Example）

> **重要声明（示例标注）**：本文件与 `examples/sample_20_assertions.json` 中展示的所有断言、URI、TriG 与 JSON-LD 片段均为**合成可复用示例（Synthetic Example）**，仅用于演示 `scipdf-nanopub-extractor` Skill 的四图结构、受控词表与校验逻辑，**不代表任何平台正式评测案例结果**。

---

## 1. 一键运行内置自检（同时覆盖 PDF 与 JATS/JSON 语料）

```bash
python /skills/weekly-skill/scripts/self_test.py --work-dir /tmp/nanopub_demo
```

---

## 2. Harbor 评测环境标准四步调用

```bash
# 步骤 1：探查 /input/case.json、语料清单与源文件，写入初始诚实检查点 /app/output/result.json
python /skills/weekly-skill/scripts/inspect_case.py \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output

# 步骤 2：对 PDF 或 JATS/JSON 语料建立段落/表格级原文索引（严格区分 PDF 页码与 JATS 段落 ID）
python /skills/weekly-skill/scripts/index_source_passages.py \
  --input-dir /input \
  --out-dir /app/work/indexed

# 步骤 3：（Agent 依据 indexed_passages.json 将复合结论拆解为原子断言并写入 /app/work/assertions_draft.json 后）
# 执行 6 项质量门控校验、去重与矛盾检测，并序列化导出 TriG / JSON-LD / N-Quads / JSONL 与审计报告
python /skills/weekly-skill/scripts/build_and_validate_nanopubs.py \
  --index-json /app/work/indexed/indexed_passages.json \
  --draft-json /app/work/assertions_draft.json \
  --case-json /input/case.json \
  --output-dir /app/output \
  --strict
```

---

## 3. W3C TriG 四图序列化示意（【示例数据 / EXAMPLE ONLY】）

```turtle
@prefix np: <http://www.nanopub.org/nschema#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix scirel: <https://w3id.org/scipdf/rel/> .
@prefix this: <urn:nanopub:example:NP001:> .

this:Head {
  this: a np:Nanopublication ;
    np:hasAssertion this:assertion ;
    np:hasProvenance this:provenance ;
    np:hasPublicationInfo this:pubinfo .
}

this:assertion {
  this:claim a scirel:AtomicAssertion ;
    scirel:subject "Zeo-Micro microchannel reactor" ;
    scirel:predicate scirel:achieves_metric ;
    scirel:object "96.1% product selectivity" ;
    scirel:conditions "wall temperature 260 C; laminar flow 12.5 mL/min" ;
    scirel:negated false .
}

this:provenance {
  this:assertion prov:wasDerivedFrom <doi:10.1000/synthetic.example.2025> ;
    scirel:sourceId "primary" ;
    scirel:locationRef "page=1,block=P0006" ;
    scirel:evidenceType "direct_experimental" ;
    scirel:minimalEvidenceQuote "Zeo-Micro achieves 96.1% selectivity at 260 C and 12.5 mL/min." .
}

this:pubinfo {
  this: dct:created "2026-11-26T00:00:00Z" ;
    dct:creator "scipdf-nanopub-extractor/1.0" ;
    scirel:contentSha256 "e3b0c44298fc1c149afbf4c8996fb924..." .
}
```
