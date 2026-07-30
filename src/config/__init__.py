"""配置子系统入口：按约定路径加载 configs/ 下的量规、提示词与仲裁策略。

- `compiler`: 量规/策略/提示词的路径约定与加载（RubricSnapshot、PolicySnapshot）
- `rubric_validation`: 量规结构校验，缺字段在加载时就报错
- `errors`: ConfigCompileError
"""
