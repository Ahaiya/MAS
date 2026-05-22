#!/usr/bin/env python3
"""批量评估 data/training/physics_experiment/ 下所有 .md 文件。

对每个样本文件评估四个维度：A1, A3, D1, F1。
已有结果（feedback.json 存在）自动跳过。

用法：
  python scripts/batch_physics.py
  python scripts/batch_physics.py --dry-run        # 仅预览，不实际运行
  python scripts/batch_physics.py --dims A1,D1     # 只跑指定维度
  python scripts/batch_physics.py --workers 2      # 并行数（默认 1，顺序执行）
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data" / "training" / "physics_experiment"
_ARTIFACTS_DIR = _PROJECT_ROOT / "artifacts" / "physics_experiment"
_DEFAULT_DIMS = ["A1", "A3", "D1", "F1"]


def _sample_id(p: Path) -> str:
    stem = p.stem.strip()
    import re
    m = re.match(r"^(\d+)", stem)
    return m.group(1) if m else stem


def _is_done(sample_id: str, dim: str) -> bool:
    return (_ARTIFACTS_DIR / sample_id / dim / "feedback.json").exists()


def _run_eval(md_file: Path, dim: str) -> tuple[bool, float]:
    cmd = [
        sys.executable, "-m", "scripts", "eval",
        str(md_file),
        "--dim", dim,
        "--no-verbose",
    ]
    t0 = time.monotonic()
    r = subprocess.run(cmd, cwd=str(_PROJECT_ROOT))
    elapsed = time.monotonic() - t0
    return r.returncode == 0, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description="批量评估 physics_experiment 样本")
    parser.add_argument("--dry-run", action="store_true", help="只打印待运行任务，不执行")
    parser.add_argument("--dims", default=",".join(_DEFAULT_DIMS),
                        help=f"逗号分隔的维度列表（默认：{','.join(_DEFAULT_DIMS)}）")
    parser.add_argument("--force", action="store_true", help="忽略已有结果，强制重跑")
    args = parser.parse_args()

    dims = [d.strip() for d in args.dims.split(",") if d.strip()]
    md_files = sorted(_DATA_DIR.glob("*.md"))

    if not md_files:
        print(f"[错误] 未在 {_DATA_DIR} 找到任何 .md 文件")
        sys.exit(1)

    # 构建任务列表
    tasks: list[tuple[Path, str]] = []
    skipped: list[tuple[str, str]] = []

    for f in md_files:
        sid = _sample_id(f)
        for dim in dims:
            if not args.force and _is_done(sid, dim):
                skipped.append((sid, dim))
            else:
                tasks.append((f, dim))

    total = len(tasks) + len(skipped)
    print("=" * 60)
    print(f"  physics_experiment 批量评估")
    print(f"  样本数   : {len(md_files)}")
    print(f"  维度     : {', '.join(dims)}")
    print(f"  总任务数 : {total}  (跳过已完成: {len(skipped)}, 待执行: {len(tasks)})")
    print("=" * 60)

    if skipped:
        print(f"\n[跳过 {len(skipped)} 个已完成任务]")
        for sid, dim in skipped:
            print(f"  ✓ {sid:<16} {dim}")

    if not tasks:
        print("\n所有任务已完成，无需重跑。")
        return

    if args.dry_run:
        print(f"\n[DRY-RUN] 将执行以下 {len(tasks)} 个任务：")
        for f, dim in tasks:
            sid = _sample_id(f)
            print(f"  - {sid:<16} {dim}")
        return

    print(f"\n[开始] 顺序执行 {len(tasks)} 个评估任务\n")

    succeeded, failed = [], []

    for idx, (f, dim) in enumerate(tasks, 1):
        sid = _sample_id(f)
        print(f"[{idx}/{len(tasks)}] {sid}  --dim {dim}  ...", flush=True)
        ok, elapsed = _run_eval(f, dim)
        status = "OK" if ok else "FAIL"
        print(f"  → {status}  ({elapsed:.0f}s)\n", flush=True)
        (succeeded if ok else failed).append((sid, dim))

    # 结果汇总
    print("=" * 60)
    print(f"  完成  {len(succeeded)} / {len(tasks)}")
    if failed:
        print(f"  失败  {len(failed)}")
        for sid, dim in failed:
            print(f"    ✗ {sid}  {dim}")
    else:
        print("  全部成功")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
