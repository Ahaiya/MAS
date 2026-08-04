"""
版面块（layout）→ DataPackage 的纯函数映射。

零网络、零 LLM、确定性：同样的 layouts 永远产出同样的 DataPackage。一个进白名单
的 layout 就是一个 Unit——不拼接 markdown 字符串、不做句级细分。解析服务已经把
结构算好了，拼回一根字符串再用正则切一遍是把它扔掉再猜一遍。

不做任何预算裁剪：本层产出**完整**包，预算裁剪归引擎侧（预算值耦合模型上下文
窗口，且这样换模型不必重新付费解析）。"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from src.contracts.package import DataPackage, Unit

# 白名单**写死在代码里，不进配置**：它决定的是编号身份而非内容质量。开关调错得到
# 的是质量差些的材料，看得出来；白名单调错得到的是编号与历史不可比的材料，看不出来。
LAYOUT_WHITELIST = frozenset(
    {
        "title",
        "text",
        "table",
        "table_name",
        "table_note",
        "figure",
        "picture",
        "formula",
        "contents",
    }
)


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
        # 按 index（阅读顺序）排序后再编号：编号顺序必须是人读材料的顺序。
        for layout in sorted(layouts, key=lambda item: int(item.get("index", 0))):
            layout_type = str(layout.get("type", ""))
            if layout_type not in LAYOUT_WHITELIST:
                excluded[layout_type] = excluded.get(layout_type, 0) + 1
                continue
            units.append(
                Unit(
                    id=len(units),
                    markdown=str(layout.get("markdownContent", "")),
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
