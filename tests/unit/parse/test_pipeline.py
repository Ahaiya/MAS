"""`src/parse/pipeline.py`：落盘布局、重跑语义、失败与并发策略。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.parse.config import ParseConfig
from src.parse.docmind import ParseServiceError, ParseTimeout
from src.parse.pipeline import SubmissionParseError, parse_submission

_CONFIG = ParseConfig(
    endpoint="x",
    connect_timeout_ms=1000,
    read_timeout_ms=1000,
    poll_interval_seconds=0.0,
    poll_max_seconds=30.0,
    layout_step_size=100,
    options={"llm_enhancement": True, "enhancement_mode": "VLM", "formula_enhancement": True},
)


class _ScriptedCall:
    """按文件名回放：提交时记下是哪个文件，取结果时返回它对应的 layouts 或抛错。"""

    def __init__(self, by_file: Dict[str, Any]) -> None:
        self._by_file = by_file
        self._jobs: Dict[str, str] = {}
        self.submitted: List[str] = []

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if op == "submit":
            name = payload["file_name"]
            self.submitted.append(name)
            job_id = f"job-{name}"
            self._jobs[job_id] = name
            return {"id": job_id}
        name = self._jobs[payload["id"]]
        outcome = self._by_file[name]
        if isinstance(outcome, Exception):
            raise outcome
        if op == "status":
            return {"status": "success"}
        return {"layouts": outcome, "docName": name}


def _layout(index: int, text: str) -> Dict[str, Any]:
    return {"index": index, "type": "text", "markdownContent": text, "pageNum": 0}


def _files(tmp_path: Path, *names: str) -> List[Path]:
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(path)
    return paths


def _run(tmp_path: Path, files: List[Path], call: Any, **kwargs: Any):
    return parse_submission(
        files,
        task="experiment",
        submission="2025213223",
        packages_root=tmp_path / "packages",
        config=_CONFIG,
        call=call,
        sleep=lambda _s: None,
        now=lambda: "2026-08-04T10:00:00",
        **kwargs,
    )


def _submission_dir(tmp_path: Path) -> Path:
    return tmp_path / "packages" / "experiment" / "2025213223"


# ── 落盘 ─────────────────────────────────────────────────────────────────────


def test_raw_与_package_都落地(tmp_path: Path) -> None:
    files = _files(tmp_path, "报告.pdf", "答辩.pptx")
    call = _ScriptedCall({"报告.pdf": [_layout(0, "甲")], "答辩.pptx": [_layout(0, "乙")]})
    package = _run(tmp_path, files, call)

    root = _submission_dir(tmp_path)
    assert (root / "raw" / "报告.pdf.json").exists()
    assert (root / "raw" / "答辩.pptx.json").exists()
    written = json.loads((root / "package.json").read_text(encoding="utf-8"))
    assert written == package.to_dict()
    assert package.package_id == "experiment/2025213223"
    assert [u.markdown for u in package.units] == ["甲", "乙"]


def test_raw_原样落盘不裁剪(tmp_path: Path) -> None:
    files = _files(tmp_path, "a.pdf")
    call = _ScriptedCall({"a.pdf": [_layout(0, "甲"), {"index": 1, "type": "foot"}]})
    _run(tmp_path, files, call)
    raw = json.loads((_submission_dir(tmp_path) / "raw" / "a.pdf.json").read_text(encoding="utf-8"))
    # 被白名单剔除的版面块在 raw 里仍然完整存在——字段取舍要可逆就得靠它。
    assert [item["type"] for item in raw["layouts"]] == ["text", "foot"]
    assert raw["docName"] == "a.pdf"


def test_不写进_artifacts(tmp_path: Path) -> None:
    files = _files(tmp_path, "a.pdf")
    _run(tmp_path, files, _ScriptedCall({"a.pdf": [_layout(0, "甲")]}))
    assert not (tmp_path / "artifacts").exists()


# ── 重跑 ─────────────────────────────────────────────────────────────────────


def test_重跑跳过已有_raw_只重建_package(tmp_path: Path) -> None:
    files = _files(tmp_path, "a.pdf")
    _run(tmp_path, files, _ScriptedCall({"a.pdf": [_layout(0, "甲")]}))

    second = _ScriptedCall({"a.pdf": [_layout(0, "乙")]})
    package = _run(tmp_path, files, second)
    assert second.submitted == []  # 没有再付一次费
    assert [u.markdown for u in package.units] == ["甲"]
    assert (_submission_dir(tmp_path) / "package.json").exists()


def test_force_强制重解析并覆盖_raw(tmp_path: Path) -> None:
    files = _files(tmp_path, "a.pdf")
    _run(tmp_path, files, _ScriptedCall({"a.pdf": [_layout(0, "甲")]}))

    second = _ScriptedCall({"a.pdf": [_layout(0, "乙")]})
    package = _run(tmp_path, files, second, force=True)
    assert second.submitted == ["a.pdf"]
    assert [u.markdown for u in package.units] == ["乙"]


# ── 失败 ─────────────────────────────────────────────────────────────────────


def test_一个文件失败则整个提交失败且无_package(tmp_path: Path) -> None:
    files = _files(tmp_path, "好.pdf", "坏.pdf")
    call = _ScriptedCall({"好.pdf": [_layout(0, "甲")], "坏.pdf": ParseServiceError("服务内部错误")})
    with pytest.raises(SubmissionParseError) as excinfo:
        _run(tmp_path, files, call)

    assert "坏.pdf" in str(excinfo.value)
    assert not (_submission_dir(tmp_path) / "package.json").exists()


def test_失败时成功文件的_raw_仍然落盘(tmp_path: Path) -> None:
    """整体失败不等于把已付费的结果扔掉。"""
    files = _files(tmp_path, "好.pdf", "坏.pdf")
    call = _ScriptedCall({"好.pdf": [_layout(0, "甲")], "坏.pdf": ParseServiceError("服务内部错误")})
    with pytest.raises(SubmissionParseError):
        _run(tmp_path, files, call)
    assert (_submission_dir(tmp_path) / "raw" / "好.pdf.json").exists()
    assert not (_submission_dir(tmp_path) / "raw" / "坏.pdf.json").exists()


def test_多个文件失败时错误不被吞(tmp_path: Path) -> None:
    files = _files(tmp_path, "一.pdf", "二.pdf")
    call = _ScriptedCall({"一.pdf": ParseServiceError("错误甲"), "二.pdf": ParseServiceError("错误乙")})
    with pytest.raises(SubmissionParseError) as excinfo:
        _run(tmp_path, files, call)
    message = str(excinfo.value)
    assert "错误甲" in message and "错误乙" in message


def test_超时错误保留_job_id_供续查(tmp_path: Path) -> None:
    files = _files(tmp_path, "慢.pdf")
    call = _ScriptedCall({"慢.pdf": ParseTimeout("job-9", "慢.pdf", 7200)})
    with pytest.raises(SubmissionParseError) as excinfo:
        _run(tmp_path, files, call)
    assert "job-9" in str(excinfo.value)


def test_重跑时已成功的文件不再解析(tmp_path: Path) -> None:
    """失败重跑不该为上次已经付过费的文件再付一次。"""
    files = _files(tmp_path, "好.pdf", "坏.pdf")
    first = _ScriptedCall({"好.pdf": [_layout(0, "甲")], "坏.pdf": ParseServiceError("服务内部错误")})
    with pytest.raises(SubmissionParseError):
        _run(tmp_path, files, first)

    second = _ScriptedCall({"好.pdf": [_layout(0, "甲")], "坏.pdf": [_layout(0, "乙")]})
    package = _run(tmp_path, files, second)
    assert second.submitted == ["坏.pdf"]
    assert [u.markdown for u in package.units] == ["甲", "乙"]


# ── 溯源 ─────────────────────────────────────────────────────────────────────


def test_溯源记录源文件与开关(tmp_path: Path) -> None:
    files = _files(tmp_path, "a.pdf", "b.pdf")
    call = _ScriptedCall({"a.pdf": [_layout(0, "甲")], "b.pdf": [{"index": 0, "type": "head"}]})
    package = _run(tmp_path, files, call)
    assert package.provenance["source_files"] == ["a.pdf", "b.pdf"]
    assert package.provenance["options"] == _CONFIG.options
    assert package.provenance["excluded_layouts"] == {"head": 1}
    assert package.provenance["parsed_at"] == "2026-08-04T10:00:00"


def test_空文件列表直接报错(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _run(tmp_path, [], _ScriptedCall({}))


def test_同名文件直接拒绝(tmp_path: Path) -> None:
    """raw 按文件名落盘：同名会互相覆盖，得到一份「同一文件读了两遍」的包。"""
    (tmp_path / "甲").mkdir()
    (tmp_path / "乙").mkdir()
    files = []
    for sub in ("甲", "乙"):
        path = tmp_path / sub / "报告.pdf"
        path.write_bytes(b"x")
        files.append(path)

    call = _ScriptedCall({"报告.pdf": [_layout(0, "甲")]})
    with pytest.raises(ValueError, match="同名"):
        _run(tmp_path, files, call)
    assert call.submitted == []
