from src.agents.prompt_builders import (
    build_adjudication_prompt,
    build_feedback_prompt,
    build_rater_extraction_prompt,
    build_rater_scoring_prompt,
    build_rater_select_prompt,
)
from src.contracts.package import DataPackage, Unit
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import DimensionScore, FinalDecision, RaterChainResult, ScoreSource
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

_INDICATOR_DESCRIPTION = "本指标考察学生是否能识别产品所面向的不同用户群体及其差异化需求。"

_UNITS = [
    Unit(id=0, kind="prose", text="老年用户经常无法看懂界面文字。" * 5, source_file="a.md", char_range=(0, 10), speaker=None),
    Unit(id=1, kind="prose", text="年轻用户反馈良好。", source_file="a.md", char_range=(10, 20), speaker=None),
]


def _package() -> DataPackage:
    return DataPackage(package_id="pkg-1", units=list(_UNITS), metadata={})


def test_select_prompt_shows_unit_id_and_truncated_preview() -> None:
    template = PromptLoader().load("configs/prompts/select.yaml")
    prompt = build_rater_select_prompt(_package(), _DIMENSION, template, "", preview_bytes=15)

    assert "用户群体识别的全面性" in prompt
    assert "[0](prose)" in prompt
    assert "[1](prose)" in prompt
    # 预览被截断到 15 个字节，完整正文不应整段出现
    assert _UNITS[0].text not in prompt


def test_select_prompt_preview_truncates_by_utf8_bytes_not_chars() -> None:
    template = PromptLoader().load("configs/prompts/select.yaml")
    # 每个中文字符在 UTF-8 下占 3 字节；6 字节应恰好截到前 2 个字符
    prompt = build_rater_select_prompt(_package(), _DIMENSION, template, "", preview_bytes=6)

    assert "老年" in prompt
    assert "老年用" not in prompt


def test_select_prompt_includes_dimension_anchors() -> None:
    template = PromptLoader().load("configs/prompts/select.yaml")
    prompt = build_rater_select_prompt(_package(), _DIMENSION, template, "")

    assert "能识别多类用户" in prompt


def test_extraction_prompt_shows_full_text_of_selected_units_only() -> None:
    template = PromptLoader().load("configs/prompts/extraction.yaml")
    prompt = build_rater_extraction_prompt(_package(), [0], _DIMENSION, template, "")

    assert _UNITS[0].text in prompt
    assert _UNITS[1].text not in prompt


def test_scoring_prompt_shows_full_text_of_evidence_units_and_anchors() -> None:
    template = PromptLoader().load("configs/prompts/scoring.yaml")
    prompt = build_rater_scoring_prompt(_package(), [1], _DIMENSION, template)

    assert _UNITS[1].text in prompt
    assert _UNITS[0].text not in prompt
    assert "能识别多类用户" in prompt


# ── indicator_description 的注入边界 ──────────────────────────────────────────
#
# 它是「评价标准」的泛论，和锚点抢同一个角色（判档依据）。选段/取证要它来判断
# 相关性；判档阶段（评分/仲裁/反馈）不给它看，比让它别看更可靠。


def test_select_prompt_includes_indicator_description() -> None:
    template = PromptLoader().load("configs/prompts/select.yaml")
    prompt = build_rater_select_prompt(
        _package(), _DIMENSION, template, indicator_description=_INDICATOR_DESCRIPTION
    )

    assert _INDICATOR_DESCRIPTION in prompt


def test_extraction_prompt_includes_indicator_description() -> None:
    template = PromptLoader().load("configs/prompts/extraction.yaml")
    prompt = build_rater_extraction_prompt(
        _package(), [0], _DIMENSION, template, indicator_description=_INDICATOR_DESCRIPTION
    )

    assert _INDICATOR_DESCRIPTION in prompt


def test_scoring_prompt_excludes_indicator_description() -> None:
    template = PromptLoader().load("configs/prompts/scoring.yaml")
    prompt = build_rater_scoring_prompt(_package(), [1], _DIMENSION, template)

    assert _INDICATOR_DESCRIPTION not in prompt


def test_adjudication_prompt_excludes_indicator_description() -> None:
    template = PromptLoader().load("configs/prompts/adjudication.yaml")
    prompt = build_adjudication_prompt(
        _package(), _DIMENSION, _chain("rater_1"), _chain("rater_2"), template
    )

    assert _INDICATOR_DESCRIPTION not in prompt


def test_feedback_prompt_excludes_indicator_description() -> None:
    template = PromptLoader().load("configs/prompts/feedback.yaml")
    prompt = build_feedback_prompt(_package(), _decision(), _DIMENSION, template)

    assert _INDICATOR_DESCRIPTION not in prompt


# ── 档位标签进锚点文本 ───────────────────────────────────────────────────────
#
# 锚点全档必填之后，「descriptors 全空才回落到 summary」永远走不到，档位命名
# （优秀/良好/…）会彻底失效——拼进锚点行让它活过来。


def _chain(rater_id: str) -> RaterChainResult:
    return RaterChainResult(
        rater_id=rater_id,
        dimension_id="a4_1",
        selected_unit_ids=[0, 1],
        evidence_unit_ids=[0],
        score=DimensionScore(
            dimension_id="a4_1",
            score=create_score_representation(4, "ordinal_1_5"),
            supporting_unit_ids=[0],
            rationale="r",
            confidence=0.8,
        ),
    )


def _decision() -> FinalDecision:
    return FinalDecision(
        dimension_id="a4_1",
        final_score=create_score_representation(4, "ordinal_1_5"),
        source=ScoreSource.CONSENSUS,
        unit_ids=[1],
    )


def _render_all_stages() -> dict:
    loader = PromptLoader()
    package = _package()
    return {
        "select": build_rater_select_prompt(
            package, _DIMENSION, loader.load("configs/prompts/select.yaml"), ""
        ),
        "extraction": build_rater_extraction_prompt(
            package, [0], _DIMENSION, loader.load("configs/prompts/extraction.yaml"), ""
        ),
        "scoring": build_rater_scoring_prompt(
            package, [1], _DIMENSION, loader.load("configs/prompts/scoring.yaml")
        ),
        "adjudication": build_adjudication_prompt(
            package, _DIMENSION, _chain("rater_1"), _chain("rater_2"),
            loader.load("configs/prompts/adjudication.yaml"),
        ),
        "feedback": build_feedback_prompt(
            package, _decision(), _DIMENSION, loader.load("configs/prompts/feedback.yaml")
        ),
    }


def test_every_stage_anchor_line_carries_the_level_label() -> None:
    for stage, prompt in _render_all_stages().items():
        assert "5（优秀）：能识别多类用户" in prompt, stage
        assert "1（待改进）：只提到笼统群体" in prompt, stage


def test_anchor_line_without_a_label_falls_back_to_bare_rank() -> None:
    dimension = {**_DIMENSION, "levels": [{"rank": 5, "summary": "", "descriptors": ["能识别多类用户"]}]}
    template = PromptLoader().load("configs/prompts/scoring.yaml")

    prompt = build_rater_scoring_prompt(_package(), [1], dimension, template)

    assert "5：能识别多类用户" in prompt
