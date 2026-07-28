"""量规结构校验：把静默降级换成明确报错。

在这之前，量规写错是不响的：缺 `anchors` 时锚点被过滤成空，**模型在完全看不到评分
标准的情况下打分**，跑完、出分、产物上一点痕迹都没有；缺 `scale.max` 时静默回落 5，
而真实量规是 4 级，模型因此能打出量表外的分数。

因此这里一律不回落默认值——每个必填项缺失都抛 ConfigCompileError，并指出是哪份文件的
哪个字段。加载路径与 `config validate` 共用本模块，避免"CI 拦得住、但跳过 validate
就漏了"。

只做结构校验：不读环境变量、不构造 provider，所以 CI 里没有 .env 也能跑。"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from src.config.errors import ConfigCompileError

# 权重和的容差：0.25+0.25+0.3+0.2 在浮点下不精确等于 1.0，不能用 == 判。
_WEIGHT_SUM_TOLERANCE = 1e-6

_REQUIRED_TOP_LEVEL = ("dim_id", "dim_name", "indicator_description")
_REQUIRED_OBSERVATION_POINT = ("code", "name", "weight", "anchors")


def _fail(source: str, message: str) -> None:
    raise ConfigCompileError(f"{source}: {message}")


def _require_non_blank_str(data: Dict[str, Any], key: str, source: str, where: str = "") -> str:
    value = data.get(key)
    prefix = f"{where}的 " if where else ""
    if value is None:
        _fail(source, f"缺少必填字段 {prefix}'{key}'")
    if not isinstance(value, str) or not value.strip():
        _fail(source, f"{prefix}'{key}' 必须是非空字符串，实际为 {value!r}")
    return str(value)


def _require_int(data: Dict[str, Any], key: str, source: str, where: str) -> int:
    value = data.get(key)
    if value is None:
        _fail(source, f"{where}缺少必填字段 '{key}'")
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(source, f"{where}的 '{key}' 必须是整数，实际为 {value!r}")
    return int(value)


def _normalize_rank_keys(mapping: Any) -> Dict[int, Any]:
    """YAML 的整数键可能解析成 int 也可能是 str，统一成 int；非整数键原样丢弃
    （由调用方的"覆盖每一档"检查负责报错）。"""
    out: Dict[int, Any] = {}
    for key, value in (mapping or {}).items():
        try:
            out[int(key)] = value
        except (TypeError, ValueError):
            continue
    return out


def _require_covers_every_rank(
    mapping: Any, field: str, scale_min: int, scale_max: int, source: str, where: str
) -> None:
    """量表每一档都必须有内容——缺一档就是模型少一条判档依据。

    空白字符串按缺失处理：渲染锚点时空文本同样会被过滤掉，留着只是让校验看起来通过。"""
    if not isinstance(mapping, dict):
        _fail(source, f"{where}的 '{field}' 必须是 映射(档位 → 文本)，实际为 {mapping!r}")

    normalized = _normalize_rank_keys(mapping)
    missing = [rank for rank in range(scale_min, scale_max + 1) if rank not in normalized]
    if missing:
        _fail(source, f"{where}的 '{field}' 缺少档位 {missing}（量表为 {scale_min}–{scale_max}）")

    blank = [
        rank
        for rank in range(scale_min, scale_max + 1)
        if not str(normalized[rank] or "").strip()
    ]
    if blank:
        _fail(source, f"{where}的 '{field}' 在档位 {blank} 上是空文本")


def _validate_scale(data: Dict[str, Any], source: str) -> tuple[int, int]:
    scale = data.get("scale")
    if not isinstance(scale, dict):
        _fail(source, f"缺少必填字段 'scale' 或它不是映射，实际为 {scale!r}")

    scale_min = _require_int(scale, "min", source, where="scale")
    scale_max = _require_int(scale, "max", source, where="scale")
    if scale_max <= scale_min:
        _fail(source, f"scale 的 'max'({scale_max}) 必须大于 'min'({scale_min})")

    if "levels" not in scale:
        _fail(source, "scale 缺少必填字段 'levels'")
    _require_covers_every_rank(scale["levels"], "levels", scale_min, scale_max, source, where="scale")
    return scale_min, scale_max


def _validate_observation_points(
    data: Dict[str, Any], scale_min: int, scale_max: int, source: str
) -> None:
    points = data.get("dimensions")
    if not isinstance(points, list) or not points:
        _fail(source, f"缺少必填字段 'dimensions'（观测点列表）或它为空，实际为 {points!r}")

    seen_codes: Dict[str, int] = {}
    weights: List[float] = []

    for index, point in enumerate(points):
        where = f"dimensions[{index}]"
        if not isinstance(point, dict):
            _fail(source, f"{where} 必须是映射，实际为 {point!r}")

        missing = [key for key in _REQUIRED_OBSERVATION_POINT if key not in point]
        if missing:
            _fail(source, f"{where} 缺少必填字段 {missing}")

        code = _require_non_blank_str(point, "code", source, where=where)
        where = f"观测点 {code}"
        _require_non_blank_str(point, "name", source, where=where)

        if code in seen_codes:
            _fail(source, f"观测点 code '{code}' 重复（dimensions[{seen_codes[code]}] 与 [{index}]）"
                          "——同 code 会在查找表里互相覆盖，静默丢掉一个观测点")
        seen_codes[code] = index

        weight = point["weight"]
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            _fail(source, f"{where}的 'weight' 必须是数字，实际为 {weight!r}")
        # 必须为正：0 权重的观测点对分数毫无贡献（写它就是配置错误），且当它恰好是
        # 唯一评价成功的观测点时，聚合的按存活权重归一化会除以 0。
        if weight <= 0:
            _fail(source, f"{where}的 'weight' 必须大于 0，实际为 {weight!r}")
        weights.append(float(weight))

        _require_covers_every_rank(point["anchors"], "anchors", scale_min, scale_max, source, where=where)

    total = math.fsum(weights)
    if not math.isclose(total, 1.0, abs_tol=_WEIGHT_SUM_TOLERANCE):
        _fail(
            source,
            f"全部观测点的 weight 之和为 {total:g}，必须为 1.0"
            "——加了观测点忘了调权重时，分数会静默漂移",
        )


def validate_rubric(data: Any, source: str) -> None:
    """校验一份量规文件的结构；任何一项不满足都抛 ConfigCompileError。

    Args:
        data  : 已解析的量规 YAML 内容。
        source: 用于错误信息的来源标识（通常是文件路径）。"""
    if not isinstance(data, dict):
        _fail(source, f"量规文件的顶层必须是映射，实际为 {type(data).__name__}")

    for key in _REQUIRED_TOP_LEVEL:
        _require_non_blank_str(data, key, source)

    scale_min, scale_max = _validate_scale(data, source)
    _validate_observation_points(data, scale_min, scale_max, source)
