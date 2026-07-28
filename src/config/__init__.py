"""配置子系统入口：加载 bundle 声明的任务量规。

v1 的 bundle 工件引用解析 + 冻结哈希（ConfigCompiler / ConfigResolver / freeze）
已随旧流程删除，现在只保留「按 task_id + dim_id 读量规」这条路径。"""

from .compiler import (
    ConfigCompileError,
    list_task_dimension_ids,
    load_dimension_rubric,
    strip_configs_prefix,
)

__all__ = [
    "ConfigCompileError",
    "list_task_dimension_ids",
    "load_dimension_rubric",
    "strip_configs_prefix",
]
