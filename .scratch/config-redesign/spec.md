# configs/ 重设计：字段整肃 + 术语对齐 + 静默降级封堵

Status: ready-for-agent

> 本 spec 是一次 grilling 会话逐项拍板的结果（12 个决策点）。它是
> `.scratch/config-hygiene/spec.md` 的续篇——上一轮只整肃了 `model_config.yaml`
> 与 `.env`，并把 `task_context.yaml` 挂成悬案。这一轮处理 `configs/` 其余全部内容。

## Problem Statement

`configs/` 目录里近一半字段是死的——写在 YAML 里，没有任何代码读取。这带来三类实际伤害：

1. **配置在描述一个不存在的系统。** 有人照着 `resolution_strategy.fallback_if_no_resolution:
   "average_of_raters"` 去理解仲裁行为，会得出完全错误的结论——代码在缺 Rater3 时是直接
   报错，不是取平均。`scoring.allow_decimal_scores: true` 同理：分数结构上就是整数，
   非 int 直接 `TypeError`。配置不是过时，是**在说谎**。

2. **量规作者的意图没有被兑现。** 7 份 rubric 每份都精心写了各观测点的 `weight`
   （F2 是 0.4/0.6，D3 是 0.25/0.25/0.3/0.2），而聚合代码写死等权平均。配置说一件事，
   代码做另一件事。同样地，`indicator_description`（dim 的完整解释，量规的核心资产）
   被读进内存后没有任何消费方，模型从头到尾看不到它。

3. **术语和量规原文错位了整整一级。** 量规原文 `rubric.md` 里，A/B/C/D/E/F 是一级指标、
   A1/A3/F2 是二级指标；而代码和文档把 `dim_id: a1` 叫"一级指标"、把 `code: A1-1` 叫
   "二级指标"。教师拿着量规说"A1 是二级指标"，系统文档说"a1 是一级指标"，沟通必然出错。

此外还有一个隐蔽的洞：**rubric 文件缺字段时系统静默降级**。缺 `anchors` → prompt 里
一条锚点都没有，模型在没有量规的情况下打分，跑完、出分、产物上完全看不出来；缺
`scale.max` → 静默回落到 5（真实量规是 4 级），模型可以打出量表外的分数。前端量规配置页
（长远规划）一旦上线，表单漏填就会直接踩中这个洞。

## Solution

按一条统一判据重设计 `configs/`：**默认删，只有能说出「补了会更准 / 更安全」的才补。**

结果是一个每个字段都有代码读、每个行为都有配置对应的目录：

```
configs/
├── model_config.yaml              # providers + runtime（新增 context_budget_tokens）
├── adjudication.yaml              # 两个数字，取代原来的 triggers 规则引擎
├── prompts/{select,extraction,scoring,adjudication,feedback}.yaml
└── tasks/<task>/dimension/<dim_id>_rubric.yaml
```

消失的：`bundle.yaml`、`policies/`、`rubrics/`、`tasks/*/task_context.yaml`。

同时兑现三件量规作者早已表达、但代码一直没做的事：权重生效、指标解释进 prompt、
档位标签进锚点；并在量规加载处装上结构校验，堵死静默降级。

## User Stories

### 配置使用者（教研人员 / 运维）

1. 作为教研人员，我希望 `configs/` 里每个字段都真的在影响系统行为，这样我改配置时不会
   改了个寂寞。
2. 作为教研人员，我希望调整仲裁触发条件时面对的是两个有业务含义的数字，而不是一个
   `type` 分派 + `operator` + 通配符白名单的规则引擎，这样我不需要理解引擎也能调。
3. 作为教研人员，我希望我在量规里写的 `weight` 真的影响 dim 的汇总分，这样我对观测点
   相对重要性的判断不会被代码默默抹平。
4. 作为教研人员，我希望量规里的档位命名（优秀/良好/合格/待改进）能被模型看到，这样
   模型判档时有一个粗粒度的语义参照。
5. 作为教研人员，我希望 dim 的完整解释能进入选段与取证阶段，这样模型判断"这段材料跟这个
   指标相不相关"时，不是只靠观测点那十来个字的标题。
6. 作为教研人员，我希望判档（评分/仲裁）阶段**不要**注入 dim 的泛论解释，这样两位评委
   都严格照锚点判档，不会各自发挥泛论、放大分歧。
