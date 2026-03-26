"""LoggingProvider — transparent wrapper that prints LLM call details to the terminal.

Wraps any BaseProvider and logs each complete() call without modifying the
underlying provider or any pipeline code.  Printed fields per call:

  Pre-call:   label  call#  model  prompt-char-count  [json] flag
  Post-call:  elapsed  prompt_tokens+completion_tokens=total  response preview

Cumulative stats (call_count, total_tokens, total_elapsed) are available as
properties so callers can compute per-essay deltas:

    before_calls = p.call_count
    runner.run(request)
    delta = p.call_count - before_calls

Usage:
    from src.providers.logging_provider import LoggingProvider

    provider = LoggingProvider(real_provider, label="rater_1")
    # provider is a drop-in replacement for real_provider in PipelineRunner
"""

from __future__ import annotations

import json
import sys
import time
from typing import TextIO

from src.providers.base import BaseProvider, LLMRequest, LLMResponse, ProviderCapability


def _smart_preview(content: str) -> str:
    """Parse JSON response and return a concise, readable one-line preview."""
    try:
        data = json.loads(content)
    except Exception:
        snippet = content[:60].replace("\n", " ").replace("\r", " ")
        return f'"{snippet}"'

    # Scoring hypothesis: show proposed score
    if "proposed_score" in data:
        return f"score={data['proposed_score']}"

    # Evidence extraction: show span count + first few words from distinct spans
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

    # Fallback
    snippet = content[:60].replace("\n", " ").replace("\r", " ")
    return f'"{snippet}"'


class LoggingProvider(BaseProvider):
    """Wraps a BaseProvider; logs each complete() call to *file* (default: stdout)."""

    def __init__(
        self,
        inner: BaseProvider,
        label: str = "",
        file: TextIO | None = None,
    ) -> None:
        """
        Args:
            inner: The real provider to delegate to.
            label: Human-readable stage/rater name shown in every log line,
                   e.g. "rater_1", "evidence_extraction", "feedback".
            file:  Output stream.  Defaults to sys.stdout so output is
                   interleaved with typer.echo() without buffering issues.
        """
        self._inner = inner
        self._label = label
        self._file = file if file is not None else sys.stdout
        self._call_count: int = 0
        self._total_tokens: int = 0
        self._total_elapsed: float = 0.0

    # ── BaseProvider interface ─────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return self._inner.capabilities

    def complete(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1

        # Resolve model name: request override > inner provider attribute > "?"
        model = getattr(self._inner, "_model_id", "?") or "?"
        if request.model_id:
            model = request.model_id

        structured_tag = "  [json]" if request.output_schema else ""
        prompt_len = len(request.prompt)
        label_col = f"{self._label:<22}" if self._label else f"{'llm':<22}"

        # ── Pre-call line ──────────────────────────────────────────────────────
        print(
            f"         ▶ {label_col}  #{self._call_count:<3}  "
            f"{model:<20}  {prompt_len:>5} p-chars{structured_tag}",
            file=self._file,
            flush=True,
        )

        t0 = time.time()
        response = self._inner.complete(request)
        elapsed = time.time() - t0

        self._total_tokens += response.usage.total_tokens
        self._total_elapsed += elapsed

        u = response.usage

        # ── Post-call line ─────────────────────────────────────────────────────
        token_str = f"{u.prompt_tokens}+{u.completion_tokens}={u.total_tokens} tok"
        preview = _smart_preview(response.content)
        print(
            f"           ✓ {elapsed:>5.1f}s  {token_str}  → {preview}",
            file=self._file,
            flush=True,
        )

        return response

    # ── Cumulative stats ───────────────────────────────────────────────────────

    @property
    def model_id(self) -> str:
        """Model identifier of the wrapped provider."""
        return getattr(self._inner, "_model_id", None) or "?"

    @property
    def call_count(self) -> int:
        """Total number of complete() calls made through this wrapper."""
        return self._call_count

    @property
    def total_tokens(self) -> int:
        """Cumulative total tokens across all calls."""
        return self._total_tokens

    @property
    def total_elapsed(self) -> float:
        """Cumulative wall-clock seconds spent inside complete() calls."""
        return self._total_elapsed
