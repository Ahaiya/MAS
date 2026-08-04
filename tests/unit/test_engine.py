import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.contracts.package import DataPackage, Unit
from src.engine import DimensionEvaluation, Engine, EngineConfigError
from src.providers.base import (
    BaseProvider,
    LLMRequest,
    LLMResponse,
    ProviderCallError,
    ProviderCapability,
    TokenUsage,
)
from src.providers.fake import FakeProvider, fake_response

_PROMPT_NAMES = ["select.yaml", "extraction.yaml", "scoring.yaml", "adjudication.yaml", "feedback.yaml"]

_DIM_YAML_TEMPLATE = """
dim_id: "{dim_id}"
dim_name: "test {dim_id}"
indicator_description: "desc"
scale:
  min: 1
  max: 5
  levels: {{1: 待改进, 2: 合格, 3: 良好, 4: 优秀, 5: 卓越}}
dimensions:
{sub_dims}
"""

_SUB_DIM_TEMPLATE = '  - {{code: "{code}", name: "sub {code}", weight: {weight}, anchors: {{1: 一档, 2: 二档, 3: 三档, 4: 四档, 5: 五档}}}}'


def _write_dim_yaml(dim_dir: Path, dim_id: str, sub_dim_codes: list, weights: Optional[list] = None) -> None:
    # 权重必须和为 1.0（量规校验要求）；缺省按观测点数均分
    weights = weights or [1 / len(sub_dim_codes)] * len(sub_dim_codes)
    sub_dims = "\n".join(
        _SUB_DIM_TEMPLATE.format(code=code, weight=w) for code, w in zip(sub_dim_codes, weights)
    )
    (dim_dir / f"{dim_id}_rubric.yaml").write_text(
        _DIM_YAML_TEMPLATE.format(dim_id=dim_id, sub_dims=sub_dims), encoding="utf-8"
    )


@pytest.fixture
def configs_root(tmp_path: Path) -> Path:
    """一个最小 configs_root：一个任务下两个二级指标（d1 有 1 个观测点，
    d2 有 1 个观测点），复用仓库真实的 v2 prompt yaml + adjudication policy。"""
    root = tmp_path / "configs"
    (root / "tasks" / "testtask" / "dimension").mkdir(parents=True)
    (root / "prompts").mkdir(parents=True)

    for name in _PROMPT_NAMES:
        shutil.copy(f"configs/prompts/{name}", root / "prompts" / name)
    shutil.copy("configs/adjudication.yaml", root / "adjudication.yaml")

    dim_dir = root / "tasks" / "testtask" / "dimension"
    _write_dim_yaml(dim_dir, "d1", ["D1-1"])
    _write_dim_yaml(dim_dir, "d2", ["D2-1"])

    return root


@pytest.fixture
def configs_root_multi(tmp_path: Path) -> Path:
    """一个最小 configs_root：一个任务下一个二级指标 d1，含 3 个观测点
    （D1-1/D1-2/D1-3）——用于练到观测点级并发（单个观测点不足以触发并发）。"""
    root = tmp_path / "configs"
    (root / "tasks" / "testtask" / "dimension").mkdir(parents=True)
    (root / "prompts").mkdir(parents=True)

    for name in _PROMPT_NAMES:
        shutil.copy(f"configs/prompts/{name}", root / "prompts" / name)
    shutil.copy("configs/adjudication.yaml", root / "adjudication.yaml")

    dim_dir = root / "tasks" / "testtask" / "dimension"
    _write_dim_yaml(dim_dir, "d1", ["D1-1", "D1-2", "D1-3"])

    return root


@pytest.fixture
def configs_root_weighted(tmp_path: Path) -> Path:
    """一个最小 configs_root：二级指标 d1 含 3 个观测点，权重 0.2/0.3/0.5（非均分）——
    用于验证量规里的 weight 真的影响 dim 汇总分。"""
    root = tmp_path / "configs"
    (root / "tasks" / "testtask" / "dimension").mkdir(parents=True)
    (root / "prompts").mkdir(parents=True)

    for name in _PROMPT_NAMES:
        shutil.copy(f"configs/prompts/{name}", root / "prompts" / name)
    shutil.copy("configs/adjudication.yaml", root / "adjudication.yaml")

    _write_dim_yaml(
        root / "tasks" / "testtask" / "dimension", "d1", ["D1-1", "D1-2", "D1-3"], [0.2, 0.3, 0.5]
    )
    return root


