"""Account and model discovery through the locally installed Codex App Server."""

import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class ConnectionState:
    ready: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ModelOption:
    id: str
    display_name: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class LoginSession:
    url: str
    pending: bool = True


class _ConnectionError(Exception):
    pass


class _Session:
    def __init__(self, process, timeout: float, error_file):
        self.process = process
        self.timeout = timeout
        self.error_file = error_file
        self.lines: queue.Queue[str] = queue.Queue()
        self.next_id = 1
        self.reader = threading.Thread(target=self._read_lines, daemon=True)
        self.reader.start()

    def _read_lines(self) -> None:
        while True:
            line = self.process.stdout.readline()
            self.lines.put(line)
            if not line:
                return

    def _send(self, payload: dict) -> None:
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError, BrokenPipeError) as exc:
            raise _ConnectionError("Codex 连接已断开") from exc

    def notify(self, method: str) -> None:
        self._send({"method": method})

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        payload = {"method": method, "id": request_id}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        while True:
            try:
                line = self.lines.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise _ConnectionError("Codex 响应超时") from exc
            if not line:
                raise _ConnectionError(self._exit_reason())
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise _ConnectionError("Codex 响应格式有误") from exc
            if not isinstance(message, dict):
                raise _ConnectionError("Codex 响应格式有误")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise _ConnectionError("Codex 暂时无法完成请求")
            result = message.get("result")
            if not isinstance(result, dict):
                raise _ConnectionError("Codex 响应格式有误")
            return result

    def _exit_reason(self) -> str:
        try:
            self.error_file.seek(0)
            diagnostic = self.error_file.read(4096).casefold()
        except OSError:
            diagnostic = ""
        if "could not find home directory" in diagnostic:
            return "Codex 无法识别用户目录，请检查 Codex 的运行环境"
        if "not logged in" in diagnostic or "not authenticated" in diagnostic:
            return "Codex 账号未登录，请点击连接 Codex"
        return "Codex 连接已结束，请检查安装与运行环境"

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.error_file):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass


class CodexConnection:
    RPC_TIMEOUT = 6.0
    _MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
    _AUTH_HOSTS = {"chatgpt.com", "auth.openai.com"}

    def __init__(self, executable: str | None = None):
        self.executable = executable
        self._login_session: _Session | None = None

    def _executable_path(self) -> str:
        chosen = self.executable or shutil.which("codex")
        if not chosen and not self.executable:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                install_dir = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
                try:
                    installed = [path for path in install_dir.glob("*/codex.exe")
                                 if path.is_file()]
                    if installed:
                        chosen = str(max(installed, key=lambda path: path.stat().st_mtime))
                except OSError:
                    pass
        if not chosen:
            raise _ConnectionError("未找到 Codex，请先安装或打开 Codex")
        if not Path(chosen).is_file() and shutil.which(chosen) is None:
            raise _ConnectionError("未找到 Codex，请先安装或打开 Codex")
        return chosen

    def _open_session(self) -> _Session:
        executable = self._executable_path()
        error_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        try:
            process = subprocess.Popen(
                [executable, "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=error_file,
                text=True,
                encoding="utf-8",
                bufsize=1,
                cwd=tempfile.gettempdir(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            error_file.close()
            raise _ConnectionError("无法启动 Codex，请检查安装") from exc
        session = _Session(process, self.RPC_TIMEOUT, error_file)
        try:
            session.request("initialize", {"clientInfo": {
                "name": "github_radar", "title": "StarTrail", "version": "0.4.0",
            }})
            session.notify("initialized")
            return session
        except _ConnectionError:
            session.close()
            raise

    @classmethod
    def _models(cls, payload: dict) -> tuple[ModelOption, ...]:
        data = payload.get("data")
        if not isinstance(data, list):
            raise _ConnectionError("Codex 模型列表格式有误")
        found: dict[str, ModelOption] = {}
        for item in data:
            if not isinstance(item, dict) or item.get("hidden") is True:
                continue
            modalities = item.get("inputModalities", ["text", "image"])
            if not isinstance(modalities, list) or "text" not in modalities:
                continue
            model_id = item.get("id")
            if not isinstance(model_id, str) or not cls._MODEL_ID.fullmatch(model_id):
                continue
            label = item.get("displayName")
            found.setdefault(model_id, ModelOption(
                model_id, label if isinstance(label, str) and label.strip() else model_id,
                item.get("isDefault") is True,
            ))
        return tuple(found.values())

    def probe(self) -> ConnectionState:
        try:
            session = self._open_session()
            try:
                account = session.request("account/read", {"refreshToken": False}).get("account")
                if not isinstance(account, dict) or account.get("type") != "chatgpt":
                    return ConnectionState(False, "请连接 Codex 的 ChatGPT 账号")
                models = self._models(session.request("model/list", {
                    "limit": 100, "includeHidden": False,
                }))
                if not models:
                    return ConnectionState(False, "Codex 当前没有可用的文本模型")
                return ConnectionState(True, "Codex 已连接")
            finally:
                session.close()
        except _ConnectionError as exc:
            return ConnectionState(False, str(exc))

    def list_models(self) -> tuple[ModelOption, ...]:
        try:
            session = self._open_session()
            try:
                return self._models(session.request("model/list", {
                    "limit": 100, "includeHidden": False,
                }))
            finally:
                session.close()
        except _ConnectionError:
            return ()

    def begin_login(self) -> LoginSession:
        self.cancel_login()
        session = self._open_session()
        try:
            result = session.request("account/login/start", {
                "type": "chatgpt", "useHostedLoginSuccessPage": True,
                "appBrand": "chatgpt",
            })
            url = result.get("authUrl")
            if not isinstance(url, str):
                raise ValueError("Codex 未返回登录地址")
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname not in self._AUTH_HOSTS:
                raise ValueError("Codex 返回了不可信的登录地址")
            self._login_session = session
            return LoginSession(url)
        except (ValueError, _ConnectionError):
            session.close()
            raise

    def cancel_login(self) -> None:
        session = self._login_session
        self._login_session = None
        if session is not None:
            session.close()
