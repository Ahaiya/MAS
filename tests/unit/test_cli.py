"""`scripts/cli.py` 的行为测试。

CLI 的职责只有两件：把命令行参数正确翻译成 Engine 调用，把结果打印出来。测试
因此只断言这两件事——参数怎么传进去（monkeypatch `Engine.from_configs`，不碰真
provider/真 LLM），以及结果怎么印出来（`_render_summary` 纯函数直测）。

`config validate` 不需要任何 provider/密钥，直接对真实的最小 configs 树端到端跑。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from typer.testing import CliRunner

from scripts import cli
from src.contracts.package import DataPackage
from src.contracts.trace import RunTraceSummary
from src.engine import DimensionEvaluation

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

runner = CliRunner()

_PROMPT_NAMES = [f"{stage}.yaml" for stage in cli.PROMPT_STAGES]

_DIM_YAML = """
dim_id: "{dim_id}"
dim_name: "test {dim_id}"
indicator_description: "desc"
scale:
  min: 1
  max: 5
  levels: {{1: 待改进, 2: 合格, 3: 良好, 4: 优秀, 5: 卓越}}
dimensions:
  - {{code: "{code}", name: "sub {code}", weight: 1.0, anchors: {{1: 一档, 2: 二档, 3: 三档, 4: 四档, 5: 五档}}}}
"""

_ADJUDICATION_YAML = """
score_gap_threshold: 1
drift_min_dimensions: 2
"""


_MODEL_CONFIG_OK = """
providers:
  rater_1: {model: "m", api_base: "https://x/v1", api_key_env: "DEEPSEEK_API_KEY"}
  rater_2: {model: "m", api_base: "https://y/v1", api_key_env: "DASHSCOPE_API_KEY"}
  feedback: {model: "m", api_base: "https://x/v1", api_key_env: "DEEPSEEK_API_KEY"}
runtime:
  max_workers: 4
