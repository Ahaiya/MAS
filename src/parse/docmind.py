"""
Document Mind（文档解析·大模型版）客户端：一个文件进、完整 layouts 出。

三步异步：`SubmitDocParserJobAdvance`（本地文件流直传）→ `QueryDocParserStatus`
（轮询）→ `GetDocParserResult`（按 LayoutNum / LayoutStepSize 分页拉全）。**拉全
之前不算成功**——拉了一半就当成功，会得到一份静悄悄缺尾的材料。

不走 URL 版 `SubmitDocParserJob`：那要求材料先落到公网可达的 OSS，而学生材料隐私
敏感，为此引入一个 OSS 桶和第二套凭证不值得。

**接缝**：本模块把「发起调用」抽成一个函数参数 `call(op, payload) -> dict`，默认是
真 SDK（`sdk_caller()`），测试传假的。不抽接口、不做工厂、不做 FakeParser 类——解析
只有一个实现，为它造接口纯属仪式。

超时**不算永久失败**：抛出的 ParseTimeout 带 job id，结果 24 小时内可凭它续查，
不必重新提交重新付费。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from src.parse.config import ParseConfig

# call(op, payload) -> dict，op ∈ {"submit", "status", "result"}。
CallFn = Callable[[str, Dict[str, Any]], Dict[str, Any]]

_SUCCESS_STATUSES = {"success"}
_FAILURE_STATUSES = {"fail", "failed", "error"}

# 错误码 → 类别。取子串匹配是有意的：阿里云的码带点分层级（Throttling.Api、
# Throttling.User），逐个枚举必然漏。
_QUOTA_MARKERS = ("throttl", "quota", "limitexceeded", "flowcontrol", "insufficientbalance")
_FORMAT_MARKERS = ("format", "filetype", "unsupport", "notsupport", "filesizeexceed")


class DocMindError(Exception):
    """解析服务相关错误的基类。"""


class ParseQuotaError(DocMindError):
    """配额/限流/余额——重跑无用，得先去控制台。"""


class UnsupportedFormatError(DocMindError):
    """服务不接受这个文件（格式或体积）——换材料才有用。"""


class ParseServiceError(DocMindError):
    """服务侧解析失败，且不属于上面两类。"""


class ParseNetworkError(DocMindError):
    """网络/传输层错误——通常重跑就好。"""


class ParseTimeout(DocMindError):
    """轮询到达上限。既不算成功也不算永久失败，带着 job id 好续查。"""

    def __init__(self, job_id: str, source_file: str, waited_seconds: float) -> None:
        super().__init__(
            f"{source_file}：解析等待超过 {waited_seconds:.0f} 秒仍未完成。"
            f"任务未取消，结果 24 小时内可凭 job id 续查：{job_id}"
        )
        self.job_id = job_id
        self.source_file = source_file


def _classify(payload: Dict[str, Any], fallback: str) -> DocMindError:
    """按错误码把服务侧失败分成可区分的几类——统一吞成一个 Exception 的话，
    「该重跑」和「该去充值」在命令行上长得一模一样。"""
    code = str(payload.get("code") or "")
    message = str(payload.get("message") or "") or fallback
    detail = f"{message}（code={code}）" if code else message
    lowered = code.lower()
    if any(marker in lowered for marker in _QUOTA_MARKERS):
        return ParseQuotaError(detail)
    if any(marker in lowered for marker in _FORMAT_MARKERS):
        return UnsupportedFormatError(detail)
    return ParseServiceError(detail)


def _submit(path: Path, config: ParseConfig, call: CallFn) -> str:
    response = call(
        "submit",
        {
            "file_path": str(path),
            "file_name": path.name,
            "options": dict(config.options),
        },
    )
    job_id = str(response.get("id") or "")
    if not job_id:
        if response.get("code") or response.get("message"):
            raise _classify(response, f"{path.name}：提交解析任务失败。")
        raise DocMindError(f"{path.name}：提交解析任务后没拿到 job id，响应为 {response!r}。")
    return job_id


def _wait_until_done(
    job_id: str,
    path: Path,
    config: ParseConfig,
    call: CallFn,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> None:
    """轮询到 success 为止。到上限抛 ParseTimeout，**不**顺手去拉一份残缺结果。"""
    started = clock()
    while True:
        response = call("status", {"id": job_id})
        status = str(response.get("status") or "").lower()
        if status in _SUCCESS_STATUSES:
            return
        if status in _FAILURE_STATUSES:
            raise _classify(response, f"{path.name}：解析失败。")
        # 其余一律当作「处理中」：把未知状态当失败，会把一个还在跑的付费任务扔掉。
        waited = clock() - started
        if waited >= config.poll_max_seconds:
            raise ParseTimeout(job_id, path.name, config.poll_max_seconds)
        sleep(config.poll_interval_seconds)


def _fetch_all_layouts(
    job_id: str, path: Path, config: ParseConfig, call: CallFn
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """分页拉全。返回 (全部 layouts, 最后一页的响应)。

    终止条件是「这一页没拉满」——满页就继续要下一页，包括最后恰好拉满时多要一次
    拿到空页。少要一次的代价是一份缺尾的材料，而它在产物上完全看不出来。"""
    layouts: List[Dict[str, Any]] = []
    last_page: Dict[str, Any] = {}
    layout_num = 0
    while True:
        page = call(
            "result",
            {"id": job_id, "layout_num": layout_num, "layout_step_size": config.layout_step_size},
        )
        if page.get("code") and not page.get("layouts"):
            raise _classify(page, f"{path.name}：取解析结果失败。")
        last_page = page
        batch = list(page.get("layouts") or [])
        layouts.extend(batch)
        if len(batch) < config.layout_step_size:
            return layouts, last_page
        layout_num += len(batch)


def parse_file(
    path: Path,
    *,
    config: ParseConfig,
    call: CallFn,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    """解析一个文件，返回**合并成一份**的完整响应（raw）。

    分页是传输层的事，跟「材料被识别成了什么」无关：layouts 按顺序拼全，其余字段
    取最后一次的值，存成一份。存成 N 份只会让每个读它的人先自己拼一遍。

    Raises:
        ParseQuotaError / UnsupportedFormatError / ParseServiceError /
        ParseNetworkError / ParseTimeout —— 各自可区分，见模块内各类的说明。"""
    job_id = _submit(path, config, call)
    _wait_until_done(job_id, path, config, call, sleep, clock)
    layouts, last_page = _fetch_all_layouts(job_id, path, config, call)
    return {**last_page, "layouts": layouts, "job_id": job_id}


# ── 真 SDK 的「发起调用」实现 ────────────────────────────────────────────────


def sdk_caller(config: ParseConfig, credentials: Tuple[str, str]) -> CallFn:
    """返回打真网络的 call 函数。SDK 在函数内导入——上面的纯逻辑不该为它买单。

    传输层异常统一转成 ParseNetworkError；服务侧的业务失败照原样返回（code /
    message / status），由 parse_file 分类。"""
    from alibabacloud_docmind_api20220711 import models as docmind_models
    from alibabacloud_docmind_api20220711.client import Client
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models

    access_key_id, access_key_secret = credentials
    client = Client(
        open_api_models.Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint=config.endpoint,
            connect_timeout=config.connect_timeout_ms,
            read_timeout=config.read_timeout_ms,
        )
    )

    def _submit_job(payload: Dict[str, Any]) -> Dict[str, Any]:
        options = payload["options"]
        path = Path(payload["file_path"])
        request = docmind_models.SubmitDocParserJobAdvanceRequest(
            file_name=payload["file_name"],
            file_name_extension=path.suffix.lstrip(".").lower(),
            llm_enhancement=bool(options["llm_enhancement"]),
            enhancement_mode=str(options["enhancement_mode"]),
            formula_enhancement=bool(options["formula_enhancement"]),
        )
        with path.open("rb") as stream:
            request.file_url_object = stream
            response = client.submit_doc_parser_job_advance(request, util_models.RuntimeOptions())
        body = response.body
        return {
            "id": getattr(body.data, "id", None) if body.data else None,
            "code": body.code,
            "message": body.message,
        }

    def _query_status(payload: Dict[str, Any]) -> Dict[str, Any]:
        response = client.query_doc_parser_status(
            docmind_models.QueryDocParserStatusRequest(id=payload["id"])
        )
        body = response.body
        return {
            "status": getattr(body.data, "status", None) if body.data else None,
            "code": body.code,
            "message": body.message,
        }

    def _get_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        response = client.get_doc_parser_result(
            docmind_models.GetDocParserResultRequest(
                id=payload["id"],
                layout_num=payload["layout_num"],
                layout_step_size=payload["layout_step_size"],
            )
        )
        body = response.body
        data = dict(body.data or {})
        if body.code:
            data.setdefault("code", body.code)
            data.setdefault("message", body.message)
        return data

    handlers = {"submit": _submit_job, "status": _query_status, "result": _get_result}

    def call(op: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return handlers[op](payload)
        except DocMindError:
            raise
        except OSError as exc:  # 连接中断、读文件失败
            raise ParseNetworkError(f"解析服务调用（{op}）失败：{exc}") from exc
        except Exception as exc:  # SDK 的 TeaException 等，不额外依赖它的类型
            raise ParseNetworkError(f"解析服务调用（{op}）失败：{type(exc).__name__}: {exc}") from exc

    return call