7. 作为教研人员，我希望量规文件缺字段时系统立刻报错，而不是照常跑完给出一个没有量规
   支撑的分数。
8. 作为教研人员，我希望权重和不为 1.0 时系统报错，这样我加了一个观测点忘了调权重时，
   分数不会静默漂移。
9. 作为运维，我希望上下文预算（决定哪些材料会被丢弃）和模型定义写在同一个文件里，
   这样我换一个上下文窗口更小的模型时能一眼看见它。

### 系统使用者（跑评价的人）

10. 作为跑评价的人，我希望用 `--task` 指定评哪个任务，这样切任务不需要修改一个被 git
    跟踪的文件，不会每次实验都带一个脏 diff。
11. 作为跑评价的人，我希望忘记指定任务时系统报错并列出可选任务，而不是静默沿用上次
    留在文件里的值。
12. 作为跑评价的人，我希望 `config validate` 能在不接触密钥的前提下，把量规结构问题
    全部查出来，这样 CI 里没有 `.env` 也能跑。

### 阅读代码 / 文档的人

13. 作为读文档的人，我希望"一级指标 / 二级指标 / 观测点"这三个词在代码、注释、文档、
    量规原文里指的是同一层东西，这样我不需要在脑子里做一次层级平移。
14. 作为读文档的人，我希望 `CONTEXT.md` 的词汇表就是唯一权威，这样术语有分歧时有地方
    可查。
15. 作为新人，我希望写一份新量规时能直接复制一份现役量规来改，而不是照着一份可能已经
    过期的手写模板，这样我不会继承模板里的错误。
16. 作为维护者，我希望 `configs/` 里只放引擎会读的文件，这样"这个文件还有用吗"永远不是
    一个需要考古的问题。

### 未来的前端配置页（本轮不实现，但本轮的设计要为它留好路）

17. 作为前端配置页的开发者，我希望 rubric 的必填字段有一份代码里的权威校验，这样表单
    校验规则有唯一来源，不会和后端各写一套。
18. 作为前端配置页的开发者，我希望档位命名仍然是量规数据的一部分，这样表单里"四档分别
    叫什么"这一栏有地方存。

## Implementation Decisions

### D1. 判据

**默认删；只有能说出「补了会更准 / 更安全」的才补。** 所有下列决策都是这条判据的应用。
死字段有两类：纯装饰（删）、背后是掉了的功能（逐个按判据审，不默认补）。

### D2. 仲裁配置：拍平规则引擎

原 `triggers` 列表（`type` 分派 + `operator` 七分支 + `applies_to_dimensions` /
`exclusions` 通配符）**全部删除**，替换为两个标量：

```yaml
score_gap_threshold: 1     # 两位评委分差 > 此值 → 该观测点触发仲裁
drift_min_dimensions: 2    # 分差恰为 1 且同向的观测点数 ≥ 此值 → 这些观测点全部触发
```

关键论据：**这个列表的扩展性是假的**——想加第三种触发规则必须去代码里加 `elif` 分支，
配置里摆规则引擎的架子换不来任何"不改代码就加规则"的能力，只是把「加规则要改代码」
这件事藏起来。实测佐证：全仓（含测试）`applies_to_dimensions` 永远是 `["*"]`、
`operator` 永远是 `">"`、`exclusions` 一次都没出现过，`_compare` 的七个分支里六个
从未被任何代码路径触达。

固化的两个值：`drift_gap` 固定为 1（"相邻"的定义就是差 1；且差 ≥2 早被
`score_gap_threshold` 单独触发，这个旋钮转到 2 以上永远无效，是个假旋钮）；
`require_same_direction` 固定为 true（"系统性同向漂移"的定义）。

一并删除的死字段：`policy_id`（流进 `policy_version` 后再无读取方）、`raters.*`（3 个）、
`scoring.*`（2 个）、`triggers[].{trigger_id, description, action, priority}`、
`resolution_strategy.*`（3 个）。其中 `description` 降级为 YAML `#` 注释保留——规则意图
值得留给人看，但不该伪装成配置字段。

裁决模块的实现随之简化：删除 `_compare`、`_dimension_matches`、type 分派，直接读两个数。

**文件位置**：`configs/policies/adjudication/engineering_eval_adjudication.yaml`
→ `configs/adjudication.yaml`，`configs/policies/` 目录消失。

### D3. 删除 bundle，任务选择改为命令行参数

