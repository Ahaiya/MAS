# The Philosophy and Boundaries of the MAS Evaluation Engine

## 1. 核心应用场景 (Application Domain)
本项目致力于解决一个明确的业务问题：**基于量规（Rubric）的非结构化文本的自动化评价及反馈。**
系统需读取结构化的评分标准（Rubric）和非结构化的用户输入（如学生复杂工程报告、Essay 等），通过多智能体协作，输出具备可解释性的分数以及针对性的指导反馈。

## 2. 核心架构原则 (The Golden Rules)
* **极致可泛化 (Extreme Generalizability)：** 系统必须是一个通用的评价引擎。当前开发与验证的沙盒数据为公开数据集 **ASAP Set 8**，但系统架构绝不能与此数据集绑定。
* **零硬编码与配置驱动 (Zero Hardcoding & Config-Driven)：**
    * **严禁**在代码逻辑中硬编码任何与 ASAP Set 8 相关的业务逻辑、评价标准或特定维度的 Prompt。
    * 所有的 Rubric、Prompt 模板（如 Jinja2 风格）、系统指令，必须从外部配置文件（YAML/JSON）动态读取并注入到 Agent 中。
* **LLM 接口抽象化 (LLM-Agnostic)：** 底层模型调用必须被解耦。系统需设计统一的 LLM 适配器层，确保未来可以在不同的模型（如 Claude, Gemini, DeepSeek 等）之间无缝热切换。

## 3. 系统评价与验证指标 (Evaluation Metrics)
本系统的最终目标是达到甚至超越人类专家的评价水平。验证脚本必须内置对以下两个维度的数学度量：
1.  **多智能体间的一致性 (Inter-Agent Consistency)：** 衡量系统内部不同 Agent（或多次采样结果）之间的一致性，确保系统输出的稳定性。
2.  **与人类专家的一致性 (Human-System Alignment)：** 这是系统的北极星指标。必须使用 **二次加权卡帕值 (Quadratic Weighted Kappa)** 作为核心评估标准，计算系统打分与公开数据集 (Ground Truth) 专家打分的一致性。

## 4. 架构形态约束 (Architectural Paradigm)
* 采用 **有向图/状态机模式 (Graph-based State Machine)** 来构建 MAS 流程（采用 LangGraph 思想），摒弃不可控的自由对话 Agent 模式。
* 必须实现清晰的**状态流转 (State Routing)** 与 **异常回溯机制 (Fallback/Revert)**，确保在长文本信息抽取遗漏或量规未对齐时，系统能够自动打回重做。