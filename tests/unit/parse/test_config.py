"""`src/parse/config.py`：缺字段、缺密钥都当场报错，不回落默认值。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.parse.config import ParseConfigError, load_parse_config, require_credentials

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_OK_YAML = """
endpoint: docmind-api.cn-hangzhou.aliyuncs.com
connect_timeout_ms: 10000
read_timeout_ms: 60000
poll_interval_seconds: 10
poll_max_seconds: 7200
layout_step_size: 100
options:
  llm_enhancement: true
  enhancement_mode: VLM
  formula_enhancement: true
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "parse.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_加载完整配置() -> None:
    config = load_parse_config(_PROJECT_ROOT / "configs" / "parse.yaml")
    assert config.endpoint
    assert config.poll_interval_seconds > 0
    assert config.options == {
        "llm_enhancement": True,
        "enhancement_mode": "VLM",
        "formula_enhancement": True,
    }


def test_仓库自带的配置三个增强开关默认全开() -> None:
    config = load_parse_config(_PROJECT_ROOT / "configs" / "parse.yaml")
    assert config.options["llm_enhancement"] is True
    assert config.options["formula_enhancement"] is True
    assert config.options["enhancement_mode"] == "VLM"


def test_文件不存在报错(tmp_path: Path) -> None:
    with pytest.raises(ParseConfigError, match="不存在"):
        load_parse_config(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    "missing",
    ["endpoint", "connect_timeout_ms", "read_timeout_ms", "poll_interval_seconds",
     "poll_max_seconds", "layout_step_size"],
)
def test_缺字段直接报错不回落默认值(tmp_path: Path, missing: str) -> None:
    lines = [line for line in _OK_YAML.strip().splitlines() if not line.startswith(f"{missing}:")]
    with pytest.raises(ParseConfigError, match=missing):
        load_parse_config(_write(tmp_path, "\n".join(lines)))


@pytest.mark.parametrize("missing", ["llm_enhancement", "enhancement_mode", "formula_enhancement"])
def test_缺增强开关直接报错(tmp_path: Path, missing: str) -> None:
    lines = [line for line in _OK_YAML.strip().splitlines() if missing not in line]
    with pytest.raises(ParseConfigError, match=missing):
        load_parse_config(_write(tmp_path, "\n".join(lines)))


def test_数值字段非数字报错(tmp_path: Path) -> None:
    bad = _OK_YAML.replace("poll_interval_seconds: 10", "poll_interval_seconds: 十秒")
    with pytest.raises(ParseConfigError, match="poll_interval_seconds"):
        load_parse_config(_write(tmp_path, bad))


def test_缺密钥在启动时即报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALIBABA_CLOUD_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "secret")
    with pytest.raises(ParseConfigError, match="ALIBABA_CLOUD_ACCESS_KEY_ID"):
        require_credentials()


def test_密钥齐全时返回二元组(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "sk")
    assert require_credentials() == ("ak", "sk")


def test_不是合法_yaml_报错(tmp_path: Path) -> None:
    with pytest.raises(ParseConfigError, match="YAML"):
        load_parse_config(_write(tmp_path, "endpoint: [未闭合"))


def test_顶层不是键值对报错(tmp_path: Path) -> None:
    with pytest.raises(ParseConfigError, match="键值对"):
        load_parse_config(_write(tmp_path, "- 一\n- 二\n"))


def test_缺_options_段报错(tmp_path: Path) -> None:
    lines = _OK_YAML.strip().splitlines()
    without_options = "\n".join(lines[: lines.index("options:")])
    with pytest.raises(ParseConfigError, match="options"):
        load_parse_config(_write(tmp_path, without_options))
