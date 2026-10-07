import io
import json
import socket
import unittest
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from github_radar.github_client import GitHubClient, GitHubRateLimitError, GitHubRequestError
from github_radar.models import Repository


REPO_PAYLOAD = {
    "id": 7,
    "full_name": "example/project",
    "html_url": "https://github.com/example/project",
    "description": "Local AI tools",
    "topics": ["local-ai", "tools"],
    "language": "Python",
    "stargazers_count": 1500,
    "archived": False,
}


class FakeResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.headers = headers or {}


class GithubClientTests(unittest.TestCase):
    def test_graphql_mixed_limit_and_permission_error_stays_failure(self):
        payload={'data':None,'errors':[{'type':'RATE_LIMITED'},{'type':'FORBIDDEN'}]}
        client=GitHubClient(opener=lambda *a,**kw:FakeResponse(payload),token_provider=lambda:'isolated-test')
        try:client.get_repositories_batch(('org/repo',))
        except GitHubRateLimitError:self.fail('independent permission failure must not become quota success')
        except GitHubRequestError:pass
        else:self.fail('invalid response accepted')

    def test_graphql_expired_zero_is_reset_with_other_quotas(self):
        client=GitHubClient();client.graphql_remaining=0;client.graphql_reset_at=10
        client.renew_expired_quotas(now=11)
        self.assertIsNone(client.graphql_remaining);self.assertIsNone(client.graphql_reset_at)

    def test_graphql_http_200_explicit_rate_limit_is_quota_not_invalid_metadata(self):
        payload={'data':None,'errors':[{'type':'RATE_LIMITED','message':'quota exhausted'}]}
        client=GitHubClient(opener=lambda *a,**kw:FakeResponse(payload,{'x-ratelimit-resource':'graphql','x-ratelimit-remaining':'0','x-ratelimit-reset':'9999999999'}),token_provider=lambda:'isolated-test')
        with self.assertRaises(GitHubRateLimitError):client.get_repositories_batch(('org/repo',))

    def test_star_history_weeks_preserve_raw_timestamp_and_reject_invalid_values(self):
        self.assertTrue(hasattr(GitHubClient, "star_history_weeks"), "raw week interface missing")
        payload = [{"week": 1789862400, "days": [1, 2, 3, 4, 5, 6, 7]}]
        client = GitHubClient(opener=lambda request, timeout: FakeResponse(payload))
        result = client.star_history_weeks("owner/repo")
        self.assertEqual(result[0].week, 1789862400)
        self.assertEqual(result[0].days, (1, 2, 3, 4, 5, 6, 7))
        for broken in (True, -1, "1", 1.5):
            with self.subTest(broken=broken):
                payload[0]["days"][0] = broken
                with self.assertRaises(GitHubRequestError):
                    client.star_history_weeks("owner/repo")

    def test_readme_is_bounded(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            response = io.BytesIO(("文档" * 5000).encode("utf-8"))
            response.headers = {"x-ratelimit-resource": "core", "x-ratelimit-remaining": "39"}
            return response

        client = GitHubClient(opener=opener)
        self.assertEqual(len(client.readme_excerpt("owner/repo")), 2400)
        self.assertEqual(urlparse(requests[0].full_url).path, "/repos/owner/repo/readme")
        self.assertEqual(requests[0].get_header("Accept"), "application/vnd.github.raw+json")
        self.assertEqual(client.core_remaining, 39)

    def test_readme_404_is_optional(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 404, "missing", {}, io.BytesIO())

        self.assertIsNone(GitHubClient(opener=opener).readme_excerpt("owner/missing"))

    def test_search_encodes_query_and_decodes_repository(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                {"items": [REPO_PAYLOAD]},
                {"x-ratelimit-remaining": "8", "x-ratelimit-reset": "1780000000"},
            )

        client = GitHubClient(opener=opener)
        result = client.search("Local AI", page=2, per_page=50)
        self.assertEqual(
            result,
            [Repository(7, "example/project", "https://github.com/example/project", "Local AI tools", ("local-ai", "tools"), "Python", 1500, False)],
        )
        query = parse_qs(urlparse(requests[0][0].full_url).query)
        self.assertEqual(query, {"q": ["Local AI"], "sort": ["stars"], "order": ["desc"], "page": ["2"], "per_page": ["50"]})
        self.assertIn("GitHubRadar", requests[0][0].get_header("User-agent"))
        self.assertEqual(requests[0][1], 20)
        self.assertEqual(client.remaining, 8)
        self.assertEqual(client.reset_at, 1780000000)

    def test_get_repository_reads_single_repository(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse(REPO_PAYLOAD)

        result = GitHubClient(opener=opener).get_repository("example/project")
        self.assertEqual(result.id, 7)
        self.assertEqual(urlparse(requests[0].full_url).path, "/repos/example/project")

    def test_star_history_expands_weeks_in_date_order(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse([
                {"week": 1789862400, "total": 28, "days": [1, 2, 3, 4, 5, 6, 7]},
                {"week": 1789257600, "total": 70, "days": [10, 10, 10, 10, 10, 10, 10]},
            ], {"x-ratelimit-resource": "core", "x-ratelimit-remaining": "42"})

        client = GitHubClient(opener=opener)
        days = client.star_history("owner/repo name")
        self.assertEqual((days[0].stat_date, days[0].added), ("2026-09-13", 10))
        self.assertEqual((days[-1].stat_date, days[-1].added), ("2026-09-26", 7))
        self.assertEqual(len(days), 14)
        self.assertEqual(urlparse(requests[0].full_url).path, "/repos/owner/repo%20name/stargazers/history")
        self.assertEqual(parse_qs(urlparse(requests[0].full_url).query), {"per_page": ["2"]})
        self.assertEqual(client.core_remaining, 42)

    def test_star_history_rejects_malformed_day_counts(self):
        payload = [{"week": 1789862400, "total": 4, "days": [1, 2, "bad"]}]
        client = GitHubClient(opener=lambda request, timeout: FakeResponse(payload))
        with self.assertRaises(GitHubRequestError):
            client.star_history("owner/repo")

    def test_tracks_search_and_core_quotas_separately(self):
        def opener(request, timeout):
            if "/search/" in request.full_url:
                return FakeResponse({"items": []}, {"x-ratelimit-resource": "search", "x-ratelimit-remaining": "7"})
            return FakeResponse(REPO_PAYLOAD, {"x-ratelimit-resource": "core", "x-ratelimit-remaining": "39"})

        client = GitHubClient(opener=opener)
        client.search("Local AI")
        client.get_repository("example/project")
        self.assertEqual(client.search_remaining, 7)
        self.assertEqual(client.core_remaining, 39)

    def test_rate_limit_is_not_misreported_as_empty_results(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                403,
                "rate limit",
                {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1780000000"},
                io.BytesIO(b'{"message":"API rate limit exceeded"}'),
            )

        with self.assertRaises(GitHubRateLimitError) as caught:
            GitHubClient(opener=opener).search("AI")
        self.assertEqual(caught.exception.reset_at, 1780000000)

    def test_timeout_is_not_misreported_as_empty_results(self):
        def opener(request, timeout):
            raise URLError(socket.timeout("timed out"))

        with self.assertRaises(GitHubRequestError):
            GitHubClient(opener=opener).search("AI")

    def test_malformed_response_is_a_request_error(self):
        def opener(request, timeout):
            return FakeResponse({"items": [{"id": 7}]})

        with self.assertRaises(GitHubRequestError):
            GitHubClient(opener=opener).search("AI")

    def test_incomplete_search_is_not_accepted_as_success(self):
        def opener(request, timeout):
            return FakeResponse({"items": [REPO_PAYLOAD], "incomplete_results": True})

        with self.assertRaises(GitHubRequestError):
            GitHubClient(opener=opener).search("AI")

    def test_truncated_http_body_is_a_request_error(self):
        class BrokenResponse:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                raise IncompleteRead(b'{"items":', 100)

        with self.assertRaises(GitHubRequestError):
            GitHubClient(opener=lambda request, timeout: BrokenResponse()).search("AI")

    def test_search_page_keeps_partition_metadata(self):
        self.assertTrue(hasattr(GitHubClient, "search_page"))
        client = GitHubClient(opener=lambda req, timeout: FakeResponse(
            {"items": [REPO_PAYLOAD], "total_count": 1234, "incomplete_results": True}))
        result = client.search_page("MCP")
        self.assertEqual((result.total_count, result.incomplete_results), (1234, True))
        self.assertEqual(result.items[0].id, 7)

    def test_public_repository_cursor_uses_link(self):
        self.assertTrue(hasattr(GitHubClient, "public_repository_page"))
        client = GitHubClient(opener=lambda req, timeout: FakeResponse([REPO_PAYLOAD],
            {"Link": '<https://api.github.com/repositories?since=77>; rel="next"'}))
        result = client.public_repository_page(None)
        self.assertEqual(result.cursor, "77")
        self.assertEqual(result.candidates[0].repo_id, 7)
        self.assertIsNone(result.candidates[0].cached_repo)

    def test_github_response_over_10mb_is_rejected(self):
        class Large(io.BytesIO):
            headers={}
            def read(self,size=-1): return b'x' * size
        with self.assertRaises(GitHubRequestError):
            GitHubClient(opener=lambda req, timeout:Large()).search("MCP")


if __name__ == "__main__":
    unittest.main()
