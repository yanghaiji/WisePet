"""自动更新网关：查 GitHub Releases / 解析资产 / 下载安装包。

设计底线（AGENT §2.9）：复用 web_search 的合并信任链 HTTP 客户端，零新依赖；
所有网络操作在调用方后台线程执行，不阻塞 UI。失败一律降级为空 / None，
绝不因更新检查影响桌宠主流程。安装阶段不静默自替换运行中的程序——下载后
交组装根打开安装包（Windows setup.exe/zip、macOS dmg），由用户/系统完成安装。
"""
from __future__ import annotations

import logging
import platform
from dataclasses import dataclass

from ..domain.version import is_newer
from .web_search import _HttpClient

logger = logging.getLogger(__name__)

API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
UPDATE_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class ReleaseInfo:
    """一条可用的新版本发布信息。"""
    version: str
    notes: str
    asset_name: str
    download_url: str
    page_url: str


def parse_release(data: dict, current_version: str, repo: str) -> ReleaseInfo | None:
    """从 GitHub releases/latest 响应里挑当前平台资产；无更新返回 None。

    平台资产匹配：Windows 优先 setup.exe，其次 zip；macOS 选 dmg；架构 x64/arm64。
    """
    tag = str(data.get("tag_name") or "")
    if not tag or not is_newer(tag, current_version):
        return None
    if str(data.get("draft") or "").lower() == "true" \
            or str(data.get("prerelease") or "").lower() == "true":
        return None
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "arm64" if "arm" in machine or "aarch64" in machine else "x64"
    assets = data.get("assets") or []
    chosen = None
    for asset in assets:
        name = str(asset.get("name") or "")
        low = name.lower()
        if system == "windows":
            if "windows" not in low and ".exe" not in low and ".zip" not in low:
                continue
            if "setup" in low and low.endswith(".exe"):
                chosen = asset                      # Windows 首选安装包
                break
            if low.endswith(".zip") and chosen is None:
                chosen = asset
        elif system == "darwin":
            if low.endswith(".dmg") and "macos" in low:
                if arch in low or "universal" in low or chosen is None:
                    chosen = asset
        else:
            if low.endswith((".zip", ".tar.gz")) and chosen is None:
                chosen = asset
    if chosen is None:
        logger.info("update release %s has no asset for %s/%s", tag, system, arch)
        return None
    page = str(data.get("html_url")
               or f"https://github.com/{repo}/releases/tag/{tag}")
    return ReleaseInfo(
        version=tag,
        notes=str(data.get("body") or "")[:600],
        asset_name=str(chosen.get("name") or ""),
        download_url=str(chosen.get("browser_download_url") or ""),
        page_url=page)


class UpdateService:
    """repo_provider: () -> 'owner/repo'；current_version: 当前版本号字符串。"""

    def __init__(self, repo_provider, current_version: str, http_client=None):
        self._repo_provider = repo_provider
        self._current = current_version
        self._client = http_client

    def _http(self):
        return self._client or _HttpClient.get()

    def repo(self) -> str:
        return str(self._repo_provider() or "").strip().strip("/")

    def check(self) -> ReleaseInfo | None:
        """后台线程调用：查最新发布；无配置/无更新/失败均返回 None。"""
        repo = self.repo()
        if not repo or "/" not in repo:
            return None
        try:
            resp = self._http().get(
                API_LATEST.format(repo=repo), timeout=UPDATE_TIMEOUT_S,
                headers={"Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            return parse_release(resp.json(), self._current, repo)
        except Exception as exc:  # noqa: BLE001 - 更新检查失败静默
            logger.info("update check skipped: %s", exc)
            return None

    def download(self, release: ReleaseInfo, dest_dir) -> str:
        """后台线程调用：流式下载安装包到 dest_dir，返回落盘路径；失败返回空串。"""
        from pathlib import Path
        dest = Path(dest_dir)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / release.asset_name
            with self._http().stream(
                    "GET", release.download_url, timeout=UPDATE_TIMEOUT_S) as resp:
                resp.raise_for_status()
                with target.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                        fh.write(chunk)
            logger.info("update downloaded: %s", target)
            return str(target)
        except Exception as exc:  # noqa: BLE001 - 下载失败静默
            logger.info("update download failed: %s", exc)
            return ""
