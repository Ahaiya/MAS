"""
MAS 前端 API 服务 — 纯只读，不触发 LLM，不修改 src/ 任何逻辑。
提供 6 个 GET 接口，供前端 React SPA 调用。

启动方式：
    uvicorn api.server:app --reload --port 8000
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ── 路径常量 ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent  # MAS/
ARTIFACTS_DIR = ROOT / "artifacts" / "eval"
TSV_PATH = ROOT / "data" / "training_set_8.tsv"

# Trait 列到 dimension_id 的固定映射（来自量规配置）
TRAIT_TO_DIM = {
    "rater1_trait1": "ideas_content",
    "rater1_trait2": "organization",
    "rater1_trait3": "voice",
    "rater1_trait4": "word_choice",
    "rater1_trait5": "sentence_fluency",
    "rater1_trait6": "conventions",
    "rater2_trait1": "ideas_content",
    "rater2_trait2": "organization",
    "rater2_trait3": "voice",
    "rater2_trait4": "word_choice",
    "rater2_trait5": "sentence_fluency",
    "rater2_trait6": "conventions",
}

DIM_ORDER = [
    "ideas_content",
    "organization",
    "voice",
    "word_choice",
    "sentence_fluency",
    "conventions",
]

# ── 应用初始化 ──────────────────────────────────────────────────────────────────

app = FastAPI(title="MAS Frontend API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── 工具函数 ────────────────────────────────────────────────────────────────────


def _load_tsv() -> list[dict]:
    """加载 training_set_8.tsv，返回行列表（dict 形式）。"""
    if not TSV_PATH.exists():
        raise HTTPException(status_code=500, detail=f"TSV 文件不存在: {TSV_PATH}")
    rows = []
    with TSV_PATH.open(encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            rows.append(dict(row))
    return rows


def _find_tsv_row(essay_id: str) -> dict:
    """从 TSV 中按 essay_id 查找对应行，找不到则 404。"""
    rows = _load_tsv()
    for row in rows:
        if str(row.get("essay_id", "")).strip() == essay_id.strip():
            return row
    raise HTTPException(status_code=404, detail=f"essay_id={essay_id} 不在 TSV 中")


def _read_json(path: Path) -> dict:
    """读取 JSON 文件，找不到则 404。"""
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ── 接口 ────────────────────────────────────────────────────────────────────────


@app.get("/api/essays")
def list_essays() -> dict:
    """返回所有已完成评估的 essay_id 列表。"""
    if not ARTIFACTS_DIR.exists():
        return {"essay_ids": []}
    ids = sorted(
        d.name
        for d in ARTIFACTS_DIR.iterdir()
        if d.is_dir()
        and (d / "feedback.json").exists()
        and (d / "run_trace.json").exists()
    )
    return {"essay_ids": ids}


@app.get("/api/essays/{essay_id}/text")
def get_essay_text(essay_id: str) -> dict:
    """从 TSV 的 essay 列提取指定 essay 的原始作文文本。"""
    row = _find_tsv_row(essay_id)
    text = row.get("essay", "").strip()
    if not text:
        raise HTTPException(status_code=404, detail=f"essay_id={essay_id} 的 essay 列为空")
    return {"essay_id": essay_id, "text": text}


@app.get("/api/essays/{essay_id}/feedback")
def get_feedback(essay_id: str) -> dict:
    """返回 feedback.json 全量内容。"""
    path = ARTIFACTS_DIR / essay_id / "feedback.json"
    return _read_json(path)


@app.get("/api/essays/{essay_id}/trace")
def get_trace(essay_id: str) -> dict:
    """返回 run_trace.json 全量内容。"""
    path = ARTIFACTS_DIR / essay_id / "run_trace.json"
    return _read_json(path)


@app.get("/api/essays/{essay_id}/hypotheses")
def get_hypotheses(essay_id: str) -> dict:
    """返回 hypotheses.json 全量内容（双评审原始假设）。"""
    path = ARTIFACTS_DIR / essay_id / "hypotheses.json"
    return _read_json(path)


@app.get("/api/essays/{essay_id}/human-scores")
def get_human_scores(essay_id: str) -> dict:
    """从 TSV 提取人类评分，按维度对齐后返回。"""
    row = _find_tsv_row(essay_id)

    dimensions: dict[str, dict] = {dim: {} for dim in DIM_ORDER}

    for trait_col, dim_id in TRAIT_TO_DIM.items():
        val = row.get(trait_col, "")
        try:
            score = int(val) if val.strip() else None
        except ValueError:
            score = None

        rater_key = "rater1" if trait_col.startswith("rater1") else "rater2"
        dimensions[dim_id][rater_key] = score

    # 计算均值
    for dim_id, scores in dimensions.items():
        r1 = scores.get("rater1")
        r2 = scores.get("rater2")
        if r1 is not None and r2 is not None:
            scores["mean"] = round((r1 + r2) / 2, 1)
        elif r1 is not None:
            scores["mean"] = float(r1)
        elif r2 is not None:
            scores["mean"] = float(r2)
        else:
            scores["mean"] = None

    return {"essay_id": essay_id, "dimensions": dimensions}
