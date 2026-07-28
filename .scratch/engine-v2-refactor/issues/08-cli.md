# 08 — CLI 收敛

**What to build:** 命令行用户用 `python scripts/cli.py eval <file> --dim a4` 发起评价，不传 `--dim` 则评当前任务全部一级指标。CLI 收敛为单文件、参数精简、去掉失效开关。

**Blocked by:** 06（Engine 门面就位）

**Status:** done

- [x] 新增 `scripts/cli.py`（typer），文件头自注入 `sys.path`（不用 `-m`、不装 console_scripts）
- [x] `eval` 命令：位置 `INPUT_FILE` + `--bundle`（默认 `configs/bundle.yaml`）+ `--dim`（缺省评全部一级指标）+ `--output-dir`，命令体只做「建 Engine → evaluate → 打印」
- [x] 删参数：`--input/-i`、`--verbose`、`--debug-bundle`、`--model-config`（固定读 `configs/model_config.yaml`）；不加 `--batch`
- [x] `config validate` 命令并入 `cli.py`
- [x] 删除 `scripts/__main__.py`、`scripts/mas.py`；`server.py` 独立保留（三期）
- [x] 手动/冒烟验证：见下

## Comments

### 冒烟验证结果

- `config validate` 对真实 `configs/bundle.yaml` 通过：4 个一级指标 × 3 个二级指标全部解析。
- `eval sample.md --dim a4` 真实跑了一次（约 21 次 LLM 调用）。全链路打通——切分
  （1597 单元，超预算丢弃 1080 并显式告警）→ 建 Engine → 二级指标级并发 → 失败隔离
  → 三层产物落盘（`maker_hackathon/sample/package.json` + `a4/{feedback,rater_chains,run_trace}.json`）
  → 错误摘要 → 退非零。
- **但没评出分数：`RATER_2_API_KEY` 无效。** 三个二级指标全部报
  `API error 401 ... invalid_api_key`，来源是 `dashscope.aliyuncs.com`（rater_2 =
  qwen3.6-plus）。这是环境密钥问题、不是代码问题，**需要你更新 `.env` 里的
  `RATER_2_API_KEY` 后重跑才能拿到真实分数**。
- 边界错误已验证报一行人话而非 traceback：缺密钥、`--dim` 拼错、`--bundle` 不存在。

### 顺带修掉的（冒烟 + code review 挖出来的）

1. **失败隔离会把根因埋掉**（07 引入）：全部二级指标失败时空 `decisions` 会一路飘到
   `aggregate_final_decisions` 才炸出"decisions 不能为空"，与根因无关。真实冒烟里
   这条正是把 401 invalid_api_key 埋掉的元凶。现在短路掉 reconcile/feedback，产出
   `primary_score=None` 的空评价，失败原因逐条落在 `run_trace.json` 的 `failed_dims`。
2. **一个一级指标整体失败不再拖垮同 sample 其余一级指标**（US31「不崩整个 sample」）。
   最初的修法是抛异常，被 spec review 指出会让缺省（不传 `--dim`）跑法在一个 dim 挂掉时
   全盘无产出——已改为隔离而非抛出。CLI 在"一个分都没评出来"时退非零。
3. **密钥缺失归类为配置错误**：`build_provider` 原抛裸 `ValueError`，现包成
   `EngineConfigError`，CLI 因此能对最常见的首次运行错误印一行人话。
4. `strip_configs_prefix` 从 `engine.py` 移到 `config/compiler.py`（code review 指出的
   feature envy——它是配置路径解析的职责，engine 不该为 CLI 导出路径工具）。

### 一并清掉的

- `src/utils/validate_config.py`（v1 的 `config validate`，只被已删的 `mas.py` 与
  console_scripts 引用；且它走的 `ConfigCompiler` 路径读不了 v2 的 `bundle.yaml`）
- `pyproject.toml` 的 `[project.scripts]` 条目（spec 明确"不装 console_scripts"）
- `python-dotenv` 从 dev 依赖移到运行时依赖——CLI 靠它读 `.env` 里的密钥

### 遗留 / 待办

- **US14 只做了一半**：超预算丢弃的单元只在 CLI 打 stderr 告警，没有持久化——
  `package.json` 里没有 `dropped_unit_ids` 字段，记录随终端消失。补它要动
  `DataPackage` 契约（01 的地盘），建议并入 09 或单开一票。
- `docs/OVERVIEW.md`、`docs/REVIEW.md` 里的 `python -m scripts` / `scripts/eval.py`
  引用仍是 v1 的，随 09 删除遗留模块时一并重写（`scripts/README.md` 本票已更新）。
- `failed_dims: List[Dict[str, str]]` 是 primitive obsession（code review 判断项），
  但它直接序列化进 `run_trace.json`，改成具名类型要动 trace 契约，留给 09。
