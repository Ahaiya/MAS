"""
Prompt template loader — load Jinja2 YAML templates and render them.

Responsibilities:
- Load a YAML prompt template file and validate it against PromptFileSchema.
- Render the template text using Jinja2 with a caller-supplied context dict.
- Raise clear errors for missing files, invalid YAML, schema violations,
  and undefined template variables (strict mode).

This module handles only IO and rendering.  It has no knowledge of rubric
dimensions, score scales, policies, or orchestration logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Union

import yaml
from jinja2 import Environment, StrictUndefined, TemplateError

from src.config.schema import PromptFileSchema


# ── Value object ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PromptTemplate:
    """
    Parsed and validated prompt template.

    Attributes:
        template_text : Raw Jinja2 template string (not yet rendered).
        metadata      : Metadata dict from the YAML file (template_version, etc.).
        source_path   : The file path or identifier this template was loaded from.
    """

    template_text: str
    metadata: Dict[str, Any]
    source_path: str


# ── Loader ────────────────────────────────────────────────────────────────────

class PromptLoader:
    """
    Loads and renders Jinja2 prompt templates from YAML files.

    Template YAML format (validated by PromptFileSchema)::

        prompt_template: |
          Hello {{ name }}!
        metadata:
          template_version: "v1"
          compatible_dimensions: ["*"]
    """

    def load(self, path: Union[str, Path]) -> PromptTemplate:
        """
        Load a YAML prompt template file into a PromptTemplate.

        Args:
            path: Absolute or relative path to the YAML template file.

        Returns:
            PromptTemplate with template_text and metadata populated.

        Raises:
            FileNotFoundError: If the file does not exist.
            yaml.YAMLError   : If the file contains invalid YAML.
            ValueError       : If the YAML does not match PromptFileSchema.
        """
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

        # Validate with schema (raises ValidationError on mismatch)
        validated = PromptFileSchema.model_validate(data)

        return PromptTemplate(
            template_text=validated.prompt_template,
            metadata=validated.metadata.model_dump(),
            source_path=str(file_path),
        )

    def render(self, template: PromptTemplate, context: Dict[str, Any]) -> str:
        """
        Render a PromptTemplate with the supplied context dict.

        Uses Jinja2 StrictUndefined so that any variable referenced in the
        template but absent from context raises an UndefinedError immediately.

        Args:
            template: A PromptTemplate previously returned by load().
            context : Dict of variable bindings for the template.

        Returns:
            Rendered string.

        Raises:
            jinja2.UndefinedError: If a template variable is not in context.
            jinja2.TemplateError : On other Jinja2 rendering errors.
        """
        env = Environment(undefined=StrictUndefined, autoescape=False)
        jinja_template = env.from_string(template.template_text)
        return jinja_template.render(**context)


# ── Module-level convenience helpers ─────────────────────────────────────────

_default_loader = PromptLoader()


def load_template(path: Union[str, Path]) -> PromptTemplate:
    """Load a YAML prompt template using the default loader."""
    return _default_loader.load(path)


def render_template(template: PromptTemplate, context: Dict[str, Any]) -> str:
    """Render a PromptTemplate using the default loader."""
    return _default_loader.render(template, context)
