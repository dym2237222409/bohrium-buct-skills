#!/usr/bin/env python3
"""End-to-end synthetic self-test (positive + negative cases) for scipdf-methods-experiments-writer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
EXTRACT_SCRIPT = SKILL_ROOT / "scripts" / "extract_experiment_facts.py"
AUDIT_SCRIPT = SKILL_ROOT / "scripts" / "audit_and_compile_methods.py"


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
                "case_id": "synthetic-methods-test-01",
                "language": "zh",
                "output_contract": {
                    "required_fields": ["case_id", "status", "manuscript", "sources", "uncertainty", "change_records"]
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (in_dir / "task.md").write_text("根据以下配置、日志与实验记录撰写方法与实验章节。", encoding="utf-8")
    (in_dir / "config.yaml").write_text(
        """# Synthetic default config
learning_rate: 1e-4
batch_size: 32
epochs: 50
optimizer: AdamW
weight_decay: 0.01
seed: 42
""",
        encoding="utf-8",
    )
    (in_dir / "run_train.log").write_text(
        """[INFO] Hardware: NVIDIA A100 80GB
[INFO] Software: Python 3.10.12, PyTorch 2.2.1, CUDA 12.1
[INFO] Launch command:
python train.py --lr 3e-4 --batch_size 32 --epochs 50 --seeds 42,43,44
[INFO] Repeated 3 runs; evaluation metric: MAE and R2; paired t-test p < 0.01
""",
        encoding="utf-8",
    )
    (in_dir / "data_notes.md").write_text(
        """# Dataset & Baseline Notes (Synthetic)
