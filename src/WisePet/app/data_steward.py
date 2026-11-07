"""本地数据管家：让「只存本机、绝不上传」配上「我随时能导出 / 删除 / 看占用」。

隐私承诺的另一半是**可携与可删**；此前只能按天翻记录，没有导出与删除入口。
全部读写都在 data_dir 内，导出路径由用户经命令面板触发后显式指定。
"""
from __future__ import annotations

import json
from pathlib import Path

_ROLE_TEXT = {"user": "你", "assistant": "小墨", "system": "系统"}


def render_records(records, day: str, fmt: str = "md") -> str:
    """纯函数：Markdown 或 JSON 导出，便于不带 Qt/IO 单测。"""
    items = [{"day": day, "role": r.role, "content": r.content,
              "source": r.source, "ts": r.ts, "session_id": r.session_id}
             for r in records]
    if fmt == "json":
        return json.dumps(items, ensure_ascii=False, indent=2)
    lines = [f"# WisePet 聊天记录 · {day}", ""]
    for item in items:
        who = _ROLE_TEXT.get(item["role"], item["role"])
        lines.append(f"- **{who}**（{item['source'] or '—'}）：{item['content']}")
    return "\n".join(lines) + "\n"


class DataSteward:
    def __init__(self, chat_repo, data_dir: Path | str, telemetry=None):
        self._chat = chat_repo
        self._dir = Path(data_dir)
        self._telemetry = telemetry

    # ---------- 查看 ----------

    def days(self) -> list[str]:
        return self._chat.days()

    def usage(self) -> dict:
        """只统计本机文件大小，不读消息内容以外的任何东西。"""
        db = Path(self._chat.path)
        logs = self._dir / "logs"
        telemetry = self._dir / "telemetry.json"
        return {
            "db_kb": round(db.stat().st_size / 1024, 1) if db.is_file() else 0.0,
            "days": len(self.days()),
            "records": self._chat.count(),
            "logs_kb": round(sum(p.stat().st_size for p in logs.glob("*")
                                 if p.is_file()) / 1024, 1) if logs.is_dir() else 0.0,
            "telemetry_kb": (round(telemetry.stat().st_size / 1024, 1)
                             if telemetry.is_file() else 0.0),
        }

    # ---------- 导出 ----------

    def export_all(self, destination: Path | str, fmt: str = "md") -> int:
        """导出全部有记录的日期，返回写入的条数。"""
        chunks, total = [], 0
        for day in self.days():
            records = self._chat.load(day)
            chunks.append(render_records(records, day, fmt))
            total += len(records)
        path = Path(destination).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            payload = [json.loads(chunk) for chunk in chunks]
            path.write_text(json.dumps([item for group in payload for item in group],
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            path.write_text("\n".join(chunks), encoding="utf-8")
        return total

    def export_day(self, day: str, destination: Path | str,
                   fmt: str = "md") -> int:
        records = self._chat.load(day)
        path = Path(destination).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_records(records, day, fmt), encoding="utf-8")
        return len(records)

    # ---------- 删除 ----------

    def delete_day(self, day: str) -> int:
        return self._chat.delete_day(day)

    def clear_telemetry(self) -> None:
        if self._telemetry is not None:
            self._telemetry.clear()
