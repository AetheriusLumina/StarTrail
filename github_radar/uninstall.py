"""Prepare this installation for removal without touching other tasks or data."""

import ctypes
import msvcrt
import re
import shlex
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.request import Request

from .browser_launcher import _HTTP, _healthy, _read_record
from .diagnostic_log import record_error
from .storage import RadarStore
from .update_lock import UpdateBusyError, update_lock
from .windows_scheduler import SchedulerController, SchedulerError


class UninstallError(RuntimeError):
    """Uninstall must stop so the user can resolve a specific problem."""


_NATIVE_NAME = re.compile(r"unins\d+\.exe", re.IGNORECASE)


def _lock_free(data_dir: Path) -> bool:
    path = data_dir / ".radar-running.lock"
    if not path.exists():
        return True
    try:
        with path.open("r+b") as handle:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return True
    except OSError as exc:
        raise UninstallError(f"无法检查软件运行状态：{exc}") from exc


def _update_free(data_dir: Path) -> bool:
    try:
        with update_lock(data_dir):
            return True
    except UpdateBusyError:
        return False
    except OSError as exc:
        raise UninstallError(f"无法检查正在进行的更新：{exc}") from exc


def _request_quit(record: dict) -> None:
    origin = f"http://127.0.0.1:{record['port']}"
    request = Request(origin + "/api/quit", data=b"{}", headers={
        "X-Radar-Token": record["token"], "Origin": origin,
        "Content-Type": "application/json",
    })
    try:
        with _HTTP.open(request, timeout=2) as response:
            if response.status != 202:
                raise UninstallError("软件未接受正常退出请求，请稍后重试")
    except OSError as exc:
        raise UninstallError(f"无法让运行中的软件正常退出：{exc}") from exc


def _await_idle(data_dir: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    requested_instance = None
    while True:
        running = not _lock_free(data_dir)
        updating = not _update_free(data_dir)
        if not running and not updating:
            return
        if running:
            record = _read_record(data_dir / "radar-running.json")
            if (record and record["instance_id"] != requested_instance
                    and _healthy(record)):
                _request_quit(record)
                requested_instance = record["instance_id"]
        if time.monotonic() >= deadline:
            raise UninstallError("等待软件或更新正常退出超时，请稍后重试")
        time.sleep(0.05)


def prepare_uninstall(install_dir: Path, data_dir: Path,
                      timeout_seconds: float = 120.0) -> None:
    root = install_dir.resolve()
    data = data_dir.resolve()
    if data != root / "UserData":
        raise UninstallError("个人数据目录不属于当前安装位置，已停止卸载")
    _await_idle(data, timeout_seconds)
    try:
        store = RadarStore(data)
        settings = store.load_auto_update_settings()
        command = root / "GitHubRadar.exe"
        arguments = ("--scheduled-refresh", "--data-dir", str(data))
        scheduler = SchedulerController(root, command, arguments, root,
                                        binding_store=store)
        scheduler.sync(False, settings.time)
    except SchedulerError as exc:
        raise UninstallError(f"无法移除本软件的每日更新任务：{exc}") from exc
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise UninstallError(f"无法读取个人数据或每日更新设置：{exc}") from exc
    _await_idle(data, timeout_seconds)


def native_uninstaller_path(install_dir: Path, uninstall_command: str) -> Path:
    parts = shlex.split(uninstall_command, posix=False)
    if not parts:
        raise UninstallError("没有找到此安装的卸载程序")
    candidate = Path(parts[0].strip('"')).resolve()
    expected = (install_dir.resolve() / "AppFiles")
    if (candidate.parent != expected or not _NATIVE_NAME.fullmatch(candidate.name)
            or not candidate.is_file()):
        raise UninstallError("卸载程序路径不属于当前 StarTrail 安装目录")
    return candidate


def launch_uninstaller(install_dir: Path) -> int:
    try:
        root = install_dir.resolve()
        marker = root / "AppFiles" / "github-radar-uninstaller.txt"
        name = marker.read_text(encoding="utf-8-sig").strip()
        if not _NATIVE_NAME.fullmatch(name):
            raise UninstallError("当前安装的卸载程序记录无效")
        native = native_uninstaller_path(
            root, f'"{root / "AppFiles" / name}"')
        if not native.with_suffix(".dat").is_file():
            raise UninstallError("当前安装缺少卸载资料文件")
        subprocess.Popen([str(native)], cwd=str(install_dir))
        return 0
    except (OSError, UnicodeError, UninstallError, ValueError) as exc:
        message = f"无法启动 StarTrail 卸载向导：{exc}"
        record_error(install_dir / "UserData", "uninstall", message)
        ctypes.windll.user32.MessageBoxW(None, message, "StarTrail", 0x10)
        return 1
