"""用于外环反馈集成的人工纠正数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
import json


@dataclass
class ScoreCorrection:
    """人工修改了最终的维度分数。"""
    dimension_code: str   # 例如 "A4-1"
    original_score: int
    corrected_score: int


@dataclass
class FeedbackCorrection:
    """人工重写了某个维度的反馈文本。"""
    dimension_code: str
    original_feedback: str
    corrected_feedback: str


@dataclass
class EvidenceAddition:
    """人工添加了 AI 遗漏的证据引用。"""
    dimension_code: str
    added_quotes: list[str]


@dataclass
class CorrectionEvent:
    """人工针对单个样本提交的所有纠正。"""
    sample_id: str
    timestamp: str
    score_corrections: list[ScoreCorrection] = field(default_factory=list)
    feedback_corrections: list[FeedbackCorrection] = field(default_factory=list)
    evidence_additions: list[EvidenceAddition] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.score_corrections
            or self.feedback_corrections
            or self.evidence_additions
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "timestamp": self.timestamp,
            "score_corrections": [
                {
                    "dimension_code": c.dimension_code,
                    "original_score": c.original_score,
                    "corrected_score": c.corrected_score,
                }
                for c in self.score_corrections
            ],
            "feedback_corrections": [
                {
                    "dimension_code": c.dimension_code,
                    "original_feedback": c.original_feedback,
                    "corrected_feedback": c.corrected_feedback,
                }
                for c in self.feedback_corrections
            ],
            "evidence_additions": [
                {
                    "dimension_code": c.dimension_code,
                    "added_quotes": c.added_quotes,
                }
                for c in self.evidence_additions
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CorrectionEvent:
        return cls(
            sample_id=str(d.get("sample_id", "")),
            timestamp=str(d.get("timestamp", "")),
            score_corrections=[
                ScoreCorrection(
                    dimension_code=str(c["dimension_code"]),
                    original_score=int(c["original_score"]),
                    corrected_score=int(c["corrected_score"]),
                )
                for c in (d.get("score_corrections") or [])
            ],
            feedback_corrections=[
                FeedbackCorrection(
                    dimension_code=str(c["dimension_code"]),
                    original_feedback=str(c.get("original_feedback", "")),
                    corrected_feedback=str(c.get("corrected_feedback", "")),
                )
                for c in (d.get("feedback_corrections") or [])
            ],
            evidence_additions=[
                EvidenceAddition(
                    dimension_code=str(c["dimension_code"]),
                    added_quotes=[str(q) for q in (c.get("added_quotes") or [])],
                )
                for c in (d.get("evidence_additions") or [])
            ],
        )


class PendingCorrections:
    """等待处理的纠正事件队列。"""

    DEFAULT_PATH = Path("experiments/pending_corrections.json")
    ARCHIVE_DIR = Path("experiments/processed_corrections")

    def __init__(self, events: list[CorrectionEvent], path: Path) -> None:
        self.events = events
        self.path = path

    @classmethod
    def load(cls, path: Path | None = None) -> PendingCorrections:
        resolved = Path(path) if path else cls.DEFAULT_PATH
        if not resolved.exists():
            return cls(events=[], path=resolved)
        raw = json.loads(resolved.read_text(encoding="utf-8"))
        events = [
            CorrectionEvent.from_dict(e)
            for e in (raw.get("corrections") or [])
            if isinstance(e, dict)
        ]
        return cls(events=[e for e in events if not e.is_empty()], path=resolved)

    def is_empty(self) -> bool:
        return not self.events

    def archive(self) -> None:
        """将待处理文件移动到已处理归档并清空队列。"""
        if not self.path.exists():
            return
        self.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        archive_path = self.ARCHIVE_DIR / f"{ts}_corrections.json"
        self.path.rename(archive_path)
        self.events = []


__all__ = [
    "ScoreCorrection",
    "FeedbackCorrection",
    "EvidenceAddition",
    "CorrectionEvent",
    "PendingCorrections",
]
