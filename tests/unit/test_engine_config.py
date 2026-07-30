"""`src/engine_config.py` 与 `providers/factory.py` 的配置契约测试。

核心不变式：**model_config.yaml 是模型/参数的唯一来源，.env 只装密钥值**。
任何"配置没写全就悄悄用别的值顶上"的路径都必须报错——静默降级正是这套配置
要杜绝的事（一次跑成了单评委却以为跑了双评，从产物上根本看不出来）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from src.engine_config import (
    DEFAULT_CONTEXT_BUDGET_TOKENS,
    DEFAULT_MAX_WORKERS,
    EngineConfigError,
    load_context_budget_tokens,
    load_providers_from_model_config,
    load_runtime_config,
)
from src.providers.factory import build_provider
from src.providers.guards import RetryConfig
from src.contracts.configuration import ProviderEntryConfig

_ENV_KEYS = ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "LLM_API_KEY", "LLM_MODEL", "LLM_API_BASE")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例都从干净环境出发——否则本机 .env 会把断言染成假绿。"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _entry(**overrides: Any) -> ProviderEntryConfig:
    base: Dict[str, Any] = {
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
        "api_base": "https://api.deepseek.com/v1",
        "params": {},
    }
    base.update(overrides)
    return ProviderEntryConfig(**base)


def _write(tmp_path: Path, data: Dict[str, Any]) -> Path:
    path = tmp_path / "model_config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _provider_entry(api_key_env: str = "DEEPSEEK_API_KEY") -> Dict[str, Any]:
    return {
        "model": "deepseek-chat",
        "api_base": "https://api.deepseek.com/v1",
        "api_key_env": api_key_env,
        "params": {"temperature": 0.0},
    }


def _minimal_config(**extra: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "providers": {
            "rater_1": _provider_entry(),
            "rater_2": _provider_entry("DASHSCOPE_API_KEY"),
            "feedback": _provider_entry(),
        }
    }
    data.update(extra)
    return data


# ── build_provider：没有任何兜底 ──────────────────────────────────────────────


def test_build_provider_requires_its_own_key_without_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_API_KEY 兜底已删——否则 A 厂商的 key 会被发给 B 厂商，
    换来一句莫名其妙的 401。"""
    monkeypatch.setenv("LLM_API_KEY", "sk-some-other-vendor")

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        build_provider(_entry(), RetryConfig())


def test_build_provider_requires_model_in_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """model 只能来自 yaml；LLM_MODEL 环境变量与 gpt-4o-mini 硬编码兜底都已删——
    否则会静默跑一个这个项目根本不用的模型。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")

    with pytest.raises(ValueError, match="model"):
        build_provider(_entry(model=""), RetryConfig())


def test_build_provider_requires_api_base_in_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_API_BASE", "https://elsewhere.example.com/v1")

    with pytest.raises(ValueError, match="api_base"):
        build_provider(_entry(api_base=""), RetryConfig())


def test_build_provider_uses_the_retry_config_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时/重试来自 yaml 的 runtime 段，不再从环境变量偷读。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")

    provider = build_provider(_entry(), RetryConfig(max_retries=7, timeout_seconds=12.0))

    assert provider._config.max_retries == 7
    assert provider._config.timeout_seconds == 12.0


# ── providers: 段 ────────────────────────────────────────────────────────────


def test_loads_every_declared_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-a")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-b")
    config = _minimal_config()
    config["providers"]["rater_3"] = _provider_entry()

    providers = load_providers_from_model_config(_write(tmp_path, config), RetryConfig())

    assert set(providers) == {"rater_1", "rater_2", "rater_3", "feedback"}


@pytest.mark.parametrize("missing", ["rater_1", "rater_2", "feedback"])
def test_missing_required_provider_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-a")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-b")
    config = _minimal_config()
    del config["providers"][missing]

    with pytest.raises(EngineConfigError, match=missing):
        load_providers_from_model_config(_write(tmp_path, config), RetryConfig())


def test_rater_3_may_be_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rater_3 只在真正触发仲裁时才需要，报错时机在 reconcile.py。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-a")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-b")

    providers = load_providers_from_model_config(_write(tmp_path, _minimal_config()), RetryConfig())

    assert "rater_3" not in providers


def test_missing_key_value_is_reported_as_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """密钥没配是最常见的首次运行错误，要归到 EngineConfigError，
    CLI 才能印一行人话而不是甩 traceback。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-a")  # DASHSCOPE 故意不设

    with pytest.raises(EngineConfigError, match="DASHSCOPE_API_KEY"):
        load_providers_from_model_config(_write(tmp_path, _minimal_config()), RetryConfig())


@pytest.mark.parametrize("field", ["model", "api_base", "api_key_env"])
def test_provider_entry_missing_required_field_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-a")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-b")
    config = _minimal_config()
    del config["providers"]["rater_1"][field]

    with pytest.raises(EngineConfigError) as excinfo:
        load_providers_from_model_config(_write(tmp_path, config), RetryConfig())

    assert field in str(excinfo.value)
    assert "rater_1" in str(excinfo.value)


def test_old_format_gives_a_pointed_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧格式（raters:/stages: 分组）要报得明确——schema_version 已全局删除，
    没有版本号可依，只能靠这条错误告诉人「你这是旧格式」。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-a")
    old = {"raters": {"rater_1": _provider_entry()}, "stages": {"feedback": _provider_entry()}}

    with pytest.raises(EngineConfigError, match="providers"):
        load_providers_from_model_config(_write(tmp_path, old), RetryConfig())


# ── runtime: 段 ──────────────────────────────────────────────────────────────


