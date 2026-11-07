"""打包程序自检：Qt 裁剪白名单、产物校验、历史目录清理、压缩包分级。

只做静态/文件级验证，不真的调用 PyInstaller（CI 与真机负责冻结）。
"""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("wisepet_package", ROOT / "tools" / "package.py")
pkg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pkg)


def _healthy_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "WisePet"
    (bundle / "assets" / "pet" / "cat" / "idle").mkdir(parents=True)
    (bundle / "WisePet.exe").write_bytes(b"exe")
    (bundle / "assets" / "pet" / "cat" / "idle" / "01.png").write_bytes(b"png")
    return bundle


def should_exclude_unused_qt_modules_but_keep_pptx_dependencies():
    assert "PySide6.QtQml" in pkg.EXCLUDE_MODULES
    assert "PySide6.QtWebEngineCore" in pkg.EXCLUDE_MODULES
    # python-pptx（工具执行层）传递依赖，排除会让「生成 PPT」在打包版里静默坏掉
    assert not any(name in pkg.EXCLUDE_MODULES for name in ("lxml", "PIL", "pptx"))


def should_fail_verification_when_exe_or_assets_are_missing(tmp_path):
    report = pkg.verify_bundle(tmp_path / "WisePet", "windows")
    assert report["ok"] is False
    assert report["exe"] is False and report["assets"] is False


def should_pass_verification_for_a_healthy_bundle(tmp_path):
    report = pkg.verify_bundle(_healthy_bundle(tmp_path), "windows")
    assert report["ok"] is True and report["files"] == 2


def should_fail_verification_when_file_count_exceeds_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(pkg, "MAX_BUNDLE_FILES", 1)
    report = pkg.verify_bundle(_healthy_bundle(tmp_path), "windows")
    assert report["ok"] is False and report["files"] == 2


def should_clean_stale_extracted_dirs_and_all_old_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(pkg, "DIST", tmp_path / "dist")
    monkeypatch.setattr(pkg, "TMP", tmp_path / "tmp")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "WisePet-v1.8" / "assets").mkdir(parents=True)
    (dist / "WisePet-v1.8" / "assets" / "a.txt").write_text("old", encoding="utf-8")
    (dist / "WisePet-0.0.1-windows-x64.zip").write_bytes(b"old zip")
    (dist / "WisePet-0.1.0-windows-x64-setup.exe").write_bytes(b"old exe")

    pkg.clean("0.1.0")

    assert list(dist.glob("WisePet-*")) == []


def should_store_already_compressed_images_without_deflating(tmp_path, monkeypatch):
    monkeypatch.setattr(pkg, "DIST", tmp_path)
    bundle = _healthy_bundle(tmp_path)
    (bundle / "notes.txt").write_text("hello" * 50, encoding="utf-8")

    archive = pkg.zip_bundle(bundle, "9.9.9", dry_run=False)

    with zipfile.ZipFile(archive) as zf:
        kinds = {Path(i.filename).name: i.compress_type for i in zf.infolist()}
    assert kinds["01.png"] == zipfile.ZIP_STORED
    assert kinds["notes.txt"] == zipfile.ZIP_DEFLATED
