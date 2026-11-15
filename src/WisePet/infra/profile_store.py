"""本地身份（profile）：同一台电脑多个人各用各的宠物，零登录零服务器。

- 目录布局：`~/.WisePet/` 是「默认身份」的数据目录（老用户零迁移）；
  新建身份放在 `~/.WisePet/profiles/<名字>/`，全套数据（配置/SQLite/记忆/成长）隔离；
- `active_profile.txt` 只记「下次启动用哪个身份」，切换后重启生效；
- 不同身份的单实例守卫端口按名字派生：同身份互斥、不同身份可以并行；
- 可选 PIN 码（v1.32）：scrypt 哈希落盘、连续错 3 次锁 30 秒。诚实边界——
  PIN 防「误入/随手翻看」，防不了绕过界面直接读文件的懂行人（那需要数据加密）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
import zlib
from pathlib import Path

logger = logging.getLogger(__name__)

PROFILE_DIR_NAME = "profiles"
ACTIVE_FILE_NAME = "active_profile.txt"
PIN_FILE_NAME = "pin.json"
#: 名字只允许中英文、数字、下划线连字符；长度 ≤ 24（文件系统与显示都友好）
_NAME_RE = re.compile(r"^[\w\u4e00-\u9fa5-]{1,24}$")
MAX_PROFILES = 20

#: PIN 规则：4~12 位数字（家庭场景好记；不存明文只存 scrypt 哈希）
_PIN_RE = re.compile(r"^\d{4,12}$")
PIN_MAX_FAILS = 3
PIN_LOCK_SECONDS = 30.0
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _DKLEN = 16384, 8, 1, 32


def safe_name(name: str) -> str:
    """清洗身份名：拒绝路径分隔符与非法字符，保证只落到 profiles/ 一层。"""
    value = str(name or "").strip()
    if not _NAME_RE.match(value):
        raise ValueError("身份名只能是中英文、数字、下划线或连字符（≤24 字）")
    return value


def guard_port_for(name: str, base_port: int = 45789) -> int:
    """按身份名派生单实例守卫端口：同身份互斥，不同身份可并行。"""
    if not name:
        return base_port
    crc = zlib.crc32(name.encode("utf-8"))
    return base_port + 1 + (crc % 999)


class ProfileStore:
    def __init__(self, base_dir: Path | str):
        self._base = Path(base_dir)
        self._root = self._base / PROFILE_DIR_NAME

    @property
    def root(self) -> Path:
        return self._root

    def list(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.name for p in self._root.iterdir() if p.is_dir())

    def active(self) -> str:
        """下次启动使用的身份；空 = 默认身份（base_dir 根目录）。"""
        try:
            return self._read_active().strip()
        except (OSError, UnicodeDecodeError):
            return ""

    def set_active(self, name: str) -> str:
        value = safe_name(name)
        self.ensure(value)                   # 身份目录先落位，list() 才能看到
        self._base.mkdir(parents=True, exist_ok=True)
        (self._base / ACTIVE_FILE_NAME).write_text(value + "\n", encoding="utf-8")
        logger.info("active profile set: %s (effective next start)", value)
        return value

    def clear_active(self) -> None:
        """切回默认身份（active 置空）。"""
        self._base.mkdir(parents=True, exist_ok=True)
        (self._base / ACTIVE_FILE_NAME).write_text("\n", encoding="utf-8")
        logger.info("active profile cleared (default identity next start)")

    def remove(self, name: str, pin: str = "") -> bool:
        """删除自建身份的整个数据目录（不可恢复）。

        设了 PIN 的身份必须先通过验证；默认身份（base_dir 根目录）不可删除。
        调用方负责二次确认（UI 要求完整输入身份名）。
        """
        value = str(name or "").strip()
        if not value or not (self._root / value).is_dir():
            return False                        # 默认身份/不存在的身份
        ok, _remain = self.verify_pin(value, pin) if self.has_pin(value) \
            else (True, 0.0)
        if not ok:
            logger.warning("profile remove denied (pin): %s", value)
            return False
        import shutil
        shutil.rmtree(self._root / value)
        if self.active() == value:
            self.clear_active()
        logger.info("profile removed: %s", value)
        return True

    def ensure(self, name: str) -> Path:
        """确认身份目录存在并返回其数据目录；不复制任何旧数据。"""
        value = safe_name(name)
        target = self._root / value
        target.mkdir(parents=True, exist_ok=True)
        return target

    def data_dir_for(self, name: str) -> Path:
        """身份对应的数据目录：默认身份 = base_dir 根目录（零迁移兼容老用户）。"""
        return self.ensure(name) if name else self._base

    def pet_info(self, name: str) -> tuple[str, str]:
        """身份的宠物名与皮肤（读该目录的 config.json；坏/缺文件容错回落）。"""
        config_path = self.data_dir_for(name) / "config.json"
        pet_name, skin = "", ""
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                pet_name = str((data.get("persona") or {}).get("name") or "")
                skin = str((data.get("pet") or {}).get("skin") or "")
        except (OSError, ValueError):
            pass
        return (pet_name or name or "默认身份", skin or "cat")

    def _write_pin_state(self, name: str, state: dict) -> None:
        try:
            self._pin_path(name).write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            logger.exception("pin state save failed")

    def is_manager(self, name: str) -> bool:
        """管理员 = 默认身份（最早使用、不可删除的那只）。

        管理动作（共享 AI 给全家）只有管理员能做。诚实边界：这是本机 UI
        约定，不是服务端权限——防误操作，防不了绕过界面的人。
        """
        return not str(name or "").strip()

    def _read_active(self) -> str:
        return (self._base / ACTIVE_FILE_NAME).read_text(encoding="utf-8")

    # ---------- PIN 验证（v1.32） ----------

    @staticmethod
    def valid_pin(pin: str) -> bool:
        return bool(_PIN_RE.match(str(pin or "").strip()))

    def _pin_path(self, name: str) -> Path:
        return self.data_dir_for(name) / PIN_FILE_NAME

    @staticmethod
    def _pin_digest(pin: str, salt: str) -> str:
        return hashlib.scrypt(str(pin).encode("utf-8"), salt=salt.encode("utf-8"),
                              n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                              dklen=_DKLEN).hex()

    def _read_pin_state(self, name: str) -> dict:
        try:
            data = json.loads(self._pin_path(name).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def has_pin(self, name: str) -> bool:
        return bool(self._read_pin_state(name).get("hash"))

    def set_pin(self, name: str, pin: str) -> bool:
        """为身份设置/更换 PIN（不存明文，只存 scrypt 哈希）。"""
        pin = str(pin or "").strip()
        if not self.valid_pin(pin):
            return False
        state = {"salt": secrets.token_hex(16),
                 "hash": self._pin_digest(pin, ""),
                 "failed": 0, "locked_until": 0.0}
        state["hash"] = self._pin_digest(pin, state["salt"])
        self._base.mkdir(parents=True, exist_ok=True)
        try:
            self._pin_path(name).write_text(
                json.dumps(state), encoding="utf-8")
        except OSError:
            logger.exception("pin save failed")
            return False
        return True

    def clear_pin(self, name: str, old_pin: str) -> bool:
        """解除 PIN（必须先验证旧 PIN——防别人顺手解掉）。"""
        ok, _remain = self.verify_pin(name, old_pin)
        if not ok:
            return False
        try:
            self._pin_path(name).unlink(missing_ok=True)
        except OSError:
            logger.exception("pin clear failed")
            return False
        return True

    def reset_pin(self, name: str) -> bool:
        """管理员重置：直接清除身份的 PIN（下次进入引导重新设置）。

        仅限自建身份；调用方（管理员入口）应先验证管理员自己的 PIN。
        """
        value = str(name or "").strip()
        if not value or not (self._root / value).is_dir():
            return False
        try:
            self._pin_path(value).unlink(missing_ok=True)
            logger.info("pin reset by manager: %s", value)
            return True
        except OSError:
            logger.exception("pin reset failed")
            return False

    def verify_pin(self, name: str, pin: str) -> tuple[bool, float]:
        """验证 PIN；返回 (是否通过, 还需等待的秒数)。

        连续错 PIN_MAX_FAILS 次锁 PIN_LOCK_SECONDS 秒（锁定状态落盘，
        重启也不能绕过冷却）。
        """
        pin = str(pin or "").strip()
        state = self._read_pin_state(name)
        if not state.get("hash"):
            return True, 0.0                    # 未设 PIN = 不验证
        now = time.time()
        locked_until = float(state.get("locked_until") or 0.0)
        if now < locked_until:
            return False, locked_until - now
        if self._pin_digest(pin, str(state.get("salt") or "")) == state.get("hash"):
            if state.get("failed"):
                state["failed"] = 0
                self._write_pin_state(name, state)
            return True, 0.0
        failed = int(state.get("failed") or 0) + 1
        state["failed"] = failed
        if failed >= PIN_MAX_FAILS:
            state["locked_until"] = now + PIN_LOCK_SECONDS
            state["failed"] = 0
            logger.warning("pin locked for identity %s (%ss)", name, PIN_LOCK_SECONDS)
        self._write_pin_state(name, state)
        remain = max(0.0, locked_until - now)
        return False, remain
