"""
一次提交的编排：并发解析 N 个文件 → 落盘 → 组包。

```
packages/{task}/{submission}/
├── raw/<源文件名>.json    # 完整响应，分页已合并
└── package.json           # 派生的编号空间
```

**不放进 `artifacts/`**：那是「觉得不对就整个删掉重跑」的产出目录，而解析结果是
花钱买来的**输入**。

**任一文件失败 → 整个 submission 失败**，不产出 package。这与引擎内部的失败隔离
取向相反，是有意为之：引擎隔离的是同一份材料上的多个判断（缺一个仍然诚实），这里
缺的是材料本身——报告 PDF 挂了只剩 PPT 却照样评出分，教师看到的是一个偏低的分数，
而看不出材料缺了一半。

成功文件的 raw 照常落盘（不扔掉已付费的结果），重跑时已有 raw 直接跳过。"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

from src.contracts.package import DataPackage
from src.parse.config import ParseConfig
from src.parse.docmind import CallFn, DocMindError, parse_file
from src.parse.package import build_package


class SubmissionParseError(DocMindError):
    """一次提交里有文件解析失败——不产出数据包。逐个文件说明是哪类失败。"""

    def __init__(self, failures: Sequence[Tuple[str, Exception]]) -> None:
        detail = "\n".join(f"  - {name}：{exc}" for name, exc in failures)
        super().__init__(
            f"本次提交有 {len(failures)} 个文件解析失败，不产出数据包"
            f"（材料缺一块，后面每个判断都建立在残缺输入上）：\n{detail}"
        )
        self.failures = list(failures)


def submission_dir(packages_root: Path, task: str, submission: str) -> Path:
    """`packages/{task}/{submission}/`——一次提交自包含在一个目录里。"""
    return Path(packages_root) / task / submission


def package_path(packages_root: Path, task: str, submission: str) -> Path:
    return submission_dir(packages_root, task, submission) / "package.json"


def _raw_path(packages_root: Path, task: str, submission: str, source_file: str) -> Path:
    return submission_dir(packages_root, task, submission) / "raw" / f"{source_file}.json"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _default_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_submission(
    files: Sequence[Path],
    *,
    task: str,
    submission: str,
    packages_root: Path,
    config: ParseConfig,
    call: CallFn,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], str] = _default_now,
) -> DataPackage:
    """解析一次提交的全部文件，落盘 raw 与 package.json，返回 DataPackage。

    N 个文件**并发**提交、各自轮询——轮询是纯等待，串行等于把等待时间乘 N。

    Raises:
        SubmissionParseError: 任意一个文件失败。此时 package.json 不产出，但已经
            成功的文件的 raw 仍然留在盘上（下次重跑不再付费）。"""
    if not files:
        raise ValueError("parse_submission: 一次提交至少要有一个文件。")

    paths = [Path(f) for f in files]
    # raw 以文件名命名，同名文件会互相覆盖——那会得到一份「同一个文件被读了两遍」
    # 的包，编号照样连续、看不出问题。宁可在这里拒绝。
    duplicates = sorted({p.name for p in paths if [q.name for q in paths].count(p.name) > 1})
    if duplicates:
        raise ValueError(
            f"parse_submission: 一次提交里有同名文件：{'、'.join(duplicates)}。"
            "解析原件按文件名落盘，同名会互相覆盖——请先改名再提交。"
        )
    to_parse = [
        path
        for path in paths
        if force or not _raw_path(packages_root, task, submission, path.name).exists()
    ]

    failures: List[Tuple[str, Exception]] = []
    if to_parse:
        with ThreadPoolExecutor(max_workers=len(to_parse)) as pool:
            futures = {
                path.name: pool.submit(
                    parse_file, path, config=config, call=call, sleep=sleep
                )
                for path in to_parse
            }
        for name, future in futures.items():
            try:
                raw = future.result()
            except Exception as exc:  # noqa: BLE001 —— 见下
                # 这里必须拦下**所有**异常而不只是 DocMindError：漏网的一个
                # 会从收集循环里逃出去，让排在它后面、已经解析成功的文件的 raw
                # 根本没机会落盘——等于把已付费的结果扔了。
                failures.append((name, exc))
                continue
            # 逐个落盘：整体失败不等于把已付费的结果扔掉。
            _write_json(_raw_path(packages_root, task, submission, name), raw)

    if failures:
        raise SubmissionParseError(failures)

    parsed: List[Tuple[str, List[Dict[str, Any]]]] = []
    for path in paths:
        raw = json.loads(
            _raw_path(packages_root, task, submission, path.name).read_text(encoding="utf-8")
        )
        parsed.append((path.name, list(raw.get("layouts") or [])))

    package = build_package(
        parsed,
        package_id=f"{task}/{submission}",
        options=dict(config.options),
        parsed_at=now(),
    )
    _write_json(package_path(packages_root, task, submission), package.to_dict())
    return package
