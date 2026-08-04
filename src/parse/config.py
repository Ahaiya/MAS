"""
`configs/parse.yaml` 的加载器与凭证检查。

缺字段直接报错、不回落默认值。

凭证是阿里云账号 AK/SK 两个值（`ALIBABA_CLOUD_ACCESS_KEY_ID` / `_SECRET`），在
parse 启动时就检查，不等到提交任务、传完文件才失败。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import yaml

_ACCESS_KEY_ID_ENV = "ALIBABA_CLOUD_ACCESS_KEY_ID"
_ACCESS_KEY_SECRET_ENV = "ALIBABA_CLOUD_ACCESS_KEY_SECRET"

_OPTION_FIELDS = ("llm_enhancement", "enhancement_mode", "formula_enhancement")


class ParseConfigError(Exception):
    """parse.yaml 缺字段、值非法，或凭证未配置时抛出——不静默降级。"""


@dataclass(frozen=True)
class ParseConfig:
    """解析层的全部旋钮。"""

    endpoint: str
    connect_timeout_ms: int
    read_timeout_ms: int
    poll_interval_seconds: float
    poll_max_seconds: float
    layout_step_size: int
    options: Dict[str, Any]


def _require(data: Dict[str, Any], field: str, caster: Callable[[Any], Any], path: Path) -> Any:
    """取一个必填字段并转型；缺失或转不了都报出**是哪个字段、值是什么**。"""
    if field not in data or data[field] is None:
        raise ParseConfigError(f"{path} 缺少必填字段 {field}。")
    try:
        return caster(data[field])
    except (TypeError, ValueError) as exc:
        raise ParseConfigError(f"{path} 的 {field} 值无效：{data[field]!r}（{exc}）") from exc


def load_parse_config(path: Path) -> ParseConfig:
    """读 `configs/parse.yaml`。任何一个字段缺失都报错，不回落默认值。"""
    path = Path(path)
    if not path.exists():
        raise ParseConfigError(f"解析配置不存在：{path}。")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ParseConfigError(f"{path} 不是合法的 YAML：{exc}") from exc
    if not isinstance(data, dict):
        raise ParseConfigError(f"{path} 应是一组键值对，当前是 {type(data).__name__}。")

    raw_options = data.get("options")
    if not isinstance(raw_options, dict):
        raise ParseConfigError(f"{path} 缺少 options 段（三个增强开关）。")
    for field in _OPTION_FIELDS:
        if field not in raw_options or raw_options[field] is None:
            raise ParseConfigError(f"{path} 的 options 缺少必填开关 {field}。")

    return ParseConfig(
        endpoint=_require(data, "endpoint", str, path),
        connect_timeout_ms=_require(data, "connect_timeout_ms", int, path),
        read_timeout_ms=_require(data, "read_timeout_ms", int, path),
        poll_interval_seconds=_require(data, "poll_interval_seconds", float, path),
        poll_max_seconds=_require(data, "poll_max_seconds", float, path),
        layout_step_size=_require(data, "layout_step_size", int, path),
        options={
            "llm_enhancement": bool(raw_options["llm_enhancement"]),
            "enhancement_mode": str(raw_options["enhancement_mode"]),
            "formula_enhancement": bool(raw_options["formula_enhancement"]),
        },
    )


def require_credentials() -> Tuple[str, str]:
    """返回 (access_key_id, access_key_secret)；任一缺失即报错。"""

    missing = [name for name in (_ACCESS_KEY_ID_ENV, _ACCESS_KEY_SECRET_ENV) if not os.getenv(name)]
    if missing:
        raise ParseConfigError(
            f".env 缺少解析服务的凭证：{'、'.join(missing)}。"
        )
    return os.environ[_ACCESS_KEY_ID_ENV], os.environ[_ACCESS_KEY_SECRET_ENV]
