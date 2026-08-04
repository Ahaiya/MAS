"""`scripts/cli.py` 的行为测试。

CLI 的职责只有两件：把命令行参数正确翻译成 Engine 调用，把结果打印出来。测试
因此只断言这两件事——参数怎么传进去（monkeypatch `Engine.from_configs`，不碰真
provider/真 LLM），以及结果怎么印出来（`_render_summary` 纯函数直测）。

`config validate` 不需要任何 provider/密钥，直接对真实的最小 configs 树端到端跑。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from typer.testing import CliRunner

from scripts import cli
from src.contracts.package import DataPackage, Unit
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
    """最小 configs 树：一个任务 testtask，两个二级指标 d1/d2，复用仓库真实的
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
def packages_root(tmp_path: Path) -> Path:
    """一份已经 parse 过的提交：packages/testtask/student1/package.json。"""
    root = tmp_path / "packages"
    package = DataPackage(
        package_id="testtask/student1",
        units=[
            Unit(id=0, markdown="# 标题", type="title", source_file="a.pdf", page=0),
            Unit(id=1, markdown="这是第一句。", type="text", source_file="a.pdf", page=0),
        ],
        provenance={"source_files": ["a.pdf"]},
    )
    path = root / "testtask" / "student1" / "package.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(package.to_dict(), ensure_ascii=False), encoding="utf-8")
    return root


def _run_eval(
    packages: Path, configs: Path, *extra: str, task: str = "testtask", submission: str = "student1"
) -> Any:
    return runner.invoke(
        cli.app,
        ["eval", "--packages", str(packages), "--configs", str(configs), "--task", task,
         "--submission", submission, *extra],
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
    failed_codes: Optional[List[Dict[str, str]]] = None,
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
            adjudicated_codes=[f"{dim_id}_2"],
            stage_traces=[],
            failed_codes=failed_codes or [],
        ),
    )


def _evaluation_all_failed(dim_id: str) -> DimensionEvaluation:
    """二级指标下全部观测点都失败时 engine 产出的空评价。"""
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
            adjudicated_codes=[],
            stage_traces=[],
            failed_codes=[{"code": f"{dim_id}_1", "error": "401 unauthorized"}],
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


def test_eval_读回已解析的数据包(
    configs_root: Path, packages_root: Path, recorded: Dict[str, Any]
) -> None:
    """eval 不再吃文件路径：按约定去 packages/{task}/{submission}/package.json 找包。"""
    result = _run_eval(packages_root, configs_root, "--dim", "d1")

    assert result.exit_code == 0, result.output
    package, dim = recorded["engine"].evaluate_calls[0]
    assert package.package_id == "testtask/student1"
    assert [u.markdown for u in package.units] == ["# 标题", "这是第一句。"]
    assert dim == "d1"


def test_eval_不传_dim_时评全部二级指标(
    configs_root: Path, packages_root: Path, recorded: Dict[str, Any]
) -> None:
    result = _run_eval(packages_root, configs_root)

    assert result.exit_code == 0, result.output
    _package, dim = recorded["engine"].evaluate_calls[0]
    assert dim is None


def test_eval_透传配置根任务与产物目录(
    configs_root: Path, packages_root: Path, tmp_path: Path, recorded: Dict[str, Any]
) -> None:
    out = tmp_path / "out"
    result = _run_eval(packages_root, configs_root, "--output-dir", str(out))

    assert result.exit_code == 0, result.output
    configs_arg, task_arg = recorded["args"]
    assert Path(configs_arg) == configs_root
    assert task_arg == "testtask"
    assert Path(recorded["kwargs"]["output_dir"]) == out
    assert "model_config_path" not in recorded["kwargs"]


# ── eval：--task / --submission 必须显式指定 ──────────────────────────────────


def test_eval_必须给_task_并列出可选任务(
    configs_root: Path, packages_root: Path, recorded: Dict[str, Any]
) -> None:
    """漏传 --task 时报错退出，并把可选任务列出来——不许有默认任务静默评错。"""
    result = runner.invoke(
        cli.app, ["eval", "--configs", str(configs_root), "--submission", "student1"]
    )

    assert result.exit_code == 1
    assert "--task" in result.output
    assert "testtask" in result.output
    assert recorded["engine"].evaluate_calls == []


