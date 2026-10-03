"""Narrow Windows Task Scheduler boundary for one installation's daily refresh."""

from __future__ import annotations

import hashlib
import base64
import csv
import locale
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .daily_update import valid_update_time


_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_DESCRIPTION = "GitHub Radar daily update"
ET.register_namespace("", _NS)


class SchedulerError(RuntimeError):
    """Windows could not safely read or change this installation's task."""


@dataclass(frozen=True, slots=True)
class TaskState:
    exists: bool
    command: str | None = None
    arguments: str | None = None
    description: str | None = None
    xml: str | None = None


def task_name(install_dir: str | Path) -> str:
    location = str(Path(install_dir).resolve()).casefold()
    identity = hashlib.sha256(location.encode("utf-8")).hexdigest()[:12]
    return f"GitHubRadar-{identity}"


def build_task_xml(command: Path, arguments: tuple[str, ...], working_dir: Path,
                   time: str, user_sid: str, start_date: date | None = None) -> str:
    if not valid_update_time(time) or not user_sid.startswith("S-1-"):
        raise ValueError("每日更新时间或 Windows 用户无效")
    day = start_date or date.today()
    start = datetime.combine(day, datetime.strptime(time, "%H:%M").time())
    element = lambda tag, parent: ET.SubElement(parent, f"{{{_NS}}}{tag}")
    root = ET.Element(f"{{{_NS}}}Task", {"version": "1.3"})
    element("Description", element("RegistrationInfo", root)).text = _DESCRIPTION
    triggers = element("Triggers", root)
    for offset in range(3):
        trigger = element("CalendarTrigger", triggers)
        element("StartBoundary", trigger).text = (start + timedelta(hours=offset)).isoformat()
        element("Enabled", trigger).text = "true"
        element("DaysInterval", element("ScheduleByDay", trigger)).text = "1"
    principal = element("Principal", element("Principals", root))
    principal.set("id", "Author")
    element("UserId", principal).text = user_sid
    element("LogonType", principal).text = "InteractiveToken"
    element("RunLevel", principal).text = "LeastPrivilege"
    settings = element("Settings", root)
    for tag, value in (("MultipleInstancesPolicy", "IgnoreNew"),
                       ("StartWhenAvailable", "true"), ("WakeToRun", "false"),
                       ("DisallowStartIfOnBatteries", "false"),
                       ("StopIfGoingOnBatteries", "false"),
                       ("ExecutionTimeLimit", "PT30M"), ("Enabled", "true")):
        element(tag, settings).text = value
    actions = element("Actions", root)
    actions.set("Context", "Author")
    action = element("Exec", actions)
    element("Command", action).text = str(command)
    element("Arguments", action).text = subprocess.list2cmdline(list(arguments))
    element("WorkingDirectory", action).text = str(working_dir)
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _run(args: list[str], runner: Callable = subprocess.run) -> subprocess.CompletedProcess:
    try:
        result = runner(args, capture_output=True, text=False, check=False, timeout=15,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SchedulerError(f"Windows 定时任务暂不可用：{exc}") from exc
    def decode(value: bytes | str) -> str:
        if isinstance(value, str):
            return value
        if value.startswith((b"\xff\xfe", b"\xfe\xff")):
            return value.decode("utf-16")
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode(locale.getpreferredencoding(False), errors="replace")

    completed = subprocess.CompletedProcess(result.args, result.returncode,
                                            decode(result.stdout or b""),
                                            decode(result.stderr or b""))
    completed.raw_stdout = result.stdout or b""
    return completed


def _export_task_xml(name: str, runner: Callable) -> bytes:
    if not re.fullmatch(r"GitHubRadar-[0-9a-f]{12}", name):
        raise SchedulerError("每日任务名称无效")
    script = ("$ErrorActionPreference='Stop';"
              "$xml=Export-ScheduledTask -TaskName '" + name + "';"
              "[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($xml))")
    result = _run(["powershell.exe", "-NoProfile", "-NonInteractive",
                   "-Command", script], runner)
    if result.returncode:
        raise SchedulerError("无法可靠读取 Windows 定时任务：" +
                             (result.stderr.strip() or result.stdout.strip()))
    try:
        xml = base64.b64decode(result.stdout.strip(), validate=True).decode("utf-16-le")
        xml = re.sub(r"^\ufeff?\s*<\?xml[^>]*\?>", "", xml, count=1).lstrip()
        return xml.encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise SchedulerError("Windows 定时任务资料编码无效") from exc


def _task_xml_bytes(xml: str) -> bytes:
    return ET.tostring(ET.fromstring(xml), encoding="utf-16", xml_declaration=True)


def query_task(name: str, *, runner: Callable = subprocess.run) -> TaskState:
    result = _run(["schtasks.exe", "/query", "/tn", name, "/xml", "/hresult"], runner)
    if result.returncode:
        # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND).
        if (result.returncode & 0xffffffff) in {0x80070002, 0x80070003}:
            return TaskState(False)
        raise SchedulerError(f"无法查询 Windows 定时任务：{result.stderr.strip() or result.stdout.strip()}")
    raw = result.raw_stdout
    source = result.stdout
    if isinstance(raw, bytes) and not raw.startswith((b"\xff\xfe", b"\xfe\xff")) \
            and any(byte >= 128 for byte in raw):
        source = _export_task_xml(name, runner)
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise SchedulerError("无法读取 Windows 定时任务资料") from exc
    ns = {"t": _NS}
    return TaskState(True,
                     root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns),
                     root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns),
                     root.findtext("t:RegistrationInfo/t:Description", namespaces=ns),
                     ET.tostring(root, encoding="unicode"))


