import importlib
import importlib.util
import io
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
import unittest
from github_radar.github_client import GitHubClient


class AccountTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("github_radar.github_account"))
        return importlib.import_module("github_radar.github_account")

    def test_device_flow_pending_slow_down_expired_cancel(self):
        ticks=[100];requests=[]
        responses=[{"device_code":"PRIVATE_DEVICE", "user_code":"ABCD-EFGH",
                    "verification_uri":"https://github.com/login/device","expires_in":30,"interval":5},
                   {"error":"authorization_pending"},{"error":"slow_down"}]
        def opener(req,timeout):
            requests.append(req)
            response=io.BytesIO(json.dumps(responses.pop(0)).encode());response.headers={};return response
        with tempfile.TemporaryDirectory() as d:
            account=self.module().GitHubAccount(Path(d),client_id="test-app",opener=opener,clock=lambda:ticks[0])
            public=account.begin()
            self.assertNotIn("PRIVATE_DEVICE",str(asdict(public)))
            self.assertEqual(account.poll().state,"pending")
            self.assertEqual(len(requests),1)
            ticks[0]=105;self.assertEqual(account.poll().state,"pending")
            ticks[0]=110;self.assertEqual(account.poll().state,"pending")
            ticks[0]=115;account.poll();self.assertEqual(len(requests),3)
            ticks[0]=131;self.assertEqual(account.poll().state,"expired")
            account.cancel();self.assertEqual(account.status().state,"disconnected")

    def test_missing_application_does_not_fake_login(self):
        with tempfile.TemporaryDirectory() as d:
            account=self.module().GitHubAccount(Path(d),client_id=None)
            self.assertEqual(account.status().state,"unavailable")
            with self.assertRaises(ValueError):account.begin()
            self.assertIsNone(account.access_token())

    def test_invalid_or_foreign_user_credential_keeps_data_readable(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'github-credential.bin').write_bytes(b'foreign-user-protected')
            def bad(data):raise OSError('decrypt failure')
            account=self.module().GitHubAccount(Path(d),client_id="test-app",unprotect=bad)
            self.assertEqual(account.status().state,"reconnect")
            self.assertIsNone(account.access_token())
            self.assertTrue(Path(d,'github-credential.bin').exists())

    def test_credentials_never_reach_logs_or_external_source(self):
        ticks=[100];requests=[]
        responses=[{"device_code":"PRIVATE_DEVICE","user_code":"ABCD-EFGH",
                    "verification_uri":"https://github.com/login/device","expires_in":30,"interval":5},
                   {"access_token":"FAKE_TEST_TOKEN","token_type":"bearer","scope":""},
                   {"login":"example-user"}]
        def opener(req,timeout):
            requests.append(req)
            response=io.BytesIO(json.dumps(responses.pop(0)).encode());response.headers={};return response
        with tempfile.TemporaryDirectory() as d:
            account=self.module().GitHubAccount(Path(d),client_id="test-app",opener=opener,clock=lambda:ticks[0],
                                                protect=lambda b:b[::-1],unprotect=lambda b:b[::-1])
            account.begin();ticks[0]=105;status=account.poll()
            self.assertEqual(status.state,"connected")
            self.assertNotIn("FAKE_TEST_TOKEN",str(asdict(status)))
            self.assertNotIn(b"FAKE_TEST_TOKEN",Path(d,'github-credential.bin').read_bytes())
            restored=self.module().GitHubAccount(Path(d),client_id="test-app",unprotect=lambda b:b[::-1])
            self.assertEqual(restored.access_token(),"FAKE_TEST_TOKEN")
            self.assertEqual(requests[0].data,b'client_id=test-app&scope=')
            restored.disconnect();self.assertIsNone(restored.access_token())
            self.assertFalse(Path(d,'github-credential.bin').exists())

    def test_token_provider_used_for_official_json_and_readme_only(self):
        import inspect
        self.assertIn("token_provider",inspect.signature(GitHubClient).parameters)
        requests=[]
        def opener(req,timeout):
            requests.append(req)
            payload=b'example' if req.full_url.endswith('/readme') else b'{"items": []}'
            response=io.BytesIO(payload);response.headers={};return response
        client=GitHubClient(opener=opener,token_provider=lambda:'FAKE_TEST_TOKEN')
        client.search('MCP');client.readme_excerpt('a/repo')
        self.assertTrue(all(r.get_header('Authorization')=='Bearer FAKE_TEST_TOKEN' for r in requests))