`bundle.yaml` 四段里三段是空转：`bundle_id` 死、`policies:` 与 `prompts:` 是指向固定
路径的引用。整个文件删除，路径改为按约定固定：

- 仲裁策略：`<configs_root>/adjudication.yaml`
- 提示词：`<configs_root>/prompts/<stage>.yaml`
- 量规：`<configs_root>/tasks/<task_id>/dimension/<dim_id>_rubric.yaml`

`active_task_id` 从配置变为 CLI 参数。理由是实际工作方式：切任务时改一个 tracked 文件，
意味着每次实验都带一个脏 diff，多任务并行还会互相冲突（git 历史里"从 main 摘回
pendulum_experiment 任务配置"就是这么来的）。

- CLI：`--bundle <path>` → `--configs <dir>`（默认 `configs`）+ `--task <task_id>`
- `--task` 无默认值。缺失时报错，并列出 `<configs_root>/tasks/` 下的可选任务——不静默
  沿用任何值。
- Engine 构造入口：`Engine.from_bundle(bundle_path, configs_root=...)` →
  `Engine.from_configs(configs_root, task_id, ...)`，其余参数（`providers` 注入、
  `output_dir`）不变。

**连带改名**：`configs/prompts/rater_scoring.yaml` → `scoring.yaml`，让"提示词文件名 =
阶段名"这条约定真正成立。

### D4. 权重生效 + 和值校验

聚合从等权平均改为按观测点 `weight` 加权平均。`weight` 变为**必填**，且同一份量规内
所有观测点的权重和必须为 1.0（浮点比较留合理容差），不满足即报错。

量级说明（避免高估这项改动）：F2 两个观测点得 3 和 4 分时，等权 3.5、加权 3.6，**差
0.1**。在 1–4 量表、2–4 个观测点的规模下，加权与等权的差距通常 <0.2 分。权重只影响 dim
的汇总分这一个派生数字，观测点分数、证据、反馈一个字都不变。选择实现而非删除，理由是
消除"配置说一件事、代码做另一件事"这个最坏状态，且量规作者的权重是逐份精心设定的
（不是全部相等凑数）。

### D5. `indicator_description` 只注入选段与取证

注入 select 与 extraction 两个阶段的 prompt 上下文；**不注入** scoring、adjudication、
feedback。

划线依据：`indicator_description` 是**评价标准**的泛论，和锚点抢同一个角色（判档依据）。
在判档阶段给它，等于让两位评委各自去发挥这段泛论，会放大分歧、削弱可复现性。而在
选段/取证阶段，模型是在判断"这段材料跟这个指标相不相关"，只给观测点那十来个字的标题
明显吃亏，补上语境会更准。

不采用"全阶段注入 + 在 prompt 里写一句『仅作背景，判档以锚点为准』"的方案：靠 prompt
里一句话约束模型不使用某段已经给它的信息，不可靠；**不给它看，比让它别看更可靠**。

### D6. 档位标签进锚点文本

`scale.levels`（`4:"优秀" 3:"良好" 2:"合格" 1:"待改进"`）当前处于半死状态：它流进
`levels[].summary`，而渲染时只在 descriptors 全空时才回落到 summary——D8 的校验一旦
要求锚点全档必填，这条回落路径就永远走不到。

处置是让它活过来而非删除：渲染锚点时把档位标签拼进文本（`4（优秀）：不仅保证了…`
而非裸的 `4：不仅保证了…`）。理由有二：档位命名是量规设计的一部分，而**前端量规配置页
的表单里必然有这一栏**，现在删掉将来要原样重建；让它有真实消费方的代价只有一处渲染
逻辑的改动。

产物侧的用法（`feedback.json` 里把最终分映射成档位名给教师看）方向正确但属于产物/前端
工作，不在本轮，且与本决策不冲突。

### D7. 上下文预算进 runtime

`DEFAULT_CONTEXT_BUDGET_TOKENS`（48000，决定超预算时丢弃哪些单元）移入
`model_config.yaml` 的 `runtime:` 段，由 Engine 读取后传给切分。

理由是它与模型选择存在**硬耦合**：换一个上下文窗口更小的模型，这个数会直接把请求撑爆，
而它藏在切分模块里，改模型的人不会想到去看它。放进 `runtime:` 后，与它耦合的 provider
定义就在同一个文件里。

`DEFAULT_SELECT_PREVIEW_BYTES`（200）**保持硬编码**：纯调参、无跨文件耦合，实验期直接改
常量比改配置更快，不满足"补了会更安全"。

