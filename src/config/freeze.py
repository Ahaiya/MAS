"""
配置冻结工具，负责计算配置内容哈希并形成可追踪的版本闭包。

Config Freeze：用于版本闭包的内容哈希工具。

为以下内容提供确定性、与顺序无关的哈希计算：
- 单个 artifact 文件内容（SHA-256）
- 合并后的 bundle 哈希（先排序，再 SHA-256）

这些哈希保证了可重放安全性：只要配置文件相同，
系统总会生成相同的冻结 bundle 哈希。"""

import hashlib
from typing import Sequence


def compute_content_hash(content: str) -> str:
    """计算文件内容字符串的 SHA-256 哈希。
    
        Args:
            content: 待哈希的原始文本内容。
    
        Returns:
            64 个字符的小写 hex string（SHA-256 digest）。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_bundle_hash(content_hashes: Sequence[str]) -> str:
    """计算一个确定性、与顺序无关的合并哈希。
    
        在合并之前对输入哈希进行排序，从而保证无论产物加载顺序如何，
        bundle 哈希都保持稳定。
    
        Args:
            content_hashes: 各个 artifact 内容哈希的序列。
    
        Returns:
            64 个字符的小写 hex string（SHA-256 digest）。"""
    sorted_hashes = sorted(content_hashes)
    combined = "\n".join(sorted_hashes)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
