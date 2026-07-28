"""
把 `configs/model_config.yaml` 读成 provider 实例与运行期旋钮。

文件形状：

    providers:            # 角色 → 模型端点绑定，键名即 Engine 消费的角色名
      rater_1: {model, api_base, api_key_env, params}
      rater_2: ...
      rater_3: ...        # 可选，只在真正触发仲裁时才需要
      feedback: ...
    runtime:              # 运行期旋钮，全部可省略
      max_workers, timeout_seconds, max_retries, retry_delay_seconds

model_config 是模型/参数的唯一来源：`providers` 缺 rater_1/rater_2/feedback 直接
报错，条目缺 model/api_base/api_key_env 也直接报错，绝不静默降级——一次跑成了单
评委却以为跑了双评，从产物上根本看不出来。

`api_key_env` 存的是**环境变量的名字**，密钥值只在 .env 里。名字按**厂商**取
（DEEPSEEK_API_KEY / DASHSCOPE_API_KEY）而非按角色：凭证属于厂商账号，多个角色
共用同一个账号时不必把同一个值在 .env 里抄好几遍。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import yaml

from src.contracts.artifact_bundle import ProviderEntryConfig
from src.providers.base import BaseProvider
from src.providers.factory import build_provider
from src.providers.guards import RetryConfig

DEFAULT_MAX_WORKERS = 8
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 1.0
DEFAULT_CONTEXT_BUDGET_TOKENS = 48000

REQUIRED_PROVIDERS = ("rater_1", "rater_2", "feedback")
_REQUIRED_ENTRY_FIELDS = ("model", "api_base", "api_key_env")
_RUNTIME_FIELDS = (
    "max_workers",
    "timeout_seconds",
    "max_retries",
    "retry_delay_seconds",
    "context_budget_tokens",
)


def _coerce(runtime: Dict[str, Any], field: str, default: Any, caster: Callable[[Any], Any]) -> Any:
    """按字段逐个转换，失败时报出**是哪个字段、值是什么**。

    整段 try 起来的写法只会吐一句 `invalid literal for int()`——不告诉人该改哪一行。"""
    raw = runtime.get(field, default)
    try:
        return caster(raw)
    except (TypeError, ValueError) as exc:
        raise EngineConfigError(f"runtime.{field} 的值无效：{raw!r}（{exc}）") from exc


def _default_retry_config() -> RetryConfig:
    return RetryConfig(
        max_retries=DEFAULT_MAX_RETRIES,
        retry_delay_seconds=DEFAULT_RETRY_DELAY_SECONDS,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    )


class EngineConfigError(Exception):
    """model_config 缺失必需配置时抛出——不静默降级。"""


def _load(model_config_path: Path) -> Dict[str, Any]:
    if not model_config_path.exists():
        raise EngineConfigError(f"model_config not found: {model_config_path}")
    return yaml.safe_load(model_config_path.read_text(encoding="utf-8")) or {}


def _entry_from_dict(raw: Dict[str, Any], *, label: str) -> ProviderEntryConfig:
    """把一个 provider 条目转成 ProviderEntryConfig，三个必填字段缺一不可。"""
    for field in _REQUIRED_ENTRY_FIELDS:
        if not raw.get(field):
            raise EngineConfigError(f"model_config 中 providers.{label} 缺少必填字段 {field}。")
    return ProviderEntryConfig(
        api_key_env=raw["api_key_env"],
        model=raw["model"],
        api_base=raw["api_base"],
        params=dict(raw.get("params") or {}),
    )


def validate_model_config(model_config_path: Path) -> Dict[str, ProviderEntryConfig]:
    """只校验结构、不建 provider、不读密钥，因此没有 .env 也能在 CI 里跑。

        Returns:
            {角色名: ProviderEntryConfig}，供调用方进一步建 provider 或打印摘要。

        Raises:
            EngineConfigError: 文件缺失、providers 段缺失、必需角色缺失，或条目字段不全。"""
    data = _load(model_config_path)

    providers_raw = data.get("providers")
    if not providers_raw:
        raise EngineConfigError(
            f"model_config 缺少 providers 段：{model_config_path}。"
            "（旧格式用 raters: / stages: 两组，现已合并为单一 providers: 映射。）"
        )

    missing = [name for name in REQUIRED_PROVIDERS if name not in providers_raw]
    if missing:
        raise EngineConfigError(
            f"model_config 的 providers 缺少必需角色：{missing}。"
            "rater_1/rater_2 是独立双链路评价所必需，feedback 每次评价都要用。"
        )

    return {name: _entry_from_dict(raw or {}, label=name) for name, raw in providers_raw.items()}


def load_providers_from_model_config(
    model_config_path: Path, retry_config: RetryConfig
) -> Dict[str, BaseProvider]:
    """校验结构后逐个建 provider。

        rater_3 允许缺失——它只在真正触发仲裁时才需要，报错时机在 reconcile.py。

        Raises:
            EngineConfigError: 结构不合法，或某个 api_key_env 在环境里没有值。
                密钥没配是最常见的首次运行错误，归到这里 CLI 才能印一行人话。"""
    entries = validate_model_config(model_config_path)
    try:
        return {name: build_provider(entry, retry_config) for name, entry in entries.items()}
    except ValueError as exc:
        raise EngineConfigError(str(exc)) from exc


def _runtime_section(model_config_path: Path) -> Dict[str, Any]:
    """读出 runtime 段并校验形状与字段名（值的转换由各调用方按字段做）。"""
    runtime = _load(model_config_path).get("runtime") or {}
    if not isinstance(runtime, dict):
        raise EngineConfigError(
            f"model_config 的 runtime 段应是一组键值对，当前是 {type(runtime).__name__}。"
        )
    unknown = sorted(set(runtime) - set(_RUNTIME_FIELDS))
    if unknown:
        raise EngineConfigError(
            f"model_config 的 runtime 段有无法识别的字段：{unknown}。"
            f"可用字段：{sorted(_RUNTIME_FIELDS)}。"
        )
    return runtime


def load_context_budget_tokens(model_config_path: Path) -> int:
    """读 runtime.context_budget_tokens，缺省回落 DEFAULT_CONTEXT_BUDGET_TOKENS。

        它决定超预算时从尾部丢弃哪些单元，与模型上下文窗口硬耦合——所以住在
        providers 旁边：换一个窗口更小的模型时，改模型的人一眼能看到它。"""
    if not model_config_path.exists():
        return DEFAULT_CONTEXT_BUDGET_TOKENS

    runtime = _runtime_section(model_config_path)
    budget = _coerce(runtime, "context_budget_tokens", DEFAULT_CONTEXT_BUDGET_TOKENS, int)
    if budget < 1:
        raise EngineConfigError(f"runtime.context_budget_tokens 必须 >= 1，当前为 {budget}。")
    return budget


def load_runtime_config(model_config_path: Path) -> Tuple[int, RetryConfig]:
    """读 runtime 段，返回 (max_workers, RetryConfig)。

        「整段缺失 → 用默认值」是有意为之：runtime 是性能与健壮性旋钮而非必需配置，
        且注入 providers 的测试路径根本没有 model_config 文件，仍要能拿到一套默认值。

        但「写了却写错」是另一回事——拼错的键、0 或负数、非数字，全都当场报错。
        静默顺着默认值滑过去，会让人以为自己调了参数而实际没有（`max_worker: 3`
        拼错一个字母就白调），或者把一句与配置无关的报错推迟到 ThreadPoolExecutor
        里才炸（`max_workers: 0`）。"""
    if not model_config_path.exists():
        return DEFAULT_MAX_WORKERS, _default_retry_config()

    runtime = _runtime_section(model_config_path)

    max_workers = _coerce(runtime, "max_workers", DEFAULT_MAX_WORKERS, int)
    timeout_seconds = _coerce(runtime, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS, float)
    max_retries = _coerce(runtime, "max_retries", DEFAULT_MAX_RETRIES, int)
    retry_delay = _coerce(runtime, "retry_delay_seconds", DEFAULT_RETRY_DELAY_SECONDS, float)

    # RetryConfig.__post_init__ 已经在管 max_retries/retry_delay_seconds 的下界，
    # 它抛的 ValueError 在这里转成 EngineConfigError——CLI 的错误网只认后者。
    try:
        retry = RetryConfig(
            max_retries=max_retries,
            retry_delay_seconds=retry_delay,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        raise EngineConfigError(f"model_config 的 runtime 段字段有误：{exc}") from exc

    if max_workers < 1:
        raise EngineConfigError(f"runtime.max_workers 必须 >= 1，当前为 {max_workers}。")
    if timeout_seconds <= 0:
        raise EngineConfigError(f"runtime.timeout_seconds 必须 > 0，当前为 {timeout_seconds:g}。")
    return max_workers, retry