def test_runtime_section_is_read(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _minimal_config(
            runtime={
                "max_workers": 3,
                "timeout_seconds": 45,
                "max_retries": 5,
                "retry_delay_seconds": 2,
            }
        ),
    )

    max_workers, retry = load_runtime_config(path)

    assert max_workers == 3
    assert retry == RetryConfig(max_retries=5, retry_delay_seconds=2.0, timeout_seconds=45.0)


def test_runtime_section_may_be_omitted_entirely(tmp_path: Path) -> None:
    """runtime 是性能旋钮而非必需配置——缺了用默认值，不像 providers 那样报错。"""
    max_workers, retry = load_runtime_config(_write(tmp_path, _minimal_config()))

    assert max_workers == DEFAULT_MAX_WORKERS
    assert retry.max_retries == 3
    assert retry.timeout_seconds == 60.0


def test_missing_model_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(EngineConfigError, match="not found"):
        load_providers_from_model_config(tmp_path / "nope.yaml", RetryConfig())


# ── runtime 段的输入校验 ──────────────────────────────────────────────────────
#
# 「整段缺失 → 用默认值」是有意为之（注入 providers 的测试路径根本没有配置文件）。
# 但「写了但写错」是另一回事：它是人打错字，必须当场报错，不能顺着默认值滑过去。


def test_unknown_runtime_key_is_rejected(tmp_path: Path) -> None:
    """拼错的键必须报错——静默忽略等于让人以为自己调了参数，其实没有。"""
    path = _write(tmp_path, _minimal_config(runtime={"max_worker": 3}))

    with pytest.raises(EngineConfigError) as excinfo:
        load_runtime_config(path)

    assert "max_worker" in str(excinfo.value)
    assert "max_workers" in str(excinfo.value)  # 提示正确拼法


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_max_workers_is_rejected(tmp_path: Path, bad: int) -> None:
    """否则会在 ThreadPoolExecutor 里炸出一句与配置无关的错。"""
    path = _write(tmp_path, _minimal_config(runtime={"max_workers": bad}))

    with pytest.raises(EngineConfigError, match="max_workers"):
        load_runtime_config(path)


@pytest.mark.parametrize("bad", [0, -5])
def test_non_positive_timeout_is_rejected(tmp_path: Path, bad: int) -> None:
    path = _write(tmp_path, _minimal_config(runtime={"timeout_seconds": bad}))

    with pytest.raises(EngineConfigError, match="timeout_seconds"):
        load_runtime_config(path)


def test_negative_retry_values_are_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _minimal_config(runtime={"max_retries": -1}))

    with pytest.raises(EngineConfigError, match="max_retries"):
        load_runtime_config(path)


def test_non_numeric_runtime_value_is_a_config_error_not_a_bare_valueerror(
    tmp_path: Path,
) -> None:
    """一个字符的 yaml 笔误不该让 CLI 甩一屏 traceback——EngineConfigError 才在
    CLI 的"用户能自己修"错误网里。"""
    path = _write(tmp_path, _minimal_config(runtime={"max_workers": "eight"}))

    with pytest.raises(EngineConfigError, match="max_workers"):
        load_runtime_config(path)


@pytest.mark.parametrize(
    "bad_runtime",
    [{"max_workers": [1, 2]}, {"timeout_seconds": None}],
    ids=["list-value", "null-value"],
)
def test_wrong_typed_runtime_value_is_a_config_error(tmp_path: Path, bad_runtime: Any) -> None:
    """TypeError 同样不在 CLI 的"用户能自己修"错误网里，必须转成 EngineConfigError。"""
    with pytest.raises(EngineConfigError):
        load_runtime_config(_write(tmp_path, _minimal_config(runtime=bad_runtime)))


def test_runtime_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(EngineConfigError, match="键值对"):
        load_runtime_config(_write(tmp_path, _minimal_config(runtime="nope")))


# ── runtime.context_budget_tokens ────────────────────────────────────────────
#
# 它决定超预算时丢弃哪些单元，与模型的上下文窗口硬耦合——放在 providers 旁边，
# 换模型的人才会顺手看到它。


def test_context_budget_is_read_from_runtime(tmp_path: Path) -> None:
    path = _write(tmp_path, _minimal_config(runtime={"context_budget_tokens": 12000}))

    assert load_context_budget_tokens(path) == 12000


def test_context_budget_falls_back_to_default(tmp_path: Path) -> None:
    assert load_context_budget_tokens(_write(tmp_path, _minimal_config())) == DEFAULT_CONTEXT_BUDGET_TOKENS
    assert DEFAULT_CONTEXT_BUDGET_TOKENS == 48000


def test_context_budget_falls_back_when_file_missing(tmp_path: Path) -> None:
    assert load_context_budget_tokens(tmp_path / "nope.yaml") == DEFAULT_CONTEXT_BUDGET_TOKENS


@pytest.mark.parametrize("bad", [0, -1, "many"])
def test_invalid_context_budget_is_rejected(tmp_path: Path, bad: Any) -> None:
    path = _write(tmp_path, _minimal_config(runtime={"context_budget_tokens": bad}))

    with pytest.raises(EngineConfigError, match="context_budget_tokens"):
        load_context_budget_tokens(path)


def test_context_budget_key_is_accepted_by_load_runtime_config(tmp_path: Path) -> None:
    """两个读取入口共用同一份字段白名单——否则写了预算反而让 runtime 段报未知字段。"""
    path = _write(tmp_path, _minimal_config(runtime={"context_budget_tokens": 12000}))

    max_workers, _retry = load_runtime_config(path)

    assert max_workers == DEFAULT_MAX_WORKERS
