"""
datapackage schema，定义切分后的单元与数据包结构。

Unit 是切分流水线（segment.py）产出的最小可引用文本单元，全局连续编号，
证据链（RaterChainResult/DimensionScore）以 unit_ids 引用它而非复述原文。

DataPackage 是引擎的最小输入单元：量规 + DataPackage → 评价。引擎不关心
数据包的来源（.md/.txt 直读，或未来的多源解析接入层）。

设计不变式：
- Unit / DataPackage 均为冻结（不可变）dataclass。
- Unit.id 全局唯一；跨多文件共享同一编号空间（不要求严格连续，超预算丢弃单元后
  允许出现空洞）。
- Unit.char_range 是 [start, end) 左闭右开区间，映射回 source_file 原文。
- DataPackage.metadata 是前端透传字段（学生ID/任务ID/轮次/时间戳等），引擎不解释。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

UNIT_KINDS = frozenset({"prose", "code", "table_row", "heading", "image"})


# ── Unit ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Unit:
    """segment 产出的最小可引用文本单元。

    Attributes：
        id: 全局连续编号，跨多文件共享同一编号空间。
        kind: 内容类型 —— prose | code | table_row | heading | image。
        text: 单元文本（图片单元为其 caption/描述文本）。
        source_file: 该单元来自的源文件。
        char_range: (start, end) 左闭右开字符偏移，映射回 source_file 原文。
        speaker: 对话轮次归属；非对话内容为 None。"""

    id: int
    kind: str
    text: str
    source_file: str
    char_range: Tuple[int, int]
    speaker: Optional[str]

    def __post_init__(self) -> None:
        if self.kind not in UNIT_KINDS:
            raise ValueError(
                f"Unit {self.id}: kind must be one of {sorted(UNIT_KINDS)}, "
                f"got {self.kind!r}."
            )
        start, end = self.char_range
        if end <= start:
            raise ValueError(
                f"Unit {self.id}: char_range end ({end}) must be > start ({start})."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "source_file": self.source_file,
            "char_range": list(self.char_range),
            "speaker": self.speaker,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Unit":
        start, end = data["char_range"]
        return cls(
            id=int(data["id"]),
            kind=str(data["kind"]),
            text=str(data["text"]),
            source_file=str(data["source_file"]),
            char_range=(int(start), int(end)),
            speaker=data.get("speaker"),
        )


# ── DataPackage ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DataPackage:
    """引擎的最小输入单元：量规 + DataPackage → 评价。

    Attributes：
        package_id: 该数据包的唯一标识符。
        units: 全局连续编号的 Unit 列表。
        metadata: 前端透传字段（学生ID/任务ID/轮次/时间戳等），引擎不解释。"""

    package_id: str
    units: List[Unit]
    metadata: Dict[str, Any]

    def __post_init__(self) -> None:
        seen_ids = set()
        for unit in self.units:
            if unit.id in seen_ids:
                raise ValueError(
                    f"DataPackage '{self.package_id}': duplicate unit id {unit.id}."
                )
            seen_ids.add(unit.id)

    def get_unit(self, unit_id: int) -> Optional[Unit]:
        """返回给定编号的 Unit；不存在则返回 None。"""
        for unit in self.units:
            if unit.id == unit_id:
                return unit
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "units": [u.to_dict() for u in self.units],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DataPackage":
        """读回 package.json——产物里的 unit_ids 要解读回原文就得有这一步。"""
        return cls(
            package_id=str(data["package_id"]),
            units=[Unit.from_dict(u) for u in data["units"]],
            metadata=dict(data.get("metadata", {})),
        )