def _model_config_with_max_workers(tmp_path: Path, max_workers: int) -> Path:
    path = tmp_path / f"model_config_workers_{max_workers}.yaml"
    path.write_text(f"runtime:\n  max_workers: {max_workers}\n", encoding="utf-8")
    return path


class _StageAwareProvider(BaseProvider):
    """按 request.metadata 里的 stage_name 返回固定响应，内容不依赖调用顺序——
    多个观测点并发调用同一个 provider 实例时，谁先谁后都产出同样的分数，
    用来验证"并发结果与串行一致"而不必依赖 FakeProvider 的 FIFO 顺序（并发下
    多个线程对同一个 FakeProvider 的调用顺序本就是不确定的）。

    `fail_on_code` 指定时，命中该 code 的调用直接抛错，用来
    验证单个观测点失败被隔离、不拖垮其余观测点。"""

    _RESPONSES = {
        "select": {"selected_unit_ids": [0, 1]},
        "extract": {"evidence_unit_ids": [0]},
    }

    def __init__(
        self,
        *,
        score: int = 3,
        scores: Optional[Dict[str, int]] = None,
        fail_on_code: Optional[str] = None,
        name: str = "stage_aware",
    ) -> None:
        self._score = score
        self._scores = scores or {}
        self._fail_on_code = fail_on_code
        self._name = name
        self.requests: list = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> "frozenset[ProviderCapability]":
        return frozenset({ProviderCapability.TEXT_COMPLETION, ProviderCapability.STRUCTURED_OUTPUT})

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        code = request.metadata.get("code")
        if self._fail_on_code in ("*", code):
            raise ProviderCallError(f"boom on {code}")
        stage = request.metadata.get("stage_name")
        if stage == "score":
            data = {
                "proposed_score": self._scores.get(str(code), self._score),
                "supporting_unit_ids": [0],
                "confidence": 0.8,
                "rationale": "ok",
            }
        else:
            data = self._RESPONSES[stage]
        return fake_response(data)


def _package() -> DataPackage:
    units = [
        Unit(id=i, markdown=f"text {i}", type="text", source_file="a.md", page=0)
        for i in range(5)
    ]
    return DataPackage(package_id="student1", units=units, provenance={})


def _text_response(text: str, tokens: int = 10) -> LLMResponse:
    return LLMResponse(
        content=text, structured_data=None, usage=TokenUsage(tokens, tokens, tokens * 2),
        provider_name="fake", model_id="m",
    )


def _consensus_rater_providers(score: int = 3) -> Dict[str, BaseProvider]:
    """两个 rater 对每个观测点都打相同分——一致，不触发仲裁。"""
    script = [
        fake_response({"selected_unit_ids": [0, 1]}),
        fake_response({"evidence_unit_ids": [0]}),
        fake_response({"proposed_score": score, "supporting_unit_ids": [0], "confidence": 0.8, "rationale": "ok"}),
    ]
    return {"rater_1": FakeProvider(list(script)), "rater_2": FakeProvider(list(script))}


def _engine(
    configs_root: Path,
    *,
    extra_providers: Optional[Dict[str, BaseProvider]] = None,
    rater_providers: Optional[Dict[str, BaseProvider]] = None,
    output_dir: Optional[Path] = None,
) -> Engine:
    providers: Dict[str, BaseProvider] = dict(rater_providers or _consensus_rater_providers())
    providers["feedback"] = FakeProvider([_text_response("反馈")] * 4)
    providers.update(extra_providers or {})
    return Engine.from_configs(
        configs_root, "testtask",
        providers=providers,
        output_dir=output_dir or (configs_root.parent / "artifacts"),
    )


# ── 单 dim ───────────────────────────────────────────────────────────────────


def test_evaluate_single_dim_returns_only_that_dim(configs_root: Path) -> None:
    engine = _engine(configs_root)

    results = engine.evaluate(_package(), dim="d1")

    assert set(results.keys()) == {"d1"}
    assert isinstance(results["d1"], DimensionEvaluation)
    assert results["d1"].feedback_report["dimensions"]["D1-1"]["source"] == "consensus"


# ── 全 dim（缺省） ───────────────────────────────────────────────────────────