def test_eval_必须给_submission(
    configs_root: Path, packages_root: Path, recorded: Dict[str, Any]
) -> None:
    result = runner.invoke(
        cli.app, ["eval", "--configs", str(configs_root), "--task", "testtask"]
    )

    assert result.exit_code == 1
    assert "--submission" in result.output
    assert recorded["engine"].evaluate_calls == []


def test_eval_拒绝不存在的任务(
    configs_root: Path, packages_root: Path, recorded: Dict[str, Any]
) -> None:
    result = _run_eval(packages_root, configs_root, task="nosuchtask")

    assert result.exit_code == 1
    assert "nosuchtask" in result.output
    assert "testtask" in result.output
    assert recorded["engine"].evaluate_calls == []


# ── eval：找不到包 ───────────────────────────────────────────────────────────


def test_eval_找不到包时提示先跑_parse(
    configs_root: Path, packages_root: Path, recorded: Dict[str, Any]
) -> None:
    """包不存在 = 还没解析过。报人话，并且**不**顺手替他解析（那一步要花钱）。"""
    result = _run_eval(packages_root, configs_root, submission="没解析过的人")

    assert result.exit_code == 1
    assert "parse" in result.output
    assert "没解析过的人" in result.output
    assert recorded["engine"].evaluate_calls == []


