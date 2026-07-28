from src.agents.prompt_builders import (
    build_rater_extraction_prompt,
    build_rater_scoring_prompt,
    build_rater_select_prompt,
)
from src.contracts.package import DataPackage, Unit
from src.providers.prompt_loader import PromptLoader

_DIMENSION = {
    "dimension_id": "a4_1",
    "code": "A4-1",
    "name": "用户群体识别的全面性",
    "scale_ref": "ordinal_1_5",
    "levels": [
        {"rank": 5, "summary": "优秀", "descriptors": ["能识别多类用户"]},
        {"rank": 1, "summary": "待改进", "descriptors": ["只提到笼统群体"]},
    ],
}

_UNITS = [
    Unit(id=0, kind="prose", text="老年用户经常无法看懂界面文字。" * 5, source_file="a.md", char_range=(0, 10), speaker=None),
    Unit(id=1, kind="prose", text="年轻用户反馈良好。", source_file="a.md", char_range=(10, 20), speaker=None),
]


def _package() -> DataPackage:
    return DataPackage(package_id="pkg-1", units=list(_UNITS), metadata={})


def test_select_prompt_shows_unit_id_and_truncated_preview() -> None:
    template = PromptLoader().load("configs/prompts/select.yaml")
    prompt = build_rater_select_prompt(_package(), _DIMENSION, template, preview_bytes=15)

    assert "用户群体识别的全面性" in prompt
    assert "[0](prose)" in prompt
    assert "[1](prose)" in prompt
    # 预览被截断到 15 个字节，完整正文不应整段出现
    assert _UNITS[0].text not in prompt


def test_select_prompt_preview_truncates_by_utf8_bytes_not_chars() -> None:
    template = PromptLoader().load("configs/prompts/select.yaml")
    # 每个中文字符在 UTF-8 下占 3 字节；6 字节应恰好截到前 2 个字符
    prompt = build_rater_select_prompt(_package(), _DIMENSION, template, preview_bytes=6)

    assert "老年" in prompt
    assert "老年用" not in prompt


def test_select_prompt_includes_dimension_anchors() -> None:
    template = PromptLoader().load("configs/prompts/select.yaml")
    prompt = build_rater_select_prompt(_package(), _DIMENSION, template)

    assert "能识别多类用户" in prompt


def test_extraction_prompt_shows_full_text_of_selected_units_only() -> None:
    template = PromptLoader().load("configs/prompts/extraction.yaml")
    prompt = build_rater_extraction_prompt(_package(), [0], _DIMENSION, template)

    assert _UNITS[0].text in prompt
    assert _UNITS[1].text not in prompt


def test_scoring_prompt_shows_full_text_of_evidence_units_and_anchors() -> None:
    template = PromptLoader().load("configs/prompts/scoring.yaml")
    prompt = build_rater_scoring_prompt(_package(), [1], _DIMENSION, template)

    assert _UNITS[1].text in prompt
    assert _UNITS[0].text not in prompt
    assert "能识别多类用户" in prompt
