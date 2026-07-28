# 配置字段整肃：model_config.yaml 与 .env

Status: done

> 本 spec 是一次 grilling 会话逐项拍板的结果。目标：让两份配置符合 v2 引擎现状、
> 去掉冗余字段、把职责边界钉死。

## Problem Statement

v2 重构（引擎 01–09）完成后，两份配置严重滞后：

- `model_config.yaml` 里 `default:` 整段、`stages.chunking`/`evidence_extraction`/`scoring`
  三段、`schema_version` 全都没有任何代码读取。
- `.env` 24 个键里只有 10 个活着，死键包括 `CHUNKING_API_KEY`、`COVERAGE_API_KEY`、
  `EXTRACTOR_API_KEY`、`OUTER_LOOP_*`（5 个）、`LLM_CACHE_*`、`LLM_PROVIDER`、
  `LLM_RATE_LIMIT_RPM`、`LLM_MAX_TOKENS`、`LLM_TEMPERATURE`。
- 更要命的是职责冲突：`build_provider()` 允许 `model`/`api_base` 从 `.env` 兜底，
  还有硬编码的 `"gpt-4o-mini"` 最终兜底。这直接违反 spec 的 US7「密钥值只放 .env、
  模型选择只放 yaml」与 US6「杜绝静默降级」。
- 实测发现：`RATER_1_API_KEY` / `RATER_3_API_KEY` / `FEEDBACK_API_KEY` **全是空的**，
  系统能跑完全靠 `LLM_API_KEY` 兜底。而 `RATER_2_API_KEY` 有值但无效（401）——
  这正是之前真实冒烟里那句难以归因的报错。

## 决策（逐条拍板）

1. **彻底分家**：`model` / `api_base` 只从 yaml 读，缺失即报错。删除 `LLM_MODEL`、
   `LLM_API_BASE` 两级兜底与 `"gpt-4o-mini"` 硬编码。
2. **删 `LLM_API_KEY` 兜底**；`api_key_env` 按**厂商**命名（`DEEPSEEK_API_KEY` /
   `DASHSCOPE_API_KEY`）而非按角色。理由：凭证属于厂商账号，不属于评委角色；
   角色命名会导致同一个值在 .env 里抄多遍，且缺失时会把 A 厂商 key 发给 B 厂商。
3. 超时/重试从 `.env` 挪进 yaml，与并发合并为统一 **`runtime:`** 段。`.env` 因此
   变成纯凭证文件。
4. provider 层**拍平为单一 `providers:` 映射**（删 `raters:` / `stages:` 分组与
   `default:` 段）——与 Engine 实际消费的 `Dict[str, BaseProvider]` 1:1。
5. `config validate` **一并校验 model_config 结构**（必需角色齐全、每条目
   model/api_base/api_key_env 非空、runtime 类型正确），但不读 env、不建 provider，
   CI 无密钥也能跑。
6. 三个 deepseek 条目**显式写三遍**，不用 YAML 锚点去重——换厂商时三行挨在一起，
   漏改一眼可见。
7. **所有配置 yaml 的 `schema_version` 全删**（零代码读取）。例外：
   `task_context.yaml` 用户明确要求「先不管这个文件」，完全不碰。
8. **不修改用户的 `.env`**（内含真实凭证），只提供 `.env.example` 模板 + 迁移对照表。

## 目标形状

```yaml
providers:
  rater_1:   {model, api_base, api_key_env: DEEPSEEK_API_KEY,  params}
  rater_2:   {model, api_base, api_key_env: DASHSCOPE_API_KEY, params}
  rater_3:   {...}   # 可选，仲裁用
  feedback:  {...}
runtime:
  max_workers: 8
  timeout_seconds: 60
  max_retries: 3
  retry_delay_seconds: 1
```

`.env` 只剩 `DEEPSEEK_API_KEY` 与 `DASHSCOPE_API_KEY` 两行。

## Out of Scope

- `task_context.yaml` 的任何改动（含它没进 prompt 这一悬案）。
- 用户 `.env` 的实际迁移——由用户自己执行。
- per-provider 的独立超时（现为全局一份，YAGNI）。

## Further Notes

- 悬案：`task_context.yaml`（`evidence_focus` / `calibration_notes` / `feedback_hints`）
  在 v2 里完全没进 prompt。像是重构中掉的功能而非死配置，待单开一票。
- 术语沉淀进 `CONTEXT.md`：**凭证属于厂商账号，不属于评委角色**。