@pytest.mark.parametrize(
    "exc",
    [
        cli.EngineConfigError("model_config 缺少 raters.rater_2"),
        cli.ConfigCompileError("Dimension rubric file not found: .../zz9_rubric.yaml"),
        FileNotFoundError("configs/nope.yaml"),
        KeyError("providers"),
    ],
)
def test_eval_用户可修的错误只印一行(
    configs_root: Path, packages_root: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """配置写错（yaml 少字段、--dim 拼错、引用文件缺失）都是用户能自己修的，
    印一行人话即可，不甩一屏 traceback。"""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise exc

    monkeypatch.setattr(cli.Engine, "from_configs", _boom)

    result = _run_eval(packages_root, configs_root)

    assert result.exit_code == 1
    assert type(exc).__name__ in result.output
    assert "Traceback" not in result.output


def test_eval_拒绝不存在的配置目录(
    packages_root: Path, tmp_path: Path, recorded: Dict[str, Any]
) -> None:
    result = _run_eval(packages_root, tmp_path / "nope")

    assert result.exit_code == 1
    assert "nope" in result.output
    assert recorded["engine"].evaluate_calls == []


def test_eval_一个分都没评出来时退非零(
    configs_root: Path, packages_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全维度失败时 engine 不再抛异常（否则会拖垮同 submission 其余二级指标），
    但 CLI 必须退非零——否则脚本会把"什么都没评出来"当成功。"""

    class _AllFailedEngine:
        def evaluate(self, _package: Any, dim: Optional[str] = None) -> Dict[str, Any]:
            return {"d1": _evaluation_all_failed("d1")}

    monkeypatch.setattr(cli.Engine, "from_configs", lambda *a, **k: _AllFailedEngine())

    result = _run_eval(packages_root, configs_root)

    assert result.exit_code == 1
    assert "全部观测点评价失败" in result.output
    assert "401 unauthorized" in result.output


# ── parse ────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_docmind(monkeypatch: pytest.MonkeyPatch, configs_root: Path) -> Dict[str, Any]:
    """注入假的「发起调用」函数——parse 的测试一律零网络、零密钥。"""
    shutil.copy(_PROJECT_ROOT / "configs" / "parse.yaml", configs_root / "parse.yaml")
    box: Dict[str, Any] = {"submitted": []}

    def _call(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "submit":
            box["submitted"].append(payload["file_name"])
            return {"id": f"job-{payload['file_name']}"}
        if op == "status":
            return {"status": "success"}
        return {
            "layouts": [
                {"index": 0, "type": "title", "markdownContent": "# 标题", "pageNum": 0},
                {"index": 1, "type": "text", "markdownContent": "正文一句。", "pageNum": 0},
                {"index": 2, "type": "foot", "markdownContent": "第 1 页", "pageNum": 0},
            ]
        }

    monkeypatch.setattr(cli, "require_credentials", lambda: ("ak", "sk"))
    monkeypatch.setattr(cli, "sdk_caller", lambda _config, _credentials: _call)
    return box


def _material(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x")
    return path


def test_parse_解析一次提交的全部文件(
    configs_root: Path, tmp_path: Path, fake_docmind: Dict[str, Any]
) -> None:
    files = [_material(tmp_path, "报告.pdf"), _material(tmp_path, "答辩.pptx")]
    packages = tmp_path / "packages"

    result = runner.invoke(
        cli.app,
        ["parse", *[str(f) for f in files], "--configs", str(configs_root),
         "--task", "testtask", "--submission", "2025213223", "--packages", str(packages)],
    )

    assert result.exit_code == 0, result.output
    assert fake_docmind["submitted"] == ["报告.pdf", "答辩.pptx"]
    package_json = packages / "testtask" / "2025213223" / "package.json"
    assert package_json.exists()
    package = DataPackage.from_dict(json.loads(package_json.read_text(encoding="utf-8")))
    assert [u.id for u in package.units] == [0, 1, 2, 3]
    # 被剔除的版面块要在命令行上被看见，不静默丢弃。
    assert "foot" in result.output


def test_parse_必须给_submission(configs_root: Path, tmp_path: Path, fake_docmind: Dict[str, Any]) -> None:
    result = runner.invoke(
        cli.app,
        ["parse", str(_material(tmp_path, "a.pdf")), "--configs", str(configs_root),
         "--task", "testtask"],
    )
    assert result.exit_code == 1
    assert "--submission" in result.output


def test_parse_源文件不存在时报错(configs_root: Path, tmp_path: Path, fake_docmind: Dict[str, Any]) -> None:
    result = runner.invoke(
        cli.app,
        ["parse", str(tmp_path / "没有这个.pdf"), "--configs", str(configs_root),
         "--task", "testtask", "--submission", "s1"],
    )
    assert result.exit_code == 1
    assert "没有这个.pdf" in result.output
    assert fake_docmind["submitted"] == []


def test_parse_解析失败时非零退出且不产出数据包(
    configs_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_docmind: Dict[str, Any]
) -> None:
    """解析类错误打一行人话，不甩 traceback。"""

    def _boom(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "submit":
            return {"id": "job-1"}
        return {"status": "Fail", "message": "服务内部错误"}

    monkeypatch.setattr(cli, "sdk_caller", lambda _config, _credentials: _boom)
    packages = tmp_path / "packages"

    result = runner.invoke(
        cli.app,
        ["parse", str(_material(tmp_path, "a.pdf")), "--configs", str(configs_root),
         "--task", "testtask", "--submission", "s1", "--packages", str(packages)],
    )

    assert result.exit_code == 1
    assert "a.pdf" in result.output and "服务内部错误" in result.output
    assert "Traceback" not in result.output
    assert not (packages / "testtask" / "s1" / "package.json").exists()


def test_parse_之后_eval_能直接跑起来(
    configs_root: Path, tmp_path: Path, fake_docmind: Dict[str, Any], recorded: Dict[str, Any]
) -> None:
    """两条命令串起来：parse 落包 → eval 按约定找到它。"""
    packages = tmp_path / "packages"
    parse_result = runner.invoke(
        cli.app,
        ["parse", str(_material(tmp_path, "a.pdf")), "--configs", str(configs_root),
         "--task", "testtask", "--submission", "s1", "--packages", str(packages)],
    )
    assert parse_result.exit_code == 0, parse_result.output

    eval_result = _run_eval(packages, configs_root, submission="s1")

    assert eval_result.exit_code == 0, eval_result.output
    package, _dim = recorded["engine"].evaluate_calls[0]
    assert package.package_id == "testtask/s1"
    assert [u.markdown for u in package.units] == ["# 标题", "正文一句。"]


# ── 打印 ──────────────────────────────────────────────────────────────────────


def test_render_summary_shows_scores_sources_and_totals() -> None:
    text = cli._render_summary({"d1": _evaluation("d1")})

    assert "d1" in text
    assert "3.5" in text
    assert "d1_1" in text and "consensus" in text
    assert "d1_2" in text and "adjudicated" in text
    assert "1234" in text


def test_render_summary_surfaces_failed_dimensions() -> None:
    """失败隔离下被跳过的观测点必须打印出来，否则用户以为评了全部维度。"""
    evaluation = _evaluation("d1", failed_codes=[{"code": "d1_3", "error": "boom"}])

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
