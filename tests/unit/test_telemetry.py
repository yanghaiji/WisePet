"""本地埋点：可算的北极星指标、隐私边界、崩溃留痕与 30 天裁剪。"""
from __future__ import annotations

from WisePet.app.telemetry_service import TelemetryService, compute_metrics
from WisePet.infra.telemetry_store import TelemetryStore

_DAY = "2026-11-07"
_NOW = 1_762_464_000.0


def _service(tmp_path, now=None):
    return TelemetryService(TelemetryStore(tmp_path),
                            now_provider=lambda: now or _NOW)


def should_compute_activation_retention_and_guardrails():
    data = {
        "first_seen": "2026-10-25",
        "firsts": {"first_chat_done": "2026-10-25"},
        "days": {
            "2026-10-25": {"app_start": 1, "first_chat_done": 1,
                           "reminder_shown": 4, "reminder_dismissed": 1,
                           "cmd:reminder": 2},
            "2026-11-01": {"app_start": 1},
        },
    }

    metrics = compute_metrics(data, _DAY)

    assert metrics["ttfv_chat"] is True and metrics["ttfv_reminder"] is False
    assert metrics["active_days"] == 2
    assert metrics["d7_eligible"] is True and metrics["d7_retained"] is True
    assert metrics["reminder_dismiss_rate"] == 0.25
    assert metrics["feature_reach"]["cmd:reminder"] == 2


def should_not_claim_retention_before_day_seven():
    data = {"first_seen": "2026-11-05", "firsts": {},
            "days": {"2026-11-05": {"app_start": 1}}}
    metrics = compute_metrics(data, _DAY)
    assert metrics["d7_eligible"] is False and metrics["d7_retained"] is False


def should_track_only_event_names_and_never_message_content(tmp_path):
    service = _service(tmp_path)
    service.track("app_start")
    service.track("cmd:reminder")
    service.track("cmd:reminder")

    raw = (tmp_path / "telemetry.json").read_text(encoding="utf-8")
    assert "app_start" in raw and "cmd:reminder" in raw
    assert "今天怎么样" not in raw          # 从不把用户输入写进来
    assert service.metrics()["events_total"] == 3


def should_fire_first_events_only_once_and_survive_reopen(tmp_path):
    first = _service(tmp_path)
    assert first.once("first_chat_done") is True
    assert first.once("first_chat_done") is False

    reopened = _service(tmp_path)
    assert reopened.metrics()["ttfv_chat"] is True
    assert reopened.once("first_chat_done") is False


def should_clear_all_local_metrics(tmp_path):
    service = _service(tmp_path)
    service.track("app_start")
    service.clear()
    assert service.metrics()["events_total"] == 0
    assert service.metrics()["ttfv_chat"] is False


def should_prune_days_older_than_thirty(tmp_path):
    service = _service(tmp_path)
    service._data["days"] = {"2020-01-01": {"app_start": 9},
                             _DAY: {"app_start": 1}}
    service._prune()
    assert list(service._data["days"]) == [_DAY]


def should_record_crash_locally_without_leaking_keys(tmp_path):
    service = _service(tmp_path)
    try:
        raise RuntimeError("boom sk-abcdef1234567890")
    except RuntimeError as exc:
        service.record_crash(type(exc), exc, exc.__traceback__)

    text = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "RuntimeError" in text
    assert "sk-abcdef1234567890" not in text
    assert "***" in text
    assert service.metrics()["crash_count"] == 1
