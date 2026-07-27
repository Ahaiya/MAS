"""
确定性单元切分，替代旧的 chunker.py（LLM 语义分块）+ quote_matcher.py（模糊匹配回溯）。

按内容类型把文本切成全局连续编号的 Unit：散文按句（。？！；+换行）、代码块（```
围栏）整块、表格（`|`）每行、标题（`#`）1 单元、图片（`![alt](src)`）1 单元。
零 LLM、确定性、可复现——同样的输入永远产出同样的 Unit 列表。

切分本身不受文档长度影响（证据编号锚点需要细粒度引用，不论文档长短）；只有
当多文件合并后的总 token 数超过"上下文安全余量"时，才会从尾部丢弃单元以适配
预算，且被丢弃的单元编号会显式返回，绝不静默丢弃。"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from src.contracts.package import DataPackage, Unit
from src.utils.dialogue_sources import extract_dialogue_source_spans, source_for_range

# 上下文安全余量：语义替代旧 chunker.py 的 4000 token 阈值（真实样本中位数约
# 21700 token，旧阈值在生产数据上几乎从未命中语义分块路径）。
DEFAULT_CONTEXT_BUDGET_TOKENS = 48000

_CHINESE_CHAR_RE = re.compile(r"[一-鿿]")
_NON_WS_RE = re.compile(r"\S")
_SENTENCE_END_RE = re.compile(r"[。？！；]")
_HEADING_RE = re.compile(r"^#{1,6}(\s|$)")
_IMAGE_LINE_RE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\([^)]*\)$")
_SUPPORTED_SUFFIXES = {".md", ".txt"}


def estimate_tokens(text: str) -> int:
    """按语言构成估算 token 数（无 tokenizer 依赖，纯启发式）。"""
    if not text:
        return 0
    non_ws = len(_NON_WS_RE.findall(text))
    if non_ws == 0:
        return 0
    zh_ratio = len(_CHINESE_CHAR_RE.findall(text)) / non_ws
    if zh_ratio >= 0.2:
        return math.ceil(len(text) * 1.5)
    return math.ceil(len(text.split()) * 1.3)


def _iter_lines(text: str) -> List[Tuple[int, int, str]]:
    """按行切分并保留每行内容（不含换行符）在原文中的精确字符偏移。"""
    lines: List[Tuple[int, int, str]] = []
    pos = 0
    for raw_line in text.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        lines.append((pos, pos + len(content), content))
        pos += len(raw_line)
    return lines


def _trim_span(text: str, start: int, end: int) -> Tuple[int, int, str]:
    """收紧 (start, end) 至去除首尾空白后的实际内容，保持 char_range 与 text 的切片一致。"""
    raw = text[start:end]
    lstripped = raw.lstrip()
    left_trim = len(raw) - len(lstripped)
    trimmed = lstripped.rstrip()
    return start + left_trim, start + left_trim + len(trimmed), trimmed


def _sentence_spans(line: str) -> List[Tuple[int, int]]:
    """把一行文本按 。？！；切成句子跨度（换行本身已由逐行扫描给出边界）。"""
    spans: List[Tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_END_RE.finditer(line):
        end = match.end()
        spans.append((cursor, end))
        cursor = end
    if cursor < len(line):
        spans.append((cursor, len(line)))
    return spans


def _speaker_for(start: int, end: int, source_spans: List[Dict[str, Any]]) -> Optional[str]:
    """对话轮次单元的说话人标注：复用 dialogue_sources 的区域标签。"""
    source_type, source_label = source_for_range(start, end, source_spans, allow_mixed=False)
    if source_type == "unknown":
        return None
    return source_label or source_type


def _emit_prose_units(
    text: str,
    line_start: int,
    line_content: str,
    source_file: str,
    source_spans: List[Dict[str, Any]],
    next_id: int,
) -> Tuple[List[Unit], int]:
    """把一行文本按句切成 prose Unit；散文分支与对话围栏解包分支共用。"""
    emitted: List[Unit] = []
    for s_start, s_end in _sentence_spans(line_content):
        abs_start, abs_end = line_start + s_start, line_start + s_end
        s2, e2, trimmed = _trim_span(text, abs_start, abs_end)
        if not trimmed:
            continue
        emitted.append(
            Unit(
                id=next_id,
                kind="prose",
                text=trimmed,
                source_file=source_file,
                char_range=(s2, e2),
                speaker=_speaker_for(s2, e2, source_spans),
            )
        )
        next_id += 1
    return emitted, next_id


def _handle_code_fence(
    text: str,
    lines: List[Tuple[int, int, str]],
    i: int,
    source_file: str,
    source_spans: List[Dict[str, Any]],
    next_id: int,
) -> Tuple[List[Unit], int, int]:
    """消费一个 ``` 围栏块。历史训练数据把对话轮次正文也包在 ```text 围栏里——
    围栏本身不是代码，是这条格式的结构噪音，命中已知对话区间时按句拆开正文并打上
    说话人，否则整块当作一个 code Unit。返回 (emitted, next_line_index, next_id)。"""
    n = len(lines)
    start, end, _ = lines[i]
    close_idx: Optional[int] = None
    j = i + 1
    while j < n:
        if lines[j][2].strip().startswith("```"):
            close_idx = j
            break
        j += 1
    fence_end = lines[close_idx][1] if close_idx is not None else lines[n - 1][1]
    inner_lines = lines[i + 1 : close_idx] if close_idx is not None else lines[i + 1 : n]
    next_i = (close_idx + 1) if close_idx is not None else n

    inner_start = inner_lines[0][0] if inner_lines else end
    inner_end = inner_lines[-1][1] if inner_lines else end
    dialogue_type, _ = source_for_range(inner_start, inner_end, source_spans, allow_mixed=False)

    units: List[Unit] = []
    if inner_lines and dialogue_type != "unknown":
        for line_start, _, line_content in inner_lines:
            emitted, next_id = _emit_prose_units(text, line_start, line_content, source_file, source_spans, next_id)
            units.extend(emitted)
    else:
        units.append(
            Unit(
                id=next_id,
                kind="code",
                text=text[start:fence_end],
                source_file=source_file,
                char_range=(start, fence_end),
                speaker=None,
            )
        )
        next_id += 1
    return units, next_i, next_id


def _handle_heading(text: str, start: int, end: int, source_file: str, next_id: int) -> Tuple[Unit, int]:
    """标题行整行成 1 个 Unit；层级由原始 `#` 前缀数量隐式携带在 text 里。"""
    s2, e2, trimmed = _trim_span(text, start, end)
    unit = Unit(id=next_id, kind="heading", text=trimmed, source_file=source_file, char_range=(s2, e2), speaker=None)
    return unit, next_id + 1


def _handle_table_run(
    text: str, lines: List[Tuple[int, int, str]], i: int, source_file: str, next_id: int
) -> Tuple[List[Unit], int, int]:
    """消费连续的 `|` 表格行，每行 1 个 Unit。返回 (emitted, next_line_index, next_id)。"""
    units: List[Unit] = []
    n = len(lines)
    while i < n:
        start, end, content = lines[i]
        if not content.strip().startswith("|"):
            break
        s2, e2, trimmed = _trim_span(text, start, end)
        units.append(
            Unit(id=next_id, kind="table_row", text=trimmed, source_file=source_file, char_range=(s2, e2), speaker=None)
        )
        next_id += 1
        i += 1
    return units, i, next_id


def _handle_image(
    text: str, start: int, end: int, match: "re.Match[str]", source_file: str, next_id: int
) -> Tuple[Unit, int]:
    """整行的图片语法成 1 个 Unit。一期无解析 API 描述，用 alt 兜底作 text；
    char_range 仍指向完整 markdown 语法所在位置（供前端定位），两者不必相等。"""
    s2, e2, trimmed = _trim_span(text, start, end)
    alt = match.group("alt").strip()
    unit = Unit(id=next_id, kind="image", text=alt or trimmed, source_file=source_file, char_range=(s2, e2), speaker=None)
    return unit, next_id + 1


def segment_text(text: str, source_file: str, start_id: int = 0) -> List[Unit]:
    """纯函数：把单个文件的文本按内容类型切成从 start_id 起全局连续编号的 Unit 列表。"""
    source_spans = extract_dialogue_source_spans(text)
    lines = _iter_lines(text)
    units: List[Unit] = []
    next_id = start_id
    i = 0
    n = len(lines)

    while i < n:
        start, end, content = lines[i]
        stripped = content.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            emitted, i, next_id = _handle_code_fence(text, lines, i, source_file, source_spans, next_id)
            units.extend(emitted)
            continue

        if _HEADING_RE.match(stripped):
            unit, next_id = _handle_heading(text, start, end, source_file, next_id)
            units.append(unit)
            i += 1
            continue

        if stripped.startswith("|"):
            emitted, i, next_id = _handle_table_run(text, lines, i, source_file, next_id)
            units.extend(emitted)
            continue

        image_match = _IMAGE_LINE_RE.match(stripped)
        if image_match:
            unit, next_id = _handle_image(text, start, end, image_match, source_file, next_id)
            units.append(unit)
            i += 1
            continue

        emitted, next_id = _emit_prose_units(text, start, content, source_file, source_spans, next_id)
        units.extend(emitted)
        i += 1

    return units


def _apply_budget(units: List[Unit], budget_tokens: int) -> Tuple[List[Unit], List[int]]:
    """超预算时从尾部丢弃单元以适配上下文安全余量；丢弃的单元编号显式返回。

    始终保留第一个单元，避免单一超大单元把整包裁成空包。"""
    kept: List[Unit] = []
    dropped: List[int] = []
    total = 0
    over_budget = False
    for unit in units:
        if over_budget:
            dropped.append(unit.id)
            continue
        tokens = estimate_tokens(unit.text)
        if kept and total + tokens > budget_tokens:
            over_budget = True
            dropped.append(unit.id)
            continue
        total += tokens
        kept.append(unit)
    return kept, dropped


def build_package(
    files: Sequence[Tuple[str, str]],
    package_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> Tuple[DataPackage, List[int]]:
    """纯函数：多个 (source_file, text) 合并进一个 DataPackage，共享同一全局编号空间。

    返回 (package, dropped_unit_ids)——超预算丢弃的单元编号，未超预算则为空列表。"""
    all_units: List[Unit] = []
    next_id = 0
    for source_file, text in files:
        file_units = segment_text(text, source_file, start_id=next_id)
        all_units.extend(file_units)
        if file_units:
            next_id = file_units[-1].id + 1

    kept_units, dropped_ids = _apply_budget(all_units, budget_tokens)
    package = DataPackage(package_id=package_id, units=kept_units, metadata=dict(metadata or {}))
    return package, dropped_ids


def read_text_file(
    paths: Union[str, Path, Sequence[Union[str, Path]]],
    package_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> Tuple[DataPackage, List[int]]:
    """IO 边界：从一个或多个 .md/.txt 文件读取文本并构造 DataPackage。"""
    path_list = [paths] if isinstance(paths, (str, Path)) else list(paths)
    files: List[Tuple[str, str]] = []
    for raw_path in path_list:
        path = Path(raw_path)
        if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
            raise ValueError(
                f"read_text_file: unsupported file extension '{path.suffix}' for {path} "
                f"(expected one of {sorted(_SUPPORTED_SUFFIXES)})"
            )
        files.append((str(path), path.read_text(encoding="utf-8")))
    return build_package(files, package_id=package_id, metadata=metadata, budget_tokens=budget_tokens)