"""


@pytest.fixture
def configs_root(tmp_path: Path) -> Path:
    """最小 configs 树：一个任务 testtask，两个一级指标 d1/d2，复用仓库真实的
    prompt yaml；路径全部按约定固定，没有 bundle 文件。"""
    root = tmp_path / "configs"
    (root / "tasks" / "testtask" / "dimension").mkdir(parents=True)
    (root / "prompts").mkdir(parents=True)

    for name in _PROMPT_NAMES:
        shutil.copy(_PROJECT_ROOT / "configs" / "prompts" / name, root / "prompts" / name)
    (root / "adjudication.yaml").write_text(_ADJUDICATION_YAML, encoding="utf-8")

    dim_dir = root / "tasks" / "testtask" / "dimension"
    for dim_id, code in (("d1", "D1-1"), ("d2", "D2-1")):
        (dim_dir / f"{dim_id}_rubric.yaml").write_text(
            _DIM_YAML.format(dim_id=dim_id, code=code), encoding="utf-8"
        )

    (root / "model_config.yaml").write_text(_MODEL_CONFIG_OK, encoding="utf-8")
    return root


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    path = tmp_path / "student1.md"
    path.write_text("# 标题\n\n这是第一句。这是第二句。\n", encoding="utf-8")
    return path


def _run_eval(input_file: Path, configs: Path, *extra: str, task: str = "testtask") -> Any:
    return runner.invoke(
        cli.app,
        ["eval", str(input_file), "--configs", str(configs), "--task", task, *extra],
    )


def _run_validate(configs: Path, task: str = "testtask") -> Any:
    return runner.invoke(cli.app, ["config", "validate", "--configs", str(configs), "--task", task])


# ── eval：参数如何翻译成 Engine 调用 ──────────────────────────────────────────


class _RecordingEngine:
    """记录 evaluate() 收到什么的假 Engine。"""

    def __init__(self) -> None:
        self.evaluate_calls: List[Any] = []

    def evaluate(
        self, package: DataPackage, dim: Optional[str] = None
    ) -> Dict[str, DimensionEvaluation]:
        self.evaluate_calls.append((package, dim))
        return {"d1": _evaluation("d1")}


def _evaluation(
    dim_id: str,
    *,
    failed_dims: Optional[List[Dict[str, str]]] = None,
) -> DimensionEvaluation:
    return DimensionEvaluation(
        dim_id=dim_id,
        feedback_report={
            "primary_score": 3.5,
            "radar": [],
            "dimensions": {
                f"{dim_id}_1": {
                    "final_score": 3, "source": "consensus", "unit_ids": [0], "feedback": "还行",
                },
                f"{dim_id}_2": {
                    "final_score": 4, "source": "adjudicated", "unit_ids": [1], "feedback": "不错",
                },
            },
        },
        rater_chains_report={},
        run_trace=RunTraceSummary(
            run_id="run-1",
            configs_ref="configs",
            dim=dim_id,
            total_tokens=1234,
            total_ms=5678.0,
            adjudicated_dims=[f"{dim_id}_2"],
            stage_traces=[],
            failed_dims=failed_dims or [],
        ),
    )


def _evaluation_all_failed(dim_id: str) -> DimensionEvaluation:
    """一级指标下全部二级指标都失败时 engine 产出的空评价。"""
    return DimensionEvaluation(
        dim_id=dim_id,
        feedback_report={"primary_score": None, "radar": [], "dimensions": {}},
        rater_chains_report={"chains": [], "final_decisions": []},
        run_trace=RunTraceSummary(
            run_id="run-2",
            configs_ref="configs",
            dim=dim_id,
            total_tokens=0,
            total_ms=0.0,
            adjudicated_dims=[],
            stage_traces=[],
            failed_dims=[{"dimension_id": f"{dim_id}_1", "error": "401 unauthorized"}],
        ),
    )


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """把 Engine.from_configs 换成记录器，返回 {"args": ..., "kwargs": ..., "engine": ...}。"""
    box: Dict[str, Any] = {"kwargs": None, "args": None, "engine": _RecordingEngine()}

    def _fake_from_configs(*args: Any, **kwargs: Any) -> _RecordingEngine:
        box["args"] = args
        box["kwargs"] = kwargs
        return box["engine"]

    monkeypatch.setattr(cli.Engine, "from_configs", _fake_from_configs)
    return box


def test_eval_builds_package_from_input_file(
    configs_root: Path, sample_file: Path, tmp_path: Path, recorded: Dict[str, Any]
) -> None:
    """位置参数 INPUT_FILE 被切分成 DataPackage，sample 名取文件名（不含后缀）。"""
    result = _run_eval(sample_file, configs_root, "--dim", "d1")

    assert result.exit_code == 0, result.output
    package, dim = recorded["engine"].evaluate_calls[0]
    assert package.package_id == "student1"
    assert [u.text for u in package.units] == ["# 标题", "这是第一句。", "这是第二句。"]
    assert dim == "d1"


def test_eval_without_dim_evaluates_all_primary_dimensions(
    configs_root: Path, sample_file: Path, recorded: Dict[str, Any]
) -> None:
    """不传 --dim 时传 None 给 engine——由 engine 自己发现当前任务全部一级指标。"""
    result = _run_eval(sample_file, configs_root)

    assert result.exit_code == 0, result.output
    _package, dim = recorded["engine"].evaluate_calls[0]
    assert dim is None


def test_eval_passes_configs_root_task_and_output_dir(
    configs_root: Path, sample_file: Path, tmp_path: Path, recorded: Dict[str, Any]
) -> None:
    """configs_root 与 task_id 是 from_configs 的两个位置参数；output_dir 原样透传；
    不传 model_config_path——固定读 configs_root/model_config.yaml 是 Engine 的默认行为。"""
    out = tmp_path / "out"
    result = _run_eval(sample_file, configs_root, "--output-dir", str(out))

    assert result.exit_code == 0, result.output
    configs_arg, task_arg = recorded["args"]
    assert Path(configs_arg) == configs_root
    assert task_arg == "testtask"
    assert Path(recorded["kwargs"]["output_dir"]) == out
    assert "model_config_path" not in recorded["kwargs"]


def test_eval_uses_default_configs_root_when_not_given(
    sample_file: Path, recorded: Dict[str, Any]
) -> None:
    """不给 --configs 时用仓库默认的 configs/；--task 仍必须显式给。"""
    result = runner.invoke(cli.app, ["eval", str(sample_file), "--task", "experiment"])

    assert result.exit_code == 0, result.output
    configs_arg, task_arg = recorded["args"]
    assert Path(configs_arg) == _PROJECT_ROOT / "configs"
    assert task_arg == "experiment"


# ── eval：--task 必须显式指定 ─────────────────────────────────────────────────


def test_eval_requires_task_and_lists_available_ones(
    configs_root: Path, sample_file: Path, recorded: Dict[str, Any]
) -> None:
    """漏传 --task 时报错退出，并把可选任务列出来——不许有默认任务静默评错。"""
    result = runner.invoke(cli.app, ["eval", str(sample_file), "--configs", str(configs_root)])

    assert result.exit_code == 1
    assert "--task" in result.output
    assert "testtask" in result.output
    assert recorded["engine"].evaluate_calls == []


def test_eval_rejects_unknown_task(
    configs_root: Path, sample_file: Path, recorded: Dict[str, Any]
) -> None:
    """指定了不存在的任务同样报错退出，并列出可选任务。"""
    result = _run_eval(sample_file, configs_root, task="nosuchtask")

    assert result.exit_code == 1
    assert "nosuchtask" in result.output
    assert "testtask" in result.output
    assert recorded["engine"].evaluate_calls == []


# ── eval：输入校验（系统边界） ────────────────────────────────────────────────


def test_eval_rejects_missing_input_file(
    configs_root: Path, tmp_path: Path, recorded: Dict[str, Any]
) -> None:
    result = _run_eval(tmp_path / "nope.md", configs_root)

    assert result.exit_code == 1
    assert "nope.md" in result.output
    assert recorded["engine"].evaluate_calls == []


def test_eval_rejects_unsupported_extension(
    configs_root: Path, tmp_path: Path, recorded: Dict[str, Any]
) -> None:
    bad = tmp_path / "sample.pdf"
    bad.write_text("x", encoding="utf-8")

    result = _run_eval(bad, configs_root)

    assert result.exit_code == 1
    assert ".pdf" in result.output
    assert recorded["engine"].evaluate_calls == []


def test_eval_reports_dropped_units_instead_of_dropping_them_silently(
    configs_root: Path, tmp_path: Path, recorded: Dict[str, Any]
) -> None:
    """超上下文预算的丢弃必须在 CLI 上被看见——静默丢证据是可解释性漏洞。"""
    big = tmp_path / "big.md"
    big.write_text("这是一个很长的句子用来撑满上下文预算。" * 4000, encoding="utf-8")

    result = _run_eval(big, configs_root)

    assert result.exit_code == 0, result.output
    assert "丢弃" in result.output


@pytest.mark.parametrize(
    "exc",
    [
        cli.EngineConfigError("model_config 缺少 raters.rater_2"),
        cli.ConfigCompileError("Dimension rubric file not found: .../zz9_rubric.yaml"),
        FileNotFoundError("configs/nope.yaml"),
        KeyError("providers"),
    ],
)
def test_eval_reports_user_fixable_errors_as_one_line(
    configs_root: Path, sample_file: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """配置写错（yaml 少字段、--dim 拼错、引用文件缺失）都是用户能自己修的，
    印一行人话即可，不甩一屏 traceback。"""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise exc

    monkeypatch.setattr(cli.Engine, "from_configs", _boom)

    result = _run_eval(sample_file, configs_root)

    assert result.exit_code == 1
    assert type(exc).__name__ in result.output
    assert "Traceback" not in result.output


def test_eval_rejects_missing_configs_root(
    sample_file: Path, tmp_path: Path, recorded: Dict[str, Any]
) -> None:
    result = _run_eval(sample_file, tmp_path / "nope")

    assert result.exit_code == 1
    assert "nope" in result.output
    assert recorded["engine"].evaluate_calls == []


def test_eval_exits_nonzero_when_nothing_scored(
    configs_root: Path, sample_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全维度失败时 engine 不再抛异常（否则会拖垮同 sample 其余一级指标），
    但 CLI 必须退非零——否则脚本会把"什么都没评出来"当成功。"""

    class _AllFailedEngine:
        def evaluate(self, _package: Any, dim: Optional[str] = None) -> Dict[str, Any]:
            return {"d1": _evaluation_all_failed("d1")}

    monkeypatch.setattr(cli.Engine, "from_configs", lambda *a, **k: _AllFailedEngine())

    result = _run_eval(sample_file, configs_root)

    assert result.exit_code == 1
    assert "全部二级指标评价失败" in result.output
    assert "401 unauthorized" in result.output


