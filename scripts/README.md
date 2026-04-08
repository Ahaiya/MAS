# MAS CLI

官方统一入口：

```bash
python -m scripts --help
```

命令分组：

```bash
python -m scripts eval ...
python -m scripts outer-loop ...
python -m scripts task ...
python -m scripts metrics ...
python -m scripts config ...
```

评估脚本已收束为一个正式入口：

- `python scripts/eval.py ...`
- 最短可用写法：`python -m scripts eval engineering "data/training/7组—专属社区娱乐学习软件.md"`

默认联动规则：

- 样本 ID 会优先从文件名前缀自动推导
  - 例如 `4组—AI助手.md -> id=4`
- 输出目录默认联动为 `artifacts/eval_engineering/{id}`
- debug bundle 默认开启
- 如需兼容旧写法，仍可继续使用 `--input`

`python scripts/eval_engineering.py ...` 仅保留为兼容别名。

其余兼容入口仍然保留，但只建议用于平滑迁移：

- `python scripts/outer_loop.py ...`
- `python scripts/compute_qwk.py ...`
- `python scripts/compute_coverage_metrics.py ...`
- `python scripts/validate_config.py ...`
- `python scripts/mas.py ...`
