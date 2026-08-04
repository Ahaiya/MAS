"""`src/parse/package.py` 的纯函数测试——零网络、零 mock。"""

from __future__ import annotations

from typing import Any, Dict, List

from src.parse import package as package_module
from src.parse.package import LAYOUT_BLACKLIST, build_package


def _layout(index: int, type: str = "text", **overrides: Any) -> Dict[str, Any]:
    layout = {
        "index": index,
        "type": type,
        "text": f"纯文本 {index}",
        "markdownContent": f"**markdown {index}**",
        "pageNum": 0,
    }
    layout.update(overrides)
    return layout


def _build(files: List[tuple[str, List[Dict[str, Any]]]], **kwargs: Any):
    return build_package(
        files,
        package_id="experiment/2025213223",
        options={"llm_enhancement": True},
        parsed_at="2026-08-04T10:00:00",
        **kwargs,
    )


def test_每个版面块产出一个单元() -> None:
    package = _build([("a.pdf", [_layout(0), _layout(1, "title")])])
    assert [u.type for u in package.units] == ["text", "title"]


def test_单元取_markdown_content_而非_text() -> None:
    package = _build([("a.pdf", [_layout(0)])])
    assert package.units[0].markdown == "**markdown 0**"


def test_图块取_text_因为_markdown_content_只是过期图片链接() -> None:
    """figure/picture 的 markdownContent 是带过期签名的 OSS 链接，VLM 的图片理解在 text。"""
    layouts = [
        _layout(0, "figure", markdownContent="![x](http://oss/3.png?Expires=1)", text="折线图：振幅衰减"),
        _layout(1, "picture", markdownContent="![y](http://oss/4.png?Expires=1)", text="示意图：装置"),
    ]
    package = _build([("a.docx", layouts)])
    assert [u.markdown for u in package.units] == ["折线图：振幅衰减", "示意图：装置"]


def test_未知类型默认进包() -> None:
    """黑名单没列的一律放行：白名单会把没见过的 type 连同整段材料静默扔掉。"""
    package = _build([("a.pdf", [_layout(0, "brand_new_type"), _layout(1, "other")])])
    assert [u.type for u in package.units] == ["brand_new_type", "other"]
    assert package.provenance["excluded_layouts"] == {}


def test_黑名单当前为空_全部放行() -> None:
    assert LAYOUT_BLACKLIST == frozenset()


def test_黑名单里的版面块被剔除且按类型计数(monkeypatch: Any) -> None:
    monkeypatch.setattr(package_module, "LAYOUT_BLACKLIST", frozenset({"foot", "logo"}))
    layouts = [_layout(0), _layout(1, "foot"), _layout(2, "foot"), _layout(3, "logo")]
    package = _build([("a.pdf", layouts)])
    assert len(package.units) == 1
    assert package.provenance["excluded_layouts"] == {"foot": 2, "logo": 1}


def test_编号全局连续且跨文件共享编号空间() -> None:
    package = _build(
        [
            ("a.pdf", [_layout(0), _layout(1)]),
            ("b.pptx", [_layout(0), _layout(1)]),
        ]
    )
    assert [u.id for u in package.units] == [0, 1, 2, 3]
    assert [u.source_file for u in package.units] == ["a.pdf", "a.pdf", "b.pptx", "b.pptx"]


def test_剔除的版面块不产生编号空洞(monkeypatch: Any) -> None:
    monkeypatch.setattr(package_module, "LAYOUT_BLACKLIST", frozenset({"head"}))
    package = _build([("a.pdf", [_layout(0), _layout(1, "head"), _layout(2)])])
    assert [u.id for u in package.units] == [0, 1]


def test_按_index_阅读顺序排序后编号() -> None:
    package = _build([("a.pdf", [_layout(2, markdownContent="第三"), _layout(0, markdownContent="第一"), _layout(1, markdownContent="第二")])])
    assert [u.markdown for u in package.units] == ["第一", "第二", "第三"]


def test_跨页时先按页码再按页内_index() -> None:
    """index 是页内序号（每页从 0 重数）。只按 index 排会把各页的同序号块归堆。"""
    package = _build(
        [
            (
                "a.docx",
                [
                    _layout(1, pageNum=1, markdownContent="二之二"),
                    _layout(0, pageNum=1, markdownContent="二之一"),
                    _layout(1, pageNum=0, markdownContent="一之二"),
                    _layout(0, pageNum=0, markdownContent="一之一"),
                ],
            )
        ]
    )
    assert [u.markdown for u in package.units] == ["一之一", "一之二", "二之一", "二之二"]


def test_页码取_page_num_零起() -> None:
    package = _build([("a.pdf", [_layout(0, pageNum=3)])])
    assert package.units[0].page == 3


def test_溯源信息完整() -> None:
    package = _build([("a.pdf", [_layout(0)]), ("b.png", [])])
    prov = package.provenance
    assert prov["parsed_at"] == "2026-08-04T10:00:00"
    assert prov["source_files"] == ["a.pdf", "b.png"]
    assert prov["options"] == {"llm_enhancement": True}
    assert prov["excluded_layouts"] == {}


def test_空_layouts_不产生编号空洞() -> None:
    package = _build([("empty.pdf", []), ("b.pdf", [_layout(0)])])
    assert [u.id for u in package.units] == [0]


def test_不做任何预算裁剪() -> None:
    """产出完整包，裁剪归引擎侧。"""
    layouts = [_layout(i, markdownContent="很长的一段" * 500) for i in range(50)]
    package = _build([("a.pdf", layouts)])
    assert len(package.units) == 50
