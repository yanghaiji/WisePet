"""macOS 打包链路的可选签名/公证开关：配了才触发，没配绝不碰 xcrun。

这些只在 GitHub Actions 的 macos runner + 用户配置的 secrets 下才真正执行，
本机/offscreen 无法跑 macOS 工具链，这里只验证「开关与凭据判定」这一纯逻辑层。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _pkg():
    return importlib.import_module("tools.package")


def test_notarize_skips_without_flag(monkeypatch, tmp_path):
    pkg = _pkg()
    calls: list = []
    monkeypatch.setattr(pkg, "run", lambda *a, **k: calls.append(a))
    monkeypatch.delenv("WISEPET_NOTARIZE", raising=False)
    pkg.notarize_dmg(tmp_path / "x.dmg", dry_run=False)
    assert calls == []


def test_notarize_skips_when_flag_set_but_no_apple_creds(monkeypatch, tmp_path):
    pkg = _pkg()
    calls: list = []
    monkeypatch.setattr(pkg, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("WISEPET_NOTARIZE", "1")
    monkeypatch.delenv("APPLE_ID", raising=False)
    monkeypatch.delenv("APPLE_APP_PASSWORD", raising=False)
    monkeypatch.delenv("APPLE_TEAM_ID", raising=False)
    pkg.notarize_dmg(tmp_path / "x.dmg", dry_run=False)
    assert calls == []


def test_notarize_runs_when_flag_and_creds_present(monkeypatch, tmp_path):
    pkg = _pkg()
    calls: list = []
    monkeypatch.setattr(pkg, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("WISEPET_NOTARIZE", "1")
    monkeypatch.setenv("APPLE_ID", "me@example.com")
    monkeypatch.setenv("APPLE_APP_PASSWORD", "app-specific-pw")
    monkeypatch.setenv("APPLE_TEAM_ID", "TEAM123")
    pkg.notarize_dmg(tmp_path / "x.dmg", dry_run=False)
    assert len(calls) == 2
    assert "notarytool" in calls[0][0]
    assert "stapler" in calls[1][0]


def test_sign_dmg_skips_without_identity(monkeypatch, tmp_path):
    pkg = _pkg()
    calls: list = []
    monkeypatch.setattr(pkg, "run", lambda *a, **k: calls.append(a))
    monkeypatch.delenv("WISEPET_CODESIGN_IDENTITY", raising=False)
    pkg.sign_dmg(tmp_path / "x.dmg", dry_run=False)
    assert calls == []


def test_sign_dmg_runs_with_identity(monkeypatch, tmp_path):
    pkg = _pkg()
    calls: list = []
    monkeypatch.setattr(pkg, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("WISEPET_CODESIGN_IDENTITY", "Developer ID Application: X (TEAM)")
    pkg.sign_dmg(tmp_path / "x.dmg", dry_run=False)
    assert len(calls) == 1 and "codesign" in calls[0][0]
