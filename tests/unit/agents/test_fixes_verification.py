"""
修正验证测试 — 覆盖 review.md 记录的全部 5 个问题。

Fix 1: consistency_checker（真实版）能正确识别 Cusp Rule（pattern_match 触发器）
Fix 2: adjudicator 使用 rater_3 作为权威裁决，is_resolved=True
Fix 3: compute_composite 被调用时能产出正确总分（无裁决路径）
Fix 4: rater_3 缺失时，兜底路径使用 ResolutionPath.HUMAN_REVIEW 而非 THIRD_RATER
Fix 5: with_resolution 变体使用 average_per_trait_then_weighted_sum + source_raters=["rater_3"]，
       非冲突维度同样取 R3 分数
"""

import pytest

from src.contracts.artifact_bundle import PolicySnapshot
from src.contracts.score_representation import create_score_representation
from src.contracts.scoring import (
    AdjudicationRecord,
    ConflictRecord,
    ConflictType,
    FinalDimensionDecision,
    ResolutionPath,
    ScoreHypothesis,
)
# ConflictType 实际值：NON_ADJACENT / CUSP / OTHER
from src.agents import consistency_checker
from src.agents import adjudicator
from src.policies.aggregation import compute_composite


# ── 维度常量（ASAP Set 8）────────────────────────────────────────────────────

I_DIM = "ideas_content"
O_DIM = "organization"
V_DIM = "voice"
W_DIM = "word_choice"
S_DIM = "sentence_fluency"
C_DIM = "conventions"

CUSP_DIMS = [I_DIM, O_DIM, S_DIM, C_DIM]
ALL_DIMS = [I_DIM, O_DIM, V_DIM, W_DIM, S_DIM, C_DIM]

SCALE = "asap_set8_1_6"


# ── Policy Fixtures ──────────────────────────────────────────────────────────

_ADJ_POLICY = {
    "policy_id": "asap_set8_default_adjudication",
    "policy_version": "v1",
    "raters": {
        "rater_labels": ["rater_1", "rater_2"],
        "resolution_rater_label": "rater_3",
    },
    "triggers": [
        {
            "trigger_id": "non_adjacent_trait_scores",
            "type": "score_distance",
            "applies_to_dimensions": ["*"],
            "threshold": {"operator": ">", "value": 1},
            "action": "invoke_resolution",
            "priority": 1,
        },
        {
            "trigger_id": "cusp_rule",
            "type": "pattern_match",
            "applies_to_dimensions": CUSP_DIMS,
            "pattern": {
                "one_rater_all_scores": [4, 4, 4, 4],
                "other_rater_has_one_3_and_three_4s": True,
            },
            "exclusions": [V_DIM, W_DIM],
            "action": "invoke_resolution",
            "priority": 2,
        },
    ],
}

_AGG_POLICY_WITHOUT_RES = {
    "policy_id": "asap_set8_composite",
    "policy_version": "v1",
    "composite_formula": [
        {
            "variant_id": "without_resolution",
            "applies_when": "resolution_not_used",
            "source_raters": ["rater_1", "rater_2"],
            "aggregation_method": "average_per_trait_then_weighted_sum",
            "weights": {
                I_DIM: 2, O_DIM: 2, V_DIM: 0, W_DIM: 0, S_DIM: 2, C_DIM: 4,
            },
        },
        {
            "variant_id": "with_resolution",
            "applies_when": "resolution_used",
            "source_raters": ["rater_3"],
            "aggregation_method": "average_per_trait_then_weighted_sum",
            "weights": {
                I_DIM: 2, O_DIM: 2, V_DIM: 0, W_DIM: 0, S_DIM: 2, C_DIM: 4,
            },
        },
    ],
}


@pytest.fixture
def policy():
    return PolicySnapshot(
        adjudication_policy=_ADJ_POLICY,
        aggregation_policy=_AGG_POLICY_WITHOUT_RES,
        explanation_policy={},
        policy_version="test-v1",
    )


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _hyp(dim_id: str, rater_id: str, score_val: int) -> ScoreHypothesis:
    return ScoreHypothesis(
        hypothesis_id=f"hyp-{dim_id}-{rater_id}",
        observation_id=f"obs-{dim_id}",
        dimension_id=dim_id,
        rater_id=rater_id,
        score=create_score_representation(score_val, SCALE),
        descriptor_refs=[],
        evidence_span_ids=[],
        rationale="test",
        confidence=0.9,
    )


def _decision(dim_id: str, score_val: int, adj_id: str = None) -> FinalDimensionDecision:
    return FinalDimensionDecision(
        decision_id=f"dec-{dim_id}",
        dimension_id=dim_id,
        final_score=create_score_representation(score_val, SCALE),
        primary_hypothesis_id=f"hyp-{dim_id}-rater_1",
        adjudication_id=adj_id,
        evidence_span_ids=[],
        descriptor_refs=[],
        decision_confidence=0.9,
        decision_note=None,
    )


def _adj_record(dim_id: str, is_resolved: bool = True) -> AdjudicationRecord:
    return AdjudicationRecord(
        adjudication_id=f"adj-{dim_id}",
        conflict_id=f"con-{dim_id}",
        dimension_id=dim_id,
        resolution_path=ResolutionPath.THIRD_RATER if is_resolved else ResolutionPath.HUMAN_REVIEW,
        policy_rule_id="cusp_rule",
        resolved_score=create_score_representation(5, SCALE) if is_resolved else None,
        resolver_hypothesis_id=f"hyp-{dim_id}-rater_3" if is_resolved else None,
        resolution_note=None,
        is_resolved=is_resolved,
    )


# ── Fix 1: consistency_checker 识别 Cusp Rule ────────────────────────────────

class TestFix1CuspRuleDetected:
    """Fix 1: runner 现在调用真实 consistency_checker，能识别 Cusp Rule。"""

    def test_cusp_pattern_44_vs_34_triggers_conflict(self, policy):
        """一方 [4,4,4,4]，另一方 [3,4,4,4]，应产生冲突记录。"""
        hyps = [
            _hyp(I_DIM, "rater_1", 4), _hyp(O_DIM, "rater_1", 4),
            _hyp(S_DIM, "rater_1", 4), _hyp(C_DIM, "rater_1", 4),
            _hyp(V_DIM, "rater_1", 3), _hyp(W_DIM, "rater_1", 3),
            _hyp(I_DIM, "rater_2", 3), _hyp(O_DIM, "rater_2", 4),
            _hyp(S_DIM, "rater_2", 4), _hyp(C_DIM, "rater_2", 4),
            _hyp(V_DIM, "rater_2", 3), _hyp(W_DIM, "rater_2", 3),
        ]
        conflicts = consistency_checker.run(hyps, policy)
        assert len(conflicts) > 0, "Cusp Rule 应产生至少一个冲突记录"

    def test_cusp_pattern_trigger_id_is_cusp_rule(self, policy):
        """冲突记录应来自 cusp_rule 触发器。"""
        hyps = [
            _hyp(I_DIM, "rater_1", 4), _hyp(O_DIM, "rater_1", 4),
            _hyp(S_DIM, "rater_1", 4), _hyp(C_DIM, "rater_1", 4),
            _hyp(V_DIM, "rater_1", 3), _hyp(W_DIM, "rater_1", 3),
            _hyp(I_DIM, "rater_2", 3), _hyp(O_DIM, "rater_2", 4),
            _hyp(S_DIM, "rater_2", 4), _hyp(C_DIM, "rater_2", 4),
            _hyp(V_DIM, "rater_2", 3), _hyp(W_DIM, "rater_2", 3),
        ]
        conflicts = consistency_checker.run(hyps, policy)
        trigger_ids = {c.trigger_rule_id for c in conflicts}
        assert "cusp_rule" in trigger_ids

    def test_no_cusp_no_distance_no_conflict(self, policy):
        """所有分数相同且无 cusp pattern，不应产生冲突。"""
        hyps = [_hyp(dim, "rater_1", 4) for dim in ALL_DIMS] + \
               [_hyp(dim, "rater_2", 4) for dim in ALL_DIMS]
        conflicts = consistency_checker.run(hyps, policy)
        assert conflicts == []

    def test_score_distance_still_triggers(self, policy):
        """|R1 - R2| > 1 仍应触发冲突（标准规则）。"""
        hyps = [_hyp(dim, "rater_1", 4) for dim in ALL_DIMS] + \
               [_hyp(dim, "rater_2", 4) for dim in ALL_DIMS]
        # ideas_content: R1=4, R2=2 → distance=2 > 1
        hyps = [h for h in hyps if not (h.dimension_id == I_DIM and h.rater_id == "rater_2")]
        hyps.append(_hyp(I_DIM, "rater_2", 2))
        conflicts = consistency_checker.run(hyps, policy)
        assert any(c.dimension_id == I_DIM for c in conflicts)


# ── Fix 2: adjudicator 使用 rater_3 ─────────────────────────────────────────

class TestFix2AdjudicatorUsesRater3:
    """Fix 2: 有冲突时，adjudicator 以 rater_3 分数为权威。"""

    def _make_conflict(self, dim_id: str) -> ConflictRecord:
        return ConflictRecord(
            conflict_id=f"con-{dim_id}",
            dimension_id=dim_id,
            trigger_rule_id="non_adjacent_trait_scores",
            conflict_type=ConflictType.NON_ADJACENT,
            hypothesis_ids=[f"hyp-{dim_id}-rater_1", f"hyp-{dim_id}-rater_2"],
            recommended_path=ResolutionPath.THIRD_RATER,
            conflict_detail="score distance > 1",
        )

    def test_conflicted_dim_uses_rater3_score(self, policy):
        """有冲突维度，选 rater_3 hypothesis 作为权威，final_score 等于 R3 分数。"""
        conflicts = [self._make_conflict(I_DIM)]
        hyps = [
            _hyp(I_DIM, "rater_1", 2),
            _hyp(I_DIM, "rater_2", 5),
            _hyp(I_DIM, "rater_3", 4),  # R3 = 4，应为权威
        ]
        _, decisions = adjudicator.run(conflicts, hyps, policy)
        i_dec = next(d for d in decisions if d.dimension_id == I_DIM)
        assert i_dec.final_score.canonical_score == 4

    def test_conflicted_dim_is_resolved_true(self, policy):
        """有 rater_3 时，AdjudicationRecord.is_resolved 应为 True。"""
        conflicts = [self._make_conflict(I_DIM)]
        hyps = [
            _hyp(I_DIM, "rater_1", 2),
            _hyp(I_DIM, "rater_2", 5),
            _hyp(I_DIM, "rater_3", 4),
        ]
        adj_records, _ = adjudicator.run(conflicts, hyps, policy)
        assert all(r.is_resolved for r in adj_records)

    def test_conflicted_dim_resolution_path_third_rater(self, policy):
        """成功裁决时，ResolutionPath 应为 THIRD_RATER。"""
        conflicts = [self._make_conflict(I_DIM)]
        hyps = [
            _hyp(I_DIM, "rater_1", 2),
            _hyp(I_DIM, "rater_2", 5),
            _hyp(I_DIM, "rater_3", 4),
        ]
        adj_records, _ = adjudicator.run(conflicts, hyps, policy)
        assert adj_records[0].resolution_path == ResolutionPath.THIRD_RATER

    def test_non_conflicted_dim_uses_rater1_score(self, policy):
        """无冲突维度，取 rater_id 字典序最小者（rater_1）的分数。"""
        conflicts = []  # 无冲突
        hyps = [
            _hyp(O_DIM, "rater_1", 3),
            _hyp(O_DIM, "rater_2", 3),
        ]
        _, decisions = adjudicator.run(conflicts, hyps, policy)
        o_dec = next(d for d in decisions if d.dimension_id == O_DIM)
        assert o_dec.final_score.canonical_score == 3
        assert o_dec.adjudication_id is None


# ── Fix 4: rater_3 缺失时升级 HUMAN_REVIEW ───────────────────────────────────

class TestFix4FallbackHumanReview:
    """Fix 4: rater_3 不存在时，兜底路径为 HUMAN_REVIEW，不触发非法状态转换。"""

    def _make_conflict(self, dim_id: str) -> ConflictRecord:
        return ConflictRecord(
            conflict_id=f"con-{dim_id}",
            dimension_id=dim_id,
            trigger_rule_id="non_adjacent_trait_scores",
            conflict_type=ConflictType.NON_ADJACENT,
            hypothesis_ids=[f"hyp-{dim_id}-rater_1", f"hyp-{dim_id}-rater_2"],
            recommended_path=ResolutionPath.THIRD_RATER,
            conflict_detail="score distance > 1",
        )

    def test_missing_rater3_gives_human_review_path(self, policy):
        """无 rater_3 hypothesis 时，resolution_path 应为 HUMAN_REVIEW。"""
        conflicts = [self._make_conflict(I_DIM)]
        hyps = [
            _hyp(I_DIM, "rater_1", 2),
            _hyp(I_DIM, "rater_2", 5),
            # 故意不提供 rater_3
        ]
        adj_records, _ = adjudicator.run(conflicts, hyps, policy)
        assert adj_records[0].resolution_path == ResolutionPath.HUMAN_REVIEW

    def test_missing_rater3_is_resolved_false(self, policy):
        """无 rater_3 时，is_resolved 应为 False。"""
        conflicts = [self._make_conflict(I_DIM)]
        hyps = [_hyp(I_DIM, "rater_1", 2), _hyp(I_DIM, "rater_2", 5)]
        adj_records, _ = adjudicator.run(conflicts, hyps, policy)
        assert adj_records[0].is_resolved is False

    def test_missing_rater3_resolved_score_is_none(self, policy):
        """无 rater_3 时，resolved_score 应为 None（不能用 R1/R2 充数）。"""
        conflicts = [self._make_conflict(I_DIM)]
        hyps = [_hyp(I_DIM, "rater_1", 2), _hyp(I_DIM, "rater_2", 5)]
        adj_records, _ = adjudicator.run(conflicts, hyps, policy)
        assert adj_records[0].resolved_score is None


# ── Fix 3: compute_composite 正确计算总分（无裁决路径）────────────────────────

class TestFix3CompositeWithoutResolution:
    """Fix 3a: 无裁决时，composite 使用 R1+R2 平均后加权求和。"""

    def test_all_4_scores_gives_40(self, policy):
        """
        R1=R2=4 for I/O/S/C, V/W weight=0。
        公式：avg(I)*2 + avg(O)*2 + avg(S)*2 + avg(C)*4 = 4*2+4*2+4*2+4*4 = 40
        """
        hyps = (
            [_hyp(dim, "rater_1", 4) for dim in ALL_DIMS] +
            [_hyp(dim, "rater_2", 4) for dim in ALL_DIMS]
        )
        decisions = [_decision(dim, 4) for dim in ALL_DIMS]
        composite = compute_composite(decisions, hyps, [], policy)
        assert composite is not None
        assert composite.composite_score.canonical_score == 40

    def test_variant_id_is_without_resolution(self, policy):
        """无裁决时，应选 without_resolution 变体。"""
        hyps = (
            [_hyp(dim, "rater_1", 3) for dim in ALL_DIMS] +
            [_hyp(dim, "rater_2", 3) for dim in ALL_DIMS]
        )
        decisions = [_decision(dim, 3) for dim in ALL_DIMS]
        composite = compute_composite(decisions, hyps, [], policy)
        assert composite.aggregation_detail["variant_id"] == "without_resolution"

    def test_voice_word_choice_excluded(self, policy):
        """V 和 W 权重为 0，即使分数很高也不影响总分。"""
        hyps = (
            [_hyp(dim, "rater_1", 4) for dim in [I_DIM, O_DIM, S_DIM, C_DIM]] +
            [_hyp(dim, "rater_2", 4) for dim in [I_DIM, O_DIM, S_DIM, C_DIM]] +
            [_hyp(V_DIM, "rater_1", 6), _hyp(V_DIM, "rater_2", 6),
             _hyp(W_DIM, "rater_1", 6), _hyp(W_DIM, "rater_2", 6)]
        )
        decisions = [_decision(dim, 4) for dim in [I_DIM, O_DIM, S_DIM, C_DIM]] + \
                    [_decision(V_DIM, 6), _decision(W_DIM, 6)]
        composite = compute_composite(decisions, hyps, [], policy)
        # V/W 不入总分，结果仍为 40
        assert composite.composite_score.canonical_score == 40


# ── Fix 5: with_resolution 变体使用 R3 分数（含非冲突维度）──────────────────

class TestFix5WithResolutionUsesRater3ForAll:
    """
    Fix 5: with_resolution 变体改为 average_per_trait_then_weighted_sum +
    source_raters=["rater_3"]，所有 composite 维度均取 R3 分数，
    无论该维度是否发生过冲突。
    """

    def test_all_r3_scores_5_gives_50(self, policy):
        """
        全部 R3=5，公式：5*2+5*2+5*2+5*4 = 50
        """
        # R1/R2 分数（低，确保公式不会混用）
        hyps = (
            [_hyp(dim, "rater_1", 2) for dim in ALL_DIMS] +
            [_hyp(dim, "rater_2", 2) for dim in ALL_DIMS] +
            [_hyp(dim, "rater_3", 5) for dim in ALL_DIMS]
        )
        decisions = [_decision(dim, 5, adj_id=f"adj-{dim}") for dim in ALL_DIMS]
        adj_records = [_adj_record(I_DIM, is_resolved=True)]  # 只有 I 维度有裁决记录
        composite = compute_composite(decisions, hyps, adj_records, policy)
        assert composite is not None
        assert composite.composite_score.canonical_score == 50

    def test_variant_id_is_with_resolution(self, policy):
        """有 is_resolved=True 的裁决记录时，应选 with_resolution 变体。"""
        hyps = (
            [_hyp(dim, "rater_1", 3) for dim in ALL_DIMS] +
            [_hyp(dim, "rater_3", 4) for dim in ALL_DIMS]
        )
        decisions = [_decision(dim, 4) for dim in ALL_DIMS]
        adj_records = [_adj_record(I_DIM, is_resolved=True)]
        composite = compute_composite(decisions, hyps, adj_records, policy)
        assert composite.aggregation_detail["variant_id"] == "with_resolution"

    def test_non_conflicted_dims_also_use_r3(self, policy):
        """
        只有 I 维度有冲突，但 with_resolution 变体应对 O/S/C 也取 R3 分数。
        R3: I=5, O=3, S=3, C=3
        期望总分：5*2 + 3*2 + 3*2 + 3*4 = 10+6+6+12 = 34
        """
        hyps = (
            [_hyp(I_DIM, "rater_1", 2), _hyp(I_DIM, "rater_2", 5), _hyp(I_DIM, "rater_3", 5)] +
            [_hyp(O_DIM, "rater_1", 3), _hyp(O_DIM, "rater_2", 3), _hyp(O_DIM, "rater_3", 3)] +
            [_hyp(S_DIM, "rater_1", 3), _hyp(S_DIM, "rater_2", 3), _hyp(S_DIM, "rater_3", 3)] +
            [_hyp(C_DIM, "rater_1", 3), _hyp(C_DIM, "rater_2", 3), _hyp(C_DIM, "rater_3", 3)] +
            [_hyp(V_DIM, "rater_1", 4), _hyp(V_DIM, "rater_3", 4),
             _hyp(W_DIM, "rater_1", 4), _hyp(W_DIM, "rater_3", 4)]
        )
        decisions = [_decision(dim, 5 if dim == I_DIM else 3) for dim in ALL_DIMS]
        adj_records = [_adj_record(I_DIM, is_resolved=True)]
        composite = compute_composite(decisions, hyps, adj_records, policy)
        assert composite.composite_score.canonical_score == 34

    def test_r1_r2_not_used_in_resolution_variant(self, policy):
        """
        with_resolution 时，即使 R1/R2 有极高分数，也不应影响总分。
        R3=1 → 总分应为 1*2+1*2+1*2+1*4 = 10，不受 R1=R2=6 影响。
        """
        hyps = (
            [_hyp(dim, "rater_1", 6) for dim in ALL_DIMS] +
            [_hyp(dim, "rater_2", 6) for dim in ALL_DIMS] +
            [_hyp(dim, "rater_3", 1) for dim in ALL_DIMS]
        )
        decisions = [_decision(dim, 1) for dim in ALL_DIMS]
        adj_records = [_adj_record(I_DIM, is_resolved=True)]
        composite = compute_composite(decisions, hyps, adj_records, policy)
        assert composite.composite_score.canonical_score == 10
