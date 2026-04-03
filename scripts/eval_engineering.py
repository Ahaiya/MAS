#!/usr/bin/env python3
"""兼容入口：工程评价脚本已收束到 scripts/eval.py。

推荐使用：
  python scripts/eval.py "data/1组—虚拟故居重建计划.md"

统一入口：
  python -m scripts eval engineering "data/1组—虚拟故居重建计划.md"
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.eval import app, main

__all__ = ["app", "main"]


if __name__ == "__main__":
    app()