- Dataset: CatYield-10k (10240 entries, RDKit canonical SMILES preprocessing, 80/10/10 stratified split).
- Baseline: GNN-Base (commit a1b2c3d), trained under identical 50-epoch and single-A100 budget.
- Observed failure: accuracy drops sharply on multi-metal catalysts with > 4 coordination sites.
""",
        encoding="utf-8",
    )


def build_clean_plan() -> dict:
    return {
        "case_id": "synthetic-methods-test-01",
        "language": "zh",
        "target_format": "both",
        "method_steps": [
            {
                "step_id": "S1",
                "name": "分子图标准化预处理",
                "inputs": "CatYield-10k 原始反应 SMILES",
                "operation": "使用 RDKit 规范化 SMILES 并构建原子-化学键特征图",
                "outputs": "标准化分子图样本（10240 条）",
                "decision_or_stop_rule": "剔除无法解析的非法 SMILES",
                "source_locator": "data_notes.md:2",
            },
            {
                "step_id": "S2",
                "name": "模型训练与早停选择",
                "inputs": "80% 训练集与 10% 验证集",
                "operation": "使用 AdamW（weight_decay=0.01，batch_size=32，learning_rate=3e-4）训练 50 epochs",
                "outputs": "验证集 MAE 最优模型权重",
                "decision_or_stop_rule": "达到 50 epochs 终止并选取验证集最低 MAE 权重",
                "source_locator": "run_train.log:4; config.yaml:3-5",
            },
        ],
        "experimental_setup": {
            "training": "在 80% 训练集上使用 AdamW 优化器训练 50 epochs，batch_size=32，实际运行学习率为 3e-4（覆盖默认配置 1e-4）。",
            "validation": "在 10% 验证集上监控 MAE 以挑选最优检查点，不参与梯度更新。",
            "testing": "固定最优检查点在独立 10% 测试集上计算 MAE 与 R2，跨 3 个随机种子（42, 43, 44）汇报均值与配对 t 检验。",
            "hardware": "NVIDIA A100 80GB",
            "software_versions": ["Python 3.10.12", "PyTorch 2.2.1", "CUDA 12.1"],
            "random_seeds": "42, 43, 44（实际运行）；config.yaml 默认值为 42",
            "repeat_count": "3 次独立重复运行",
            "source_locators": ["run_train.log:1-5", "config.yaml:2-7"],
        },
        "parameter_table": [
            {
                "name": "learning_rate",
                "canonical_key": "learning_rate",
                "value": "3e-4",
                "unit": "-",
                "target_component": "AdamW Optimizer",
                "source_locator": "run_train.log:4 (overrides config.yaml:2=1e-4)",
                "verification_status": "conflict_resolved",
            },
            {
                "name": "batch_size",
                "canonical_key": "batch_size",
                "value": "32",
                "unit": "samples",
                "target_component": "DataLoader",
                "source_locator": "config.yaml:3; run_train.log:4",
                "verification_status": "verified",
            },
            {
                "name": "epochs",
                "canonical_key": "epochs",
                "value": "50",
                "unit": "epochs",
                "target_component": "Trainer",
                "source_locator": "config.yaml:4; run_train.log:4",
                "verification_status": "verified",
            },
            {
                "name": "weight_decay",
                "canonical_key": "weight_decay",
                "value": "0.01",
                "unit": "-",
                "target_component": "AdamW Optimizer",
                "source_locator": "config.yaml:6",
                "verification_status": "verified",
            },
        ],
        "datasets": [
            {
                "name": "CatYield-10k",
                "version": "[MISSING_FROM_MATERIALS: dataset_version]",
                "size": "10240 reactions",
                "preprocessing": "RDKit canonical SMILES",
                "split_strategy": "80/10/10 stratified split",
                "subset_roles": "80% 训练、10% 验证选模、10% 独立测试",
                "source_locator": "data_notes.md:2",
            }
        ],
        "baselines": [
            {
                "name": "GNN-Base",
                "version": "commit a1b2c3d",
                "same_budget_verified": "true (identical 50-epoch & single-A100 budget)",
                "metrics": "MAE, R2",
                "statistical_test": "paired t-test (p < 0.01 across 3 seeds)",
                "source_locator": "data_notes.md:3; run_train.log:5",
            }
        ],
        "reproducibility_checklist": {
            "code_version": "[MISSING_FROM_MATERIALS: main_repo_commit_hash]",
            "environment": "NVIDIA A100 80GB; Python 3.10.12; PyTorch 2.2.1; CUDA 12.1",
            "commands": ["python train.py --lr 3e-4 --batch_size 32 --epochs 50 --seeds 42,43,44"],
            "seeds": "42, 43, 44",
            "data_access": "[MISSING_FROM_MATERIALS: CatYield-10k_download_url_or_DOI]",
        },
        "limitations": [
            {
                "failure_regime": "多金属配位位点超过 4 个的复杂催化体系预测误差显著放大",
                "trigger_condition": "配位位点数 > 4 且训练集中对应拓扑稀疏",
                "applicable_scope": "主要适用于单/双金属且配位位点 <= 4 的反应体系",
                "source_basis": "data_notes.md:4",
            }
        ],
        "conflict_resolutions": [
            {
                "canonical_key": "learning_rate",
                "conflicting_values": ["1e-4", "3e-4"],
                "resolution": "运行日志 run_train.log:4 显示命令行显式传入 --lr 3e-4 覆盖 config.yaml:2 默认值 1e-4；正文采用实际运行值 3e-4 并请作者复核。",
                "adopted_value": "3e-4",
            },
            {
                "canonical_key": "seed",
                "conflicting_values": ["42", "42,43,44"],
                "resolution": "config.yaml:7 记录单种子 42，而 run_train.log:4 执行了 3 种子重复实验 42,43,44；正文采用实际多运行种子列表。",
                "adopted_value": "42,43,44",
            },
        ],
        "author_questions": [
            {
                "element": "datasets.version",
                "gap_or_conflict": "CatYield-10k 未提供具体发布版本号及数据获取链接",
                "question": "请补充 CatYield-10k 的精确版本号、发布日期或 DOI/下载链接。",
            },
            {
                "element": "learning_rate",
                "gap_or_conflict": "config.yaml (1e-4) 与 run_train.log (3e-4) 存在冲突",
                "question": "请确认最终汇报结果是否严格对应 run_train.log 中的 3e-4。",
            },
        ],
        "prose_sections": {
            "method_steps": "本方法按两阶段执行：首先对 CatYield-10k 的 10240 条反应 SMILES 使用 RDKit 规范化并构建分子图特征；随后以验证集 MAE 为选择准则，采用 AdamW 优化器训练 50 epochs 并输出最优模型权重。",
            "experimental_setup": "训练、验证与测试严格分离。所有实验在单卡 NVIDIA A100 80GB 上基于 Python 3.10.12、PyTorch 2.2.1 与 CUDA 12.1 执行。实际运行参数为 batch_size = 32、epochs = 50、weight_decay = 0.01、learning_rate = 3e-4（覆盖 config.yaml 默认值 1e-4），并在随机种子 42, 43, 44 上重复 3 次。",
            "datasets_and_splits": "实验基于 CatYield-10k 数据集（10240 条反应，具体版本号待作者确认 [MISSING_FROM_MATERIALS: dataset_version]），按 80/10/10 分层划分为训练集、验证集与独立测试集。",
            "baselines_and_evaluation": "对照模型选取 GNN-Base（commit a1b2c3d），在相同 50 epochs 与单卡 A100 算力预算下对比 MAE 与 R2，并基于 3 次重复实验开展配对 t 检验（p < 0.01）。",
            "reproducibility_and_limitations": "实验可通过 `python train.py --lr 3e-4 --batch_size 32 --epochs 50 --seeds 42,43,44` 复现（主仓库 commit 与数据获取链接待作者补充）。本方法在配位位点数超过 4 个的多金属催化体系上误差显著增大，适用范围限定于单/双金属且配位位点不超过 4 的反应体系。",
        },
        "change_records": [
            {
                "section": "all",
                "change": "抽取配置与日志参数、披露 learning_rate 与 seed 跨文件冲突，并生成作者确认问题单。",
                "reason": "保证参数可溯源、正文与表格零漂移、缺失要素不编造。",
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

        # 1. Extract facts and conflicts
        facts_path = work_dir / "experiment_facts.json"
        p1 = run_py(EXTRACT_SCRIPT, "--input-dir", str(in_dir), "--out", str(facts_path))
        assert p1.returncode == 0, f"extract failed: {p1.stderr}"
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        conflict_keys = {c["canonical_key"] for c in facts["cross_file_conflicts"]}
        assert "learning_rate" in conflict_keys, f"Expected learning_rate conflict, got {conflict_keys}"

        # 2. Positive test: clean plan must PASS all 7 gates
        clean_path = work_dir / "clean_plan.json"
        clean_path.write_text(json.dumps(build_clean_plan(), ensure_ascii=False, indent=2), encoding="utf-8")
        p2 = run_py(AUDIT_SCRIPT, "--facts", str(facts_path), "--plan", str(clean_path), "--output-dir", str(out_ok))
        assert p2.returncode == 0, f"clean audit failed: {p2.stdout} {p2.stderr}"
        res_ok = json.loads((out_ok / "result.json").read_text(encoding="utf-8"))
        assert res_ok["summary"]["audit_passed"] is True
        assert (out_ok / "methods_and_experiments.md").exists()
        assert (out_ok / "methods_and_experiments.tex").exists()
        assert (out_ok / "parameter_table.md").exists()
        assert (out_ok / "reproducibility_and_questions.md").exists()
        assert (out_ok / "fact_provenance.json").exists()

        # 3. Negative test: fabricated parameter + prose/table drift + hidden conflict + boilerplate limitation
        bad_plan = build_clean_plan()
        bad_plan["parameter_table"].append(
            {
                "name": "dropout",
                "canonical_key": "dropout",
                "value": "0.77",
                "unit": "-",
                "target_component": "MLP",
                "source_locator": "config.yaml:99",
                "verification_status": "verified",
            }
        )
        bad_plan["prose_sections"]["experimental_setup"] += " 其中 batch_size = 128。"
        bad_plan["conflict_resolutions"] = []
        bad_plan["author_questions"] = []
        bad_plan["limitations"][0]["failure_regime"] = "未来工作将进一步提升性能。"
        bad_path = work_dir / "bad_plan.json"
        bad_path.write_text(json.dumps(bad_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        p3 = run_py(AUDIT_SCRIPT, "--facts", str(facts_path), "--plan", str(bad_path), "--output-dir", str(out_bad))
        assert p3.returncode == 1, "Expected negative test to fail"
        gates_bad = json.loads((out_bad / "result.json").read_text(encoding="utf-8"))["audit_gates"]
        assert gates_bad["gate2_parameter_source_grounding"]["passed"] is False
        assert gates_bad["gate3_prose_table_consistency"]["passed"] is False
        assert gates_bad["gate4_cross_file_conflict_disclosure"]["passed"] is False
        assert gates_bad["gate7_concrete_limitation_lint"]["passed"] is False

    print("SELF_TEST_PASSED: scipdf-methods-experiments-writer (conflict detection + 7-gate positive/negative verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
