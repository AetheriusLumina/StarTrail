"""Small, deterministic policy for the optional Windows daily update."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import sqlite3

from .update_lock import UpdateBusyError, update_lock
from .diagnostic_log import record_error


@dataclass(frozen=True, slots=True)
class AutoUpdateSettings:
    enabled: bool = True
    time: str = "09:00"


@dataclass(frozen=True, slots=True)
class AutoAttemptState:
    attempts: int = 0
    last_attempt_at: str | None = None
    status: str | None = None
    reason: str | None = None


def valid_update_time(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hour, minute = value[:2], value[3:]
    return (hour.isascii() and minute.isascii() and hour.isdecimal()
            and minute.isdecimal() and 0 <= int(hour) <= 21 and 0 <= int(minute) <= 59)


def auto_update_due(now: datetime, settings: AutoUpdateSettings,
                    state: AutoAttemptState, latest_success_date: str | None) -> bool:
    """Decide whether a local-day automatic attempt may start now."""
    if (not settings.enabled or not valid_update_time(settings.time)
            or latest_success_date == now.date().isoformat()
            or state.attempts >= 3 or state.status == "success"
            or now.strftime("%H:%M") < settings.time):
        return False
    if state.last_attempt_at is None:
        return True
    previous = datetime.fromisoformat(state.last_attempt_at)
    return now - previous >= timedelta(hours=1)


def run_scheduled_update(store, service, now: datetime) -> str:
    """Run one eligible current-day GitHub refresh without starting a browser or AI."""
    if now.tzinfo is None:
        raise ValueError("自动更新时间必须包含时区")
    day = now.date().isoformat()
    observed_at = now.isoformat(timespec="seconds")
    try:
        with update_lock(store.data_dir):
            settings = store.load_auto_update_settings()
            state = store.auto_attempts(day)
            if not auto_update_due(now, settings, state, store.latest_successful_date()):
                return "skipped"
            if not store.begin_auto_attempt(day, observed_at):
                return "skipped"
        try:
            result = service.refresh(day, observed_at)
            if result.status == "ok":
                store.finish_auto_attempt(day, "success")
                return "success"
            reason = result.message or "GitHub 更新失败"
            store.save_refresh_failure(observed_at, reason)
        except Exception as exc:
            reason = f"更新失败：{exc}"
            store.save_refresh_failure(observed_at, reason)
        store.finish_auto_attempt(day, "error", reason)
        record_error(store.data_dir, "auto-update", reason)
        return "error"
    except UpdateBusyError:
        record_error(store.data_dir, "auto-update", "已有任务正在运行，后台更新等待下一次触发")
        return "busy"
    except (OSError, sqlite3.Error):
        # A full/unwritable UserData cannot reliably record another failure.
        # Leave the last committed result intact and let Windows retry later.
        record_error(store.data_dir, "auto-update", "个人数据暂不可写，保留上次成功结果")
        return "error"
