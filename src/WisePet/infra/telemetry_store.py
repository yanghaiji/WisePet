"""本地埋点仓储：~/.WisePet/telemetry.json。

隐私底线：只存「事件名 + 按天计数 + 首次发生日」，**从不记录消息内容、
API Key、文件路径或用户画像，也从不上传**。用户可一键清空，数据只保留近 30 天。
复用 json_store 的原子写与损坏备份。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from ..domain.chat_record import day_key
from .json_store import atomic_write, load_json_or_backup_reset

TELEMETRY_FILE_NAME = "telemetry.json"
CRASH_FILE_NAME = "crash.log"


def default_payload(now: float | None = None) -> dict:
    """全新安装的初始载荷；install_id 只用于本机去重，不含任何身份信息。"""
    return {"install_id": uuid.uuid4().hex[:12],
            "first_seen": day_key(now), "days": {}, "firsts": {}}


class TelemetryStore:
    def __init__(self, data_dir: Path | str):
        self._path = Path(data_dir) / TELEMETRY_FILE_NAME
        self._crash_path = Path(data_dir) / CRASH_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    @property
    def crash_path(self) -> Path:
        return self._crash_path

    def load(self, now: float | None = None) -> dict:
        data = load_json_or_backup_reset(self._path)
        if not data:
            return default_payload(now)
        data.setdefault("install_id", uuid.uuid4().hex[:12])
        data.setdefault("first_seen", day_key(now))
        data.setdefault("days", {})
        data.setdefault("firsts", {})
        return data

    def save(self, data: dict) -> None:
        atomic_write(self._path, data)

    def reset(self, now: float | None = None) -> dict:
        """一键清空：由 UI 调用，app 层不必反向 import infra。"""
        data = default_payload(now)
        self.save(data)
        return data

    def append_crash(self, text: str) -> None:
        """崩溃明细单独追加，不进埋点 JSON（避免堆栈撑大结构化数据）。"""
        try:
            self._crash_path.parent.mkdir(parents=True, exist_ok=True)
            with self._crash_path.open("a", encoding="utf-8") as handle:
                handle.write(text.rstrip() + "\n\n")
        except OSError:
            pass                      # 崩溃兜底本身绝不能再次抛出异常
