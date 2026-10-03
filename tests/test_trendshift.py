import importlib
import importlib.util
import io
from pathlib import Path
import unittest

ROOT = Path(__file__).parent / "fixtures" / "trendshift"
TODAY = "https://trendshift.io/github-trending-repositories?trending-range=1&trending-limit=100"


class TrendshiftTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("github_radar.trendshift"))
        return importlib.import_module("github_radar.trendshift")

    def parse(self, filename, url=TODAY):
        return self.module().parse_trendshift((ROOT / filename).read_text(encoding="utf-8"),
                                              url, "2026-09-30T10:00:00+00:00")

    def test_archive_100_is_parsed_without_ads(self):
        batch = self.parse("archive-100.html", TODAY.replace("range=1", "range=0"))
        self.assertEqual(len(batch.candidates), 100)
        self.assertEqual(len({c.full_name for c in batch.candidates}), 100)
        self.assertTrue(all(c.repo_id is None for c in batch.candidates))
        self.assertEqual(batch.candidates[0].evidence[0].rank_kind, "github_featured")
        self.assertTrue(all(c.evidence[0].period == "all" for c in batch.candidates))
        self.assertIsNone(batch.candidates[0].evidence[0].daily_added_text)

    def test_today_can_have_less_than_100(self):
        batch = self.parse("today-14.html")
        self.assertEqual(len(batch.candidates), 14)
        self.assertEqual(batch.candidates[0].full_name, "debpalash/VoiceStudio")
        self.assertIsNone(batch.candidates[0].evidence[0].day_boundary)
        self.assertEqual(batch.candidates[0].evidence[0].source_rank, 1)

    def test_today_is_not_assigned_requested_past_date(self):
        batch = self.parse("daily-25.html", "https://trendshift.io/")
        evidence = batch.candidates[0].evidence[0]
        self.assertEqual(evidence.stat_date, "2026-09-30")
        self.assertEqual(evidence.daily_added_text, "1924")
        self.assertEqual(evidence.rank_kind, "trendshift_daily")

    def test_topic_first_batch_is_not_complete_catalog(self):
        html = '<h1>AI skills</h1><a href="/repositories/123">a/skills</a><button>Load more</button>'
        batch = self.module().parse_trendshift(html, "https://trendshift.io/topics/ai-skills",
                                              "2026-09-30T10:00:00+00:00")
        self.assertEqual([c.full_name for c in batch.candidates], ["a/skills"])
        self.assertFalse(batch.batch_finished)
        self.assertEqual(batch.candidates[0].evidence[0].rank_kind, "none")
        self.assertTrue(batch.notes)

    def test_daily_sources_never_request_week_or_month(self):
        requests = []
        def opener(request, timeout):
            requests.append((request.full_url, timeout, dict(request.header_items())))
            f = "daily-25.html" if request.full_url == "https://trendshift.io/" else "today-14.html"
            response = io.BytesIO((ROOT / f).read_bytes()); response.headers = {}
            return response
        batches = self.module().TrendshiftClient(opener=opener).daily("2026-09-30T10:00:00+00:00")
        self.assertEqual([r[0] for r in requests], ["https://trendshift.io/", TODAY])
        self.assertEqual([len(b.candidates) for b in batches], [25, 14])
        self.assertTrue(all(r[1] <= 20 for r in requests))
        self.assertTrue(all("Authorization" not in r[2] for r in requests))

    def test_schema_change_is_source_failure_not_successful_empty_board(self):
        with self.assertRaises(self.module().SourceRequestError):
            self.module().parse_trendshift('<a href="https://github.com/ad/sponsor">ad/sponsor</a>',
                                           TODAY, "2026-09-30T10:00:00+00:00")

    def test_topic_uses_directory_links_not_guessed_slug(self):
        requests = []
        def opener(request, timeout):
            requests.append(request.full_url)
            html = ('<a href="/topics/ai-skills">#AI skills</a>' if request.full_url.endswith('/topics')
                    else '<h1>AI skills</h1><a href="/repositories/123">a/skills</a>')
            response = io.BytesIO(html.encode());response.headers={};return response
        client = self.module().TrendshiftClient(opener=opener)
        self.assertEqual(len(client.topic("AI skills", "2026-09-30T10:00:00+00:00").candidates), 1)
        self.assertEqual(client.topic("unknown", "2026-09-30T10:00:00+00:00").candidates, ())
        self.assertEqual(requests, ['https://trendshift.io/topics', 'https://trendshift.io/topics/ai-skills'])
