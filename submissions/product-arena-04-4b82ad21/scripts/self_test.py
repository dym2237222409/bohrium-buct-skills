#!/usr/bin/env python3
"""End-to-end synthetic self-test (positive + negative cases) for scipdf-intro-related-writer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
INSPECT_SCRIPT = SKILL_ROOT / "scripts" / "inspect_materials.py"
AUDIT_SCRIPT = SKILL_ROOT / "scripts" / "audit_and_compile_intro.py"


def run_py(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def build_synthetic_inputs(in_dir: Path) -> None:
    (in_dir / "case.json").write_text(
        json.dumps(
            {
                "case_id": "synthetic-intro-test-01",
                "language": "zh",
                "citation_style": "pandoc-key",
                "output_contract": {
                    "required_fields": ["case_id", "status", "manuscript", "sources", "uncertainty", "change_records"]
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (in_dir / "task.md").write_text("请根据提供文献与作者实验笔记，撰写论文引言与相关工作章节。", encoding="utf-8")
    (in_dir / "references.bib").write_text(
        """@article{zhang2022mem,
  author = {Zhang, Wei and Liu, Mei},
  title = {Polyimide-based membranes for flue gas CO2 capture},
  journal = {Journal of Membrane Science},
  year = {2022}
}
@article{li2023zeolite,
  author = {Li, Jun and Patel, R.},
  title = {Zeolite-filled mixed matrix membranes under dry feeds},
  journal = {AIChE Journal},
  year = {2023}
}
@article{wang2024hybrid,
  author = {Wang, Hao and Kim, S.},
  title = {Humidity-induced interfacial aging in MOF-polymer membranes},
  journal = {ACS Applied Materials & Interfaces},
  year = {2024}
}
@article{chen2025scale,
  author = {Chen, Lin and Zhao, Y.},
  title = {Crosslinked glassy polymer hollow fibers for industrial CO2 separation},
  journal = {Chemical Engineering Journal},
  year = {2025}
}
""",
        encoding="utf-8",
    )
    (in_dir / "author_notes.md").write_text(
        """# Author Draft Notes (Synthetic)
