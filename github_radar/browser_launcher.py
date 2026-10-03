"""Open one private browser session per UserData directory on Windows."""

import json
import msvcrt
import os
import threading
import time
import webbrowser
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .browser_server import BrowserServer
from .ai_service import AIService
from .codex_connection import CodexConnection
from .diagnostic_log import record_error
from .service import RadarService
from .storage import RadarStore
from .windows_scheduler import SchedulerError


_HTTP = build_opener(ProxyHandler({}))
_RECORD_NAME = "radar-running.json"


def _record_path(store: RadarStore) -> Path:
    return store.data_dir / _RECORD_NAME


def _read_record(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (type(record.get("port")) is not int or not 1 <= record["port"] <= 65535
                or not isinstance(record.get("token"), str)
                or not isinstance(record.get("instance_id"), str)
                or type(record.get("pid")) is not int):
            return None
        return record
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _url(record: dict) -> str:
    return f"http://127.0.0.1:{record['port']}/#token={record['token']}"


def _healthy(record: dict) -> bool:
    request = Request(
        f"http://127.0.0.1:{record['port']}/api/health",
        headers={"X-Radar-Token": record["token"]},
    )
    try:
        with _HTTP.open(request, timeout=0.5) as response:
            if response.status != 200:
                return False
            answer = json.loads(response.read(1024))
        return (answer.get("status") == "ok"
                and answer.get("instance_id") == record["instance_id"]
                and answer.get("pid") == record["pid"])
    except (OSError, ValueError, TypeError):
        return False


def _write_record(path: Path, record: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def handoff_scheduled_refresh(store: RadarStore, *, wait_seconds: float = 120) -> str | None:
    """Ask a verified open page to refresh, then await its bounded result."""
    record = _read_record(_record_path(store))
    if not record or not _healthy(record):
        return None
    origin = f"http://127.0.0.1:{record['port']}"
    day = date.today().isoformat()
    previous_attempts = store.auto_attempts(day).attempts
    request = Request(origin + "/api/scheduled-refresh", data=b"{}", headers={
        "X-Radar-Token": record["token"], "Origin": origin,
        "Content-Type": "application/json",
    })
    try:
        with _HTTP.open(request, timeout=2) as response:
            if response.status == 200:
                return "skipped"
            if response.status != 202:
                return "busy"
    except HTTPError as exc:
        return "busy" if exc.code == 409 else None
    except OSError:
        return None
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        state = store.auto_attempts(day)
        if state.attempts > previous_attempts and state.status != "running":
            return state.status or "error"
        time.sleep(0.1)
    return "accepted"


def launch_browser_app(
    service: RadarService,
    store: RadarStore,
    opener=None,
    *,
    ai_service: AIService | None = None,
    connection: CodexConnection | None = None,
    scheduler=None,
) -> int:
    """Block while primary runs; secondary calls reopen the verified page."""
    opener = opener or webbrowser.open
    lock_path = store.data_dir / ".radar-running.lock"
    record_path = _record_path(store)
    deadline = time.monotonic() + 5
    while True:
        lock_file = lock_path.open("a+b")
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock_file.close()
            record = _read_record(record_path)
            if record and _healthy(record):
                opener(_url(record))
                return 0
            if time.monotonic() >= deadline:
                print("StarTrail 正在启动，请稍后再试")
                return 1
            time.sleep(0.05)
            continue

        server = None
        record = None
        try:
            server = BrowserServer(service, store, ai_service, connection,
                                   scheduler=scheduler)
            if scheduler is not None:
                try:
                    settings = store.load_auto_update_settings()
                    scheduler.sync(settings.enabled, settings.time)
                except SchedulerError as exc:
                    server._schedule_error = str(exc)
                    record_error(store.data_dir, "task-scheduler", str(exc))
            server.start()
            record = {
                "port": urlsplit(server.url).port,
                "token": server.token,
                "pid": os.getpid(),
                "instance_id": server.instance_id,
            }
            _write_record(record_path, record)
            server.start_due_update()
            opener(server.url)
            server.wait_closed()
            return 0
        finally:
            if server is not None:
                server.close()
            if record is not None and _read_record(record_path) == record:
                record_path.unlink(missing_ok=True)
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            lock_file.close()
