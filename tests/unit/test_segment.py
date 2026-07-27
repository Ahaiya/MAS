from pathlib import Path

import pytest

from src.segment import build_package, estimate_tokens, read_text_file, segment_text


def _slice_matches(text: str, unit) -> bool:
    return text[unit.char_range[0] : unit.char_range[1]] == unit.text


# ── kind: prose ──────────────────────────────────────────────────────────────


def test_prose_splits_by_sentence_delimiters_and_newline() -> None:
    text = "第一句。第二句！\n第三句；第四句？\n"
    units = segment_text(text, "a.md")
    assert [u.kind for u in units] == ["prose"] * 4
    assert [u.text for u in units] == ["第一句。", "第二句！", "第三句；", "第四句？"]
    assert all(_slice_matches(text, u) for u in units)


def test_prose_char_range_maps_back_to_source() -> None:
    text = "开头无关文字 第一句。第二句！"
    units = segment_text(text, "a.md")
    assert all(_slice_matches(text, u) for u in units)


# ── kind: code ───────────────────────────────────────────────────────────────


def test_code_block_becomes_single_unit() -> None:
    text = "前言。\n\n```python\nprint(1)\nprint(2)\n```\n\n后语。\n"
    units = segment_text(text, "a.md")
    code_units = [u for u in units if u.kind == "code"]
    assert len(code_units) == 1
    assert code_units[0].text == "```python\nprint(1)\nprint(2)\n```"
    assert all(_slice_matches(text, u) for u in units)


def test_unterminated_code_fence_consumes_to_end_of_document() -> None:
    text = "```python\nprint(1)\n"
    units = segment_text(text, "a.md")
    assert len(units) == 1
    assert units[0].kind == "code"
    assert _slice_matches(text, units[0])


# ── kind: table_row ──────────────────────────────────────────────────────────


def test_table_rows_become_one_unit_per_line() -> None:
    text = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    units = segment_text(text, "a.md")
    assert [u.kind for u in units] == ["table_row"] * 3
    assert units[0].text == "| a | b |"
    assert units[2].text == "| 1 | 2 |"
    assert all(_slice_matches(text, u) for u in units)


def test_table_run_stops_at_non_table_line() -> None:
    text = "| a | b |\n| 1 | 2 |\n普通段落。\n"
    units = segment_text(text, "a.md")
    assert [u.kind for u in units] == ["table_row", "table_row", "prose"]


# ── kind: heading ────────────────────────────────────────────────────────────


def test_heading_becomes_one_unit_carrying_level_via_raw_text() -> None:
    text = "## 二级标题\n正文。\n"
    units = segment_text(text, "a.md")
    assert units[0].kind == "heading"
    assert units[0].text == "## 二级标题"
    assert _slice_matches(text, units[0])


# ── kind: image ──────────────────────────────────────────────────────────────


def test_image_line_becomes_one_unit_using_alt_as_text() -> None:
    text = "![实验装置图](img.png)\n"
    units = segment_text(text, "a.md")
    assert len(units) == 1
    assert units[0].kind == "image"
    assert units[0].text == "实验装置图"
    # char_range 指向完整 markdown 语法所在位置，供前端定位；text 是 alt caption。
    assert text[units[0].char_range[0] : units[0].char_range[1]] == "![实验装置图](img.png)"


def test_image_without_alt_falls_back_to_raw_markdown() -> None:
    text = "![](img.png)\n"
    units = segment_text(text, "a.md")
    assert units[0].text == "![](img.png)"


# ── 全局连续编号 ────────────────────────────────────────────────────────────


def test_unit_ids_are_globally_contiguous_within_one_file() -> None:
    text = "# 标题\n第一句。第二句。\n\n| a |\n| 1 |\n"
    units = segment_text(text, "a.md")
    ids = [u.id for u in units]
    assert ids == list(range(len(units)))


def test_segment_text_respects_start_id_offset() -> None:
    units = segment_text("第一句。", "a.md", start_id=7)
    assert [u.id for u in units] == [7]


# ── 跨文件共享编号空间 ────────────────────────────────────────────────────────


