"""序列帧素材加载：assets/pet/{skin}/{action}/*.png（PRD M1 / US-1 降级 / v1.2 多皮肤 / v2.0 C12）。"""
from __future__ import annotations

import logging
from pathlib import Path

from ..domain.actions import ACTION_NAMES, LEGACY_ACTION_NAMES, SIMILAR_FALLBACK
from ..domain.config_model import DEFAULT_SKIN, PET_SKINS, normalize_skin

logger = logging.getLogger(__name__)

#: 与 BehaviorState 一一对应的动作目录名：单一真相源在 domain.actions，本处只引用
ACTIONS = frozenset(ACTION_NAMES)
_IMAGE_SUFFIXES = {".png"}


class FrameAssetLoader:
    """按皮肤解析动作帧；该皮肤缺帧时回落默认皮肤，避免换形象后变成占位圆脸。"""

    def __init__(self, assets_dir: Path | str, skin: str = DEFAULT_SKIN):
        self._dir = Path(assets_dir)
        self._skin = normalize_skin(skin)

    @property
    def skin(self) -> str:
        """已归一的当前皮肤 id。"""
        return self._skin

    def _action_dir(self, action: str, skin: str) -> Path:
        return self._dir / "pet" / skin / action

    @staticmethod
    def _list(directory: Path) -> list[Path]:
        """列目录内合法帧文件，按文件名排序；目录不存在返回空。"""
        if not directory.is_dir():
            return []
        return sorted(
            (p for p in directory.iterdir()
             if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES),
            key=lambda p: p.name,
        )

    def frames(self, action: str) -> list[Path]:
        """按文件名排序返回帧路径；本皮肤→默认皮肤→相似动作都借不到时返回空列表。"""
        for skin in dict.fromkeys((self._skin, DEFAULT_SKIN)):  # 去重且保序
            frames = self._list(self._action_dir(action, skin))
            if frames:
                if skin != self._skin:
                    logger.info("skin %s lacks action=%s, falling back to %s",
                                self._skin, action, DEFAULT_SKIN)
                return frames
        return self._borrow_similar(action)

    def _borrow_similar(self, action: str) -> list[Path]:
        """缺帧时借相似动作：v2.0 新动作在旧素材上不该退化成占位圆脸（PRD C12）。"""
        twin = SIMILAR_FALLBACK.get(action)
        if not twin:
            return []
        for skin in dict.fromkeys((self._skin, DEFAULT_SKIN)):
            frames = self._list(self._action_dir(twin, skin))
            if frames:
                logger.info("action=%s has no frames, borrowing similar action %s (skin=%s)",
                            action, twin, skin)
                return frames
        return []

    def is_available(self, action: str) -> bool:
        return bool(self.frames(action))

    def available_skins(self) -> list[str]:
        """核心动作素材齐全的皮肤 id（按 PET_SKINS 序），供设置窗只列可用项。

        只校验 v1.x 就有的核心 8 动作：v2.0 新增动作可以让新素材晚到一步，
        但不能因此把现有三套素材整皮判为“未安装”（设置窗皮肤下拉会变空）。
        """
        return [skin for skin in PET_SKINS
                if all(self._list(self._action_dir(action, skin))
                       for action in sorted(LEGACY_ACTION_NAMES))]

    def missing_actions(self) -> list[str]:
        """供启动日志提示素材完整度（按当前有效解析结果，含回落）。"""
        missing = sorted(a for a in ACTIONS if not self.is_available(a))
        if missing:
            logger.warning("pet assets missing for actions: %s (fallback to placeholder)",
                           ",".join(missing))
        return missing
