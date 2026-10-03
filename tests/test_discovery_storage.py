"""Persistence contracts: discovery never silently becomes a recommendation."""
import importlib
import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import fields
from pathlib import Path

from github_radar.models import Recommendation, Repository, StarSnapshot
from github_radar.storage import RadarStore


AT = "2026-09-30T09:00:00+08:00"


class DiscoveryStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "UserData"

    def types(self):
        self.assertIsNotNone(importlib.util.find_spec("github_radar.discovery_types"),
                             "discovery persistence types are not implemented")
        return importlib.import_module("github_radar.discovery_types")

    def ranked_pick(self):
        self.assertIn("rank", {item.name for item in fields(Recommendation)},
                      "daily recommendations do not persist true ranking yet")
        return Recommendation(7, "2026-09-30", "growth", None, 42, None, AT,
                              "github_daily_new", "2026-09-28", 8, "new", (1, 2))

    def test_candidate_scan_is_not_recommendation(self):
        types = self.types()
        store = RadarStore(self.data)
        store.save_discovery_batch(types.DiscoveryBatch("daily", (
            types.DiscoveryCandidate("owner/project", 7, ("daily",), AT),
        ), "next-10", False, ()))
        store.save_discovery_batch(types.DiscoveryBatch("search", (
            types.DiscoveryCandidate("owner/renamed", 7, ("search",), AT),
        ), None, True, ()))
        reopened = RadarStore(self.data)
        candidates = reopened.catalog_candidates(10)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].repo_id, 7)
        self.assertEqual(candidates[0].full_name, "owner/renamed")
        self.assertEqual(set(candidates[0].source_names), {"daily", "search"})
        self.assertEqual(reopened.seen_repo_ids(), set())
        self.assertEqual(reopened.load_discovery_cursor("daily"), "next-10")
        self.assertIsNone(reopened.load_discovery_cursor("search"))

    def test_unverified_name_resolves_but_recycled_name_preserves_ids(self):
        types = self.types()
        store = RadarStore(self.data)
        for source, repo_id in (("daily", None), ("official", 7), ("later", 8)):
            store.save_discovery_batch(types.DiscoveryBatch(source, (
                types.DiscoveryCandidate("owner/project", repo_id, (source,), AT),
            ), None, True, ()))
        candidates = store.catalog_candidates(10)
        self.assertEqual({item.repo_id for item in candidates}, {7, 8})
        first = next(item for item in candidates if item.repo_id == 7)
        self.assertEqual(set(first.source_names), {"daily", "official"})
        self.assertEqual(store.seen_repo_ids(), set())

    def test_daily_rank_snapshot_survives_restart(self):
        store = RadarStore(self.data)
        repo = Repository(7, "owner/project", "https://github.com/owner/project",
                          "A project", (), "Python", 1903, False)
        pick = self.ranked_pick()
        store.commit_daily("2026-09-30", [repo],
                           [StarSnapshot(7, "2026-09-30", 1903, AT)], [pick])
        saved = RadarStore(self.data).daily_recommendations("2026-09-30")[0]
        self.assertEqual(saved.rank, 8)
        self.assertEqual(saved.display_role, "new")
        self.assertEqual(saved.matched_keyword_ids, (1, 2))
        self.assertEqual(saved.metric_date, "2026-09-28")

    def test_source_evidence_keeps_rank_kind_and_raw_values(self):
        types = self.types()
        store = RadarStore(self.data)
        daily = types.SourceEvidence("trendshift_daily", "owner/project",
            "https://trendshift.io/", AT, "daily", "2026-09-28",
            "trendshift_daily", 2, "1.9k", "42", repo_id=7)
        topic = types.SourceEvidence("trendshift_topic", "owner/project",
            "https://trendshift.io/topics/ai-skills", "2026-09-30T09:01:00+08:00",
            "topic", None, "none", None, "1.9k", None, ("AI skills",), repo_id=7)
        store.save_source_evidence(daily)
        store.save_source_evidence(topic)
        store.save_source_evidence(daily)
        saved = RadarStore(self.data).source_evidence(7)
        self.assertEqual(len(saved), 2)
        self.assertEqual(set(item.rank_kind for item in saved), {"trendshift_daily", "none"})
        self.assertTrue(all(item.total_stars_text == "1.9k" for item in saved))
        self.assertIsNone(next(item for item in saved if item.period == "topic").stat_date)
        self.assertEqual(store.seen_repo_ids(), set())

    def test_old_database_migration_preserves_user_data(self):
        self.data.mkdir(parents=True)
        with closing(sqlite3.connect(self.data / "radar.db")) as db, db:
            db.executescript("""
                CREATE TABLE keywords (id INTEGER PRIMARY KEY, term TEXT, term_key TEXT UNIQUE,
                    min_stars INTEGER, enabled INTEGER);
                INSERT INTO keywords VALUES (1, 'MCP', 'mcp', 1000, 1);
                CREATE TABLE repositories (id INTEGER PRIMARY KEY, full_name TEXT,
                    html_url TEXT, description TEXT, topics TEXT, language TEXT,
                    stars INTEGER, archived INTEGER);
                INSERT INTO repositories VALUES (7,'owner/project','https://github.com/owner/project',
                    'MCP project','[]','Python',1903,0);
                CREATE TABLE recommendations (repo_id INTEGER, local_date TEXT, section TEXT,
                    keyword_id INTEGER, star_delta INTEGER, baseline_at TEXT, observed_at TEXT,
                    position INTEGER, metric_basis TEXT, metric_date TEXT,
                    PRIMARY KEY(local_date,repo_id));
                INSERT INTO recommendations VALUES (7,'2026-09-29','growth',NULL,42,NULL,
                    '2026-09-29T09:00:00+08:00',1,'github_daily_new','2026-09-27');
                CREATE TABLE follows (repo_id INTEGER PRIMARY KEY, followed_at TEXT);
                INSERT INTO follows VALUES (7,'2026-09-29T09:00:00+08:00');
                CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT);
                INSERT INTO settings VALUES ('language','en');
            """)
        store = RadarStore(self.data)
        saved = store.daily_recommendations("2026-09-29")[0]
        self.assertEqual(saved.rank, 1, "Known legacy growth keeps its saved position")
        self.assertEqual(saved.display_role, 'new', "A first appearance remains new")
        self.assertEqual((saved.star_delta,saved.metric_date),(42,'2026-09-27'))
        self.assertEqual(saved.matched_keyword_ids, ())
        self.assertTrue(store.is_followed(7))
        self.assertEqual(store.list_keywords()[0].term, "MCP")
        self.assertEqual(store.load_preferences()[0], "en")
        self.assertEqual(store.seen_repo_ids(), {7})
        self.assertEqual(len(RadarStore(self.data).daily_recommendations("2026-09-29")), 1)

    def test_candidate_save_is_atomic_on_invalid_input(self):
        types = self.types()
        store = RadarStore(self.data)
        valid = types.DiscoveryCandidate("owner/project", 7, ("daily",), AT)
        invalid = types.DiscoveryCandidate("bad/path/extra", 8, ("daily",), AT)
        with self.assertRaises(ValueError):
            store.save_discovery_batch(types.DiscoveryBatch("daily", (valid, invalid),
                                                         "bad-cursor", False, ()))
        self.assertEqual(store.catalog_candidates(10), [])
        self.assertIsNone(store.load_discovery_cursor("daily"))
