import io
import unittest
from urllib.error import URLError

from github_radar.trending import TrendingClient, TrendingUnavailable


class TrendingTests(unittest.TestCase):
    def test_extracts_only_repository_cards(self):
        html = b'''<a href="/navigation/not-a-repo">Nav</a>
        <article class="Box-row"><a href="/login?return_to=repo">Star</a>
          <h2><a href="/Alice/repo-one">Alice / repo-one</a></h2></article>
        <article class="Box-row"><h2><a href="/bob/repo-two">Bob</a></h2></article>
        <article class="Box-row"><h2><a href="/alice/repo-one">Duplicate</a></h2></article>
        <article class="Box-row"><h2><a href="/bad">Invalid</a></h2></article>'''
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return io.BytesIO(html)

        result = TrendingClient(opener=opener).repo_names()
        self.assertEqual(result, ["Alice/repo-one", "bob/repo-two"])
        self.assertEqual(requests[0][0].full_url, "https://github.com/trending?since=daily")
        self.assertEqual(requests[0][1], 15)

    def test_changed_markup_is_unavailable(self):
        html = b'<main><h2><a href="/Alice/repo-one">Alice</a></h2></main>'
        client = TrendingClient(opener=lambda request, timeout: io.BytesIO(html))
        with self.assertRaises(TrendingUnavailable):
            client.repo_names()

    def test_network_failure_is_unavailable(self):
        def offline(request, timeout):
            raise URLError("offline")

        with self.assertRaises(TrendingUnavailable):
            TrendingClient(opener=offline).repo_names()


if __name__ == "__main__":
    unittest.main()
