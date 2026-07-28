# 01 — 配置形状重整：删 bundle、拍平仲裁、清死文件

**What to build:** 跑评价的人用 `--configs <目录> --task <task_id>` 启动评价，不再需要
先去改一个被 git 跟踪的文件来切任务；漏传 `--task` 时立刻报错并列出该目录下有哪些任务，
绝不静默沿用任何值。教研人员打开仲裁配置，看到的是两个有业务含义的数字，而不是一个
带类型分派、比较运算符和通配符白名单的规则引擎。打开 `configs/`，里面每个文件引擎都
真的会读。

这是第一批的全部内容，**不改变任何评价行为**：同样的输入应得出同样的分数，同样的分差
触发同样的观测点仲裁。

目标形状：

```
configs/
├── model_config.yaml
├── adjudication.yaml
├── prompts/{select,extraction,scoring,adjudication,feedback}.yaml
└── tasks/<task>/dimension/<dim_id>_rubric.yaml
```

新的仲裁配置全文：

```yaml
# 仲裁触发：满足任一条，该观测点就交 Rater3 重评
score_gap_threshold: 1     # 两位评委分差 > 此值 → 触发
drift_min_dimensions: 2    # 分差恰为 1 且同向的观测点数 ≥ 此值 → 这些观测点全部触发
```

`drift_gap` 固定为 1（"相邻"的定义），`require_same_direction` 固定为 true（"系统性同向
漂移"的定义）——前者转到 2 以上永远无效，因为差 ≥2 早被 `score_gap_threshold` 单独触发。

**Blocked by:** None — can start immediately

**Status:** done

- [x] `--configs`（默认 `configs`）与 `--task` 取代 `--bundle`；`--task` 无默认值
- [x] 漏传 `--task` 时报错，错误信息列出 `<configs_root>/tasks/` 下的可选任务
- [x] `bundle.yaml` 删除；仲裁策略、提示词、量规全部按约定路径解析
- [x] Engine 构造入口改为按 configs 根目录 + 任务 id 构造，`providers` 注入与产物目录参数不变
- [x] `configs/prompts/rater_scoring.yaml` 改名 `scoring.yaml`，使"提示词文件名 = 阶段名"成立
- [x] 仲裁配置迁至 `configs/adjudication.yaml`，内容只剩两个标量；`configs/policies/` 目录消失
- [x] 裁决模块删除比较运算符分派、维度白/黑名单匹配、触发器类型分派，直接读两个标量
- [x] 删除的死字段：`bundle_id`、`policy_id`、`raters.*`、`scoring.*`、`triggers[].{trigger_id,description,action,priority}`、`resolution_strategy.*`；规则意图以 YAML 注释形式保留
- [x] `configs/tasks/*/task_context.yaml` 删除（外环功能，属三期工作）
- [x] `configs/rubrics/rubric_schema.yaml` 删除（零引用手写模板，且已滞后：写的是 5 级量表、无 weight 字段）
- [x] `configs/rubrics/rubric.md` 移入 `docs/`；`configs/rubrics/` 目录消失
- [x] `docs/REFACTOR_DESIGN.md` 中描述 `task_context.yaml` 结构的一节删除
- [x] 端到端测试的临时 configs fixture 里两段写死的 bundle 内容删除
- [x] 全部既有测试绿；分数与仲裁触发结果与改动前完全一致

## Comments

落地时的两处超出验收项的顺带改动，记录备查：

1. `RunTraceSummary.bundle_ref` 改名 `configs_ref`（产物 `run_trace.json` 的 key 随之变）。
   bundle 已不存在，留一个名为 bundle_ref 的字段指向配置目录，正是本轮在消除的那种谎。
2. `scale.type` 一并删除（7 份量规 + compiler）。它在拆票时漏进了任何一张票——属于死字段
   清理，归到本票执行。佐证：`scale_id` 一直硬编码 `ordinal_` 前缀，写 `type: continuous`
   也不会改变任何行为。

`docs/REFACTOR_DESIGN.md` §7.1/§7.2/§8.1 描述的配置形状与 CLI 调用方式已整段过期
（`bundles/`、`chunking`、`parser`、`ConfigCompiler` 均早已删除），一并改到与现状一致。
该文档其余章节仍有大量滞后内容，未在本票处理。
