import hashlib
import base64
import json
import io
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError

from github_radar.github_client import GitHubClient, GitHubRequestError
from github_radar.models import Repository
from github_radar.readme_types import ReadmeDocument, ReadmeFetch
from github_radar.readme_service import ReadmeService
from github_radar.storage import RadarStore


class Client:
    def __init__(self):
        self.repo = Repository(42, 'owner/repo', 'https://github.com/owner/repo', '', (), None, 100, False)
        self.result = ReadmeFetch('# Project\n\nAuthor purpose.', 'etag', False, False)
        self.calls = []
    def get_repository(self, name):
        self.calls.append(('identity', name)); return self.repo
    def get_repository_by_id(self, repo_id):
        return self.get_repository(repo_id)
    def fetch_readme(self, name, etag=None):
        self.calls.append(('readme', name, etag))
        if isinstance(self.result, Exception): raise self.result
        return self.result
    def fetch_readme_by_id(self, repo_id, etag=None):
        return self.fetch_readme(repo_id, etag)


class ReadmeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = RadarStore(Path(self.temp.name))
        self.client = Client()
        self.store.commit_daily('2026-09-30', [self.client.repo], [], [])
        self.service = ReadmeService(self.client, self.store)

    def test_total_deadline_rejects_late_network_result_without_cache_write(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = self.client.fetch_readme
        def slow(name, etag=None):
            started.set()
            release.wait(2)
            try: return original(name, etag)
            finally: finished.set()
        self.client.fetch_readme = slow
        self.service.timeout = .08
        before = time.monotonic()
        try:
            view = self.service.load(42)
            self.assertTrue(started.is_set())
            self.assertLess(time.monotonic() - before, .5)
            self.assertEqual(view.status, 'error')
            self.assertIn('20', view.reason)
            self.assertIsNone(self.store.load_readme(42))
        finally:
            release.set()
            self.assertTrue(finished.wait(1))
        self.assertIsNone(self.store.load_readme(42))

    def test_cancel_during_identity_returns_promptly_and_never_saves_late_result(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = self.client.get_repository
        def slow(name):
            started.set(); release.wait(2)
            try: return original(name)
            finally: finished.set()
        self.client.get_repository = slow
        views = []
        worker = threading.Thread(target=lambda: views.append(self.service.load(42)))
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            cancel = getattr(self.service, 'cancel', lambda: None)
            cancel()
            worker.join(.5)
            self.assertFalse(worker.is_alive(), 'Cancellation must not wait for network')
            self.assertEqual(views[0].status, 'error')
        finally:
            release.set(); worker.join(3); finished.wait(1)
        self.assertIsNone(self.store.load_readme(42))

    def test_cache_304_and_offline_preserve_author_text(self):
        first = self.service.load(42)
        self.assertEqual(first.status, 'ready')
        self.assertEqual(self.service.load(42), first)
        self.assertEqual(len(self.client.calls), 2)
        self.client.result = ReadmeFetch(None, 'etag', True, False)
        self.assertEqual(self.service.load(42, True).document, first.document)
        self.assertEqual(self.client.calls[-1][-1], 'etag')
        self.client.result = GitHubRequestError('断网')
        failed = self.service.load(42, True)
        self.assertEqual(failed.status, 'stale'); self.assertEqual(failed.document, first.document)
        self.assertIn('断网', failed.reason)

    def test_reused_name_cannot_overwrite_and_rename_can_load(self):
        first = self.service.load(42)
        self.client.repo = replace(self.client.repo, id=99)
        rejected = self.service.load(42, True)
        self.assertEqual(rejected.status, 'stale')
        self.assertEqual(self.store.load_readme(42), first.document)
        self.client.repo = replace(self.client.repo, id=42, full_name='owner/renamed')
        view = self.service.load(42, True)
        self.assertEqual(view.document.full_name, 'owner/renamed')

    def test_missing_corrupt_and_save_failure_have_clear_states(self):
        self.client.result = ReadmeFetch(None, None, False, False)
        self.assertEqual(self.service.load(42).status, 'missing')
        self.assertEqual(self.service.load(900).status, 'not_found')
        self.client.result = ReadmeFetch('Author text', None, False, False)
        first = self.service.load(42)
        self.store.save_readme = lambda doc: (_ for _ in ()).throw(OSError('磁盘错误'))
        self.assertEqual(self.service.load(42, True).document, first.document)

    def test_stable_id_and_real_file_source_survive_rename_304_and_name_reuse(self):
        text = '# Project\n\nAuthor purpose. [Install](./install.md)'
        requests = []
        renamed = False
        def opener(request, timeout):
            requests.append(request.full_url)
            name = 'owner/renamed' if renamed else 'owner/repo'
            if request.full_url.endswith('/readme'):
                if request.get_header('If-none-match'):
                    raise HTTPError(request.full_url, 304, 'Not Modified', {'ETag':'v1'}, io.BytesIO())
                payload = {'type':'file', 'encoding':'base64', 'size':len(text.encode()),
                           'path':'docs/ReadMe.markdown', 'content':base64.b64encode(text.encode()).decode(),
                           'html_url':f'https://github.com/{name}/blob/docs%2Fstable/docs/ReadMe.markdown'}
            else:
                # Name based lookup now refers to an entirely different project.
                payload = {'id':42 if '/repositories/42' in request.full_url else 99,
                           'full_name':name, 'html_url':f'https://github.com/{name}',
                           'description':'', 'topics':[], 'language':None,
                           'stargazers_count':100, 'archived':False}
            response = io.BytesIO(json.dumps(payload).encode()); response.headers = {'ETag':'v1'}
            return response
        self.service.client = GitHubClient(opener=opener)
        first = self.service.load(42)
        self.assertEqual(first.status, 'ready')
        self.assertEqual(first.document.source_url,
                         'https://github.com/owner/repo/blob/docs%2Fstable/docs/ReadMe.markdown')
        renamed = True
        updated = self.service.load(42, True)
        self.assertEqual(updated.status, 'ready')
        self.assertEqual(updated.document.full_name, 'owner/renamed')
        self.assertEqual(updated.document.source_url,
                         'https://github.com/owner/renamed/blob/docs%2Fstable/docs/ReadMe.markdown')
        self.assertEqual(updated.document.text, text)
        self.assertEqual(updated.document.content_hash, first.document.content_hash)
        self.assertEqual(updated.document.observed_at, first.document.observed_at)
        self.assertEqual(RadarStore(self.store.data_dir).load_readme(42), updated.document)
        self.assertTrue(all('/repositories/42' in url for url in requests))

    def test_id_readme_limits_decoded_bytes_and_rejects_bad_sources_or_encoding(self):
        payload = {'encoding':'base64', 'path':'.github/README.zh-CN.md',
                   'html_url':'https://github.com/owner/repo/blob/main/.github/README.zh-CN.md',
                   'content':base64.b64encode('中文内容'.encode()).decode()}
        def opener(request, timeout):
            response = io.BytesIO(json.dumps(payload).encode()); response.headers = {}; return response
        client = GitHubClient(opener=opener)
        result = client.fetch_readme_by_id(42, max_bytes=7)
        self.assertEqual(result.text, '中文')
        self.assertTrue(result.truncated)
        self.assertEqual(result.source_url, payload['html_url'])
        for field, bad in [('html_url','https://evil.invalid/owner/repo/blob/main/.github/README.zh-CN.md'),
                           ('content','invalid%%%'), ('encoding','none'), ('path','../README.md')]:
            before = payload[field]; payload[field] = bad
            with self.subTest(field=field), self.assertRaises(GitHubRequestError):
                client.fetch_readme_by_id(42)
            payload[field] = before

    def test_timed_out_transport_cannot_change_shared_quota_or_spawn_more_reads(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        count = []
        def opener(request, timeout):
            count.append(request.full_url); started.set(); release.wait(2)
            response = io.BytesIO(json.dumps({'id':42, 'full_name':'owner/repo',
                'html_url':'https://github.com/owner/repo', 'stargazers_count':100,
                'archived':False}).encode())
            response.headers = {'X-RateLimit-Remaining':'0', 'X-RateLimit-Resource':'core'}
            finished.set(); return response
        client = GitHubClient(opener=opener); client.core_remaining = 17
        self.service.client = client; self.service.timeout = .08
        try:
            self.assertEqual(self.service.load(42).status, 'error')
            self.assertTrue(started.is_set())
            self.assertIn('尚未结束', self.service.load(42).reason)
            self.assertEqual(len(count), 1)
        finally:
            release.set(); finished.wait(1)
        self.assertEqual(client.core_remaining, 17)
        self.assertIsNone(self.store.load_readme(42))

    def test_bounded_client_utf8_etag_and_missing(self):
        seen = []
        def opener(request, timeout):
            seen.append((request, timeout))
            result = io.BytesIO('中文内容'.encode()); result.headers = {'ETag':'next'}; return result
        client = GitHubClient(opener=opener)
        result = client.fetch_readme('owner/repo', 'old', max_bytes=7)
        self.assertTrue(result.truncated)
        self.assertEqual(result.text, '中文')
        self.assertEqual(seen[0][0].get_header('If-none-match'), 'old')
        self.assertLessEqual(seen[0][1], 20)
        for code in [304, 404, 500]:
            def failed(request, timeout):
                raise HTTPError(request.full_url, code, 'test', {}, io.BytesIO())
            client = GitHubClient(opener=failed)
            if code == 500:
                with self.assertRaises(GitHubRequestError): client.fetch_readme('owner/repo')
            else:
                self.assertEqual(client.fetch_readme('owner/repo').not_modified, code == 304)

    def test_client_rejects_invalid_utf8_and_exhausted_quota_without_request(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            response = io.BytesIO(b'\xffbad'); response.headers = {}; return response
        client = GitHubClient(opener=opener)
        with self.assertRaisesRegex(GitHubRequestError, 'UTF-8'): client.fetch_readme('owner/repo')
        client.core_remaining = 0
        with self.assertRaises(GitHubRequestError): client.fetch_readme('owner/repo')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__': unittest.main()