def test_evaluate_without_dim_covers_all_primary_dimensions(configs_root: Path) -> None:
    rater_providers = {
        "rater_1": FakeProvider(
            [
                fake_response({"selected_unit_ids": [0, 1]}),
                fake_response({"evidence_unit_ids": [0]}),
                fake_response({"proposed_score": 3, "supporting_unit_ids": [0]}),
                fake_response({"selected_unit_ids": [0, 1]}),
                fake_response({"evidence_unit_ids": [0]}),
                fake_response({"proposed_score": 4, "supporting_unit_ids": [0]}),
            ]
        ),
        "rater_2": FakeProvider(
            [
                fake_response({"selected_unit_ids": [0, 1]}),
                fake_response({"evidence_unit_ids": [0]}),
                fake_response({"proposed_score": 3, "supporting_unit_ids": [0]}),
                fake_response({"selected_unit_ids": [0, 1]}),
                fake_response({"evidence_unit_ids": [0]}),
                fake_response({"proposed_score": 4, "supporting_unit_ids": [0]}),
            ]
        ),
    }
    engine = _engine(configs_root, rater_providers=rater_providers)

    results = engine.evaluate(_package())

    assert set(results.keys()) == {"d1", "d2"}


# ── 一致场景 ─────────────────────────────────────────────────────────────────


def test_consensus_scenario_does_not_call_rater_3(configs_root: Path) -> None:
    rater_3 = FakeProvider([])  # 一次调用都不该发生
    engine = _engine(configs_root, extra_providers={"rater_3": rater_3})

    results = engine.evaluate(_package(), dim="d1")

    assert results["d1"].feedback_report["dimensions"]["D1-1"]["source"] == "consensus"
    assert results["d1"].run_trace.adjudicated_codes == []
    assert len(rater_3.requests) == 0


# ── 分歧场景 ─────────────────────────────────────────────────────────────────


def test_disagreement_scenario_triggers_adjudication_end_to_end(configs_root: Path) -> None:
    rater_providers = {
        "rater_1": FakeProvider(
            [
                fake_response({"selected_unit_ids": [0, 1]}),
                fake_response({"evidence_unit_ids": [0]}),
                fake_response({"proposed_score": 2, "supporting_unit_ids": [0]}),
            ]
        ),
        "rater_2": FakeProvider(
            [
                fake_response({"selected_unit_ids": [0, 1]}),
                fake_response({"evidence_unit_ids": [0]}),
                fake_response({"proposed_score": 5, "supporting_unit_ids": [0]}),
            ]
        ),
    }
    rater_3 = FakeProvider(
        [fake_response({"proposed_score": 3, "supporting_unit_ids": [2], "confidence": 0.9, "rationale": "仲裁理由"})]
    )
    engine = _engine(configs_root, rater_providers=rater_providers, extra_providers={"rater_3": rater_3})

    results = engine.evaluate(_package(), dim="d1")

    dim_entry = results["d1"].feedback_report["dimensions"]["D1-1"]
    assert dim_entry["source"] == "adjudicated"
    assert dim_entry["final_score"] == 3
    assert dim_entry["unit_ids"] == [2]
    assert results["d1"].run_trace.adjudicated_codes == ["D1-1"]
    assert len(rater_3.requests) == 1


# ── 缺 provider：报错 ────────────────────────────────────────────────────────


def test_missing_required_provider_raises_at_construction(configs_root: Path) -> None:
    with pytest.raises(EngineConfigError, match="rater_2"):
        Engine.from_configs(
            configs_root, "testtask",
            providers={"rater_1": FakeProvider([]), "feedback": FakeProvider([])},
        )


def test_missing_rater_3_only_raises_when_actually_triggered(configs_root: Path) -> None:
    """没有 rater_3 时构造 Engine 不该报错——只有真正触发仲裁才需要它。"""
    engine = _engine(configs_root)  # 没有传 rater_3

    results = engine.evaluate(_package(), dim="d1")  # 一致场景，用不到 rater_3

    assert results["d1"].feedback_report["dimensions"]["D1-1"]["source"] == "consensus"


