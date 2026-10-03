import importlib
import importlib.util
import io
import json
import unittest
from urllib.parse import parse_qs, urlparse


class FreeEventsTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("github_radar.free_events"))
        return importlib.import_module("github_radar.free_events")

    def test_duplicates_and_malformed_names_are_bounded(self):
        requests = []
        def opener(request, timeout):
            requests.append(request)
            payload = {"data": [{"repo_name":"a/repo", "captured_star_events":5},
                                {"repo_name":"a/repo", "captured_star_events":5},
                                {"repo_name":"bad/name/extra", "captured_star_events":9}]}
            response = io.BytesIO(json.dumps(payload).encode());response.headers={};return response
        result = self.module().FreeEventsClient(opener=opener).discover("2026-09-28")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].activity_hint, 5)
        sql = parse_qs(urlparse(requests[0].full_url).query)['query'][0]
        self.assertIn("WatchEvent", sql)
        self.assertIn("2026-09-28", sql)
        self.assertNotIn("SETTINGS", sql)
        self.assertIn("LIMIT 500", sql)
        self.assertNotIn("Authorization", dict(requests[0].header_items()))

    def test_response_size_and_date_are_bounded(self):
        class Large(io.BytesIO):
            headers = {}
            def read(self, size=-1): return b"x" * size
        client = self.module().FreeEventsClient(opener=lambda request, timeout:Large())
        with self.assertRaises(self.module().SourceRequestError): client.discover("2026-09-28")
        with self.assertRaises(ValueError): client.discover("2026-09-28'; DROP")

    def test_cursor_from_another_day_does_not_skip_new_events(self):
        requests=[]
        def opener(request, timeout):
            requests.append(request.full_url)
            response=io.BytesIO(b'{"data": []}');response.headers={};return response
        self.module().FreeEventsClient(opener=opener).discover("2026-09-29", "2026-09-28:500")
        self.assertIn("OFFSET 0",parse_qs(urlparse(requests[0]).query)['query'][0])
