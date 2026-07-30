import pytest

from src.agents import reconcile
from src.contracts.configuration import PolicySnapshot, RubricSnapshot
from src.contracts.package import DataPackage, Unit
from src.contracts.scoring import DimensionScore, RaterChainResult, ScoreSource
from src.providers.fake import FakeProvider, fake_response
from src.providers.prompt_loader import PromptLoader

_DIMENSIONS = [
    {"code": f"A4-{i}", "name": f"dim{i}",
     "anchors": {r: f"level {r}" for r in range(1, 6)}}
    for i in (1, 2, 3)
]


def _rubric() -> RubricSnapshot:
    return RubricSnapshot(
        dim_id="a4",
        dim_name="A4 用户研究",
        indicator_description="desc",
        dimensions=_DIMENSIONS,
        scale_min=1,
        scale_max=5,
        scale_levels={r: str(r) for r in range(1, 6)},
    )


def _policy() -> PolicySnapshot:
    return PolicySnapshot(score_gap_threshold=1, drift_min_dimensions=2)


def _package() -> DataPackage:
    units = [Unit(id=i, kind="prose", text=f"text {i}", source_file="a.md", char_range=(0, 5), speaker=None) for i in range(5)]
    return DataPackage(package_id="pkg-1", units=units, metadata={})


def _chain(rater_id: str, code: str, score_val: int) -> RaterChainResult:
    return RaterChainResult(
        rater_id=rater_id,
        code=code,
        selected_unit_ids=[0, 1],
        evidence_unit_ids=[0],
        score=DimensionScore(
            score=score_val,
            supporting_unit_ids=[0],
            rationale="r",
            confidence=0.8,
        ),
    )


def _adjudication_template():
    return PromptLoader().load("configs/prompts/adjudication.yaml")


# ── 一致：不触发 Rater3 ──────────────────────────────────────────────────────


def test_all_consensus_does_not_call_rater_3() -> None:
    chains_a = [_chain("rater_1", "A4-1", 3), _chain("rater_1", "A4-2", 4), _chain("rater_1", "A4-3", 5)]
    chains_b = [_chain("rater_2", "A4-1", 3), _chain("rater_2", "A4-2", 4), _chain("rater_2", "A4-3", 5)]
    rater_3_provider = FakeProvider([])  # 一次调用都不该发生，脚本为空

    decisions = reconcile.reconcile(_package(), chains_a, chains_b, _rubric(), _policy(), rater_3_provider, _adjudication_template())

    assert {d.source for d in decisions} == {ScoreSource.CONSENSUS}
    assert len(rater_3_provider.requests) == 0
    assert {d.code: d.final_score for d in decisions} == {"A4-1": 3, "A4-2": 4, "A4-3": 5}


def test_isolated_diff_of_one_is_consensus_and_picks_first_chain_deterministically() -> None:
    """分差恰好为 1、且不构成同向漂移组时不触发仲裁（视为一致）；两侧不等时，
    一致值确定性地取 chains_a（而非平均/取高分——那是被删除的 v1 兜底路径）。"""
    chains_a = [_chain("rater_1", "A4-1", 3), _chain("rater_1", "A4-2", 4), _chain("rater_1", "A4-3", 5)]
    chains_b = [_chain("rater_2", "A4-1", 4), _chain("rater_2", "A4-2", 4), _chain("rater_2", "A4-3", 5)]

    decisions = reconcile.reconcile(_package(), chains_a, chains_b, _rubric(), _policy())
    by_dim = {d.code: d for d in decisions}

    assert by_dim["A4-1"].source == ScoreSource.CONSENSUS
    assert by_dim["A4-1"].final_score == 3  # chains_a 的值，不是平均 3.5 或取高分 4


# ── 分差 > 1：触发 Rater3 → adjudicated ──────────────────────────────────────