# ── 打印 ──────────────────────────────────────────────────────────────────────


def test_render_summary_shows_scores_sources_and_totals() -> None:
    text = cli._render_summary({"d1": _evaluation("d1")})

    assert "d1" in text
    assert "3.5" in text
    assert "d1_1" in text and "consensus" in text
    assert "d1_2" in text and "adjudicated" in text
    assert "1234" in text


def test_render_summary_surfaces_failed_dimensions() -> None:
    """失败隔离下被跳过的二级指标必须打印出来，否则用户以为评了全部维度。"""
    evaluation = _evaluation("d1", failed_dims=[{"dimension_id": "d1_3", "error": "boom"}])

    text = cli._render_summary({"d1": evaluation})

    assert "d1_3" in text
    assert "boom" in text


# ── config validate ───────────────────────────────────────────────────────────


def test_config_validate_passes_on_valid_configs(configs_root: Path) -> None:
    result = _run_validate(configs_root)

    assert result.exit_code == 0, result.output
    assert "testtask" in result.output
    assert "d1" in result.output and "d2" in result.output
    assert "PASS: 配置校验通过。" in result.output


def test_config_validate_fails_when_configs_root_missing(tmp_path: Path) -> None:
    result = _run_validate(tmp_path / "nope")

    assert result.exit_code == 1
    assert "nope" in result.output