- Method: Dual-Channel Interface Crosslinking (DCIC)
- Table 2: CO2/N2 selectivity reaches 48.6 with CO2 permeance 112 GPU at 35 °C and 70% RH.
- Figure 4: 200 h continuous wet-gas stability test shows < 3.2% permeance decay.
""",
        encoding="utf-8",
    )


def build_clean_plan() -> dict:
    return {
        "case_id": "synthetic-intro-test-01",
        "language": "zh",
        "citation_style": "pandoc-key",
        "target_format": "both",
        "background_claims": [
            {
                "claim_id": "BG-1",
                "statement": "烟气 CO2/N2 膜分离面临高湿工况下的渗透通量-选择性权衡及界面老化约束。",
                "evidence_type": "cited_fact",
                "source_ids": ["zhang2022mem", "wang2024hybrid"],
                "locator": "references.bib",
            }
        ],
        "related_work_groups": [
            {
                "group_id": "G1",
                "group_title": "聚合物本体交联与链段调控路线",
                "grouping_axis": "method_route",
                "comparison_dimensions": ["渗透-选择性权衡", "湿气稳定性"],
                "works": [
                    {
                        "ref_id": "zhang2022mem",
                        "core_method": "聚酰亚胺本体热交联",
                        "key_finding": "提升了干燥条件下的气体选择性",
                        "applicable_condition": "干燥或低湿烟气",
                        "limitation": "交联密度增加导致通量明显下降",
                        "source_locator": "references.bib:zhang2022mem",
                    },
                    {
                        "ref_id": "chen2025scale",
                        "core_method": "玻璃态中空纤维交联",
                        "key_finding": "具备工业放大加工性",
                        "applicable_condition": "常规中温分离",
                        "limitation": "仍受限于聚合物固有自由体积上限",
                        "source_locator": "references.bib:chen2025scale",
                    },
                ],
                "group_synthesis": "聚合物本体改性路线虽具放大优势，但普遍面临交联降通量的固有权衡。",
            },
            {
                "group_id": "G2",
                "group_title": "多孔填料杂化基质膜路线",
                "grouping_axis": "method_route",
                "comparison_dimensions": ["界面相容性", "高湿长时稳定性"],
                "works": [
                    {
                        "ref_id": "li2023zeolite",
                        "core_method": "分子筛填充杂化膜",
                        "key_finding": "在干气条件下显著突破通量瓶颈",
                        "applicable_condition": "干燥进料工况",
                        "limitation": "高负载易引发填料团聚与非选择性界面空隙",
                        "source_locator": "references.bib:li2023zeolite",
                    },
                    {
                        "ref_id": "wang2024hybrid",
                        "core_method": "MOF-聚合物杂化膜",
                        "key_finding": "揭示了湿气诱导界面老化的衰减机制",
                        "applicable_condition": "高湿混合气",
                        "limitation": "水分子竞争吸附导致界面结合退化与性能衰减",
                        "source_locator": "references.bib:wang2024hybrid",
                    },
                ],
                "group_synthesis": "相比聚合物本体路线，杂化膜可提高初始通量，但在高湿环境下易发生界面失效。",
            },
        ],
        "gap_derivations": [
            {
                "gap_id": "GAP-1",
                "derived_from_refs": ["li2023zeolite", "wang2024hybrid"],
                "limitation_or_conflict": "已有杂化膜在高负载界面缺陷与高湿水汽老化方面存在共同局限，而聚合物本体交联路线又难以兼顾高通量。",
                "gap_statement": "当前缺少一种能在 70% RH 高湿工况下同时抑制填料界面空隙并维持高 CO2 渗透通量的界面构筑策略。",
                "how_this_work_addresses": "本文提出双通道界面交联（DCIC）策略，在填料-基质界面形成疏水稳定交联网络。",
                "evidence_status": "verified",
            }
        ],
        "contributions": [
            {
                "contrib_id": "C1",
                "statement": "提出双通道界面交联（DCIC）构筑方法，在 35 °C、70% RH 条件下实现 CO2/N2 选择性 48.6 与渗透通量 112 GPU（见 Table 2）。",
                "supporting_evidence": ["author_notes.md:Table 2"],
                "numeric_claims": ["35", "70", "48.6", "112"],
                "scope_boundary": "经验证适用于 35 °C、70% RH 实验工况。",
                "verification_status": "verified",
            },
            {
                "contrib_id": "C2",
                "statement": "完成 200 h 连续湿气稳定性验证，渗透通量衰减低于 3.2%（见 Figure 4）。",
                "supporting_evidence": ["author_notes.md:Figure 4"],
                "numeric_claims": ["200", "3.2"],
                "scope_boundary": "基于 200 h 实验室连续测试，工业更长周期运行尚待进一步验证。",
                "verification_status": "verified",
            },
        ],
        "prose_sections": {
            "background": "在燃煤与化工烟气 CO2/N2 膜分离过程中，高湿进气条件下的渗透通量-选择性权衡以及长期界面老化是制约工程应用的核心技术瓶颈 [@zhang2022mem; @wang2024hybrid]。现有体系往往需要在干燥高选择性与高湿高通量之间做出取舍，难以直接满足真实高湿烟气的持续分离需求。",
            "related_work": "现有研究主要沿两条技术路线展开。其一为聚合物本体交联路线 [@zhang2022mem; @chen2025scale]，该类方法依托成熟纺丝工艺改善了选择性与放大可行性，然而交联网络致密化不可避免地压缩自由体积、牺牲通量。相比之下，多孔填料杂化基质路线 [@li2023zeolite; @wang2024hybrid] 通过引入分子筛或 MOF 通道提升干气渗透率，但在高负载条件下的界面缺陷以及高湿环境中的水汽竞争吸附导致界面快速退化。",
            "research_gap": "综合上述两类路线的对比可见，[@li2023zeolite] 的界面团聚与 [@wang2024hybrid] 揭示的高湿界面老化构成了当前杂化膜走向高湿烟气应用的核心研究空白。为此，本文提出双通道界面交联（DCIC）方法，以兼顾高通量传输与界面耐湿稳定。",
            "contributions": "本文主要贡献如下：（1）提出双通道界面交联策略，在 35 °C、70% RH 条件下取得 48.6 的 CO2/N2 选择性与 112 GPU 渗透通量（Table 2）；（2）在 200 h 连续湿气测试中将通量衰减控制在 3.2% 以内（Figure 4），为高湿烟气膜分离提供可复核实验依据。",
        },
        "uncertainties": [
            {
                "item": "工业烟气中 SOx/NOx 杂质对 DCIC 膜的长期影响未在当前材料中提供。",
                "category": "scope_boundary",
                "action_needed": "作者确认是否在讨论章节补充抗酸性气体限制说明。",
            }
        ],
        "change_records": [
            {
                "section": "all",
                "change": "按两条技术路线重构相关工作并绑定全部贡献至 Table 2 与 Figure 4。",
                "reason": "满足立得住、分得清、说得实、对得上四项核心写作要求。",
            }
        ],
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        in_dir = root / "input"
        work_dir = root / "work"
        out_ok = root / "output_ok"
        out_bad = root / "output_bad"
        in_dir.mkdir()
        work_dir.mkdir()

        build_synthetic_inputs(in_dir)

        # 1. Inspect materials
        manifest_path = work_dir / "material_manifest.json"
        p1 = run_py(INSPECT_SCRIPT, "--input-dir", str(in_dir), "--out", str(manifest_path))
        assert p1.returncode == 0, f"inspect failed: {p1.stderr}"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["literature_stats"]["total_refs"] == 4, "Expected 4 parsed BibTeX refs"
        assert "Table 2" in manifest["author_evidence_index"]["all_tables"]

        # 2. Positive test: clean plan must PASS all 7 gates
        clean_plan_path = work_dir / "clean_plan.json"
        clean_plan_path.write_text(json.dumps(build_clean_plan(), ensure_ascii=False, indent=2), encoding="utf-8")
        p2 = run_py(
            AUDIT_SCRIPT,
            "--manifest",
            str(manifest_path),
            "--plan",
            str(clean_plan_path),
            "--output-dir",
            str(out_ok),
        )
        assert p2.returncode == 0, f"clean audit unexpectedly failed: {p2.stdout} {p2.stderr}"
        res_ok = json.loads((out_ok / "result.json").read_text(encoding="utf-8"))
        assert res_ok["status"] == "completed"
        assert res_ok["summary"]["audit_passed"] is True
        assert res_ok["summary"]["verified_citations_used"] == 4
        assert (out_ok / "introduction_and_related_work.md").exists()
        assert (out_ok / "introduction_and_related_work.tex").exists()
        assert (out_ok / "comparison_matrix.md").exists()
        assert (out_ok / "evidence_traceability.json").exists()
        assert (out_ok / "review_checklist.md").exists()

        # 3. Negative test: inject cliché + fabricated citation + ungrounded number + overclaim + single group
        bad_plan = build_clean_plan()
        bad_plan["prose_sections"]["background"] = "近年来，膜分离技术受到广泛关注，本文首次彻底解决了所有分离难题 [@ghost2026fake]。"
        bad_plan["related_work_groups"] = bad_plan["related_work_groups"][:1]
        bad_plan["contributions"][0]["statement"] = "选择性高达 999.9（Table 2）。"
        bad_plan_path = work_dir / "bad_plan.json"
        bad_plan_path.write_text(json.dumps(bad_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        p3 = run_py(
            AUDIT_SCRIPT,
            "--manifest",
            str(manifest_path),
            "--plan",
            str(bad_plan_path),
            "--output-dir",
            str(out_bad),
        )
        assert p3.returncode == 1, "Expected negative test to fail audit gates"
        res_bad = json.loads((out_bad / "result.json").read_text(encoding="utf-8"))
        gates_bad = res_bad["audit_gates"]
        assert gates_bad["gate2_cliche_and_opener_lint"]["passed"] is False
        assert gates_bad["gate3_thematic_grouping_anti_listing"]["passed"] is False
        assert gates_bad["gate5_contribution_evidence_alignment"]["passed"] is False
        assert gates_bad["gate6_citation_integrity_coverage"]["passed"] is False
        assert gates_bad["gate7_traceability_and_honesty"]["passed"] is False

    print("SELF_TEST_PASSED: scipdf-intro-related-writer (positive + negative 7-gate checks verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
