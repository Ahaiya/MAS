"""配置子系统入口：按约定路径加载 configs/ 下的量规、提示词与仲裁策略。

v1 的 bundle 工件引用解析 + 冻结哈希（ConfigCompiler / ConfigResolver / freeze）
已随旧流程删除；bundle.yaml 本身也已删除，路径改由约定固定。"""

from .compiler import (
    PROMPT_STAGES,
    ConfigCompileError,
    list_task_dimension_ids,
    load_adjudication_policy,
    load_dimension_rubric,
    prompt_path,
)

__all__ = [
    "PROMPT_STAGES",
    "ConfigCompileError",
    "list_task_dimension_ids",
    "load_adjudication_policy",
    "load_dimension_rubric",
    "prompt_path",
]
