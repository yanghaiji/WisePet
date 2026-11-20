"""版本读取与比较的纯逻辑（自动更新用）。

版本源优先级：包内 version.txt（打包冻结时生成）→ package __version__ →
项目根 pyproject.toml（源码运行）。比较用轻量数字 tuple，不引三方依赖。
"""
from __future__ import annotations

import re
from pathlib import Path

#: 无版本文件时的兜底（若打包脚本未生成，也不至于显示 0.0.0 误报有更新）
FALLBACK_VERSION = "3.3.0"


def parse_version(text: str) -> tuple[int, ...]:
    """'v3.3.0' / '3.3.1' / '3.4' → (3,3,0)；忽略非数字后缀。"""
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:4]) if nums else (0,)


def is_newer(candidate: str, current: str) -> bool:
    """candidate 是否严格新于 current（缺失位补 0）。"""
    c, cur = parse_version(candidate), parse_version(current)
    width = max(len(c), len(cur))
    c += (0,) * (width - len(c))
    cur += (0,) * (width - len(cur))
    return c > cur


def read_current_version(runtime_root, package_version: str = "") -> str:
    """按优先级读当前版本：version.txt → 包版本 → pyproject.toml → 兜底。"""
    root = Path(runtime_root)
    version_file = root / "version.txt"
    try:
        text = version_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    if package_version and parse_version(package_version) != (0,):
        return package_version
    pyproject = root / "pyproject.toml"
    try:
        match = re.search(r'^version\s*=\s*"([^"]+)"',
                          pyproject.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return match.group(1)
    except OSError:
        pass
    return FALLBACK_VERSION
