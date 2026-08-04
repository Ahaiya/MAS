# 03 — `configs/parse.yaml` 与凭证

**What to build:** 解析层的配置文件与加载器。不碰 `model_config.yaml`——解析服务不是评委角色，parse 也不是引擎的一部分。

**Blocked by:** —

**Status:** done

- [x] 新增 `configs/parse.yaml`：endpoint、connect/read timeout、轮询间隔、轮询上限
- [x] 三个增强开关，**默认全开**：`llm_enhancement` / `enhancement_mode: VLM` / `formula_enhancement`
- [x] 加载器缺字段直接报错，不回落默认值（与仓库既有配置原则一致）
- [x] `.env.example` 增加 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`，注明这是**阿里云账号 AK/SK 两个值**、与 `DASHSCOPE_API_KEY`（百炼）不是同一套凭证
- [x] 密钥缺失在 parse 启动时即报错，不等到提交任务才失败
- [x] 白名单**不进配置**（它决定编号身份而非内容质量）
- [x] 依赖：`alibabacloud_docmind_api20220711` / `alibabacloud_tea_openapi` / `alibabacloud_credentials` / `alibabacloud_tea_util` 写进 `pyproject.toml`
- [x] 测试：配置加载、缺字段报错、缺密钥报错

## Comments

`configs/` 只放引擎会读的文件这条规矩要顺带松一格——`parse.yaml` 是解析层读的。README 的配置表格需要同步。