def test_missing_rater_3_raises_when_disagreement_occurs(configs_root: Path) -> None:
    rater_providers = {
        "rater_1": FakeProvider(
            [fake_response({"selected_unit_ids": [0]}), fake_response({"evidence_unit_ids": [0]}), fake_response({"proposed_score": 2, "supporting_unit_ids": [0]})]
        ),
        "rater_2": FakeProvider(
            [fake_response({"selected_unit_ids": [0]}), fake_response({"evidence_unit_ids": [0]}), fake_response({"proposed_score": 5, "supporting_unit_ids": [0]})]
        ),
    }
    engine = _engine(configs_root, rater_providers=rater_providers)  # 没有 rater_3

    with pytest.raises(ValueError, match="rater_3"):
        engine.evaluate(_package(), dim="d1")


# ── 主接缝：rate → reconcile → adjudicate → feedback 全链 + 落盘 ─────────────


def test_full_chain_writes_all_artifacts_with_resolvable_unit_ids(configs_root: Path) -> None:
    output_dir = configs_root.parent / "artifacts"
    engine = _engine(configs_root, output_dir=output_dir)

    engine.evaluate(_package(), dim="d1")

    import json

    written = sorted(p.relative_to(output_dir) for p in output_dir.rglob("*.json"))
    # package.json 不落在 artifacts/ 里——它是 parse 花钱买来的输入，住在 packages/。
    assert [str(p) for p in written] == [
        "testtask/student1/d1/feedback.json",
        "testtask/student1/d1/rater_chains.json",
        "testtask/student1/d1/run_trace.json",
    ]

    feedback_data = json.loads((output_dir / "testtask" / "student1" / "d1" / "feedback.json").read_text(encoding="utf-8"))
    unit_markdown_by_id = {u.id: u.markdown for u in _package().units}
    cited = feedback_data["dimensions"]["D1-1"]["unit_ids"]
    assert [unit_markdown_by_id[uid] for uid in cited] == ["text 0"]


# ── run_trace.json 阶段级明细 ────────────────────────────────────────────────


def test_run_trace_json_includes_stage_level_detail(configs_root: Path) -> None:
    """run_trace.json 不能只有运行级汇总——阶段级（stage/rater/code/llm_calls/
    tokens/ms）明细也必须落盘，否则 total_tokens/total_ms 就成了无法核对来源的
    黑箱数字；每条还要带上观测点 code，否则多观测点下分不清成本花在哪。"""
    output_dir = configs_root.parent / "artifacts"
    engine = _engine(configs_root, extra_providers={"rater_3": FakeProvider([])}, output_dir=output_dir)

    engine.evaluate(_package(), dim="d1")

    import json

    run_trace = json.loads(
        (output_dir / "testtask" / "student1" / "d1" / "run_trace.json").read_text(encoding="utf-8")
    )
    stages = run_trace["stage_traces"]
    assert {(s["stage"], s["rater"], s["code"]) for s in stages} == {
        ("select", "rater_1", "D1-1"),
        ("extract", "rater_1", "D1-1"),
        ("score", "rater_1", "D1-1"),
        ("select", "rater_2", "D1-1"),
        ("extract", "rater_2", "D1-1"),
        ("score", "rater_2", "D1-1"),
        ("feedback", None, "D1-1"),
    }
    # 一致场景不触发仲裁：没有 rater_3 的调用，也就没有 adjudicate 条目
    assert [s for s in stages if s["stage"] == "adjudicate"] == []
    assert run_trace["total_tokens"] == sum(s["tokens"] for s in stages)


def test_run_trace_records_adjudicate_stage_per_observation_point(configs_root_multi: Path) -> None:
    """触发反思裁决时，Rater3 的调用必须逐观测点在案（stage=adjudicate、
    rater=rater_3、带 code），而不是折叠进一条汇总——否则无从核对哪个观测点
    真的走了仲裁、花了多少。这里 D1-1 分差 3 触发，D1-2/D1-3 一致不触发。"""
    rater_providers = {
        "rater_1": _StageAwareProvider(scores={"D1-1": 2}, score=3, name="r1"),
        "rater_2": _StageAwareProvider(scores={"D1-1": 5}, score=3, name="r2"),
    }
    rater_3 = FakeProvider([fake_response({"proposed_score": 4, "supporting_unit_ids": [0]})])
    engine = _engine(
        configs_root_multi, rater_providers=rater_providers, extra_providers={"rater_3": rater_3}
    )

    results = engine.evaluate(_package(), dim="d1")

    traces = results["d1"].run_trace.stage_traces
    adjudicate_entries = [s for s in traces if s.stage == "adjudicate"]
    assert [(s.rater, s.code, s.llm_calls) for s in adjudicate_entries] == [("rater_3", "D1-1", 1)]
    assert results["d1"].run_trace.adjudicated_codes == ["D1-1"]
    # 同一观测点的条目按流水线顺序排列，adjudicate 落在 score 之后
    d1_1_stages = [s.stage for s in traces if s.code == "D1-1"]
    assert d1_1_stages.index("adjudicate") > d1_1_stages.index("score")