不采用"`config validate` 校验预算不超过模型窗口"的方案：需要维护一张模型→窗口对照表，
表必然过期，且新模型不在表里会误报。

### D8. rubric 结构校验

新增一份 rubric 结构校验，**加载路径与 `config validate` 共用同一个函数**。必填项：

- 顶层：`dim_id`、`dim_name`、`indicator_description`
- `scale`：`min`、`max`、`levels`（须覆盖 `min..max` 每一档）
- `dimensions[]`：`code`、`name`、`weight`、`anchors`（须覆盖 `min..max` 每一档）
- 跨项：同一量规内 `weight` 之和为 1.0

缺任何一项直接报错，不回落默认值。当前的失败模式是：缺 `anchors` → 锚点被过滤成空 →
**模型在没有量规的情况下打分，跑完、出分、产物上完全看不出来**；缺 `scale.max` → 静默
回落 5（真实量规是 4 级）。这正是本项目既定原则"杜绝静默降级"覆盖的情况，只是发生在
量规这一侧一直没人管。

与本会话早前删除的 prompt 文件 pydantic schema **不矛盾**，判据是同一条——**校验要落在
会出错的地方**：prompt 文件只有一个字段（模板字符串），校验纯属仪式；rubric 有真实结构、
真实必填项、真会静默出错。

校验只做结构，不读密钥、不建 provider，保证 CI 无 `.env` 也能跑（沿用现有约定）。

### D9. 术语对齐

确立三层词汇，写入 `CONTEXT.md` 并使代码注释与全部文档一致：

| 真实层级 | 例子 | 叫法 | 配置标识符 |
| --- | --- | --- | --- |
| 一级指标 | A 问题认知与分析能力 | domain | 不落配置 |
| 二级指标 | A1 复杂问题的识别和界定 | dim | `dim_id` |
| 观测点 | A1-1 关键变量识别的全面性 | 观测点 | `code` |

**代码标识符不改**（`dim_id` / `code` 保持原样）：`dim_id` 直接决定产物落盘路径
`artifacts/<task>/<sample>/<dim>/`，改名会让已有实验数据的目录结构对不上，为一次改名付
这个代价不值。

**domain 层级不落配置字段**：按 D1 判据，没有消费方就不进 `configs/`；且 domain 可从
`code` 首字母推出，将来要做 domain 级汇总不需要新配置字段。

改动量约 133 处（`一级指标` / `二级指标` 在 23 个文件中的出现），**必须逐处判断当前指的
是哪一层，不可全局替换**——含义整体平移了一级，机械替换必然出错。

### D10. 删除的文件

- `configs/bundle.yaml`（D3）
- `configs/policies/`（D2 迁出后目录为空）
- `configs/tasks/*/task_context.yaml`——它承载的是教师对模型评价的外环调整，而外环代码
  已随 v1 一并删除、属于三期工作。本轮先完善核心评价过程，此文件与文档中描述它的章节
  一并删除。
- `configs/rubrics/rubric_schema.yaml`——全仓零引用的手写模板，且已滞后两处（写的是 5 级
  量表而真实量规是 4 级；无 `weight` 字段）。**手写模板必然滞后，而一份正在跑的真量规
  永远不会过期**——写新量规时复制一份现役量规即可。
- `configs/rubrics/rubric.md` **移动**到 `docs/`（是给人看的权威来源文档，引擎一个字
  都不读），`configs/rubrics/` 目录消失。

由此确立一条规则并写入 `CONTEXT.md`：**`configs/` 只放引擎会读的文件。**

### D11. 落地分两批

本 spec 既含纯清理也含行为变更，建议分两批：

- **第一批（不改评价行为）**：D2 配置拍平、D3 删 bundle 改 CLI、D9 术语对齐、D10 删文件。
  完成后所有既有测试应在仅调整配置构造代码后全绿，分数不变。
- **第二批（改评价行为）**：D4 加权、D5 指标解释注入、D6 档位标签、D7 预算配置化、
  D8 结构校验。这批会改变历史分数（幅度 <0.2）与 prompt 内容，需要重新冒烟。

## Testing Decisions

好的测试只断言外部可观察行为：给定一份 configs + 一个数据包 + 确定性的假 provider，
产出的分数、产物结构、报错信息是什么。不断言中间函数被调用了几次、私有辅助函数的返回值，
也不断言 prompt 的完整字符串（脆）——只断言关键片段是否出现。

