"""系统级精确定位桥：worker 线程阻塞等待，定位在主线程异步完成。

Qt Positioning 的信号需要事件循环，只能在主线程用；而 agent 工具循环跑在
AI worker 线程。桥复用授权弹窗同一套模式：信号排队到主线程 → 异步等待
positionUpdated/超时 → threading.Event 唤醒 worker。

坐标说明：Windows 定位（WiFi/GPS）产出 WGS84 坐标；国内地图服务（高德）
使用 GCJ-02，消费方需自行转换（见 geo_location.wgs84_to_gcj02）。
"""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QTimer, Signal

try:  # QtPositioning 属于 PySide6 Add-ons，部分精简安装没有这个模块
    from PySide6.QtPositioning import (QGeoPositionInfo,
                                       QGeoPositionInfoSource)
except ImportError:                       # 缺模块时降级为「系统定位不可用」
    QGeoPositionInfo = None
    QGeoPositionInfoSource = None

logger = logging.getLogger(__name__)


class PositionBridge(QObject):
    """request() 在任意线程可调，返回 (lat, lon) 或 None（不可用/超时）。"""

    _requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        if QGeoPositionInfoSource is not None:
            self._source = QGeoPositionInfoSource.createDefaultSource(self)
        if self._source is None:
            logger.info("no system position source (QtPositioning unavailable)")
        self._requested.connect(self._do_request)

    @property
    def available(self) -> bool:
        return self._source is not None

    def request(self, timeout_s: float = 10.0):
        """阻塞请求一次定位；成功返回 (lat, lon)，失败/超时返回 None。"""
        if self._source is None:
            return None
        box: dict = {}
        done = threading.Event()
        self._requested.emit((box, done, float(timeout_s)))
        done.wait(timeout_s + 3.0)   # 主线程处理排队也要时间，多留一点
        return box.get("pos")

    # ---------- 主线程侧 ----------

    def _do_request(self, payload) -> None:
        box, done, timeout_s = payload
        if self._source is None:
            done.set()
            return
        timer = QTimer(self)
        timer.setSingleShot(True)

        def finish(pos) -> None:
            if pos is not None:
                box["pos"] = pos
            done.set()

        def cleanup() -> None:
            for sig, slot in ((self._source.positionUpdated, on_pos),
                              (self._source.errorOccurred, on_err)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            timer.stop()
            timer.deleteLater()

        def on_pos(info: QGeoPositionInfo) -> None:
            if not info.isValid():
                return                  # 首个无效帧常见：继续等有效值
            coord = info.coordinate()
            if not coord.isValid():
                return
            cleanup()
            finish((coord.latitude(), coord.longitude()))

        def on_err(*_args) -> None:
            logger.info("system position error: %s", _args)
            cleanup()
            finish(None)

        def on_timeout() -> None:
            logger.info("system position request timed out")
            cleanup()
            finish(None)

        timer.timeout.connect(on_timeout)
        self._source.positionUpdated.connect(on_pos)
        self._source.errorOccurred.connect(on_err)
        timer.start(int(timeout_s * 1000))
        self._source.requestUpdate(int(timeout_s * 1000))
