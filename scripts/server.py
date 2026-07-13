"""MAS 前端审核台的开发服务器。

将项目根目录作为静态文件服务器（支持目录列表），并暴露
POST /api/corrections 端点以接收前端的人工批改事件，
将其写入 experiments/pending_corrections.json。

用法：
    python scripts/server.py          # 默认端口 8000
    python scripts/server.py --port 8080
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PENDING_PATH = _PROJECT_ROOT / "experiments" / "pending_corrections.json"

_REQUIRED_CORRECTION_FIELDS = {"sample_id"}
_ALLOWED_LIST_FIELDS = {"score_corrections", "feedback_corrections", "evidence_additions"}


class MASHandler(SimpleHTTPRequestHandler):
    """继承 SimpleHTTPRequestHandler，增加 /api/corrections POST 端点。"""

    def __init__(self, *args, **kwargs):
        # 服务项目根目录，使 artifacts/、data/、configs/、frontend/ 均可访问。
        super().__init__(*args, directory=str(_PROJECT_ROOT), **kwargs)

    # ------------------------------------------------------------------
    # CORS 辅助方法
    # ------------------------------------------------------------------

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    # 为每个响应（包括静态文件）注入 CORS 头。
    def end_headers(self) -> None:
        self._send_cors_headers()
        super().end_headers()

    # ------------------------------------------------------------------
    # POST 分发
    # ------------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/corrections":
            self._handle_corrections()
        else:
            self.send_error(404, "Not Found")

    # ------------------------------------------------------------------
    # /api/corrections
    # ------------------------------------------------------------------

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            return json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return None

    def _json_response(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_corrections(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            self._json_response(400, {"ok": False, "error": "Invalid JSON body"})
            return

        sample_id = str(payload.get("sample_id") or "").strip()
        if not sample_id:
            self._json_response(400, {"ok": False, "error": "Missing sample_id"})
            return

        score_corrections = payload.get("score_corrections") or []
        feedback_corrections = payload.get("feedback_corrections") or []
        evidence_additions = payload.get("evidence_additions") or []

        if not isinstance(score_corrections, list):
            score_corrections = []
        if not isinstance(feedback_corrections, list):
            feedback_corrections = []
        if not isinstance(evidence_additions, list):
            evidence_additions = []

        # 没有需要记录的内容时跳过。
        if not score_corrections and not feedback_corrections and not evidence_additions:
            self._json_response(200, {"ok": True, "sample_id": sample_id, "skipped": True})
            return

        # 读取已有的待处理文件（或从头开始）。
        if _PENDING_PATH.exists():
            try:
                existing = json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {"corrections": []}
        else:
            existing = {"corrections": []}

        corrections_list = existing.get("corrections") or []
        if not isinstance(corrections_list, list):
            corrections_list = []

        # 更新或插入：替换同一样本此前的记录。
        corrections_list = [c for c in corrections_list if c.get("sample_id") != sample_id]

        event = {
            "sample_id": sample_id,
            "timestamp": datetime.now().isoformat(),
            "score_corrections": score_corrections,
            "feedback_corrections": feedback_corrections,
            "evidence_additions": evidence_additions,
        }
        corrections_list.append(event)
        existing["corrections"] = corrections_list

        try:
            _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
            _PENDING_PATH.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            self._json_response(500, {"ok": False, "error": str(exc)})
            return

        n_total = len(score_corrections) + len(feedback_corrections) + len(evidence_additions)
        print(
            f"[corrections] {sample_id}: "
            f"{len(score_corrections)} score, "
            f"{len(feedback_corrections)} feedback, "
            f"{len(evidence_additions)} evidence → {_PENDING_PATH}"
        )
        self._json_response(200, {"ok": True, "sample_id": sample_id, "total_items": n_total})

    # 默认静默请求日志。
    def log_message(self, format: str, *args: object) -> None:
        pass


def main(port: int = 8000) -> None:
    server = HTTPServer(("", port), MASHandler)
    frontend_url = f"http://localhost:{port}/frontend/"
    print(f"MAS dev server running at http://localhost:{port}")
    print(f"Open the review workbench at: {frontend_url}")
    print(f"Corrections endpoint: POST http://localhost:{port}/api/corrections")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    port = 8000
    args = sys.argv[1:]
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            try:
                port = int(args[idx + 1])
            except ValueError:
                print(f"Invalid port: {args[idx + 1]}", file=sys.stderr)
                sys.exit(1)
    main(port)
