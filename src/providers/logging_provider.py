"""日志 Provider 包装器，把每次 LLM 调用的关键信息打到终端。

LoggingProvider 透明包装任何 BaseProvider，逐调用打印，且不改动被包装 provider
或任何流水线代码。每次调用打印：

  调用前： label  call#  model  prompt-char-count  [json] flag
  调用后： elapsed  prompt_tokens+completion_tokens=total  响应预览

累计统计（call_count / total_tokens / total_elapsed）作为属性可用，便于调用者
计算每篇材料的增量：

    before = p.call_count
    engine.evaluate(package)
    delta = p.call_count - before

v1 的 debug bundle 埋点（debug_writer / set_debug_writer）已随 `src/debug/` 一并
删除——成本与性能改由 engine 的 trace 收集器记录，不再需要第二套旁路写盘机制。

用法：
    provider = LoggingProvider(real_provider, label="rater_1")
    # 可直接顶替 Engine.from_bundle(providers=...) 里的真 provider"""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import TextIO

from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability


def _smart_preview(content: str) -> str:
    """解析 JSON 响应并返回简洁、可读的单行预览。"""
    try:
        data = json.loads(content)
    except Exception:
        snippet = content[:60].replace("\n", " ").replace("\r", " ")
        return f'"{snippet}"'

    # 评分：显示建议分数
    if "proposed_score" in data:
        return f"score={data['proposed_score']}"

    # 选段/取证：显示引用到的单元编号（v2 证据一律是 unit_ids，不复述原文）
    for key in ("selected_unit_ids", "evidence_unit_ids", "supporting_unit_ids"):
        if key in data:
            ids = data[key] or []
            head = ", ".join(str(i) for i in ids[:8])
            suffix = "…" if len(ids) > 8 else ""
            return f"{key}={len(ids)} [{head}{suffix}]"

    snippet = content[:60].replace("\n", " ").replace("\r", " ")
    return f'"{snippet}"'


class LoggingProvider(BaseProvider):
    """包装 BaseProvider；将每次 complete() 调用记录到 *file*（默认：stdout）。"""

    def __init__(
        self,
        inner: BaseProvider,
        label: str = "",
        file: TextIO | None = None,
    ) -> None:
        """
        Args:
            inner: 要委托的真实 provider。
            label: 每行日志中显示的易读阶段/评委名称，例如 "rater_1"、"feedback"。
            file:  输出流。默认 sys.stdout，以便与 typer.echo() 交错且无缓冲问题。"""
        self._inner = inner
        self._label = label
        self._file = file if file is not None else sys.stdout
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

        t0 = time.time()
        response = self._inner.complete(request)
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
