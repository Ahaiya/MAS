import json
from pathlib import Path

from src.agents import feedback
from src.artifacts import (
    dim_dir,
    submission_dir,
    write_feedback_artifact,
    write_rater_chains_artifact,
)
from src.contracts.configuration import RubricSnapshot
from src.contracts.package import DataPackage, Unit
from src.contracts.scoring import FinalDecision, ScoreSource
from src.providers.base import LLMResponse, TokenUsage
from src.providers.fake import FakeProvider
from src.providers.prompt_loader import PromptLoader


def _package() -> DataPackage:
    units = [Unit(id=0, markdown="第一句。", type="text", source_file="a.md", page=0)]
    return DataPackage(package_id="pkg-1", units=units, provenance={"source_files": ["a.pdf"]})


def test_write_feedback_artifact_lands_at_dim_layer(tmp_path: Path) -> None:
    path = write_feedback_artifact(tmp_path, "maker_hackathon", "s1", "a4", {"primary_score": 4.0})

    assert path == dim_dir(tmp_path, "maker_hackathon", "s1", "a4") / "feedback.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"primary_score": 4.0}


def test_write_rater_chains_artifact_lands_at_dim_layer(tmp_path: Path) -> None:
    path = write_rater_chains_artifact(tmp_path, "maker_hackathon", "s1", "a4", {"rater_1": []})

    assert path == dim_dir(tmp_path, "maker_hackathon", "s1", "a4") / "rater_chains.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"rater_1": []}


def test_产物目录里不另存_package_json(tmp_path: Path) -> None:
    """package.json 是 parse 花钱买来的输入，住在 packages/；同一份包评 N 次，
    往 artifacts/ 里各存一份只会得到 N 份一模一样的副本。"""
    write_feedback_artifact(tmp_path, "maker_hackathon", "s1", "a4", {})
    write_rater_chains_artifact(tmp_path, "maker_hackathon", "s1", "a4", {})

    assert list(tmp_path.rglob("package.json")) == []


def test_三层目录结构(tmp_path: Path) -> None:
    write_feedback_artifact(tmp_path, "maker_hackathon", "s1", "a4", {})
    write_rater_chains_artifact(tmp_path, "maker_hackathon", "s1", "a4", {})

    relative_paths = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*.json"))
    assert [str(p) for p in relative_paths] == [
        "maker_hackathon/s1/a4/feedback.json",
        "maker_hackathon/s1/a4/rater_chains.json",
    ]


# ── 端到端落盘：build → write → 从磁盘读回 → 经 package.json 回指原文 ─────────


def test_feedback_and_package_round_trip_through_disk(tmp_path: Path) -> None:
    """consensus 与 adjudicated 两种 source 都能正确落盘；feedback.json 的
    unit_ids 能经磁盘上的 package.json 回指原文（不是只在内存里验证）。"""
    units = [
        Unit(id=i, markdown=f"text {i}", type="text", source_file="a.md", page=0)
        for i in range(5)
    ]
    package = DataPackage(package_id="pkg-1", units=units, provenance={})

    dimensions = [
        {"code": "A4-1", "name": "dim1", "weight": 0.5,
         "anchors": {r: f"level {r}" for r in range(1, 6)}},
        {"code": "A4-2", "name": "dim2", "weight": 0.5,
         "anchors": {r: f"level {r}" for r in range(1, 6)}},
    ]
    scale = {"scale_id": "ordinal_1_5", "type": "ordinal", "min": 1, "max": 5}
    rubric = RubricSnapshot(
        dim_id="a4", dim_name="A4", indicator_description="desc", dimensions=dimensions,
        scale_min=1, scale_max=5, scale_levels={r: str(r) for r in range(1, 6)},
    )
    decisions = [
        FinalDecision(
            code="A4-1",
            final_score=3,
            source=ScoreSource.CONSENSUS,
            unit_ids=[0, 1],
            rationale="定分理由",
        ),
        FinalDecision(
            code="A4-2",
            final_score=4,
            source=ScoreSource.ADJUDICATED,
            unit_ids=[3],
            rationale="定分理由",
        ),
    ]
    provider = FakeProvider(
        [
            LLMResponse(content="反馈1", structured_data=None, usage=TokenUsage(0, 0, 0), provider_name="fake", model_id="m"),
            LLMResponse(content="反馈2", structured_data=None, usage=TokenUsage(0, 0, 0), provider_name="fake", model_id="m"),
        ]
    )
    template = PromptLoader().load("configs/prompts/feedback.yaml")

    feedback_report = feedback.build_feedback_report(package, decisions, rubric, provider, template)
    write_feedback_artifact(tmp_path, "maker_hackathon", "s1", "a4", feedback_report)
    # package.json 由 parse 落在 packages/ 下，这里模拟它已经在盘上。
    package_json_path = tmp_path / "packages" / "maker_hackathon" / "s1" / "package.json"
    package_json_path.parent.mkdir(parents=True)
    package_json_path.write_text(
        json.dumps(package.to_dict(), ensure_ascii=False), encoding="utf-8"
    )

    # 从磁盘重新读回，不复用内存里的 package/feedback_report 对象
    feedback_json_path = dim_dir(tmp_path, "maker_hackathon", "s1", "a4") / "feedback.json"
    on_disk_package = json.loads(package_json_path.read_text(encoding="utf-8"))
    on_disk_feedback = json.loads(feedback_json_path.read_text(encoding="utf-8"))

    unit_text_by_id = {u["id"]: u["markdown"] for u in on_disk_package["units"]}

    consensus_entry = on_disk_feedback["dimensions"]["A4-1"]
    adjudicated_entry = on_disk_feedback["dimensions"]["A4-2"]
    assert consensus_entry["source"] == "consensus"
    assert adjudicated_entry["source"] == "adjudicated"
    assert [unit_text_by_id[uid] for uid in consensus_entry["unit_ids"]] == ["text 0", "text 1"]
    assert [unit_text_by_id[uid] for uid in adjudicated_entry["unit_ids"]] == ["text 3"]
    assert on_disk_feedback["primary_score"] == 3.5
