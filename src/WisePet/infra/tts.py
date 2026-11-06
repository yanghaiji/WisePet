"""本地 TTS（语音说话）：Windows System.Speech / macOS say / 其它平台空实现。

跨平台差异收敛在本模块，app/ui 只依赖 `SpeechSynthesizer.speak(text)` 一个动作。
所有实现都是「尽力而为」：语音引擎缺失/调用失败都静默降级，绝不带崩主程序。
文本一律走 argv 或 stdin 传入，绝不拼进 shell 命令（防注入）。
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

#: 朗读文本上限：太长会长时间占用后台线程，截断保证「说到重点」
MAX_SPEAK_CHARS = 600


class NullSynthesizer:
    """不支持的平台 / 关闭语音时的实现：speak 什么也不做。"""

    def speak(self, text: str, *, volume: int = 100, rate: int = 0) -> None:
        return None


class WindowsSynthesizer:
    """PowerShell + System.Speech（SAPI 5），免第三方依赖、免联网。

    文本走 stdin 传入，避免引号/特殊字符破坏命令；Volume 0~100、Rate -10~10。
    """

    _CMD = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.Volume={volume};"
        "$s.Rate={rate};"
        "$s.Speak([Console]::In.ReadToEnd())"
    )

    def __init__(self, runner=subprocess.run):
        self._run = runner

    def speak(self, text: str, *, volume: int = 100, rate: int = 0) -> None:
        text = str(text or "").strip()
        if not text:
            return
        cmd = self._CMD.format(volume=int(volume), rate=int(rate))
        try:
            self._run(["powershell", "-NoProfile", "-NonInteractive",
                       "-Command", cmd],
                      input=text.encode("utf-8"), timeout=30,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      creationflags=_hide_window_flag())
        except Exception:  # noqa: BLE001 - 语音尽力而为，失败只记日志
            logger.debug("windows tts failed", exc_info=True)


class MacSynthesizer:
    """macOS 自带 `say`：Rate 折算为每分钟词数，音量走内置 `[[volm]]` 指令。"""

    def __init__(self, runner=subprocess.run):
        self._run = runner

    def speak(self, text: str, *, volume: int = 100, rate: int = 0) -> None:
        text = str(text or "").strip()
        if not text:
            return
        wpm = max(80, 175 + int(rate) * 8)               # rate -10..10 → 约 95..255
        spoken = f"[[volm: {max(0, min(100, int(volume)) / 100.0):.2f}]]{text}"
        try:
            self._run(["say", "-r", str(wpm), "--", spoken], timeout=30)
        except Exception:  # noqa: BLE001 - 语音尽力而为
            logger.debug("macos tts failed", exc_info=True)


def create_synthesizer(platform: str | None = None, *, enabled: bool = True):
    """按平台装配朗读器（组装根调用）；未启用或不支持的返回 NullSynthesizer。"""
    if not enabled:
        return NullSynthesizer()
    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        return WindowsSynthesizer()
    if plat == "darwin":
        return MacSynthesizer()
    return NullSynthesizer()


def _hide_window_flag() -> int:
    """Windows 下不弹出 PowerShell 黑窗；非 Windows 返回 0。"""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def truncate(text: str) -> str:
    """朗读前统一截断（气泡/周报共用）。"""
    return str(text or "")[:MAX_SPEAK_CHARS]


#: 表情/符号码位：象形文字、杂项符号与装饰符、变体选择符、零宽连接符等。
#: 语音引擎读不出这些「表情包」，念出来只会念成乱码或多余停顿，朗读前剔除。
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # 象形文字/表情/交通/补充象形（含国旗区域指示符）
    "\u2600-\u27BF"          # 杂项符号 + 装饰符（☀❤✨ 等）
    "\u2B00-\u2BFF"          # 箭头/星形符号
    "\u2190-\u21FF"          # 方向箭头
    "\u2B06\u20E3"           # 组合键帽
    "\uFE00-\uFE0F"          # 变体选择符（emoji 呈现修饰）
    "\u200D"                 # 零宽连接符（组合 emoji）
    "]"
)


def strip_emoji(text: str) -> str:
    """朗读前去掉表情符号，并把因删除产生的多余空白收敛为单个空格。"""
    cleaned = _EMOJI_RE.sub("", str(text or ""))
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()
