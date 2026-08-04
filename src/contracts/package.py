"""
datapackage schema，定义解析后的单元与数据包结构。

Unit 是解析层（src/parse/）产出的最小可引用片段——**一个进白名单的版面块（layout）
就是一个 Unit**，不做字符串拼接、不做句级细分。证据链（RaterChainResult/
DimensionScore）以 unit_ids 引用它而非复述原文。

DataPackage 是引擎的最小输入单元：量规 + DataPackage → 评价。引擎不关心数据包
从哪来。

设计不变式：
- Unit / DataPackage 均为冻结（不可变）dataclass。
- Unit.id 全局唯一；跨多文件共享同一编号空间。
- Unit.type 是解析服务的原值，本系统不维护取值表——哪些 type 进包由解析层的
  白名单决定（src/parse/package.py），契约只存值。
- DataPackage.provenance 记「这份包是怎么来的」：解析时间、源文件清单、解析
  开关、各类型被剔除了多少。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ── Unit ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Unit:
    """一个版面块对应的最小可引用片段。

    Attributes：
        id: 全局连续编号，跨多文件共享同一编号空间。
        markdown: 单元正文，取版面块的 markdownContent（带格式），**不是** text。
        type: 版面块类型，解析服务的原值，不做翻译。
        source_file: 该单元来自这次提交里的哪个文件。
        page: 页码，**0 起**（对齐解析服务的 pageNum）。"""

    id: int
    markdown: str
    type: str
    source_file: str
    page: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "markdown": self.markdown,
            "type": self.type,
            "source_file": self.source_file,
            "page": self.page,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Unit":
        return cls(
            id=int(data["id"]),
            markdown=str(data["markdown"]),
            type=str(data["type"]),
            source_file=str(data["source_file"]),
            page=int(data["page"]),
        )


# ── DataPackage ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DataPackage:
    """引擎的最小输入单元：量规 + DataPackage → 评价。

    Attributes：
        package_id: `"{task}/{submission}"`。
        units: 全局连续编号的 Unit 列表。
        provenance: 溯源信息——parsed_at / source_files / options / excluded_layouts。"""

    package_id: str
    units: List[Unit]
    provenance: Dict[str, Any]

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
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DataPackage":
        """读回 package.json——产物里的 unit_ids 要解读回原文就得有这一步。"""
        return cls(
            package_id=str(data["package_id"]),
            units=[Unit.from_dict(u) for u in data["units"]],
            provenance=dict(data.get("provenance", {})),
        )
