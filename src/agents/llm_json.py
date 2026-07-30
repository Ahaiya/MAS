"""
LLM 结构化调用的共用管道：构造请求、调用 provider、解析 JSON、校验 unit_ids。

rater.py（select/extract/score）与 adjudicator.py（Rater3 仲裁）的每次 LLM 调用
形状完全一致——构造带 metadata 的 LLMRequest、调用 provider、解析出 dict、把
模型返回的 unit_ids 转成 int 并校验是否越界。三处共用，抽到这里而不是互相 import
对方的私有函数。"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from src.providers.base import BaseProvider, LLMRequest, LLMResponse
from src.providers.prompt_loader import PromptTemplate
from src.providers.structured_output import normalize_structured_output


def _parse_json_response(response: LLMResponse, schema: Dict[str, Any]) -> Dict[str, Any]:
    if response.structured_data is not None:
        return response.structured_data
    return normalize_structured_output(response.content, schema=schema)


def call_llm(
    provider: BaseProvider,
    prompt_text: str,
    schema: Dict[str, Any],
    *,
    node_id: str,
    stage_name: str,
    code: str,
    rater_id: str,
    template: PromptTemplate,
) -> Dict[str, Any]:
    """构造带 metadata 的 LLMRequest、调用 provider、解析结构化输出为 dict。"""
    request = LLMRequest(
        prompt=prompt_text,
        output_schema=schema,
        metadata={
            "node_id": node_id,
            "stage_name": stage_name,
            "code": code,
            "rater_id": rater_id,
            "template_source": template.source_path,
        },
    )
    response = provider.complete(request)
    return _parse_json_response(response, schema)


def coerce_int_ids(raw: Any) -> List[int]:
    """把模型返回的 unit_ids 原样容错转换为 int 列表，去重且保持首次出现顺序。"""
    if not isinstance(raw, list):
        return []
    seen: Set[int] = set()
    ids: List[int] = []
    for item in raw:
        try:
            unit_id = int(item)
        except (TypeError, ValueError):
            continue
        if unit_id in seen:
            continue
        seen.add(unit_id)
        ids.append(unit_id)
    return ids


def reject_out_of_bounds(ids: List[int], valid_ids: Set[int], *, rater_id: str, stage: str) -> None:
    """模型引用的 unit_ids 必须都在 valid_ids 内，否则直接拒绝（不静默降级）。"""
    invalid = [uid for uid in ids if uid not in valid_ids]
    if invalid:
        raise ValueError(
            f"rater '{rater_id}' {stage}: unit_ids {invalid} 引用了未展示给模型的单元编号"
            "（越界证据引用被拒绝）。"
        )
