# `scipdf-methods-experiments-writer` — 方法与实验章节循证写作与参数对齐 Skill

面向学术论文**方法（Methods）与实验（Experiments）章节**起草、补全与复现核验的可复用 Agent Skill，聚焦四项核心指标：**写得全（六类要素齐全）**、**写得准（与配置/代码/日志严格一致，零编造数值）**、**做得着（可执行步骤与复现清单）**、**说得住（显式缺口追问与具体失效场景限制）**。

## 1. 目录结构

```text
product-arena-05-64faa4a2/
├── SKILL.md                                    # Agent 执行主规范（YAML frontmatter + 7 道门禁闭环）
├── README.md                                   # 本说明文档
├── GITHUB_URL.txt                              # 公开仓库地址声明
├── resources/
│   ├── six_elements_checklist.json             # 六大要素核对规范与套话限制拦截模式
│   └── config_extraction_patterns.json         # 多格式配置/代码/日志参数与环境抽取规则
├── templates/
│   ├── methods_plan_schema.json                # 结构化写作计划（methods_plan.json）契约
│   └── result_template.json                    # Harbor 评测 result.json 输出模板
├── examples/
│   └── minimal_example.md                      # 显式标注的合成最小调用与输出示例
└── scripts/
    ├── extract_experiment_facts.py             # 多格式配置/代码/日志事实抽取与跨文件冲突检测器
    ├── audit_and_compile_methods.py            # 7 道确定性核验门禁与多产物编译器
    └── self_test.py                            # 正向 + 负向端到端合成自检脚本
```

## 2. 环境依赖

- **必需依赖**：Python 3.8+（100% 纯标准库，零外部第三方包依赖，离线容器开箱即用）。

## 3. 快速调用步骤

```bash
# 1. 从配置、脚本、日志、数据说明中抽取带 file:line 溯源的实验事实与冲突清单
python3 scripts/extract_experiment_facts.py \
  --input-dir /input \
  --out /app/work/experiment_facts.json

# 2. Agent 基于 experiment_facts.json 撰写结构化计划 /app/work/methods_plan.json
#    （遵循 templates/methods_plan_schema.json）

# 3. 执行 7 道确定性核验门禁并编译最终产物
python3 scripts/audit_and_compile_methods.py \
  --facts /app/work/experiment_facts.json \
  --plan /app/work/methods_plan.json \
  --output-dir /app/output
```

### 输出产物清单（`/app/output/`）

| 文件 | 用途 |
|---|---|
| `methods_and_experiments.md` | 可直接并入论文的方法与实验章节成稿（可选同步导出 `.tex`） |
| `parameter_table.md` | 带名称、取值、单位、作用对象、来源（`file:line`）与状态的参数总表 |
| `reproducibility_and_questions.md` | 复现清单、跨文件配置冲突说明、7 道门禁审计结果与作者追问清单 |
| `fact_provenance.json` | 机器可读的实验要素-源文件溯源映射 |
| `result.json` | 完整保留 `case_id` 与原始 `output_contract` 字段的结构化汇总结果 |

## 4. 核心能力与边界

- **跨文件冲突自动发现**：当默认配置文件（如 `config.yaml`）与实际运行日志或启动脚本（如 `train.log` / `run.sh`）中的超参不一致时，自动检出并强制要求在正文中显式披露或向作者确认，防止参数“静默失真”。
- **正文-参数表双向防漂移**：校验正文叙述中出现的超参数值是否与 `parameter_table` 及原始事实库一致，杜绝“为了段落顺畅随手补数或改数”。
- **限制条件反套话检验**：拦截“未来工作将进一步提升性能”类空泛套话，要求限制条件绑定具体失效场景与适用边界。
- **已知限制**：不会运行待测科研代码或重新训练模型；若原始材料缺少随机种子、数据集版本号或硬件型号，脚本会显式输出 `[MISSING_FROM_MATERIALS]` 并生成作者确认问题单。

## 5. 端到端自检

```bash
python3 scripts/self_test.py
```

自检构造了包含 `config.yaml` 与 `train.log` 学习率冲突且缺失数据集版本的合成工程，验证抽取器冲突发现、负向审计拦截（编造种子、未披露冲突、正文与表格不一致、空泛限制）及正向 7 道门禁全绿通过。
