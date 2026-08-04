"""`src/parse/docmind.py`：注入假「发起调用」函数，零网络。

这里测的全是不纯逻辑里错了最贵的部分：轮询提前退出、分页少拉一段、把「处理中」
误判为失败、超时后有没有保留 job id。这些 bug 在纯函数侧不会暴露，后果是拿着半份
材料评分。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.parse.config import ParseConfig
from src.parse.docmind import (
    DocMindError,
    ParseIncompleteError,
    ParseQuotaError,
    ParseServiceError,
    ParseTimeout,
    UnsupportedFormatError,
    parse_file,
)

_CONFIG = ParseConfig(
    endpoint="x",
    connect_timeout_ms=1000,
    read_timeout_ms=1000,
    poll_interval_seconds=0.0,
    poll_max_seconds=30.0,
    layout_step_size=2,
    options={"llm_enhancement": True, "enhancement_mode": "VLM", "formula_enhancement": True},
)


def _layouts(start: int, count: int) -> List[Dict[str, Any]]:
    return [{"index": i, "type": "text", "markdownContent": f"m{i}"} for i in range(start, start + count)]


class _FakeCall:
    """按脚本回放三类调用；记录收到的参数。"""

    def __init__(self, statuses: List[Any], pages: List[Any], submit: Any = None) -> None:
        self._statuses = list(statuses)
        self._pages = list(pages)
        self._submit = submit if submit is not None else {"id": "job-1"}
        self.calls: List[tuple[str, Dict[str, Any]]] = []

    def __call__(self, op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((op, dict(payload)))
        if op == "submit":
            return _raise_or_return(self._submit)
        if op == "status":
            return _raise_or_return(self._statuses.pop(0))
        if op == "result":
            return _raise_or_return(self._pages.pop(0))
        raise AssertionError(f"未知操作 {op}")


def _raise_or_return(item: Any) -> Any:
    if isinstance(item, Exception):
        raise item
    return item


def _fake_clock():
    """单调递增的假时钟——每次读取前进 10 秒，配合 poll_max_seconds 触发超时。"""
    state = {"now": 0.0}

    def clock() -> float:
        state["now"] += 10.0
        return state["now"]

    return clock


def _run(call: _FakeCall, config: ParseConfig = _CONFIG, **kwargs: Any) -> Dict[str, Any]:
    return parse_file(Path("a.pdf"), config=config, call=call, sleep=lambda _s: None, **kwargs)


# ── 轮询 ─────────────────────────────────────────────────────────────────────


def test_轮询直到完成才取结果() -> None:
    call = _FakeCall(
        statuses=[{"Status": "processing"}, {"Status": "processing"}, {"Status": "success"}],
        pages=[{"layouts": _layouts(0, 1)}],
    )
    raw = _run(call)
    assert [op for op, _ in call.calls] == ["submit", "status", "status", "status", "result"]
    assert len(raw["layouts"]) == 1


def test_处理中不被误判为失败() -> None:
    call = _FakeCall(statuses=[{"Status": "processing"}, {"Status": "success"}], pages=[{"layouts": []}])
    _run(call)  # 不抛异常即为通过


def test_轮询超时保留_job_id() -> None:
    call = _FakeCall(statuses=[{"Status": "processing"}] * 100, pages=[])
    with pytest.raises(ParseTimeout) as excinfo:
        _run(call, clock=_fake_clock())
    assert excinfo.value.job_id == "job-1"
    assert "job-1" in str(excinfo.value)


def test_超时后不去取结果() -> None:
    """超时既不算成功也不算永久失败——绝不能顺手拉一份残缺结果回来。"""
    call = _FakeCall(statuses=[{"Status": "processing"}] * 100, pages=[])
    with pytest.raises(ParseTimeout):
        _run(call, clock=_fake_clock())
    assert not any(op == "result" for op, _ in call.calls)


# ── 状态失败分类 ──────────────────────────────────────────────────────────────


def test_状态_fail_归为服务失败() -> None:
    call = _FakeCall(statuses=[{"Status": "Fail", "message": "内部错误"}], pages=[])
    with pytest.raises(ParseServiceError, match="内部错误"):
        _run(call)


def test_配额类错误可单独区分() -> None:
    call = _FakeCall(statuses=[{"Status": "Fail", "code": "Throttling.Api", "message": "超出限流"}], pages=[])
    with pytest.raises(ParseQuotaError):
        _run(call)


def test_格式不支持可单独区分() -> None:
    call = _FakeCall(
        statuses=[], pages=[],
        submit={"code": "IllegalFileFormat", "message": "不支持的文件格式"},
    )
    with pytest.raises(UnsupportedFormatError):
        _run(call)


def test_提交没拿到_job_id_即报错() -> None:
    call = _FakeCall(statuses=[], pages=[], submit={})
    with pytest.raises(DocMindError, match="job id"):
        _run(call)


# ── 分页 ─────────────────────────────────────────────────────────────────────


def test_分页拉全并按顺序拼接() -> None:
    call = _FakeCall(
        statuses=[{"Status": "success"}],
        pages=[
            {"layouts": _layouts(0, 2), "docName": "a"},
            {"layouts": _layouts(2, 2), "docName": "a"},
            {"layouts": _layouts(4, 1), "docName": "a"},
        ],
    )
    raw = _run(call)
    assert [item["index"] for item in raw["layouts"]] == [0, 1, 2, 3, 4]
    assert raw["docName"] == "a"
    result_calls = [payload for op, payload in call.calls if op == "result"]
    assert [p["layout_num"] for p in result_calls] == [0, 2, 4]
    assert all(p["layout_step_size"] == 2 for p in result_calls)


def test_分页中途失败不产出半份结果() -> None:
    call = _FakeCall(
        statuses=[{"Status": "success"}],
        pages=[{"layouts": _layouts(0, 2)}, ParseServiceError("第二页拉取失败")],
    )
    with pytest.raises(ParseServiceError):
        _run(call)


def test_取结果返回错误码时报错() -> None:
    call = _FakeCall(
        statuses=[{"Status": "success"}],
        pages=[{"code": "Throttling.Api", "message": "取结果被限流"}],
    )
    with pytest.raises(ParseQuotaError):
        _run(call)


def test_满页后返回空页也算拉全() -> None:
    call = _FakeCall(
        statuses=[{"Status": "success"}],
        pages=[{"layouts": _layouts(0, 2)}, {"layouts": []}],
    )
    raw = _run(call)
    assert len(raw["layouts"]) == 2


# ── 拉全校验与 status 留存 ───────────────────────────────────────────────────


def test_拉回条数与服务端总数一致则通过() -> None:
    call = _FakeCall(
        statuses=[{"Status": "success", "NumberOfSuccessfulParsing": 3}],
        pages=[{"layouts": _layouts(0, 2)}, {"layouts": _layouts(2, 1)}],
    )
    assert len(_run(call)["layouts"]) == 3


def test_拉回条数少于服务端总数则报错不产包() -> None:
    """分页少拉一段在产物上完全看不出来——服务端给了总数就必须核对。"""
    call = _FakeCall(
        statuses=[{"Status": "success", "NumberOfSuccessfulParsing": 5}],
        pages=[{"layouts": _layouts(0, 2)}, {"layouts": _layouts(2, 1)}],
    )
    with pytest.raises(ParseIncompleteError, match="只拉回 3 个.*共 5 个"):
        _run(call)


def test_服务端没给总数就不校验() -> None:
    """编一个期望值出来，只会把「不知道」伪装成「已核对」。"""
    call = _FakeCall(statuses=[{"Status": "success"}], pages=[{"layouts": _layouts(0, 1)}])
    assert len(_run(call)["layouts"]) == 1


def test_status_整份留在_raw_里() -> None:
    """用量与统计只有这一次机会拿到，任务过期就没了。"""
    status = {"Status": "success", "NumberOfSuccessfulParsing": 1, "PageCountEstimate": 7,
              "Tokens": 2769, "ImageCount": 1, "TableCount": 1}
    call = _FakeCall(statuses=[status], pages=[{"layouts": _layouts(0, 1)}])
    assert _run(call)["status"] == status


def test_查状态返回错误码立即报错而不是空转到超时() -> None:
    """带 code 的响应里根本没有 Status，当成「处理中」会白等两小时。"""
    call = _FakeCall(
        statuses=[{"code": "NoPermission", "message": "You are not authorized"}],
        pages=[],
    )
    with pytest.raises(ParseServiceError, match="NoPermission"):
        _run(call)


# ── 提交参数 ─────────────────────────────────────────────────────────────────


def test_提交带上三个增强开关与文件名() -> None:
    call = _FakeCall(statuses=[{"Status": "success"}], pages=[{"layouts": []}])
    _run(call)
    _, payload = call.calls[0]
    assert payload["file_name"] == "a.pdf"
    assert payload["options"] == _CONFIG.options
