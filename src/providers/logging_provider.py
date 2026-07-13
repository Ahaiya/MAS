"""日志 Provider 包装器，负责把每次 LLM 调用的关键信息输出到终端和调试包。

LoggingProvider — 透明包装器，将 LLM 调用详情打印到终端。

包装任何 BaseProvider，并记录每次 complete() 调用，且不修改
底层 provider 或任何 pipeline 代码。每次调用打印的字段：

  调用前：   label  call#  model  prompt-char-count  [json] flag
  调用后：  elapsed  prompt_tokens+completion_tokens=total  响应预览

累计统计（call_count, total_tokens, total_elapsed）作为属性可用，
以便调用者可以计算每篇文章的增量（delta）：

    before_calls = p.call_count
    runner.run(request)
    delta = p.call_count - before_calls

用法：
    from src.providers.logging_provider import LoggingProvider

    provider = LoggingProvider(real_provider, label="rater_1")
    # provider 是 PipelineRunner 中 real_provider 的直接替代品"""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import TYPE_CHECKING, TextIO

from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability

if TYPE_CHECKING:
    from src.debug.bundle import DebugBundleWriter


def _smart_preview(content: str) -> str:
    """解析 JSON 响应并返回简洁、可读的单行预览。"""
    try:
        data = json.loads(content)
    except Exception:
        snippet = content[:60].replace("\n", " ").replace("\r", " ")
        return f'"{snippet}"'

    # 评分假设：显示建议分数
    if "proposed_score" in data:
        return f"score={data['proposed_score']}"

    # 证据提取：显示 span 数量 + 来自不同 span 的前几个词
    if "evidence_spans" in data:
        spans = data["evidence_spans"]
        n = len(spans)
        if not spans:
            return "0 spans"
        snippets = []
        for span in spans[:3]:
            q = str(span.get("quote", "")).strip()
            words = q.split()[:5]
            if words:
                snippets.append('"' + " ".join(words) + '…"')
        return f"{n} spans: {', '.join(snippets)}"

    # 回退
    snippet = content[:60].replace("\n", " ").replace("\r", " ")
    return f'"{snippet}"'


class LoggingProvider(BaseProvider):
    """包装 BaseProvider；将每次 complete() 调用记录到 *file*（默认：stdout）。"""

    def __init__(
        self,
        inner: BaseProvider,
        label: str = "",
        file: TextIO | None = None,
        debug_writer: "DebugBundleWriter | None" = None,
    ) -> None:
        """
        Args:
            inner: 要委托的真实 provider。
            label: 每行日志中显示的易于阅读的阶段/评分者名称，
                   例如 "rater_1", "evidence_extraction", "feedback"。
            file:  输出流。默认为 sys.stdout，以便输出与
                   typer.echo() 交错且无缓冲问题。"""
        self._inner = inner
        self._label = label
        self._file = file if file is not None else sys.stdout
        self._debug_writer = debug_writer
        self._call_count: int = 0
        self._total_tokens: int = 0
        self._total_elapsed: float = 0.0
        self._lock = threading.Lock()

    # ── BaseProvider interface ─────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return self._inner.capabilities

    def complete(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            self._call_count += 1
            call_no = self._call_count

        # 在嵌套包装器之间一致地解析模型名称。
        # 优先级：request override > wrapped provider chain > "?"
        model = request.model_id or self.model_id

        structured_tag = "  [json]" if request.output_schema else ""
        prompt_len = len(request.prompt)
        label_col = f"{self._label:<22}" if self._label else f"{'llm':<22}"

        # ── 调用前行 ──────────────────────────────────────────────────────
        print(
            f"         ▶ {label_col}  #{call_no:<3}  "
            f"{model:<20}  {prompt_len:>5} p-chars{structured_tag}",
            file=self._file,
            flush=True,
        )

        debug_call_id = None
        if self._debug_writer is not None:
            try:
                debug_call_id = self._debug_writer.record_llm_call_started(
                    label=self._label or "llm",
                    provider_name=self.name,
                    model_id=model,
                    request=request,
                )
            except Exception as exc:
                print(
                    f"[debug-warning] failed to record llm_call_started: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

        t0 = time.time()
        try:
            response = self._inner.complete(request)
        except Exception as exc:
            elapsed = time.time() - t0
            if self._debug_writer is not None and debug_call_id is not None:
                try:
                    self._debug_writer.record_llm_call_error(
                        call_id=debug_call_id,
                        error=exc,
                        elapsed_ms=elapsed * 1000.0,
                    )
                except Exception as debug_exc:
                    print(
                        f"[debug-warning] failed to record llm_call_error: {debug_exc}",
                        file=sys.stderr,
                        flush=True,
                    )
            raise

        elapsed = time.time() - t0

        with self._lock:
            self._total_tokens += response.usage.total_tokens
            self._total_elapsed += elapsed

        u = response.usage

        # ── 调用后行 ─────────────────────────────────────────────────────
        token_str = f"{u.prompt_tokens}+{u.completion_tokens}={u.total_tokens} tok"
        preview = _smart_preview(response.content)
        print(
            f"           ✓ {elapsed:>5.1f}s  {token_str}  → {preview}",
            file=self._file,
            flush=True,
        )

        if self._debug_writer is not None and debug_call_id is not None:
            try:
                self._debug_writer.record_llm_call_finished(
                    call_id=debug_call_id,
                    response=response,
                    elapsed_ms=elapsed * 1000.0,
                )
            except Exception as exc:
                print(
                    f"[debug-warning] failed to record llm_call_finished: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

        return response

    # ── 累计统计 ───────────────────────────────────────────────────────

    @property
    def model_id(self) -> str:
        """被包装 provider 的模型标识符。"""
        p = self._inner
        visited: set[int] = set()
        while p is not None and id(p) not in visited:
            visited.add(id(p))
            public_mid = getattr(p, "model_id", None)
            if isinstance(public_mid, str) and public_mid and public_mid != "?":
                return public_mid
            private_mid = getattr(p, "_model_id", None)
            if isinstance(private_mid, str) and private_mid:
                return private_mid
            p = getattr(p, "_inner", None)
        return "?"

    @property
    def call_count(self) -> int:
        """通过此包装器进行的 complete() 调用总数。"""
        return self._call_count

    @property
    def total_tokens(self) -> int:
        """所有调用累计的 token 总数。"""
        return self._total_tokens

    @property
    def total_elapsed(self) -> float:
        """花在 complete() 调用内部的累计挂钟秒数。"""
        return self._total_elapsed

    def set_debug_writer(self, debug_writer: "DebugBundleWriter | None") -> None:
        """附加或分离每次运行的调试包写入器。"""
        self._debug_writer = debug_writer
