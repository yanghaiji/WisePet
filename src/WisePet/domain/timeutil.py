"""时间工具：夜间窗口判定（跨午夜回绕安全）。"""
from __future__ import annotations


def is_in_night(hour: int, start_hour: int, end_hour: int) -> bool:
    """判断 hour 是否处于 [start_hour, end_hour) 夜间窗口，支持跨午夜。"""
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour
