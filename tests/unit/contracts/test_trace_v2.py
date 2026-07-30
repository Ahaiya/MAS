import dataclasses

import pytest

from src.contracts.trace import RunTraceSummary, StageTrace


def _stage_trace(stage: str = "select", ms: float = 120.5) -> StageTrace:
    return StageTrace(stage=stage, rater="rater_1", code="A4-1", llm_calls=1, tokens=500, ms=ms)


def test_stage_trace_constructs_with_valid_fields() -> None:
    st = _stage_trace()
    assert st.stage == "select"
    assert st.rater == "rater_1"
    assert st.code == "A4-1"
    assert st.llm_calls == 1
    assert st.tokens == 500


def test_stage_trace_is_immutable() -> None:
    st = _stage_trace()
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.tokens = 999  # type: ignore[misc]


def test_stage_trace_rejects_negative_values() -> None:
    with pytest.raises(ValueError):
        StageTrace(stage="select", rater=None, code=None, llm_calls=-1, tokens=0, ms=0.0)


def test_stage_trace_allows_none_rater_and_code_for_cross_dimension_stages() -> None:
    st = StageTrace(stage="feedback", rater=None, code=None, llm_calls=0, tokens=0, ms=5.0)
    assert st.rater is None
    assert st.code is None


def test_run_trace_summary_constructs_with_valid_fields() -> None:
    summary = RunTraceSummary(
        run_id="run-1",
        configs_ref="configs",
        dim="a4",
        total_tokens=4000,
        total_ms=8000.0,
        adjudicated_codes=["A4-2"],
        stage_traces=[_stage_trace()],
    )
    assert summary.run_id == "run-1"
    assert summary.adjudicated_codes == ["A4-2"]
    assert summary.to_dict()["dim"] == "a4"
    assert summary.to_dict()["stage_traces"] == [_stage_trace().to_dict()]


def test_run_trace_summary_is_immutable() -> None:
    summary = RunTraceSummary(
        run_id="run-1",
        configs_ref="configs",
        dim="a4",
        total_tokens=0,
        total_ms=0.0,
        adjudicated_codes=[],
        stage_traces=[],
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.total_tokens = 1  # type: ignore[misc]
