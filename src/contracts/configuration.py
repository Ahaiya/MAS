"""
:configuration schema：量规 / 裁决策略 / provider 配置。

  RubricSnapshot      —— 一个二级指标的完整量规
  PolicySnapshot      —— 仲裁触发阈值等策略配置
  ProviderEntryConfig —— 单个 provider 的 model / api_base / api_key_env / params

设计不变式：
- 全部为冻结（不可变）的 dataclass，编译后不再改动。
- 只存环境变量的**名称**（api_key_env），密钥值只从 .env 读，绝不进配置对象。

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ProviderEntryConfig:
    """单个 LLM provider 端点的配置。

        model / api_base 都是必填：它们只能来自 model_config.yaml，没有环境变量兜底。
        密钥则相反，这里只存**环境变量的名字**，值只在 .env 里。

        Attributes:
            api_key_env: 存放 API key 的环境变量名称（按厂商取名，如 "DEEPSEEK_API_KEY"）。
            model:       模型标识符（例如 "deepseek-chat"）。
            api_base:    API BASE URL。
            params:      可选的 provider 默认请求参数，会合并到每次调用中
                         （例如 temperature、max_tokens，或 provider 特定的
                         extra_body 设置，如 reasoning controls）。"""
    api_key_env: str
    model: str = ""
    api_base: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RubricSnapshot:
    """一个二级指标的量规快照，编译一次供整轮评价读取，不重复解析 YAML。

        Attributes:
            dim_id: 二级指标
            dim_name: 二级指标名称
            indicator_description: 二级指标的完整解释，选段/取证阶段给模型看
            dimensions: 观测点定义列表（code / name / weight / anchors）
            scale_min / scale_max: 量表分数范围，全部观测点共用
            scale_levels: 量表档位标签 {档位: 名称}，如 {4: "优秀"}

        观测点由 code（"D1-1"）唯一标识，全系统（产物 JSON、trace、决策）同一个键。
        anchors 与 scale_levels 都保持量规原样 {档位: 文本}，档位号 + 档位标签 +
        锚点正文的拼接在 prompt 渲染时现场做，快照不预合成中间结构。"""
    dim_id: str
    dim_name: str
    indicator_description: str
    dimensions: List[Dict[str, Any]]
    scale_min: int
    scale_max: int
    scale_levels: Dict[int, str] = field(default_factory=dict)

    def get_dimension(self, code: str) -> Optional[Dict[str, Any]]:
        """按观测点 code 取定义；没有就返回 None。一份量规不过十来个观测点，
        线性扫足够，不额外维护一张查找表。"""
        return next((d for d in self.dimensions if d["code"] == code), None)

@dataclass(frozen=True)
class PolicySnapshot:
    """从 configs/adjudication.yaml 读出的、运行期只读的仲裁策略快照。

        两条触发规则各由一个数决定，没有通用规则引擎——加规则一定要改代码，

        Attributes:
            score_gap_threshold : 任意观测点上两位评委分差 > 此值即触发仲裁。
            drift_min_dimensions: 分差恰为 1 且同向的观测点数 ≥ 此值时，
                                  这些观测点全部触发仲裁。"""
    score_gap_threshold: int
    drift_min_dimensions: int
