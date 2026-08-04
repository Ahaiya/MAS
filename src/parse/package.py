"""
版面块（layout）→ DataPackage 的纯函数映射。

"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from src.contracts.package import DataPackage, Unit


# 黑名单，当前为空 = 全部放行；确认某个 type 恒为噪音（页眉页脚之类）再往里加。
LAYOUT_BLACKLIST: frozenset[str] = frozenset()

# 这些 type 的正文在 `text` 而非 `markdownContent`：图块的 markdownContent 只是一个
# 带过期签名的图片链接（当天失效），VLM 的图片理解结果在 text 里。
_TEXT_FIELD_TYPES = frozenset({"figure", "picture"})


def build_package(
    files: Sequence[Tuple[str, List[Dict[str, Any]]]],
    package_id: str,
    options: Dict[str, Any],
    parsed_at: str,
) -> DataPackage:
    """把多个源文件各自的 layouts 合并成一个共享编号空间的 DataPackage。

    Args:
        files: [(源文件名, 该文件的 layouts)]，顺序即编号顺序。
        package_id: `"{task}/{submission}"`。
        options: 本次解析用的增强开关，原样记入 provenance。
        parsed_at: ISO 时间戳，由调用方给（保持本函数确定性、可测）。

    Returns:
        编号从 0 起全局连续的 DataPackage；被剔除的 layout 按 type 计数记入
        `provenance.excluded_layouts`——挡住噪音，但绝不静默丢弃。"""
    units: List[Unit] = []
    excluded: Dict[str, int] = {}

    for source_file, layouts in files:
        # 按 (pageNum, index) 排序后再编号：编号顺序必须是人读材料的顺序。
        # index 是**页内**序号（每页从 0 重数）
        for layout in sorted(
            layouts, key=lambda item: (int(item.get("pageNum", 0)), int(item.get("index", 0)))
        ):
            layout_type = str(layout.get("type", ""))
            if layout_type in LAYOUT_BLACKLIST:
                excluded[layout_type] = excluded.get(layout_type, 0) + 1
                continue
            field = "text" if layout_type in _TEXT_FIELD_TYPES else "markdownContent"
            units.append(
                Unit(
                    id=len(units),
                    markdown=str(layout.get(field, "")),
                    type=layout_type,
                    source_file=source_file,
                    page=int(layout.get("pageNum", 0)),
                )
            )

    return DataPackage(
        package_id=package_id,
        units=units,
        provenance={
            "parsed_at": parsed_at,
            "source_files": [name for name, _ in files],
            "options": dict(options),
            "excluded_layouts": excluded,
        },
    )
