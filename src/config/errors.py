"""配置层异常。

单独成模块是为了打断 compiler ↔ rubric_validation 的循环导入：compiler 要调校验，
校验要抛这个异常。"""

from __future__ import annotations


class ConfigCompileError(Exception):
    """加载或校验配置失败时抛出。"""
