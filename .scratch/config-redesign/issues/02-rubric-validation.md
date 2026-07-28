# 02 — 量规结构校验，封死静默降级

**What to build:** 教研人员写错量规时，系统在评价开始前就报错并指出是哪份文件的哪个
字段，而不是照常跑完、给出一个没有量规支撑的分数。

当前的失败模式是真实存在的：量规缺 `anchors` 时锚点会被过滤成空，**模型在完全看不到
评分标准的情况下打分，跑完、出分、产物上一点痕迹都没有**；缺 `scale.max` 时静默回落到
5，而真实量规是 4 级，模型因此可以打出量表外的分数。这是本项目既定原则"杜绝静默降级"
覆盖的情况，只是一直没人管到量规这一侧。

长远要做的前端量规配置页会直接写这些文件，表单漏填必然踩中此洞——这份校验同时是那个
表单的权威规则来源。

校验只做结构，不读密钥、不建 provider，因此 CI 里没有 `.env` 也能跑。加载路径与
`config validate` **共用同一个校验函数**，避免"CI 拦得住但有人跳过 validate 就漏了"。

**Blocked by:** 01

**Status:** done

- [x] 顶层必填：`dim_id`、`dim_name`、`indicator_description`
- [x] `scale` 必填 `min`、`max`、`levels`，且 `levels` 覆盖 `min..max` 每一档
- [x] `dimensions[]` 必填 `code`、`name`、`weight`、`anchors`，且 `anchors` 覆盖 `min..max` 每一档
- [x] 同一份量规内所有观测点 `weight` 之和为 1.0（浮点比较留合理容差）
- [x] 任一项不满足即报错，不回落默认值；错误信息指出文件与字段
- [x] 加载路径与 `config validate` 调用同一个校验函数
- [x] 校验不读环境变量、不构造 provider
- [x] 现役 7 份量规全部通过校验
- [x] 测试覆盖各类缺字段与权重和不为 1 的报错

## Comments

超出验收项的两点，记录备查：

1. **新增重复 code 检查**。同一份量规里两个观测点 code 相同时，`dimension_by_id` 会
   互相覆盖，静默丢掉一个观测点——和缺 anchors 同一类病，一并堵上。
2. **空白锚点按缺失处理**。锚点写成 `"   "` 时渲染阶段同样会被过滤掉，只让校验看起来
   通过没有意义。

`ConfigCompileError` 移入新的 `src/config/errors.py`：compiler 要调校验、校验要抛这个
异常，不拆开就是循环导入。`src/config/compiler.py` 仍再导出它，调用方无感。

**测试 fixture 是靠静默降级才跑通的**：`test_engine.py` / `test_cli.py` 里的最小量规
缺 `weight`、锚点只覆盖 1 和 5 两档（量表却是 1–5）。这不是校验过严，是这些 fixture
一直在描述一份不合法的量规。已补全为合法量规。