**不新增任何测试接缝。** 四个现成接缝已经完整覆盖本 spec，按从高到低使用：

1. **端到端接缝（首选）**——`tests/unit/test_engine.py` 的临时 `configs_root` fixture +
   注入 FakeProvider + `Engine.from_configs(...).evaluate(...)`。覆盖：新配置形状能否
   加载、`--task` 语义、加权聚合对最终分的影响、量规结构错误能否在评价前报错。
   现有 fixture 里两段写死的 `bundle.yaml` 字符串直接删除，是本轮测试改动的主要部分。
   prior art：该文件现有的 `configs_root` / `configs_root_multi` fixture 与
   `_StageAwareProvider`（按阶段返回固定响应，不依赖调用顺序，因此并发下结果稳定）。

2. **CLI 接缝**——`tests/unit/test_cli.py`。覆盖：`--task` 缺失时报错并列出可选任务、
   `config validate` 对结构错误量规的报错文案、无 `.env` 时校验仍能通过。
   prior art：该文件现有的 bundle 校验用例。

3. **纯函数接缝**——`tests/unit/policies/test_adjudication_v2.py`（两个标量驱动的触发
   判断，含边界：分差恰等于阈值不触发、同向漂移达标触发、反向不触发）与
   `test_aggregation_v2.py`（加权平均、权重和不为 1 时报错）。这两处是纯计算，测起来
   最廉价，边界用例应集中在这里而非端到端。

4. **prompt 渲染接缝**——`tests/unit/agents/test_rater_prompts.py`。覆盖 D5 与 D6 的
   可观察结果：选段/取证的渲染文本中出现 `indicator_description` 片段，评分/仲裁/反馈的
   渲染文本中**不出现**它；锚点行包含档位标签。

补充：`tests/unit/test_segment.py` 需覆盖预算来自配置这一路径；`test_engine_config.py`
需覆盖 `runtime.context_budget_tokens` 的读取与缺省。

## Out of Scope

- **人工反馈外环**（教师修正回流、`CorrectionAgent` / `ConfigPatcher`）——三期工作，
  `task_context.yaml` 正因此被删而非补全。
- **前端量规配置页**——长远规划。本轮只保证它将来需要的东西（档位命名、结构校验的
  权威来源）不被提前删掉。
- **domain 层级的汇总分**（"该生 A 类能力如何"）——新功能，不属于核心评价过程。
- **代码标识符改名**（`dim_id` → `indicator_id` 等）——见 D9，代价大于收益。
- **产物侧把分数映射成档位名**——见 D6，属于产物/前端工作。
- **`preview_bytes` 配置化**——见 D7。
- **per-provider 独立超时**——沿用上一轮 spec 的结论，YAGNI。
- **`rater.py` 的评分兜底问题**——见下方 Further Notes，属于评价链路不属于配置，单开一票。

## Further Notes

### 范围外发现的同类缺陷

评分解析处对模型返回值使用了 `int(data.get("proposed_score", <scale_min>))`——**模型没有
返回分数时，静默给出量表最低分**。这与 D8 处理的是同一类病（静默降级，产物上完全看不
出来），但它位于评价链路而非配置，本轮不动，**建议单开一票**。同一位置的
`data.get("rationale", "")` 也是静默空串。

### 与上一轮 spec 的关系

`.scratch/config-hygiene/spec.md` 已处理 `model_config.yaml` 与 `.env` 的职责边界，并把
`task_context.yaml` 明确挂为悬案（"像是重构中掉的功能而非死配置，待单开一票"）。本 spec
即为那一票的答复：**判定为外环功能，本轮删除**。上一轮确立的原则（凭证属于厂商账号、
`.env` 只装凭证值、杜绝静默降级）在本轮继续适用，D8 正是把最后一条推广到量规一侧。

### 文档收尾

`docs/OVERVIEW.md` 已由用户删除，本轮不再更新它。需要同步术语与配置形状的文档：
`CONTEXT.md`（词汇表整节重写 + 新增"configs/ 只放引擎会读的文件"）、`README.md`、
`scripts/README.md`、`docs/REFACTOR_DESIGN.md`（其 7.4 节描述 `task_context.yaml` 结构，
须删除；但注意该文档对量规层级的表述反而已经符合新术语）。
