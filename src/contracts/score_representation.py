"""
分数表示契约，明确区分 canonical score 与展示层分数字段。

Score Representation Contract

定义 canonical (integer) score 与展示形式之间的明确分离。

关键原则：canonical_score 是用于所有计算（QWK、聚合公式、裁决触发评估）的权威整数。
display_annotation 是纯粹的装饰性修饰符，绝不会影响计算。应用注释的时机和方式的规则存在于
configs/ display overlay config 中，而不是在此契约中。

不变量：display_score == str(canonical_score) + (display_annotation or "")

此处不对分数尺度范围、特征名称、注释值或公式细节进行硬编码。所有此类数据通过 rubric config artifacts 流转。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ScoreRepresentation:
    """带有可选 display annotation 的 Canonical integer score。
    
        Attributes:
            canonical_score: 用于所有计算的整数分数。
                             有效范围由 scale_ref config 定义，而不是由此类定义。
            display_annotation: 可选的装饰性修饰符（例如，"-"）。
                                允许的值来自 display overlay config。
                                None 表示没有注释；展示为纯整数。
            display_score: 渲染后的展示字符串。
                           不变量：display_score == str(canonical_score) + (display_annotation or "")
            scale_ref: 对 rubric config artifact 中尺度定义的引用。
                       必须与已加载 RubricSnapshot 中的 scale_id 匹配。"""

    canonical_score: int
    display_annotation: Optional[str]
    display_score: str
    scale_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_score, int):
            raise TypeError(
                f"canonical_score must be int, got {type(self.canonical_score).__name__}"
            )
        expected_display = str(self.canonical_score) + (self.display_annotation or "")
        if self.display_score != expected_display:
            raise ValueError(
                f"display_score '{self.display_score}' is inconsistent with "
                f"canonical_score={self.canonical_score} and "
                f"display_annotation={self.display_annotation!r}. "
                f"Expected '{expected_display}'."
            )

    def is_annotated(self) -> bool:
        """如果此分数带有 display annotation，则返回 True。"""
        return self.display_annotation is not None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为纯 dict (JSON-safe)。"""
        return {
            "canonical_score": self.canonical_score,
            "display_annotation": self.display_annotation,
            "display_score": self.display_score,
            "scale_ref": self.scale_ref,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScoreRepresentation:
        """从 to_dict() 生成的纯 dict 反序列化。"""
        return cls(
            canonical_score=data["canonical_score"],
            display_annotation=data.get("display_annotation"),
            display_score=data["display_score"],
            scale_ref=data["scale_ref"],
        )


def create_score_representation(
    canonical_score: int,
    scale_ref: str,
    display_annotation: Optional[str] = None,
) -> ScoreRepresentation:
    """工厂方法：创建一个自动计算 display_score 的 ScoreRepresentation。
    
        计算 display_score = str(canonical_score) + (display_annotation or "")，
        确保在创建时始终满足不变量。
    
        Args:
            canonical_score: 用于计算的整数分数。
            scale_ref: 对 rubric scale 的引用（例如，"ordinal_4", "ordinal_10"）。
            display_annotation: 可选的 display modifier（例如，"-"）。
                                None 产生纯整数展示。
    
        Returns:
            一个新的、冻结的 ScoreRepresentation。"""
    display_score = str(canonical_score) + (display_annotation or "")
    return ScoreRepresentation(
        canonical_score=canonical_score,
        display_annotation=display_annotation,
        display_score=display_score,
        scale_ref=scale_ref,
    )
