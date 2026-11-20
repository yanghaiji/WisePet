"""自动更新回归：版本读取/比较、Release 解析、下载、配置往返。"""
from __future__ import annotations

from types import SimpleNamespace

from WisePet.domain.config_model import AppConfig
from WisePet.domain.version import (
    FALLBACK_VERSION, is_newer, parse_version, read_current_version,
)
from WisePet.infra.update_service import (
    UpdateService, parse_release,
)


# ---------- 版本纯逻辑 ----------

def test_parse_version():
    assert parse_version("v3.3.0") == (3, 3, 0)
    assert parse_version("3.4") == (3, 4)
    assert parse_version("v3.3.1-beta") == (3, 3, 1)
    assert parse_version("") == (0,)


def test_is_newer():
    assert is_newer("v3.4.0", "3.3.0") is True
    assert is_newer("3.3.1", "v3.3.0") is True
    assert is_newer("3.3.0", "v3.3.0") is False
    assert is_newer("3.2.9", "3.3.0") is False
    assert is_newer("v4", "3.99.99") is True


def test_read_current_version_priority(tmp_path):
    # version.txt 优先
    (tmp_path / "version.txt").write_text("3.5.0", encoding="utf-8")
    assert read_current_version(tmp_path, "9.9.9") == "3.5.0"
    # 无文件用包版本
    (tmp_path / "version.txt").unlink()
    assert read_current_version(tmp_path, "3.3.0") == "3.3.0"
    # 都没有读 pyproject
    (tmp_path / "pyproject.toml").write_text('version = "3.2.1"\n', encoding="utf-8")
    assert read_current_version(tmp_path, "") == "3.2.1"
    assert read_current_version(tmp_path / "nope", "") == FALLBACK_VERSION


# ---------- Release 解析 ----------

def _release(tag="v3.4.0", assets=None, **kw):
    data = {"tag_name": tag, "html_url": "https://github.com/o/r/releases/tag/v3.4.0",
            "body": "新功能", "assets": assets or []}
    data.update(kw)
    return data


def test_parse_release_picks_windows_installer(monkeypatch):
    monkeypatch.setattr("WisePet.infra.update_service.platform.system", lambda: "Windows")
    monkeypatch.setattr("WisePet.infra.update_service.platform.machine", lambda: "AMD64")
    data = _release(assets=[
        {"name": "WisePet-3.4.0-windows-x64.zip", "browser_download_url": "u/zip"},
        {"name": "WisePet-3.4.0-windows-x64-setup.exe", "browser_download_url": "u/exe"},
    ])
    info = parse_release(data, "3.3.0", "o/r")
    assert info is not None and info.version == "v3.4.0"
    assert info.asset_name.endswith("setup.exe") and info.download_url == "u/exe"
    assert info.notes == "新功能"


def test_parse_release_skips_when_no_new_version(monkeypatch):
    monkeypatch.setattr("WisePet.infra.update_service.platform.system", lambda: "Windows")
    data = _release(tag="v3.3.0", assets=[
        {"name": "WisePet-3.3.0-windows-x64.zip", "browser_download_url": "u"}])
    assert parse_release(data, "3.3.0", "o/r") is None       # 同版本
    assert parse_release(data, "3.4.0", "o/r") is None       # 更旧


def test_parse_release_skips_draft_and_no_asset(monkeypatch):
    monkeypatch.setattr("WisePet.infra.update_service.platform.system", lambda: "Windows")
    data = _release(draft="true", assets=[
        {"name": "WisePet-3.4.0-windows-x64.zip", "browser_download_url": "u"}])
    assert parse_release(data, "3.3.0", "o/r") is None       # 草稿跳过
    assert parse_release(_release(assets=[]), "3.3.0", "o/r") is None  # 无资产


# ---------- 服务层 ----------

def test_update_service_check_and_download():
    calls = []

    class FakeClient:
        def get(self, url, **kw):
            calls.append(("get", url))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: _release(
                assets=[{"name": "WisePet-3.4.0-windows-x64.zip",
                         "browser_download_url": "u/zip"}]))

        def stream(self, method, url, **kw):
            calls.append(("stream", url))
            class Resp:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def raise_for_status(self): pass
                def iter_bytes(self, chunk_size):
                    yield b"zipdata"
            return Resp()

    svc = UpdateService(lambda: "o/r", "3.3.0", http_client=FakeClient())
    info = svc.check()
    assert info is not None and info.version == "v3.4.0"
    path = svc.download(info, "tmp/test-updates")
    assert path.endswith("WisePet-3.4.0-windows-x64.zip")
    assert ("stream", "u/zip") in calls


def test_update_service_skips_without_repo():
    class NoCalls:
        def get(self, *a, **k):
            raise AssertionError("should not call without repo")
    svc = UpdateService(lambda: "", "3.3.0", http_client=NoCalls())
    assert svc.check() is None


# ---------- 配置往返 ----------

def test_update_settings_roundtrip_and_defaults():
    cfg = AppConfig.from_dict({"system": {"auto_check_update": True,
                                           "update_repo": "owner/WisePet"}})
    assert cfg.system.auto_check_update is True
    assert cfg.system.update_repo == "owner/WisePet"
    again = AppConfig.from_dict(cfg.to_dict())
    assert again.system.auto_check_update is True
    assert again.system.update_repo == "owner/WisePet"
    fresh = AppConfig()
    assert fresh.system.auto_check_update is False and fresh.system.update_repo == ""