def test_config_validate_fails_when_adjudication_policy_missing(configs_root: Path) -> None:
    """仲裁策略按约定固定在 {configs}/adjudication.yaml，缺了要报出来。"""
    (configs_root / "adjudication.yaml").unlink()

    result = _run_validate(configs_root)

    assert result.exit_code == 1
    assert "adjudication.yaml" in result.output


def test_config_validate_fails_when_prompt_reference_is_broken(configs_root: Path) -> None:
    (configs_root / "prompts" / "select.yaml").unlink()

    result = _run_validate(configs_root)

    assert result.exit_code == 1
    assert "select.yaml" in result.output


def test_config_validate_fails_when_task_has_no_rubrics(configs_root: Path) -> None:
    for path in (configs_root / "tasks" / "testtask" / "dimension").glob("*_rubric.yaml"):
        path.unlink()

    result = _run_validate(configs_root)

    assert result.exit_code == 1
    assert "testtask" in result.output


# ── config validate 覆盖 model_config ─────────────────────────────────────────



def test_config_validate_also_checks_model_config(configs_root: Path) -> None:
    """model_config 现在是模型/参数的唯一来源且必填项变多，config validate 必须
    一并校验它——否则漏填 model 要等到真跑评价才炸。"""
    (configs_root / "model_config.yaml").write_text(_MODEL_CONFIG_OK, encoding="utf-8")

    result = _run_validate(configs_root)

    assert result.exit_code == 0, result.output
    assert "rater_1" in result.output and "feedback" in result.output


def test_config_validate_fails_when_model_config_missing_required_field(configs_root: Path) -> None:
    (configs_root / "model_config.yaml").write_text(
        _MODEL_CONFIG_OK.replace('rater_2: {model: "m", ', "rater_2: {"), encoding="utf-8"
    )

    result = _run_validate(configs_root)

    assert result.exit_code == 1
    assert "model" in result.output and "rater_2" in result.output


def test_config_validate_fails_when_model_config_missing_required_provider(configs_root: Path) -> None:
    lines = [ln for ln in _MODEL_CONFIG_OK.splitlines() if "rater_2:" not in ln]
    (configs_root / "model_config.yaml").write_text("\n".join(lines), encoding="utf-8")

    result = _run_validate(configs_root)

    assert result.exit_code == 1
    assert "rater_2" in result.output


def test_config_validate_does_not_need_any_api_key(
    configs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """校验只看结构、不建 provider——CI 里没有 .env 也必须能跑通。"""
    for var in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    (configs_root / "model_config.yaml").write_text(_MODEL_CONFIG_OK, encoding="utf-8")

    result = _run_validate(configs_root)

    assert result.exit_code == 0, result.output


def test_config_validate_reports_missing_model_config(configs_root: Path) -> None:
    """configs 根目录下没有 model_config.yaml 时要报出来，而不是默默跳过。"""
    (configs_root / "model_config.yaml").unlink()

    result = _run_validate(configs_root)

    assert result.exit_code == 1
    assert "model_config" in result.output