def _belongs_to_us(state: TaskState, command: Path,
                   arguments: tuple[str, ...]) -> bool:
    return (state.description == _DESCRIPTION
            and state.command is not None
            and str(Path(state.command).resolve()).casefold() ==
            str(Path(command).resolve()).casefold()
            and state.arguments == subprocess.list2cmdline(list(arguments)))


def _same_schedule(state: TaskState, working_dir: Path,
                   time: str, user_sid: str) -> bool:
    if not state.xml:
        return False
    root = ET.fromstring(state.xml)
    ns = {"t": _NS}
    triggers = root.findall("t:Triggers/t:CalendarTrigger", ns)
    starts = [trigger.find("t:StartBoundary", ns) for trigger in triggers]
    expected = [(datetime.strptime(time, "%H:%M") + timedelta(hours=n)).strftime("%H:%M")
                for n in range(3)]
    actual = [item.text[11:16] if item is not None and item.text else ""
              for item in starts]
    daily_and_enabled = all(
        trigger.findtext("t:Enabled", namespaces=ns) in (None, "true")
        and trigger.findtext("t:ScheduleByDay/t:DaysInterval", namespaces=ns)
            in (None, "1")
        for trigger in triggers)
    old_dir = root.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=ns)
    return (actual == expected and daily_and_enabled
            and root.findtext("t:Settings/t:Enabled", namespaces=ns) in (None, "true")
            and root.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns)
                == "InteractiveToken"
            and old_dir is not None
            and str(Path(old_dir).resolve()).casefold() ==
            str(working_dir.resolve()).casefold()
            and root.findtext("t:Principals/t:Principal/t:UserId", namespaces=ns) == user_sid
            and root.findtext("t:Settings/t:StartWhenAvailable", namespaces=ns) == "true"
            and root.findtext("t:Settings/t:WakeToRun", namespaces=ns) in (None, "false"))


def register_task(name: str, command: Path, arguments: tuple[str, ...],
                  working_dir: Path, time: str, user_sid: str, *,
                  runner: Callable = subprocess.run) -> None:
    current = query_task(name, runner=runner)
    if current.exists and not _belongs_to_us(current, command, arguments):
        raise SchedulerError("同名 Windows 定时任务不属于此安装目录")
    if current.exists and _same_schedule(current, working_dir, time, user_sid):
        return
    xml = build_task_xml(command, arguments, working_dir, time, user_sid)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml",
                                         prefix="github-radar-task-", delete=False) as output:
            temporary = Path(output.name)
            output.write(_task_xml_bytes(xml))
        result = _run(["schtasks.exe", "/create", "/tn", name,
                       "/xml", str(temporary), "/f"], runner)
        if result.returncode:
            original_error = result.stderr.strip() or result.stdout.strip()
            if current.exists and current.xml:
                temporary.write_bytes(_task_xml_bytes(current.xml))
                restore = _run(["schtasks.exe", "/create", "/tn", name,
                                "/xml", str(temporary), "/f"], runner)
                if restore.returncode:
                    raise SchedulerError("Windows 定时任务创建失败，原任务恢复也失败："
                                         f"{original_error}; {restore.stderr.strip() or restore.stdout.strip()}")
            raise SchedulerError(f"Windows 定时任务创建失败：{original_error}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def remove_task(name: str, command: Path, arguments: tuple[str, ...], *,
                runner: Callable = subprocess.run) -> None:
    current = query_task(name, runner=runner)
    if not current.exists:
        return
    if not _belongs_to_us(current, command, arguments):
        raise SchedulerError("同名 Windows 定时任务不属于此安装目录")
    result = _run(["schtasks.exe", "/delete", "/tn", name, "/f"], runner)
    if result.returncode:
        raise SchedulerError(f"Windows 定时任务删除失败：{result.stderr.strip() or result.stdout.strip()}")


def current_user_sid(*, runner: Callable = subprocess.run) -> str:
    result = _run(["whoami.exe", "/user", "/fo", "csv", "/nh"], runner)
    if result.returncode:
        raise SchedulerError("无法读取当前 Windows 用户")
    rows = list(csv.reader(result.stdout.splitlines()))
    if not rows or len(rows[0]) < 2 or not rows[0][1].startswith("S-1-"):
        raise SchedulerError("当前 Windows 用户信息无效")
    return rows[0][1]


class SchedulerController:
    """One installation's schedule, without registering anything at construction."""

    def __init__(self, install_dir: Path, command: Path, arguments: tuple[str, ...],
                 working_dir: Path, *, user_sid: str | None = None,
                 runner: Callable = subprocess.run, binding_store=None):
        self.name = task_name(install_dir)
        self.install_dir = install_dir
        self.command = command
        self.arguments = arguments
        self.working_dir = working_dir
        self.user_sid = user_sid
        self.runner = runner
        self.binding_store = binding_store

    def _binding(self) -> dict:
        return {"install_dir": str(self.install_dir), "command": str(self.command),
                "arguments": list(self.arguments)}

    def status(self, enabled: bool, time: str) -> bool:
        state = query_task(self.name, runner=self.runner)
        if not enabled:
            return not state.exists
        return (state.exists and _belongs_to_us(state, self.command, self.arguments)
                and self.user_sid is not None
                and _same_schedule(state, self.working_dir, time, self.user_sid))

    def sync(self, enabled: bool, time: str) -> None:
        if enabled:
            sid = self.user_sid or current_user_sid(runner=self.runner)
            register_task(self.name, self.command, self.arguments,
                          self.working_dir, time, sid, runner=self.runner)
            self.user_sid = sid
            if self.binding_store is not None:
                self.binding_store.save_scheduler_binding(self._binding())
        else:
            remove_task(self.name, self.command, self.arguments, runner=self.runner)
            if self.binding_store is not None:
                self.binding_store.save_scheduler_binding(None)
