"""
把 `configs/model_config.yaml` 读成 provider 实例与并发上限。

model_config 是模型/参数的唯一来源：缺 raters.rater_1/rater_2 或 stages.feedback
直接报错，不读 bundle 内嵌 provider_config、不降级到单个 default provider。密钥值
只从 .env 读（build_provider() 已经这样做），配置里只放环境变量的名字。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from src.contracts.artifact_bundle import ProviderEntryConfig
from src.providers.base import BaseProvider
from src.providers.factory import build_provider

DEFAULT_MAX_WORKERS = 8


class EngineConfigError(Exception):
    """bundle/model_config 缺失必需配置时抛出——不静默降级。"""


def _entry_from_dict(raw: Dict[str, Any], *, label: str) -> ProviderEntryConfig:
    if "api_key_env" not in raw:
        raise EngineConfigError(f"model_config 中 {label} 缺少必填字段 api_key_env。")
    return ProviderEntryConfig(
        api_key_env=raw["api_key_env"],
        model=raw.get("model", "") or "",
        api_base=raw.get("api_base", "") or "",
        params=dict(raw.get("params") or {}),
    )


def load_providers_from_model_config(model_config_path: Path) -> Dict[str, BaseProvider]:
    """从 model_config.yaml 读取 raters.{rater_1,rater_2,rater_3} + stages.feedback。

    model_config 是模型/参数唯一来源，缺失该文件或缺 rater_1/rater_2/feedback
    直接报错——不读 bundle 内嵌 provider_config、不降级单 default provider。
    rater_3 允许缺失，缺失时的报错时机在 reconcile.py 里（只在真正触发仲裁时）。"""
    if not model_config_path.exists():
        raise EngineConfigError(f"model_config not found: {model_config_path}")
    data = yaml.safe_load(model_config_path.read_text(encoding="utf-8")) or {}

    raters_raw = data.get("raters") or {}
    stages_raw = data.get("stages") or {}
    for required in ("rater_1", "rater_2"):
        if required not in raters_raw:
            raise EngineConfigError(
                f"model_config 缺少 raters.{required}——每次评价都需要独立双链路的两个 rater。"
            )
    if "feedback" not in stages_raw:
        raise EngineConfigError("model_config 缺少 stages.feedback——feedback 阶段每次评价都需要 provider。")

    # build_provider 在密钥值缺失时抛 ValueError——那同样是"配置没配好"，归到
    # EngineConfigError，调用方（CLI）才能统一按配置错误印一行人话。
    try:
        providers: Dict[str, BaseProvider] = {
            rater_id: build_provider(_entry_from_dict(cfg, label=f"raters.{rater_id}"))
            for rater_id, cfg in raters_raw.items()
        }
        providers["feedback"] = build_provider(_entry_from_dict(stages_raw["feedback"], label="stages.feedback"))
    except ValueError as exc:
        raise EngineConfigError(str(exc)) from exc
    return providers


def load_max_workers(model_config_path: Path) -> int:
    """从 model_config.yaml 的 concurrency 段读取二级指标级并发上限，默认 8。

    并发是性能优化、不是必需配置：文件缺失或没有 concurrency 段时直接用默认值，
    不像 raters/feedback 那样报错。"""
    if not model_config_path.exists():
        return DEFAULT_MAX_WORKERS
    data = yaml.safe_load(model_config_path.read_text(encoding="utf-8")) or {}
    concurrency = data.get("concurrency") or {}
    return int(concurrency.get("max_workers", DEFAULT_MAX_WORKERS))
