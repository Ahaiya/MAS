# 项目定位
本项目是一个基于量规的工程能力自动评价 MAS。
内环流水线：chunking → evidence_extraction → scoring → explanation
外环：配置优化 Agent，目标 composite QWK >= 0.80

# 设计约束
- span_id 由代码层自动编号，不由 LLM 生成
- evidence_chain 由代码层从 evidence_ids 回查组装，不由 LLM 生成
- chunking 的 material_strategy 由代码从 material_context.type 自动推导
- 预切分按自然边界（对话轮次/段落），strategy 由 material_context.type 决定
- 所有配置通过 bundle.yaml 的 active_task_id 路由，切换任务只改这一个字段
- dimension_relevance 步骤已废弃，全量 chunk 传入 evidence_extraction

# 工作规范
- 每次只实现 plan.md 中的一个任务
- 改完一个模块立即写测试
- 不修改 configs/rubrics/ 下任何文件
- 所有配置读取必须通过 bundle.yaml 路由，不hardcode路径