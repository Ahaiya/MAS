"""
Prompt 模板加载器，负责读取 YAML 模板并渲染各阶段 prompt。

Prompt 模板加载器 — 加载 Jinja2 YAML 模板并渲染它们。

职责:
- 加载 YAML prompt 模板文件，并根据 PromptFileSchema 进行验证。
- 使用 Jinja2 和调用者提供的 context dict 渲染模板文本。
- 对于文件缺失、无效 YAML、schema 违规以及未定义的模板变量（严格模式）抛出明确的错误。

本模块仅处理 IO 和渲染。它不了解 rubric 维度、评分尺度、策略或编排逻辑。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Union

import yaml
from jinja2 import Environment, StrictUndefined

from src.config.schema import PromptFileSchema


# ── 值对象 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PromptTemplate:
    """
    已解析并验证的 prompt 模板。
    
    Attributes:
        template_text : 原始 Jinja2 模板字符串（尚未渲染）。
        metadata      : 来自 YAML 文件的元数据 dict（template_version 等）。
        source_path   : 加载此模板的文件路径或标识符。"""

    template_text: str
    metadata: Dict[str, Any]
    source_path: str


# ── 加载器 ────────────────────────────────────────────────────────────────────

class PromptLoader:
    """
    从 YAML 文件加载并渲染 Jinja2 prompt 模板。
    
    模板 YAML 格式（由 PromptFileSchema 验证）::
    
        prompt_template: |
          Hello {{ name }}!
        metadata:
          template_version: "v1"
          compatible_dimensions: ["*"]"""

    def load(self, path: Union[str, Path]) -> PromptTemplate:
        """
        将 YAML prompt 模板文件加载到 PromptTemplate 中。
        
        Args:
            path: YAML 模板文件的绝对或相对路径。
        
        Returns:
            填充了 template_text 和 metadata 的 PromptTemplate。
        
        Raises:
            FileNotFoundError: 如果文件不存在。
            yaml.YAMLError   : 如果文件包含无效的 YAML。
            ValueError       : 如果 YAML 不匹配 PromptFileSchema。"""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Prompt template file not found: {file_path}")

        raw = file_path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise yaml.YAMLError(f"Invalid YAML in '{file_path}': {exc}") from exc

        if not isinstance(data, dict) or "prompt_template" not in data:
            raise ValueError(
                f"'{file_path}' is missing required key 'prompt_template'. "
                "Expected keys: prompt_template, metadata."
            )

        # 使用 schema 进行验证（不匹配时抛出 ValidationError）
        validated = PromptFileSchema.model_validate(data)

        return PromptTemplate(
            template_text=validated.prompt_template,
            metadata=validated.metadata.model_dump(),
            source_path=str(file_path),
        )

    def load_with_override(
        self,
        template_name: str,
        dimension_id: str,
        prompts_root: Union[str, Path] = "configs/prompts",
    ) -> PromptTemplate:
        """如果存在 override 模板则加载；否则加载 global 模板。
        
                查找顺序:
                1) {prompts_root}/{template_name}_overrides/{dimension_id}.yaml
                2) {prompts_root}/{template_name}.yaml"""
        name = Path(template_name).stem
        root = Path(prompts_root)
        override_path = root / f"{name}_overrides" / f"{dimension_id}.yaml"
        if override_path.exists():
            return self.load(override_path)
        return self.load(root / f"{name}.yaml")

    def render(self, template: PromptTemplate, context: Dict[str, Any]) -> str:
        """
        使用提供的 context dict 渲染 PromptTemplate。
        
        使用 Jinja2 StrictUndefined，以便模板中引用但 context 中缺失的任何变量会立即抛出 UndefinedError。
        
        Args:
            template: 之前由 load() 返回的 PromptTemplate。
            context : 模板的变量绑定 dict。
        
        Returns:
            渲染后的字符串。
        
        Raises:
            jinja2.UndefinedError: 如果模板变量不在 context 中。
            jinja2.TemplateError : 出现其他 Jinja2 渲染错误时。"""
        env = Environment(undefined=StrictUndefined, autoescape=False)
        jinja_template = env.from_string(template.template_text)
        return jinja_template.render(**context)


# ── 模块级便捷辅助函数 ─────────────────────────────────────────

_default_loader = PromptLoader()


def load_template(path: Union[str, Path]) -> PromptTemplate:
    """使用默认加载器加载 YAML prompt 模板。"""
    return _default_loader.load(path)


def render_template(template: PromptTemplate, context: Dict[str, Any]) -> str:
    """使用默认加载器渲染 PromptTemplate。"""
    return _default_loader.render(template, context)
