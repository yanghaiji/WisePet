"""CI workflow 表达式的静态护栏：本机跑不了 actionlint，用纯标准库锁住已知的硬约束。

GitHub Actions 的 `if:` 只允许 github / needs / strategy / matrix / job /
runner / env / vars / steps / inputs —— **不含 secrets**。曾把
`secrets.MACOS_CERTIFICATE != ''` 写进 if: 导致整个 workflow 解析失败：
"Invalid workflow file ... Unrecognized named-value: 'secrets'"。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOW_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# 匹配 YAML 里以 `if:` 开头的行（可有前导空格；值可能是 `${{ ... }}` 或裸表达式）
_IF_LINE = re.compile(r"^\s*if:\s*(?P<expr>.*?)\s*(?:#.*)?$")


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def _if_expressions(path: Path) -> list[str]:
    exprs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _IF_LINE.match(line)
        if match:
            exprs.append(match.group("expr"))
    return exprs


def should_have_workflows_to_check():
    assert _workflow_files(), f"未找到 workflow 文件：{WORKFLOW_DIR}"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def should_balance_expression_delimiters(path):
    """所有 `${{ ... }}` 必须成对闭合（另一类常见的 workflow 解析失败）。"""
    text = path.read_text(encoding="utf-8")
    opened, closed = text.count("${{"), text.count("}}")
    assert opened == closed, (
        f"{path.name} 的表达式括号不配对：opened={opened}, closed={closed}"
    )


def should_keep_step_indentation_consistent():
    """step 列表缩进一致（本机无 YAML 解析器，做最小结构冒烟）。"""
    lines = (WORKFLOW_DIR / "build.yml").read_text(encoding="utf-8").splitlines()
    step_indents = {len(line) - len(line.lstrip())
                    for line in lines
                    if re.match(r"^\s*- (name|uses):", line)}
    assert len(step_indents) == 1, f"step 缩进层级不唯一：{step_indents}"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def should_not_reference_secrets_in_if(path):
    """`if:` 表达式里出现 secrets.* 会让 GitHub 直接判定 workflow 无效。"""
    for expr in _if_expressions(path):
        assert "secrets." not in expr, (
            f"{path.name} 的 if: 引用了 secrets context（GitHub Actions 不允许）：{expr}"
        )


def should_only_use_allowed_contexts_in_if():
    """顺带校验所有 if: 只使用允许的 context 前缀，防止下次写错别的。"""
    allowed = ("github.", "needs.", "strategy.", "matrix.", "job.", "runner.",
               "env.", "vars.", "steps.", "inputs.", "always(", "success(",
               "failure(", "cancelled(", "hashFiles(", "contains(", "startsWith(",
               "endsWith(", "fromJSON(", "toJSON(", "format(", "!",
               "true", "false", "null")
    bad = []
    for path in _workflow_files():
        for expr in _if_expressions(path):
            for token in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\.", expr):
                if f"{token}." not in allowed:
                    bad.append((path.name, expr, token))
    assert not bad, f"if: 出现不允许的 context：{bad}"


def should_not_inject_any_secrets_while_signing_disabled():
    """用户要求「先不启用密钥」：CI 不注入任何 secrets、不导入证书，打包走纯路径。

    这条是临时的产品决策，将来重新启用签名/公证时同步调整本断言即可。
    """
    build = (WORKFLOW_DIR / "build.yml").read_text(encoding="utf-8")
    assert "secrets." not in build
    assert "MACOS_CERTIFICATE" not in build
    assert "WISEPET_CODESIGN_IDENTITY" not in build
    assert "APPLE_ID" not in build


def should_emit_diagnostics_when_packaging_fails():
    """打包失败时打印 dist 清单与自检结果，便于从日志直接定位，而非靠 artifact 反推。"""
    build = (WORKFLOW_DIR / "build.yml").read_text(encoding="utf-8")
    assert "if: failure()" in build
    assert "--verify" in build
    assert "ls -lah dist/" in build
