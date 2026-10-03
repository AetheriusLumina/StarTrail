import json
import os
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_radar.codex_connection import CodexConnection


class FakeAppServer:
    def __init__(self, replies=None):
        self.replies = replies or {}
        self.lines = queue.Queue()
        self.stdin = self
        self.stdout = self
        self.stderr = self
        self.returncode = None

    def write(self, value):
        for line in value.splitlines():
            request = json.loads(line)
            if "id" not in request:
                continue
            answer = self.replies.get(request["method"])
            if answer is not None:
                self.lines.put(json.dumps({"id": request["id"], **answer}) + "\n")
        return len(value)

    def flush(self):
        pass

    def readline(self):
        try:
            return self.lines.get(timeout=1)
        except queue.Empty:
            return ""

    def close(self):
        pass

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.lines.put("")

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


def server_replies():
    return {
        "initialize": {"result": {"userAgent": "test"}},
        "account/read": {"result": {
            "account": {"type": "chatgpt", "planType": "plus"},
            "requiresOpenaiAuth": True,
        }},
        "model/list": {"result": {"data": [
            {"id": "gpt-6-sol", "displayName": "GPT-6 Sol", "hidden": False,
             "inputModalities": ["text"], "isDefault": True},
            {"id": "hidden-model", "displayName": "Hidden", "hidden": True,
             "inputModalities": ["text"], "isDefault": False},
            {"id": "audio-only", "displayName": "Audio", "hidden": False,
             "inputModalities": ["audio"], "isDefault": False},
        ], "nextCursor": None}},
        "account/login/start": {"result": {
            "type": "chatgpt", "loginId": "login-1",
            "authUrl": "https://chatgpt.com/auth/start",
        }},
    }


class CodexConnectionTests(unittest.TestCase):
    def test_missing_cli_is_unavailable(self):
        connection = CodexConnection(executable=str(Path.cwd() / "missing-codex-test" / "codex.exe"))
        state = connection.probe()
        self.assertFalse(state.ready)
        self.assertTrue(state.reason)

    def test_finds_codex_desktop_executable_when_path_has_no_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "OpenAI" / "Codex" / "bin" / "release" / "codex.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.dict(os.environ, {"LOCALAPPDATA": folder}), \
                    patch("github_radar.codex_connection.shutil.which", return_value=None):
                self.assertEqual(CodexConnection()._executable_path(), str(executable))

    def test_model_list_filters_hidden(self):
        replies = server_replies()
        replies["model/list"]["result"]["data"].append({
            "id": "older-model", "displayName": "Older", "hidden": False,
            "isDefault": False,
        })
        with patch("github_radar.codex_connection.subprocess.Popen",
                   side_effect=lambda *a, **k: FakeAppServer(replies)):
            connection = CodexConnection(executable=sys.executable)
            models = connection.list_models()
        self.assertEqual([model.id for model in models], ["gpt-6-sol", "older-model"])
        self.assertEqual(models[0].display_name, "GPT-6 Sol")

    def test_ready_requires_chatgpt_and_model_list(self):
        with patch("github_radar.codex_connection.subprocess.Popen",
                   side_effect=lambda *a, **k: FakeAppServer(server_replies())):
            state = CodexConnection(executable=sys.executable).probe()
        self.assertTrue(state.ready)

    def test_browser_login_url(self):
        fake = FakeAppServer(server_replies())
        with patch("github_radar.codex_connection.subprocess.Popen", return_value=fake):
            connection = CodexConnection(executable=sys.executable)
            login = connection.begin_login()
            self.assertTrue(login.url.startswith("https://"))
            self.assertIsNone(fake.poll())
            connection.cancel_login()
        self.assertIsNotNone(fake.poll())

    def test_browser_login_rejects_untrusted_url(self):
        replies = server_replies()
        replies["account/login/start"]["result"]["authUrl"] = "http://evil.example/start"
        fake = FakeAppServer(replies)
        with patch("github_radar.codex_connection.subprocess.Popen", return_value=fake):
            connection = CodexConnection(executable=sys.executable)
            with self.assertRaises(ValueError):
                connection.begin_login()
        self.assertIsNotNone(fake.poll())

    def test_rpc_timeout_has_safe_reason(self):
        fake = FakeAppServer({"initialize": {"result": {}}})
        with patch("github_radar.codex_connection.subprocess.Popen", return_value=fake), \
                patch.object(CodexConnection, "RPC_TIMEOUT", 0.05):
            connection = CodexConnection(executable=sys.executable)
            state = connection.probe()
        self.assertFalse(state.ready)
        self.assertTrue(state.reason)
        self.assertNotIn("token", state.reason.casefold())

    def test_early_exit_explains_known_home_directory_failure_without_leak(self):
        fake = FakeAppServer()
        fake.lines.put("")

        def start(*args, **kwargs):
            kwargs["stderr"].write("Could not find home directory; token=SECRET")
            kwargs["stderr"].flush()
            return fake

        with patch("github_radar.codex_connection.subprocess.Popen", side_effect=start):
            state = CodexConnection(executable=sys.executable).probe()
        self.assertFalse(state.ready)
        self.assertIn("用户目录", state.reason)
        self.assertNotIn("SECRET", state.reason)


if __name__ == "__main__":
    unittest.main()