# ── model_config 字段校验 ────────────────────────────────────────────────────


def test_missing_api_key_env_raises_clear_config_error(tmp_path: Path, configs_root: Path) -> None:
    from src.engine import Engine, EngineConfigError

    model_config_path = tmp_path / "model_config.yaml"
    model_config_path.write_text(
        """
providers:
  rater_1: {model: "m", api_base: "https://x/v1"}
  rater_2: {model: "m", api_base: "https://x/v1", api_key_env: "K2"}
  feedback: {model: "m", api_base: "https://x/v1", api_key_env: "KF"}
""",
        encoding="utf-8",
    )

    with pytest.raises(EngineConfigError, match="api_key_env"):
        Engine.from_configs(
            configs_root, "testtask",
            model_config_path=model_config_path,
        )


def test_missing_api_key_value_raises_engine_config_error(
    tmp_path: Path, configs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """密钥没配是最常见的首次运行错误，要归到 EngineConfigError——调用方（CLI）
    才能印一行人话，而不是甩一屏 traceback。"""
    for var in ("LLM_API_KEY", "K1", "K2", "KF"):
        monkeypatch.delenv(var, raising=False)

    model_config_path = tmp_path / "model_config.yaml"
    model_config_path.write_text(
        """
providers:
  rater_1: {model: "m", api_base: "https://x/v1", api_key_env: "K1"}
  rater_2: {model: "m", api_base: "https://x/v1", api_key_env: "K2"}
  feedback: {model: "m", api_base: "https://x/v1", api_key_env: "KF"}
""",
        encoding="utf-8",
    )

    with pytest.raises(EngineConfigError, match="K1"):
        Engine.from_configs(
            configs_root, "testtask",
            model_config_path=model_config_path,
        )


# ── 观测点级并发 ────────────────────────────────────────────────────────────


def test_concurrent_result_matches_sequential(configs_root_multi: Path, tmp_path: Path) -> None:
    """max_workers=1（串行）与 max_workers=3（并发）跑同一份数据，产出必须一致。"""

    def _run(max_workers: int) -> dict:
        providers: Dict[str, BaseProvider] = {
            "rater_1": _StageAwareProvider(score=3, name="r1"),
            "rater_2": _StageAwareProvider(score=3, name="r2"),
            "feedback": FakeProvider([_text_response("反馈")] * 3),
        }
        engine = Engine.from_configs(
            configs_root_multi, "testtask",
            providers=providers,
            model_config_path=_model_config_with_max_workers(tmp_path, max_workers),
            output_dir=tmp_path / f"artifacts_{max_workers}",
        )
        results = engine.evaluate(_package(), dim="d1")
        return results["d1"].feedback_report

    sequential = _run(max_workers=1)
    concurrent = _run(max_workers=3)

    assert set(sequential["dimensions"]) == {"D1-1", "D1-2", "D1-3"}
    assert sequential["dimensions"] == concurrent["dimensions"]
    assert sequential["primary_score"] == concurrent["primary_score"]


def test_concurrency_runs_secondary_dims_in_parallel_not_serially(configs_root_multi: Path, tmp_path: Path) -> None:
    """3 个观测点、单个 rater 调用耗时 ~50ms：串行至少 300ms（3 dim × 2 rater ×
    50ms），并发（max_workers=3）应明显快于串行耗时之和——证明确实并发而非只是
    接口上加了参数。"""

    class _SlowProvider(BaseProvider):
        def __init__(self, delay_seconds: float) -> None:
            self._delay = delay_seconds

        @property
        def name(self) -> str:
            return "slow"

        @property
        def capabilities(self) -> "frozenset[ProviderCapability]":
            return frozenset({ProviderCapability.TEXT_COMPLETION, ProviderCapability.STRUCTURED_OUTPUT})

        def complete(self, request: LLMRequest) -> LLMResponse:
            time.sleep(self._delay)
            stage = request.metadata.get("stage_name")
            data = (
                {"proposed_score": 3, "supporting_unit_ids": [0]}
                if stage == "score"
                else _StageAwareProvider._RESPONSES[stage]
            )
            return fake_response(data)

    providers: Dict[str, BaseProvider] = {
        "rater_1": _SlowProvider(0.05),
        "rater_2": _SlowProvider(0.05),
        "feedback": FakeProvider([_text_response("反馈")] * 3),
    }
    engine = Engine.from_configs(
        configs_root_multi, "testtask",
        providers=providers,
        model_config_path=_model_config_with_max_workers(tmp_path, 3),
        output_dir=tmp_path / "artifacts",
    )

    started = time.perf_counter()
    engine.evaluate(_package(), dim="d1")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.9 * 3  # 远低于 3 个观测点完全串行的下界


# ── 观测点权重 ────────────────────────────────────────────────────────────────


def _weighted_providers(scores: Dict[str, int], **kwargs: Any) -> Dict[str, BaseProvider]:
    return {
        "rater_1": _StageAwareProvider(scores=scores, name="r1", **kwargs),
        "rater_2": _StageAwareProvider(scores=scores, name="r2", **kwargs),
        "feedback": FakeProvider([_text_response("反馈")] * 3),
    }


def test_primary_score_uses_observation_point_weights(configs_root_weighted: Path, tmp_path: Path) -> None:
    """权重 0.2/0.3/0.5、得分 1/3/5：加权 3.6，等权会算成 3.0。"""
    engine = Engine.from_configs(
        configs_root_weighted, "testtask",
        providers=_weighted_providers({"D1-1": 1, "D1-2": 3, "D1-3": 5}),
        model_config_path=_model_config_with_max_workers(tmp_path, 3),
        output_dir=tmp_path / "artifacts",
    )

    results = engine.evaluate(_package(), dim="d1")

    assert results["d1"].feedback_report["primary_score"] == pytest.approx(3.6)


def test_primary_score_renormalizes_weights_when_a_point_fails(
    configs_root_weighted: Path, tmp_path: Path
) -> None:
    """D1-3（权重 0.5）评价失败：剩下 0.2/0.3 要重新归一化成 0.4/0.6，
    得分 1/3 → 2.2；不归一化会算成 1.1，一个观测点失败就让 dim 分凭空腰斩。"""
    engine = Engine.from_configs(
        configs_root_weighted, "testtask",
        providers=_weighted_providers({"D1-1": 1, "D1-2": 3}, fail_on_code="D1-3"),
        model_config_path=_model_config_with_max_workers(tmp_path, 3),
        output_dir=tmp_path / "artifacts",
    )

    results = engine.evaluate(_package(), dim="d1")

    assert [f["code"] for f in results["d1"].run_trace.failed_codes] == ["D1-3"]
    assert results["d1"].feedback_report["primary_score"] == pytest.approx(2.2)


# ── 失败隔离 ──────────────────────────────────────────────────────────────────


def test_single_dimension_failure_is_isolated(configs_root_multi: Path, tmp_path: Path) -> None:
    """D1-2 的 rater_1 调用抛错：只有 D1-2 被标记失败，D1-1/D1-3 照常产出并落盘。"""
    providers: Dict[str, BaseProvider] = {
        "rater_1": _StageAwareProvider(score=3, fail_on_code="D1-2", name="r1"),
        "rater_2": _StageAwareProvider(score=3, name="r2"),
        "feedback": FakeProvider([_text_response("反馈")] * 2),
    }
    output_dir = tmp_path / "artifacts"
    engine = Engine.from_configs(
        configs_root_multi, "testtask",
        providers=providers,
        model_config_path=_model_config_with_max_workers(tmp_path, 3),
        output_dir=output_dir,
    )

    results = engine.evaluate(_package(), dim="d1")

    dimensions_out = results["d1"].feedback_report["dimensions"]
    assert set(dimensions_out) == {"D1-1", "D1-3"}

    failed = results["d1"].run_trace.failed_codes
    assert len(failed) == 1
    assert failed[0]["code"] == "D1-2"
    assert "boom on D1-2" in failed[0]["error"]

    import json

    dim_dir = output_dir / "testtask" / "student1" / "d1"
    feedback_data = json.loads((dim_dir / "feedback.json").read_text(encoding="utf-8"))
    assert set(feedback_data["dimensions"]) == {"D1-1", "D1-3"}
    run_trace_data = json.loads((dim_dir / "run_trace.json").read_text(encoding="utf-8"))
    assert run_trace_data["failed_codes"] == [{"code": "D1-2", "error": failed[0]["error"]}]


def test_all_dimensions_failing_records_errors_without_masking_them(
    configs_root_multi: Path, tmp_path: Path
) -> None:
    """全部观测点都失败时，各维度的真实错误必须原样记下来。

    此时没有任何 FinalDecision 可聚合——硬把空 decisions 往下送会在
    aggregate_final_decisions 炸出一条与根因无关的"decisions 不能为空"，把真正
    的原因（鉴权失败/超时/限流）全埋掉。跳过 reconcile/feedback，照常落盘。"""
    providers: Dict[str, BaseProvider] = {
        "rater_1": _StageAwareProvider(score=3, fail_on_code="*", name="r1"),
        "rater_2": _StageAwareProvider(score=3, name="r2"),
        "feedback": FakeProvider([]),
    }
    output_dir = tmp_path / "artifacts"
    engine = Engine.from_configs(
        configs_root_multi, "testtask",
        providers=providers,
        model_config_path=_model_config_with_max_workers(tmp_path, 3),
        output_dir=output_dir,
    )

    results = engine.evaluate(_package(), dim="d1")

    evaluation = results["d1"]
    assert evaluation.feedback_report["dimensions"] == {}
    assert evaluation.feedback_report["primary_score"] is None
    failed_ids = {f["code"] for f in evaluation.run_trace.failed_codes}
    assert failed_ids == {"D1-1", "D1-2", "D1-3"}
    assert all("boom" in f["error"] for f in evaluation.run_trace.failed_codes)

    import json

    run_trace_data = json.loads(
        (output_dir / "testtask" / "student1" / "d1" / "run_trace.json").read_text(encoding="utf-8")
    )
    assert {f["code"] for f in run_trace_data["failed_codes"]} == {"D1-1", "D1-2", "D1-3"}


def test_one_primary_dimension_failing_does_not_kill_the_others(
    configs_root: Path, tmp_path: Path
) -> None:
    """一个二级指标整体失败，不能拖垮同一 submission 下其余二级指标（US31：不崩整份
    提交）。configs_root 里 d1/d2 各有一个观测点，让 d1 的那个必失败。"""
    providers: Dict[str, BaseProvider] = {
        "rater_1": _StageAwareProvider(score=3, fail_on_code="D1-1", name="r1"),
        "rater_2": _StageAwareProvider(score=3, name="r2"),
        "feedback": FakeProvider([_text_response("反馈")] * 4),
    }
    engine = Engine.from_configs(
        configs_root, "testtask",
        providers=providers,
        output_dir=tmp_path / "artifacts",
    )

    results = engine.evaluate(_package())

    assert set(results) == {"d1", "d2"}
    assert results["d1"].feedback_report["dimensions"] == {}
    assert [f["code"] for f in results["d1"].run_trace.failed_codes] == ["D1-1"]
    assert set(results["d2"].feedback_report["dimensions"]) == {"D2-1"}


def test_indicator_description_reaches_select_and_extract_but_not_score(configs_root: Path) -> None:
    """守一个真实 bug：Engine 不走 rater.run_chain，而是直接调 select/extract，
    曾经漏传 indicator_description——单测 run_chain 抓不到，只有端到端能抓。

    同时守住隔离方向：判档阶段绝不能拿到这段泛论，否则两位评委各自发挥它会放大分歧。"""
    rater_1 = _StageAwareProvider(score=3)
    engine = _engine(configs_root, rater_providers={"rater_1": rater_1, "rater_2": _StageAwareProvider(score=3)})
    engine.evaluate(_package(), dim="d1")

    by_stage = {r.metadata.get("stage_name"): r.prompt for r in rater_1.requests}
    assert "desc" in by_stage["select"]
    assert "desc" in by_stage["extract"]
    assert "desc" not in by_stage["score"]
