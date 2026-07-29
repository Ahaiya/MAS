import json
from pathlib import Path

from src.agents import feedback
from src.artifacts import (
    dim_dir,
    sample_dir,
    write_feedback_artifact,
    write_package_artifact,
    write_rater_chains_artifact,
)
from src.contracts.artifact_bundle import RubricSnapshot
from src.contracts.package import DataPackage, Unit
from src.contracts.scoring import FinalDecision, ScoreSource
from src.providers.base import LLMResponse, TokenUsage
from src.providers.fake import FakeProvider
from src.providers.prompt_loader import PromptLoader


def _package() -> DataPackage:
    units = [Unit(id=0, kind="prose", text="第一句。", source_file="a.md", char_range=(0, 4), speaker=None)]
    return DataPackage(package_id="pkg-1", units=units, metadata={"student_id": "s1"})


def test_write_package_artifact_lands_at_sample_layer(tmp_path: Path) -> None:
    path = write_package_artifact(tmp_path, "maker_hackathon", "s1", _package())

    assert path == sample_dir(tmp_path, "maker_hackathon", "s1") / "package.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["package_id"] == "pkg-1"
    assert data["units"][0]["text"] == "第一句。"


def test_write_feedback_artifact_lands_at_dim_layer(tmp_path: Path) -> None:
    path = write_feedback_artifact(tmp_path, "maker_hackathon", "s1", "a4", {"primary_score": 4.0})

    assert path == dim_dir(tmp_path, "maker_hackathon", "s1", "a4") / "feedback.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"primary_score": 4.0}


def test_write_rater_chains_artifact_lands_at_dim_layer(tmp_path: Path) -> None:
    path = write_rater_chains_artifact(tmp_path, "maker_hackathon", "s1", "a4", {"rater_1": []})

    assert path == dim_dir(tmp_path, "maker_hackathon", "s1", "a4") / "rater_chains.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"rater_1": []}


def test_package_json_is_shared_across_multiple_dims_in_same_sample(tmp_path: Path) -> None:
    """package.json 只在 sample 层写一份，多个 dim 目录共享同一份原文。"""
    write_package_artifact(tmp_path, "maker_hackathon", "s1", _package())
    write_feedback_artifact(tmp_path, "maker_hackathon", "s1", "a4", {})
    write_feedback_artifact(tmp_path, "maker_hackathon", "s1", "f2", {})

    package_files = list((tmp_path / "maker_hackathon" / "s1").rglob("package.json"))
    assert len(package_files) == 1
    assert package_files[0].parent == sample_dir(tmp_path, "maker_hackathon", "s1")


def test_three_layer_directory_structure(tmp_path: Path) -> None:
    write_package_artifact(tmp_path, "maker_hackathon", "s1", _package())
    write_feedback_artifact(tmp_path, "maker_hackathon", "s1", "a4", {})
    write_rater_chains_artifact(tmp_path, "maker_hackathon", "s1", "a4", {})

    relative_paths = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*.json"))
    assert [str(p) for p in relative_paths] == [
        "maker_hackathon/s1/a4/feedback.json",
        "maker_hackathon/s1/a4/rater_chains.json",
        "maker_hackathon/s1/package.json",
    ]


# ── 端到端落盘：build → write → 从磁盘读回 → 经 package.json 回指原文 ─────────


def test_feedback_and_package_round_trip_through_disk(tmp_path: Path) -> None:
    """consensus 与 adjudicated 两种 source 都能正确落盘；feedback.json 的
    unit_ids 能经磁盘上的 package.json 回指原文（不是只在内存里验证）。"""
    units = [
        Unit(id=i, kind="prose", text=f"text {i}", source_file="a.md", char_range=(0, 5), speaker=None)
        for i in range(5)
    ]
    package = DataPackage(package_id="pkg-1", units=units, metadata={})

    dimensions = [
        {"dimension_id": "a4_1", "name": "dim1", "scale_ref": "ordinal_1_5", "weight": 0.5,
         "levels": [{"rank": r, "summary": str(r), "descriptors": [f"level {r}"]} for r in range(1, 6)]},
        {"dimension_id": "a4_2", "name": "dim2", "scale_ref": "ordinal_1_5", "weight": 0.5,
         "levels": [{"rank": r, "summary": str(r), "descriptors": [f"level {r}"]} for r in range(1, 6)]},
    ]
    scale = {"scale_id": "ordinal_1_5", "type": "ordinal", "min": 1, "max": 5}
    rubric = RubricSnapshot(
        rubric_id="r", rubric_version="t", rubric_name="n", dimensions=dimensions, scales=[scale],
        dimension_by_id={d["dimension_id"]: d for d in dimensions}, dimension_by_code={},
        scale_by_id={"ordinal_1_5": scale},
    )
    decisions = [
        FinalDecision(
            dimension_id="a4_1",
            final_score=3,
            source=ScoreSource.CONSENSUS,
            unit_ids=[0, 1],
        ),
        FinalDecision(
            dimension_id="a4_2",
            final_score=4,
            source=ScoreSource.ADJUDICATED,
            unit_ids=[3],
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
    write_package_artifact(tmp_path, "maker_hackathon", "s1", package)
    write_feedback_artifact(tmp_path, "maker_hackathon", "s1", "a4", feedback_report)

    # 从磁盘重新读回，不复用内存里的 package/feedback_report 对象
    package_json_path = sample_dir(tmp_path, "maker_hackathon", "s1") / "package.json"
    feedback_json_path = dim_dir(tmp_path, "maker_hackathon", "s1", "a4") / "feedback.json"
    on_disk_package = json.loads(package_json_path.read_text(encoding="utf-8"))
    on_disk_feedback = json.loads(feedback_json_path.read_text(encoding="utf-8"))

    unit_text_by_id = {u["id"]: u["text"] for u in on_disk_package["units"]}

    consensus_entry = on_disk_feedback["dimensions"]["a4_1"]
    adjudicated_entry = on_disk_feedback["dimensions"]["a4_2"]
    assert consensus_entry["source"] == "consensus"
    assert adjudicated_entry["source"] == "adjudicated"
    assert [unit_text_by_id[uid] for uid in consensus_entry["unit_ids"]] == ["text 0", "text 1"]
    assert [unit_text_by_id[uid] for uid in adjudicated_entry["unit_ids"]] == ["text 3"]
    assert on_disk_feedback["primary_score"] == 3.5
