"""调试包写入器，负责为单次运行输出可回放、可视化的调试产物。

调试包写入器，用于开发时的 pipeline 检查。

为单次评估运行写入一个自包含的调试包：
- events.jsonl        : 有序的执行和 LLM-call 事件
- node_artifacts/     : 每个节点的输入/输出快照
- llm_calls/          : 每次调用的元数据以及 prompt/response blobs
- summary.json        : 适合查看器的聚合索引
- viewer/index.html   : 静态 HTML 检查器

该调试包仅供本地调试使用。它不在正常的生产执行路径中使用。"""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview_text(value: str, limit: int = 160) -> str:
    compact = value.replace("\r", " ").replace("\n", " ").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


class DebugBundleWriter:
    """为一个 pipeline 运行持久化仅供开发的调试包。"""

    def __init__(
        self,
        output_root: Path | str,
        session_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._base_dir = Path(output_root)
        self._session_metadata = dict(session_metadata or {})
        self._lock = threading.RLock()
        self._run_id: Optional[str] = None
        self._root_dir: Optional[Path] = None
        self._events_path: Optional[Path] = None
        self._seq = 0
        self._call_seq = 0
        self._events: List[Dict[str, Any]] = []
        self._primary_artifacts: List[Dict[str, Any]] = []
        self._node_index: Dict[str, Dict[str, Any]] = {}
        self._llm_calls: Dict[str, Dict[str, Any]] = {}
        self._manifest: Dict[str, Any] = {
            "schema_version": 1,
            "created_at": _now_iso(),
            "session_metadata": dict(self._session_metadata),
            "note": (
                "Serve this directory with `python -m http.server` before opening "
                "`viewer/index.html` so the viewer can fetch bundle files."
            ),
        }

    @property
    def run_id(self) -> Optional[str]:
        return self._run_id

    @property
    def output_dir(self) -> Optional[Path]:
        return self._root_dir

    def start_run(
        self,
        *,
        run_id: str,
        request: Dict[str, Any],
        bundle_id: str,
        bundle_version: str,
        provider_mode: str,
        provider_bindings: Optional[List[Dict[str, Any]]] = None,
    ) -> Path:
        with self._lock:
            if self._run_id is not None:
                raise RuntimeError("DebugBundleWriter.start_run() called more than once")

            self._run_id = run_id
            self._root_dir = self._base_dir / run_id
            self._root_dir.mkdir(parents=True, exist_ok=True)
            (self._root_dir / "node_artifacts").mkdir(exist_ok=True)
            (self._root_dir / "llm_calls" / "blobs").mkdir(parents=True, exist_ok=True)
            (self._root_dir / "viewer").mkdir(exist_ok=True)
            self._events_path = self._root_dir / "events.jsonl"

            self._manifest.update(
                {
                    "run_id": run_id,
                    "bundle_id": bundle_id,
                    "bundle_version": bundle_version,
                    "provider_mode": provider_mode,
                    "provider_bindings": list(provider_bindings or []),
                    "paths": {
                        "request": "request.json",
                        "events": "events.jsonl",
                        "summary": "summary.json",
                        "viewer": "viewer/index.html",
                    },
                }
            )
            self._write_json_unlocked("request.json", request)
            self.emit_event(
                "run_started",
                request_id=request.get("request_id"),
                bundle_id=bundle_id,
                bundle_version=bundle_version,
                provider_mode=provider_mode,
                essay_id=(request.get("metadata") or {}).get("essay_id"),
            )
            return self._root_dir

    def emit_event(self, event_type: str, **payload: Any) -> Dict[str, Any]:
        with self._lock:
            self._ensure_started()
            self._seq += 1
            event = {
                "event_id": f"evt-{self._seq:05d}",
                "seq": self._seq,
                "ts": _now_iso(),
                "run_id": self._run_id,
                "event_type": event_type,
            }
            event.update(payload)
            self._events.append(event)
            assert self._events_path is not None
            with self._events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            return event

    def node_started(
        self,
        *,
        node_id: str,
        node_type: str,
        input_ref: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            node_entry = self._node_index.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "node_type": node_type,
                    "artifacts": [],
                    "routes": [],
                    "fallbacks": [],
                    "llm_call_ids": [],
                },
            )
            node_entry["node_type"] = node_type
            node_entry["input_ref"] = input_ref
            node_entry["started_at"] = _now_iso()
            if metadata:
                node_entry["start_metadata"] = dict(metadata)
        self.emit_event(
            "node_started",
            node_id=node_id,
            node_type=node_type,
            input_ref=input_ref,
            metadata=dict(metadata or {}),
        )

    def node_finished(
        self,
        *,
        node_id: str,
        status: str,
        output_ref: Optional[str],
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            node_entry = self._node_index.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "artifacts": [],
                    "routes": [],
                    "fallbacks": [],
                    "llm_call_ids": [],
                },
            )
            node_entry["status"] = status
            node_entry["output_ref"] = output_ref
            node_entry["finished_at"] = _now_iso()
            node_entry["error_message"] = error_message
            if metadata:
                node_entry["finish_metadata"] = dict(metadata)
        self.emit_event(
            "node_finished",
            node_id=node_id,
            status=status,
            output_ref=output_ref,
            error_message=error_message,
            metadata=dict(metadata or {}),
        )

    def record_route_decision(
        self,
        *,
        router_name: str,
        from_state: str,
        to_state: str,
        node_id: Optional[str] = None,
        rationale: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        route_payload = {
            "router_name": router_name,
            "from_state": from_state,
            "to_state": to_state,
            "rationale": rationale,
            "metadata": dict(metadata or {}),
        }
        with self._lock:
            if node_id:
                node_entry = self._node_index.setdefault(
                    node_id,
                    {
                        "node_id": node_id,
                        "artifacts": [],
                        "routes": [],
                        "fallbacks": [],
                        "llm_call_ids": [],
                    },
                )
                node_entry["routes"].append(route_payload)
        self.emit_event(
            "route_decision",
            node_id=node_id,
            router_name=router_name,
            from_state=from_state,
            to_state=to_state,
            rationale=rationale,
            metadata=dict(metadata or {}),
        )

    def record_fallback(
        self,
        *,
        node_id: Optional[str],
        fallback_label: str,
        detail: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            if node_id:
                node_entry = self._node_index.setdefault(
                    node_id,
                    {
                        "node_id": node_id,
                        "artifacts": [],
                        "routes": [],
                        "fallbacks": [],
                        "llm_call_ids": [],
                    },
                )
                node_entry["fallbacks"].append(
                    {
                        "fallback_label": fallback_label,
                        "detail": detail,
                        "metadata": dict(metadata or {}),
                    }
                )
        self.emit_event(
            "fallback_triggered",
            node_id=node_id,
            fallback_label=fallback_label,
            detail=detail,
            metadata=dict(metadata or {}),
        )

    def write_primary_artifact(
        self,
        *,
        artifact_name: str,
        data: Any,
        summary: Optional[str] = None,
    ) -> str:
        rel_path = f"{artifact_name}.json"
        self.write_json(rel_path, data)
        record = {
            "artifact_name": artifact_name,
            "path": rel_path,
            "summary": summary,
        }
        with self._lock:
            self._primary_artifacts.append(record)
        self.emit_event(
            "artifact_emitted",
            artifact_scope="primary",
            artifact_name=artifact_name,
            artifact_path=rel_path,
            artifact_format="json",
            summary=summary,
        )
        return rel_path

    def write_node_artifact(
        self,
        *,
        node_id: str,
        artifact_name: str,
        data: Any,
        summary: Optional[str] = None,
    ) -> str:
        rel_path = f"node_artifacts/{node_id}/{artifact_name}.json"
        self.write_json(rel_path, data)
        artifact_record = {
            "artifact_name": artifact_name,
            "path": rel_path,
            "format": "json",
            "summary": summary,
        }
        with self._lock:
            node_entry = self._node_index.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "artifacts": [],
                    "routes": [],
                    "fallbacks": [],
                    "llm_call_ids": [],
                },
            )
            node_entry["artifacts"].append(artifact_record)
        self.emit_event(
            "artifact_emitted",
            artifact_scope="node",
            node_id=node_id,
            artifact_name=artifact_name,
            artifact_path=rel_path,
            artifact_format="json",
            summary=summary,
        )
        return rel_path

    def write_json(self, rel_path: str, data: Any) -> str:
        with self._lock:
            self._ensure_started()
            return self._write_json_unlocked(rel_path, data)

    def write_text(self, rel_path: str, text: str) -> str:
        with self._lock:
            self._ensure_started()
            return self._write_text_unlocked(rel_path, text)

    def record_llm_call_started(
        self,
        *,
        label: str,
        provider_name: str,
        model_id: str,
        request: Any,
    ) -> str:
        with self._lock:
            self._ensure_started()
            self._call_seq += 1
            call_id = f"call-{self._call_seq:04d}"
            req_meta = dict(getattr(request, "metadata", {}) or {})
            prompt_path = self._write_text_unlocked(
                f"llm_calls/blobs/{call_id}.prompt.txt",
                request.prompt,
            )
            system_path = None
            if getattr(request, "system", None):
                system_path = self._write_text_unlocked(
                    f"llm_calls/blobs/{call_id}.system.txt",
                    request.system or "",
                )
            schema_path = None
            if getattr(request, "output_schema", None) is not None:
                schema_path = self._write_json_unlocked(
                    f"llm_calls/blobs/{call_id}.schema.json",
                    request.output_schema,
                )

            call_record = {
                "call_id": call_id,
                "status": "started",
                "label": label,
                "provider_name": provider_name,
                "model_id": model_id,
                "started_at": _now_iso(),
                "finished_at": None,
                "elapsed_ms": None,
                "request": {
                    "system_path": system_path,
                    "prompt_path": prompt_path,
                    "output_schema_path": schema_path,
                    "params": dict(getattr(request, "params", {}) or {}),
                    "prompt_chars": len(request.prompt),
                    "has_output_schema": getattr(request, "output_schema", None) is not None,
                },
                "response": {
                    "content_path": None,
                    "structured_path": None,
                    "usage": None,
                    "preview": None,
                },
                "debug_context": req_meta,
                "call_path": f"llm_calls/{call_id}.json",
                "error": None,
            }
            self._llm_calls[call_id] = call_record
            node_id = req_meta.get("node_id")
            if node_id:
                node_entry = self._node_index.setdefault(
                    node_id,
                    {
                        "node_id": node_id,
                        "artifacts": [],
                        "routes": [],
                        "fallbacks": [],
                        "llm_call_ids": [],
                    },
                )
                node_entry["llm_call_ids"].append(call_id)

            self._write_json_unlocked(call_record["call_path"], call_record)
            self.emit_event(
                "llm_call_started",
                call_id=call_id,
                label=label,
                provider_name=provider_name,
                model_id=model_id,
                node_id=req_meta.get("node_id"),
                stage_name=req_meta.get("stage_name"),
                dimension_id=req_meta.get("dimension_id"),
                rater_id=req_meta.get("rater_id"),
                template_source=req_meta.get("template_source"),
                prompt_chars=len(request.prompt),
                has_output_schema=getattr(request, "output_schema", None) is not None,
            )
            return call_id

    def record_llm_call_finished(
        self,
        *,
        call_id: str,
        response: Any,
        elapsed_ms: float,
    ) -> None:
        with self._lock:
            call_record = self._llm_calls[call_id]
            content_path = self._write_text_unlocked(
                f"llm_calls/blobs/{call_id}.response.txt",
                response.content,
            )
            structured_path = None
            if getattr(response, "structured_data", None) is not None:
                structured_path = self._write_json_unlocked(
                    f"llm_calls/blobs/{call_id}.structured.json",
                    response.structured_data,
                )
            call_record["status"] = "succeeded"
            call_record["finished_at"] = _now_iso()
            call_record["elapsed_ms"] = round(elapsed_ms, 2)
            call_record["response"] = {
                "content_path": content_path,
                "structured_path": structured_path,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "preview": _preview_text(response.content),
            }
            self._write_json_unlocked(call_record["call_path"], call_record)
            ctx = call_record.get("debug_context") or {}
            self.emit_event(
                "llm_call_finished",
                call_id=call_id,
                label=call_record.get("label"),
                provider_name=call_record.get("provider_name"),
                model_id=call_record.get("model_id"),
                node_id=ctx.get("node_id"),
                stage_name=ctx.get("stage_name"),
                dimension_id=ctx.get("dimension_id"),
                rater_id=ctx.get("rater_id"),
                elapsed_ms=round(elapsed_ms, 2),
                total_tokens=response.usage.total_tokens,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                preview=_preview_text(response.content),
            )

    def record_llm_call_error(
        self,
        *,
        call_id: str,
        error: Exception,
        elapsed_ms: float,
    ) -> None:
        with self._lock:
            call_record = self._llm_calls[call_id]
            call_record["status"] = "failed"
            call_record["finished_at"] = _now_iso()
            call_record["elapsed_ms"] = round(elapsed_ms, 2)
            call_record["error"] = {
                "type": error.__class__.__name__,
                "message": str(error),
            }
            self._write_json_unlocked(call_record["call_path"], call_record)
            ctx = call_record.get("debug_context") or {}
            self.emit_event(
                "llm_call_finished",
                call_id=call_id,
                label=call_record.get("label"),
                provider_name=call_record.get("provider_name"),
                model_id=call_record.get("model_id"),
                node_id=ctx.get("node_id"),
                stage_name=ctx.get("stage_name"),
                dimension_id=ctx.get("dimension_id"),
                rater_id=ctx.get("rater_id"),
                elapsed_ms=round(elapsed_ms, 2),
                error_type=error.__class__.__name__,
                error_message=str(error),
            )

    def finalize(self) -> Path:
        with self._lock:
            self._ensure_started()
            summary = self._build_summary_unlocked()
            self._write_json_unlocked("summary.json", summary)
            self._write_text_unlocked("viewer/index.html", _build_viewer_html())
            self._manifest["completed_at"] = _now_iso()
            self._manifest["primary_artifacts"] = list(self._primary_artifacts)
            self._write_json_unlocked("manifest.json", self._manifest)
            assert self._root_dir is not None
            return self._root_dir

    def _build_summary_unlocked(self) -> Dict[str, Any]:
        event_counts = Counter(event["event_type"] for event in self._events)

        nodes: List[Dict[str, Any]] = []
        for node_id, node_entry in self._node_index.items():
            llm_call_ids = list(node_entry.get("llm_call_ids") or [])
            llm_calls = [self._llm_calls[cid] for cid in llm_call_ids if cid in self._llm_calls]
            total_tokens = sum(
                (
                    (
                        (call.get("response") or {}).get("usage")
                        or {}
                    ).get("total_tokens", 0)
                    or 0
                )
                for call in llm_calls
            )
            total_elapsed_ms = sum(
                float(call.get("elapsed_ms") or 0.0)
                for call in llm_calls
            )
            nodes.append(
                {
                    "node_id": node_id,
                    "node_type": node_entry.get("node_type"),
                    "status": node_entry.get("status"),
                    "input_ref": node_entry.get("input_ref"),
                    "output_ref": node_entry.get("output_ref"),
                    "error_message": node_entry.get("error_message"),
                    "started_at": node_entry.get("started_at"),
                    "finished_at": node_entry.get("finished_at"),
                    "artifacts": list(node_entry.get("artifacts") or []),
                    "routes": list(node_entry.get("routes") or []),
                    "fallbacks": list(node_entry.get("fallbacks") or []),
                    "llm_call_ids": llm_call_ids,
                    "llm_call_count": len(llm_call_ids),
                    "llm_total_tokens": total_tokens,
                    "llm_total_elapsed_ms": round(total_elapsed_ms, 2),
                }
            )

        nodes.sort(key=lambda item: item.get("started_at") or "")

        llm_calls: List[Dict[str, Any]] = []
        total_tokens = 0
        total_elapsed_ms = 0.0
        for call_id in sorted(self._llm_calls):
            call = self._llm_calls[call_id]
            usage = (call.get("response") or {}).get("usage") or {}
            tokens = int(usage.get("total_tokens") or 0)
            elapsed_ms = float(call.get("elapsed_ms") or 0.0)
            total_tokens += tokens
            total_elapsed_ms += elapsed_ms
            ctx = call.get("debug_context") or {}
            llm_calls.append(
                {
                    "call_id": call_id,
                    "status": call.get("status"),
                    "label": call.get("label"),
                    "provider_name": call.get("provider_name"),
                    "model_id": call.get("model_id"),
                    "node_id": ctx.get("node_id"),
                    "stage_name": ctx.get("stage_name"),
                    "dimension_id": ctx.get("dimension_id"),
                    "rater_id": ctx.get("rater_id"),
                    "template_source": ctx.get("template_source"),
                    "elapsed_ms": call.get("elapsed_ms"),
                    "total_tokens": tokens,
                    "call_path": call.get("call_path"),
                    "preview": (call.get("response") or {}).get("preview"),
                }
            )

        return {
            "run_id": self._run_id,
            "bundle_id": self._manifest.get("bundle_id"),
            "bundle_version": self._manifest.get("bundle_version"),
            "provider_mode": self._manifest.get("provider_mode"),
            "session_metadata": dict(self._session_metadata),
            "event_counts": dict(event_counts),
            "node_count": len(nodes),
            "llm_call_count": len(llm_calls),
            "llm_total_tokens": total_tokens,
            "llm_total_elapsed_ms": round(total_elapsed_ms, 2),
            "nodes": nodes,
            "llm_calls": llm_calls,
            "primary_artifacts": list(self._primary_artifacts),
        }

    def _write_json_unlocked(self, rel_path: str, data: Any) -> str:
        assert self._root_dir is not None
        path = self._root_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return rel_path

    def _write_text_unlocked(self, rel_path: str, text: str) -> str:
        assert self._root_dir is not None
        path = self._root_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return rel_path

    def _ensure_started(self) -> None:
        if self._root_dir is None or self._run_id is None:
            raise RuntimeError("DebugBundleWriter.start_run() must be called first")