def test_build_package_shares_numbering_across_files() -> None:
    package, dropped = build_package(
        [("a.md", "甲的第一句。甲的第二句。"), ("b.md", "乙的第一句。")],
        package_id="pkg-1",
    )
    assert dropped == []
    ids = [u.id for u in package.units]
    assert ids == list(range(len(package.units)))
    assert [u.source_file for u in package.units] == ["a.md", "a.md", "b.md"]


# ── 超预算丢弃（显式记录，不静默） ────────────────────────────────────────────


def test_short_document_drops_nothing() -> None:
    package, dropped = build_package(
        [("a.md", "第一句。第二句。第三句。")],
        package_id="pkg-1",
        budget_tokens=48000,
    )
    assert dropped == []
    assert len(package.units) == 3


def test_over_budget_drops_tail_units_and_reports_their_ids() -> None:
    sentences = "".join(f"句子{i}。" for i in range(50))
    package, dropped = build_package(
        [("a.md", sentences)], package_id="pkg-1", budget_tokens=10
    )
    kept_ids = [u.id for u in package.units]
    assert dropped, "expected some units to be dropped over budget"
    assert kept_ids + dropped == list(range(50))
    # 丢弃只发生在尾部，保留的编号前缀保持连续
    assert kept_ids == list(range(len(kept_ids)))


def test_budget_always_keeps_at_least_one_unit() -> None:
    package, dropped = build_package(
        [("a.md", "这是一句非常长会独自超出预算的句子。")],
        package_id="pkg-1",
        budget_tokens=1,
    )
    assert len(package.units) == 1
    assert dropped == []


# ── 对话轮次携带 speaker（复用 dialogue_sources） ────────────────────────────


def test_dialogue_turn_units_carry_speaker() -> None:
    text = (
        "### session_init\n"
        "标签: x\n"
        "时间: y\n"
        "```text\n"
        "你好，我需要帮助。\n"
        "```\n"
        "\n"
        "### training_chat_response\n"
        "标签: x\n"
        "时间: y\n"
        "```text\n"
        "当然，我可以帮你。\n"
        "```\n"
    )
    units = segment_text(text, "dlg.md")
    prose_units = [u for u in units if u.kind == "prose" and u.speaker is not None]
    assert {u.speaker for u in prose_units} == {"session_init", "training_chat_response"}


def test_non_dialogue_prose_has_no_speaker() -> None:
    units = segment_text("普通的一句话。", "a.md")
    assert units[0].speaker is None


def test_real_code_fence_is_not_mistaken_for_dialogue() -> None:
    units = segment_text("```python\nprint('hi')\n```\n", "a.md")
    assert len(units) == 1
    assert units[0].kind == "code"
    assert units[0].speaker is None


# ── estimate_tokens ──────────────────────────────────────────────────────────


def test_estimate_tokens_empty_string_is_zero() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_scales_with_length() -> None:
    assert estimate_tokens("word " * 100) > estimate_tokens("word " * 10)


# ── read_text_file (IO 边界) ─────────────────────────────────────────────────


def test_read_text_file_reads_md_file(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.md"
    file_path.write_text("第一句。第二句。", encoding="utf-8")
    package, dropped = read_text_file(str(file_path), package_id="pkg-1")
    assert dropped == []
    assert len(package.units) == 2
    assert package.units[0].source_file == str(file_path)


def test_read_text_file_rejects_unsupported_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.pdf"
    file_path.write_text("irrelevant", encoding="utf-8")
    with pytest.raises(ValueError):
        read_text_file(str(file_path), package_id="pkg-1")


def test_read_text_file_accepts_multiple_paths_sharing_numbering(tmp_path: Path) -> None:
    file_a = tmp_path / "a.md"
    file_b = tmp_path / "b.md"
    file_a.write_text("甲的句子。", encoding="utf-8")
    file_b.write_text("乙的句子。", encoding="utf-8")
    package, dropped = read_text_file([str(file_a), str(file_b)], package_id="pkg-1")
    assert dropped == []
    assert [u.id for u in package.units] == [0, 1]
    assert [u.source_file for u in package.units] == [str(file_a), str(file_b)]