def test_score_distance_over_one_triggers_adjudication() -> None:
    chains_a = [_chain("rater_1", "A4-1", 2), _chain("rater_1", "A4-2", 4), _chain("rater_1", "A4-3", 5)]
    chains_b = [_chain("rater_2", "A4-1", 4), _chain("rater_2", "A4-2", 4), _chain("rater_2", "A4-3", 5)]
    rater_3_provider = FakeProvider(
        [fake_response({"proposed_score": 3, "supporting_unit_ids": [0], "confidence": 0.9, "rationale": "仲裁"})]
    )

    decisions = reconcile.reconcile(_package(), chains_a, chains_b, _rubric(), _policy(), rater_3_provider, _adjudication_template())
    by_dim = {d.code: d for d in decisions}

    assert by_dim["A4-1"].source == ScoreSource.ADJUDICATED
    assert by_dim["A4-1"].final_score == 3
    assert by_dim["A4-2"].source == ScoreSource.CONSENSUS
    assert by_dim["A4-3"].source == ScoreSource.CONSENSUS
    assert len(rater_3_provider.requests) == 1


# ── 同向相邻漂移 ≥ 2：触发 ────────────────────────────────────────────────────


def test_adjacent_drift_across_two_dims_triggers_adjudication() -> None:
    chains_a = [_chain("rater_1", "A4-1", 3), _chain("rater_1", "A4-2", 3), _chain("rater_1", "A4-3", 5)]
    chains_b = [_chain("rater_2", "A4-1", 4), _chain("rater_2", "A4-2", 4), _chain("rater_2", "A4-3", 5)]
    rater_3_provider = FakeProvider(
        [
            fake_response({"proposed_score": 3, "supporting_unit_ids": [0]}),
            fake_response({"proposed_score": 4, "supporting_unit_ids": [0]}),
        ]
    )

    decisions = reconcile.reconcile(_package(), chains_a, chains_b, _rubric(), _policy(), rater_3_provider, _adjudication_template())
    by_dim = {d.code: d for d in decisions}

    assert by_dim["A4-1"].source == ScoreSource.ADJUDICATED
    assert by_dim["A4-2"].source == ScoreSource.ADJUDICATED
    assert by_dim["A4-3"].source == ScoreSource.CONSENSUS
    assert len(rater_3_provider.requests) == 2


# ── 缺 rater_3 provider/模板：直接报错 ────────────────────────────────────────


def test_missing_rater_3_provider_raises_instead_of_silent_fallback() -> None:
    chains_a = [_chain("rater_1", "A4-1", 2)]
    chains_b = [_chain("rater_2", "A4-1", 4)]

    with pytest.raises(ValueError, match="rater_3"):
        reconcile.reconcile(_package(), chains_a, chains_b, _rubric(), _policy())


def test_missing_rater_3_provider_is_fine_when_no_conflict() -> None:
    """没有分歧时根本用不到 Rater3，缺 provider 不该报错。"""
    chains_a = [_chain("rater_1", "A4-1", 3)]
    chains_b = [_chain("rater_2", "A4-1", 3)]

    decisions = reconcile.reconcile(_package(), chains_a, chains_b, _rubric(), _policy())

    assert decisions[0].source == ScoreSource.CONSENSUS


# ── 仲裁输出引用有效 unit_ids ─────────────────────────────────────────────────


def test_adjudicated_decision_unit_ids_resolve_back_to_source_text() -> None:
    chains_a = [_chain("rater_1", "A4-1", 2)]
    chains_b = [_chain("rater_2", "A4-1", 4)]
    rater_3_provider = FakeProvider([fake_response({"proposed_score": 3, "supporting_unit_ids": [2, 3]})])
    package = _package()

    decisions = reconcile.reconcile(package, chains_a, chains_b, _rubric(), _policy(), rater_3_provider, _adjudication_template())

    cited_texts = [package.get_unit(uid).text for uid in decisions[0].unit_ids]
    assert cited_texts == ["text 2", "text 3"]


# ── 维度集合不一致：报错 ─────────────────────────────────────────────────────


def test_mismatched_dimension_sets_raise() -> None:
    chains_a = [_chain("rater_1", "A4-1", 3)]
    chains_b = [_chain("rater_2", "A4-2", 3)]

    with pytest.raises(ValueError):
        reconcile.reconcile(_package(), chains_a, chains_b, _rubric(), _policy())