def _build_viewer_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MAS Debug Bundle Viewer</title>
  <style>
    :root {
      --bg: #f3efe7;
      --panel: #fffdf8;
      --line: #d9d0c4;
      --text: #1f1b18;
      --muted: #6f6256;
      --accent: #b54d2f;
      --accent-soft: #f4d4c9;
      --ok: #23613a;
      --warn: #9b6400;
      --bad: #9a2d2d;
      --shadow: 0 10px 30px rgba(31, 27, 24, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(181, 77, 47, 0.12), transparent 28%),
        linear-gradient(180deg, #f8f4ed 0%, var(--bg) 100%);
      color: var(--text);
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
    }
    .page {
      width: min(1480px, calc(100vw - 32px));
      margin: 24px auto 40px;
      display: grid;
      gap: 16px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      box-shadow: var(--shadow);
    }
    .hero {
      padding: 22px 24px;
    }
    .hero h1 {
      margin: 0;
      font-size: 30px;
      letter-spacing: 0.02em;
    }
    .hero p {
      margin: 10px 0 0;
      color: var(--muted);
      line-height: 1.5;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      padding: 0 24px 24px;
    }
    .stat {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px 16px;
      background: rgba(255, 255, 255, 0.72);
    }
    .stat .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .stat .value {
      margin-top: 8px;
      font-size: 26px;
      font-weight: 700;
    }
    .grid {
      display: grid;
      grid-template-columns: 380px 1fr;
      gap: 16px;
    }
    .sidebar, .detail {
      min-height: 720px;
      overflow: hidden;
    }
    .section {
      padding: 18px 20px;
      border-bottom: 1px solid var(--line);
    }
    .section:last-child {
      border-bottom: none;
    }
    .section h2, .section h3 {
      margin: 0 0 12px;
      font-size: 18px;
    }
    .flow {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    .flow-node {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 10px 14px;
      background: #fff;
      font-size: 14px;
      white-space: nowrap;
    }
    .flow-arrow {
      color: var(--muted);
      font-size: 18px;
    }
    .item-list {
      display: grid;
      gap: 10px;
      max-height: 420px;
      overflow: auto;
      padding-right: 4px;
    }
    .item {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px 14px;
      background: #fff;
      cursor: pointer;
      transition: transform 120ms ease, border-color 120ms ease, background 120ms ease;
    }
    .item:hover {
      transform: translateY(-1px);
      border-color: var(--accent);
      background: #fffaf7;
    }
    .item .title {
      font-weight: 700;
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }
    .item .meta {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      border: 1px solid var(--line);
      background: #f7f3eb;
    }
    .badge.ok { color: var(--ok); border-color: rgba(35, 97, 58, 0.2); background: rgba(35, 97, 58, 0.08); }
    .badge.warn { color: var(--warn); border-color: rgba(155, 100, 0, 0.2); background: rgba(155, 100, 0, 0.09); }
    .badge.bad { color: var(--bad); border-color: rgba(154, 45, 45, 0.2); background: rgba(154, 45, 45, 0.08); }
    .detail {
      display: grid;
      grid-template-rows: auto 1fr;
    }
    .detail-header {
      padding: 18px 20px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
    }
    .detail-body {
      padding: 18px 20px;
      overflow: auto;
      display: grid;
      gap: 14px;
      align-content: start;
    }
    .kv {
      display: grid;
      grid-template-columns: 180px 1fr;
      gap: 8px 14px;
      align-items: start;
      font-size: 14px;
    }
    .kv .k { color: var(--muted); }
    .subpanel {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #fff;
      overflow: hidden;
    }
    .subpanel h4 {
      margin: 0;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      font-size: 15px;
      background: #faf5ed;
    }
    .subpanel .content {
      padding: 12px 14px;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
      font-size: 12px;
      line-height: 1.5;
    }
    button.link {
      appearance: none;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 999px;
      padding: 8px 12px;
      cursor: pointer;
      font: inherit;
    }
    button.link:hover {
      border-color: var(--accent);
      color: var(--accent);
    }
    .artifact-list, .button-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .hint {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }
    .error {
      color: var(--bad);
      font-weight: 700;
    }
    @media (max-width: 1100px) {
      .grid { grid-template-columns: 1fr; }
      .sidebar, .detail { min-height: auto; }
      .kv { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="page">
    <section class="panel hero">
      <h1>MAS Debug Bundle Viewer</h1>
      <p id="hero-text">正在加载调试包…</p>
    </section>
    <section class="panel stats" id="stats"></section>
    <section class="panel section">
      <h2>Execution Flow</h2>
      <div class="flow" id="flow"></div>
    </section>
    <section class="grid">
      <aside class="panel sidebar">
        <div class="section">
          <h2>Nodes</h2>
          <div class="item-list" id="node-list"></div>
        </div>
        <div class="section">
          <h2>LLM Calls</h2>
          <div class="item-list" id="call-list"></div>
        </div>
      </aside>
      <section class="panel detail">
        <div class="detail-header">
          <div>
            <h2 style="margin:0" id="detail-title">Bundle Summary</h2>
            <div class="hint" id="detail-subtitle">点击左侧节点或 LLM 调用查看详情。</div>
          </div>
        </div>
        <div class="detail-body" id="detail-body"></div>
      </section>
    </section>
  </div>
  <script>
    const base = "..";
    const state = { manifest: null, summary: null, events: [] };

    async function loadJson(path) {
      const res = await fetch(path);
      if (!res.ok) throw new Error(`Failed to fetch ${path}: ${res.status}`);
      return await res.json();
    }

    async function loadText(path) {
      const res = await fetch(path);
      if (!res.ok) throw new Error(`Failed to fetch ${path}: ${res.status}`);
      return await res.text();
    }

    async function loadJsonl(path) {
      const text = await loadText(path);
      return text
        .split(/\\n+/)
        .map(line => line.trim())
        .filter(Boolean)
        .map(line => JSON.parse(line));
    }

    function statusBadge(status) {
      const cls = status === "succeeded" || status === "success"
        ? "ok"
        : status === "failed"
          ? "bad"
          : "warn";
      return `<span class="badge ${cls}">${status || "unknown"}</span>`;
    }

    function renderStats(summary) {
      const cards = [
        ["Run ID", summary.run_id || "N/A"],
        ["Provider Mode", summary.provider_mode || "N/A"],
        ["Nodes", String(summary.node_count || 0)],
        ["LLM Calls", String(summary.llm_call_count || 0)],
        ["LLM Tokens", String(summary.llm_total_tokens || 0)],
        ["LLM Time (ms)", String(summary.llm_total_elapsed_ms || 0)],
      ];
      document.getElementById("stats").innerHTML = cards.map(([label, value]) => `
        <div class="stat">
          <div class="label">${label}</div>
          <div class="value">${value}</div>
        </div>
      `).join("");
    }

    function renderFlow(nodes) {
      const el = document.getElementById("flow");
      if (!nodes.length) {
        el.innerHTML = '<div class="hint">没有节点数据。</div>';
        return;
      }
      const parts = [];
      nodes.forEach((node, idx) => {
        parts.push(`<div class="flow-node">${node.node_id}</div>`);
        if (idx < nodes.length - 1) {
          parts.push('<div class="flow-arrow">→</div>');
        }
      });
      el.innerHTML = parts.join("");
    }

    function renderNodeList(nodes) {
      const el = document.getElementById("node-list");
      el.innerHTML = nodes.map(node => `
        <div class="item" data-node-id="${node.node_id}">
          <div class="title">
            <span>${node.node_id}</span>
            ${statusBadge(node.status)}
          </div>
          <div class="meta">
            <div>Artifacts: ${node.artifacts.length}</div>
            <div>LLM Calls: ${node.llm_call_count}</div>
            <div>Output: ${node.output_ref || "N/A"}</div>
          </div>
        </div>
      `).join("");
      [...el.querySelectorAll("[data-node-id]")].forEach(item => {
        item.addEventListener("click", () => showNode(item.dataset.nodeId));
      });
    }

    function renderCallList(calls) {
      const el = document.getElementById("call-list");
      el.innerHTML = calls.map(call => `
        <div class="item" data-call-id="${call.call_id}">
          <div class="title">
            <span>${call.call_id}</span>
            ${statusBadge(call.status)}
          </div>
          <div class="meta">
            <div>${call.label || "llm"} · ${call.model_id || "?"}</div>
            <div>${call.node_id || "N/A"} · ${call.dimension_id || "all"}</div>
            <div>${call.total_tokens || 0} tok · ${call.elapsed_ms || 0} ms</div>
          </div>
        </div>
      `).join("");
      [...el.querySelectorAll("[data-call-id]")].forEach(item => {
        item.addEventListener("click", () => showCall(item.dataset.callId));
      });
    }

    function renderSummaryDetail() {
      const body = document.getElementById("detail-body");
      const manifest = state.manifest;
      const summary = state.summary;
      const eventRows = Object.entries(summary.event_counts || {}).map(([k, v]) => `<div>${k}: ${v}</div>`).join("");
      body.innerHTML = `
        <div class="subpanel">
          <h4>Bundle Metadata</h4>
          <div class="content kv">
            <div class="k">Run ID</div><div>${summary.run_id || "N/A"}</div>
            <div class="k">Bundle</div><div>${summary.bundle_id || "N/A"}@${summary.bundle_version || "N/A"}</div>
            <div class="k">Provider Mode</div><div>${summary.provider_mode || "N/A"}</div>
            <div class="k">Session Metadata</div><div><pre>${JSON.stringify(summary.session_metadata || {}, null, 2)}</pre></div>
          </div>
        </div>
        <div class="subpanel">
          <h4>Event Counts</h4>
          <div class="content">${eventRows || '<div class="hint">无事件。</div>'}</div>
        </div>
        <div class="subpanel">
          <h4>Primary Artifacts</h4>
          <div class="content artifact-list">
            ${(manifest.primary_artifacts || []).map(item =>
              `<button class="link" data-artifact-path="${item.path}" data-artifact-title="${item.artifact_name}">${item.artifact_name}</button>`
            ).join("") || '<div class="hint">无主产物。</div>'}
          </div>
        </div>
      `;
      [...body.querySelectorAll("[data-artifact-path]")].forEach(btn => {
        btn.addEventListener("click", () => showArtifact(btn.dataset.artifactTitle, btn.dataset.artifactPath));
      });
    }

    async function showArtifact(title, relPath) {
      document.getElementById("detail-title").textContent = title;
      document.getElementById("detail-subtitle").textContent = relPath;
      const body = document.getElementById("detail-body");
      try {
        const text = await loadText(`${base}/${relPath}`);
        let rendered = text;
        try {
          rendered = JSON.stringify(JSON.parse(text), null, 2);
        } catch (_) {}
        body.innerHTML = `
          <div class="subpanel">
            <h4>${title}</h4>
            <div class="content"><pre>${escapeHtml(rendered)}</pre></div>
          </div>
        `;
      } catch (err) {
        body.innerHTML = `<div class="error">${escapeHtml(String(err))}</div>`;
      }
    }

    function showNode(nodeId) {
      const node = state.summary.nodes.find(item => item.node_id === nodeId);
      if (!node) return;
      document.getElementById("detail-title").textContent = node.node_id;
      document.getElementById("detail-subtitle").textContent = `${node.node_type || "node"} · ${node.status || "unknown"}`;
      const body = document.getElementById("detail-body");
      body.innerHTML = `
        <div class="subpanel">
          <h4>Node Summary</h4>
          <div class="content kv">
            <div class="k">Type</div><div>${node.node_type || "N/A"}</div>
            <div class="k">Status</div><div>${statusBadge(node.status)}</div>
            <div class="k">Input Ref</div><div>${node.input_ref || "N/A"}</div>
            <div class="k">Output Ref</div><div>${node.output_ref || "N/A"}</div>
            <div class="k">LLM Calls</div><div>${node.llm_call_count || 0}</div>
            <div class="k">LLM Tokens</div><div>${node.llm_total_tokens || 0}</div>
            <div class="k">Started</div><div>${node.started_at || "N/A"}</div>
            <div class="k">Finished</div><div>${node.finished_at || "N/A"}</div>
            <div class="k">Error</div><div>${node.error_message || "N/A"}</div>
          </div>
        </div>
        <div class="subpanel">
          <h4>Artifacts</h4>
          <div class="content artifact-list">
            ${node.artifacts.map(item =>
              `<button class="link" data-artifact-path="${item.path}" data-artifact-title="${item.artifact_name}">${item.artifact_name}</button>`
            ).join("") || '<div class="hint">该节点没有落盘产物。</div>'}
          </div>
        </div>
        <div class="subpanel">
          <h4>Route Decisions</h4>
          <div class="content"><pre>${escapeHtml(JSON.stringify(node.routes || [], null, 2))}</pre></div>
        </div>
        <div class="subpanel">
          <h4>Fallbacks</h4>
          <div class="content"><pre>${escapeHtml(JSON.stringify(node.fallbacks || [], null, 2))}</pre></div>
        </div>
      `;
      [...body.querySelectorAll("[data-artifact-path]")].forEach(btn => {
        btn.addEventListener("click", () => showArtifact(btn.dataset.artifactTitle, btn.dataset.artifactPath));
      });
    }

    async function showCall(callId) {
      const call = state.summary.llm_calls.find(item => item.call_id === callId);
      if (!call) return;
      document.getElementById("detail-title").textContent = call.call_id;
      document.getElementById("detail-subtitle").textContent = `${call.label || "llm"} · ${call.model_id || "?"}`;
      const body = document.getElementById("detail-body");
      try {
        const full = await loadJson(`${base}/${call.call_path}`);
        const request = full.request || {};
        const response = full.response || {};
        body.innerHTML = `
          <div class="subpanel">
            <h4>Call Summary</h4>
            <div class="content kv">
              <div class="k">Status</div><div>${statusBadge(full.status)}</div>
              <div class="k">Provider</div><div>${full.provider_name || "N/A"}</div>
              <div class="k">Model</div><div>${full.model_id || "N/A"}</div>
              <div class="k">Node</div><div>${(full.debug_context || {}).node_id || "N/A"}</div>
              <div class="k">Stage</div><div>${(full.debug_context || {}).stage_name || "N/A"}</div>
              <div class="k">Dimension</div><div>${(full.debug_context || {}).dimension_id || "N/A"}</div>
              <div class="k">Rater</div><div>${(full.debug_context || {}).rater_id || "N/A"}</div>
              <div class="k">Elapsed</div><div>${full.elapsed_ms || 0} ms</div>
              <div class="k">Tokens</div><div>${(response.usage || {}).total_tokens || 0}</div>
            </div>
          </div>
          <div class="subpanel">
            <h4>Debug Context</h4>
            <div class="content"><pre>${escapeHtml(JSON.stringify(full.debug_context || {}, null, 2))}</pre></div>
          </div>
          <div class="subpanel">
            <h4>Artifacts</h4>
            <div class="content button-row">
              ${request.prompt_path ? `<button class="link" data-open-text="${request.prompt_path}" data-title="Prompt">Prompt</button>` : ""}
              ${request.system_path ? `<button class="link" data-open-text="${request.system_path}" data-title="System">System</button>` : ""}
              ${request.output_schema_path ? `<button class="link" data-open-json="${request.output_schema_path}" data-title="Output Schema">Output Schema</button>` : ""}
              ${response.content_path ? `<button class="link" data-open-text="${response.content_path}" data-title="Response">Response</button>` : ""}
              ${response.structured_path ? `<button class="link" data-open-json="${response.structured_path}" data-title="Structured Output">Structured Output</button>` : ""}
            </div>
          </div>
          <div class="subpanel">
            <h4>Raw Call Record</h4>
            <div class="content"><pre>${escapeHtml(JSON.stringify(full, null, 2))}</pre></div>
          </div>
        `;
        [...body.querySelectorAll("[data-open-text]")].forEach(btn => {
          btn.addEventListener("click", () => showArtifact(btn.dataset.title, btn.dataset.openText));
        });
        [...body.querySelectorAll("[data-open-json]")].forEach(btn => {
          btn.addEventListener("click", () => showArtifact(btn.dataset.title, btn.dataset.openJson));
        });
      } catch (err) {
        body.innerHTML = `<div class="error">${escapeHtml(String(err))}</div>`;
      }
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
    }

    async function init() {
      try {
        const [manifest, summary, events] = await Promise.all([
          loadJson(`${base}/manifest.json`),
          loadJson(`${base}/summary.json`),
          loadJsonl(`${base}/events.jsonl`),
        ]);
        state.manifest = manifest;
        state.summary = summary;
        state.events = events;
        document.getElementById("hero-text").textContent =
          `${summary.run_id} · ${summary.bundle_id}@${summary.bundle_version} · ` +
          `${summary.llm_call_count} 次 LLM 调用 · ${summary.node_count} 个节点`;
        renderStats(summary);
        renderFlow(summary.nodes || []);
        renderNodeList(summary.nodes || []);
        renderCallList(summary.llm_calls || []);
        renderSummaryDetail();
      } catch (err) {
        document.getElementById("hero-text").innerHTML =
          `<span class="error">加载失败：</span> ${escapeHtml(String(err))}`;
        document.getElementById("detail-title").textContent = "Viewer Load Error";
        document.getElementById("detail-body").innerHTML = `
          <div class="subpanel">
            <h4>What to do next</h4>
            <div class="content hint">
              1. 先确认你打开的是调试包目录下的 <code>viewer/index.html</code><br/>
              2. 再用调试包目录执行 <code>python -m http.server 8000</code><br/>
              3. 最后访问 <code>http://localhost:8000/viewer/</code>
            </div>
          </div>
        `;
      }
    }

    init();
  </script>
</body>
</html>
"""
