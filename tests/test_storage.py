import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from github_radar.models import GrowthCoverage, Recommendation, Repository, StarSnapshot
from github_radar.ai_types import (AIRepositoryInput, CandidateBatch, InsightText,
                                   ProjectExplanation, RelevanceVerdict)
from github_radar.storage import RadarStore


def repository(repo_id=7, stars=1500):
    return Repository(
        id=repo_id,
        full_name="example/project",
        html_url="https://github.com/example/project",
        description="A local AI project",
        topics=("local-ai",),
        language="Python",
        stars=stars,
        archived=False,
    )


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / "UserData"

    def test_scheduler_binding_survives_directory_move(self):
        store = RadarStore(self.data_dir)
        binding = {"install_dir": str(self.data_dir.parent / "Radar old"), "command": str(self.data_dir.parent / "Radar old" / "GitHubRadar.exe"),
                   "arguments": ["--scheduled-refresh", "--data-dir", str(self.data_dir.parent / "Radar old" / "UserData")]}
        self.assertIsNone(store.load_scheduler_binding())
        store.save_scheduler_binding(binding)
        self.assertEqual(RadarStore(self.data_dir).load_scheduler_binding(), binding)
        store.save_scheduler_binding(None)
        self.assertIsNone(RadarStore(self.data_dir).load_scheduler_binding())

    def test_history_reads_each_date_and_its_own_snapshot(self):
        store = RadarStore(self.data_dir)
        first, second = "2026-09-26", "2026-09-27"
        at_first, at_second = first + "T08:00:00+08:00", second + "T08:00:00+08:00"
        first_pick = Recommendation(7, first, "growth", None, 40, None, at_first)
        second_pick = Recommendation(7, second, "growth", None, 90, None, at_second)
        store.commit_daily(first, [repository(7, 1500)],
                           [StarSnapshot(7, first, 1500, at_first)], [first_pick])
        store.commit_daily(second, [repository(7, 1590), repository(8, 3000)],
                           [StarSnapshot(7, second, 1590, at_second)],
                           [Recommendation(8, second, "growth", None, 100, None, at_second),
                            second_pick])
        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.history_dates(), [(second, 2), (first, 1)])
        self.assertEqual(reopened.snapshots_on(first, [7])[7].stars, 1500)
        self.assertEqual(reopened.snapshots_on(second, [7])[7].stars, 1590)
        self.assertEqual(reopened.recommendation_on(first, 7), first_pick)
        self.assertEqual(reopened.recommendation_on(second, 7), second_pick)
        self.assertEqual(reopened.latest_recommendation_for_repo(7), second_pick)
        self.assertEqual(reopened.history_dates()[0][1], len(reopened.daily_recommendations(second)))

    def test_history_does_not_replace_missing_snapshot_with_current_stars(self):
        store = RadarStore(self.data_dir)
        day, at = "2026-09-26", "2026-09-26T08:00:00+08:00"
        store.commit_daily(day, [repository(7, 1500)], [],
                           [Recommendation(7, day, "growth", None, 30, None, at)])
        self.assertEqual(store.snapshots_on(day, [7]), {})
        self.assertEqual(store.snapshots_on(day, []), {})
        self.assertIsNone(store.recommendation_on("2026-09-25", 7))
        self.assertEqual(store.daily_recommendations("2026-09-25"), [])

    def test_following_persists_and_reads_latest_local_snapshot(self):
        store = RadarStore(self.data_dir)
        day, at = "2026-09-26", "2026-09-26T08:00:00+08:00"
        next_day, later = "2026-09-27", "2026-09-27T08:00:00+08:00"
        pick = Recommendation(7, day, "growth", None, 20, None, at)
        store.commit_daily(day, [repository(7, 1500)],
                           [StarSnapshot(7, day, 1500, at)], [pick])
        store.commit_daily(next_day, [repository(7, 1550)],
                           [StarSnapshot(7, next_day, 1550, later)], [])
        self.assertTrue(store.set_followed(7, True, at))
        self.assertTrue(store.set_followed(7, True, later))
        reopened = RadarStore(self.data_dir)
        self.assertTrue(reopened.is_followed(7))
        followed = reopened.followed_repositories()
        self.assertEqual(len(followed), 1)
        self.assertEqual(followed[0][0].id, 7)
        self.assertEqual(followed[0][1], at)
        self.assertEqual(followed[0][2].stars, 1550)
        self.assertEqual(followed[0][2].observed_at, later)
        self.assertEqual(reopened.daily_recommendations(day), [pick])
        self.assertFalse(reopened.set_followed(7, False, later))
        self.assertFalse(RadarStore(self.data_dir).is_followed(7))
        self.assertEqual(RadarStore(self.data_dir).followed_repositories(), [])

    def test_following_rejects_unknown_repo_and_keeps_unsnapshotted_repo(self):
        store = RadarStore(self.data_dir)
        with self.assertRaises(ValueError):
            store.set_followed(999, True, "2026-09-26T08:00:00+08:00")
        day, at = "2026-09-26", "2026-09-26T08:00:00+08:00"
        store.commit_daily(day, [repository()], [],
                           [Recommendation(7, day, "growth", None, None, None, at)])
        self.assertTrue(store.set_followed(7, True, at))
        self.assertIsNone(RadarStore(self.data_dir).followed_repositories()[0][2])

    def test_keyword_normalization_and_reopen(self):
        store = RadarStore(self.data_dir)
        first = store.add_keyword(" AI Coding ")
        second = store.add_keyword("ai coding")
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.term, "AI Coding")
        self.assertEqual(first.min_stars, 1000)
        self.assertEqual(len(RadarStore(self.data_dir).list_keywords()), 1)

    def test_add_keyword_rejects_non_integer_minimum(self):
        store = RadarStore(self.data_dir)
        for invalid in (1.5, True, "1000", -1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                store.add_keyword("MCP", invalid)
        self.assertEqual(store.list_keywords(), [])

    def test_schema_upgrade_rolls_back_when_a_migration_fails(self):
        self.data_dir.mkdir(parents=True)
        db = self.data_dir / "radar.db"
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("""CREATE TABLE keywords (
                id INTEGER PRIMARY KEY, term TEXT NOT NULL,
                term_key TEXT NOT NULL UNIQUE, min_stars INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1)""")
            connection.execute("INSERT INTO keywords VALUES (1, 'MCP', 'mcp', 1800, 1)")
        with patch.object(RadarStore, "_migrate_recommendation_key",
                          side_effect=RuntimeError("forced migration failure")):
            with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                RadarStore(self.data_dir)
        with closing(sqlite3.connect(db)) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(keywords)")]
            follows = connection.execute(
                "SELECT name FROM sqlite_master WHERE name='follows'").fetchone()
        self.assertNotIn("deleted_at", columns)
        self.assertIsNone(follows)
        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.list_keywords()[0].min_stars, 1800)

    def test_keyword_soft_delete_keeps_history_and_restores_original_rule(self):
        store = RadarStore(self.data_dir)
        rule = store.add_keyword("MCP", min_stars=1800)
        day, at = "2026-09-26", "2026-09-26T08:00:00+08:00"
        item = Recommendation(7, day, "keyword", rule.id, None, None, at)
        store.commit_daily(day, [repository()], [StarSnapshot(7, day, 1500, at)], [item])
        before = store.seen_repo_ids()
        store.soft_delete_keyword(rule.id, at)
        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.list_keywords(), [])
        self.assertEqual(reopened.all_keywords()[0].term, "MCP")
        self.assertEqual(reopened.daily_recommendations(day), [item])
        self.assertEqual(reopened.seen_repo_ids(), before)
        restored = reopened.add_keyword("mcp", min_stars=9999)
        self.assertEqual((restored.id, restored.min_stars, restored.enabled),
                         (rule.id, 1800, True))
        self.assertEqual(len(RadarStore(self.data_dir).list_keywords()), 1)

    def test_keyword_settings_validate_and_change_future_selection_only(self):
        store = RadarStore(self.data_dir)
        rule = store.add_keyword("MCP", min_stars=1000)
        self.assertFalse(store.set_keyword_enabled(rule.id, False).enabled)
        self.assertEqual(store.set_keyword_min_stars(rule.id, 2500).min_stars, 2500)
        with self.assertRaises(ValueError):
            store.set_keyword_min_stars(rule.id, -1)
        with self.assertRaises(ValueError):
            store.set_keyword_enabled(999, True)
        self.assertEqual(RadarStore(self.data_dir).list_keywords()[0].min_stars, 2500)
        store.soft_delete_keyword(rule.id, "2026-09-26T08:00:00+08:00")
        with self.assertRaises(ValueError):
            store.set_keyword_enabled(rule.id, True)

    def test_deleted_keyword_cannot_be_committed_by_update_already_running(self):
        store = RadarStore(self.data_dir)
        rule = store.add_keyword("MCP")
        day, at = "2026-09-26", "2026-09-26T08:00:00+08:00"
        store.soft_delete_keyword(rule.id, at)
        store.commit_daily(day, [repository()], [StarSnapshot(7, day, 1500, at)],
                           [Recommendation(7, day, "keyword", rule.id, None, None, at)],
                           completed_at=at, completed_sections=[("keyword", rule.id)])
        self.assertEqual(store.daily_recommendations(day), [])
        self.assertNotIn(("keyword", rule.id), store.daily_sections(day))

    def test_old_keyword_table_migrates_repeatedly_without_losing_rule(self):
        self.data_dir.mkdir(parents=True)
        with closing(sqlite3.connect(self.data_dir / "radar.db")) as connection, connection:
            connection.execute("""CREATE TABLE keywords (
                id INTEGER PRIMARY KEY, term TEXT NOT NULL,
                term_key TEXT NOT NULL UNIQUE,
                min_stars INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
            )""")
            connection.execute(
                "INSERT INTO keywords(id, term, term_key, min_stars, enabled) "
                "VALUES (1, 'MCP', 'mcp', 1800, 1)"
            )
        first = RadarStore(self.data_dir)
        second = RadarStore(self.data_dir)
        self.assertEqual(first.list_keywords(), second.list_keywords())
        self.assertEqual(second.list_keywords()[0].min_stars, 1800)
        second.soft_delete_keyword(1, "2026-09-26T08:00:00+08:00")
        self.assertEqual(RadarStore(self.data_dir).list_keywords(), [])

    def test_language_and_font_preferences_persist_without_touching_ai_model(self):
        store = RadarStore(self.data_dir)
        self.assertEqual(store.load_preferences(), ("zh", "normal"))
        store.save_ai_model("gpt-6-luna")
        store.save_preferences("en", "large")
        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.load_preferences(), ("en", "large"))
        self.assertEqual(reopened.load_ai_model(), "gpt-6-luna")
        with self.assertRaises(ValueError):
            reopened.save_preferences("fr", "small")
        with self.assertRaises(ValueError):
            reopened.save_preferences("zh", "gigantic")
        self.assertEqual(reopened.load_preferences(), ("en", "large"))

    def test_ai_migration_preserves_old_data(self):
        store = RadarStore(self.data_dir)
        store.commit_daily("2026-09-26", [repository(1)], [], [
            Recommendation(1, "2026-09-26", "growth", None, 10, None,
                           "2026-09-26T08:00:00+08:00")])
        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.daily_recommendations("2026-09-26")[0].repo_id, 1)
        self.assertEqual(reopened.ai_progress("2026-09-26", 10, None).checked_count, 0)

    def test_batch_replaces_only_keyword_picks(self):
        store = RadarStore(self.data_dir)
        key = store.add_keyword("local ai")
        other = store.add_keyword("agents")
        day, at = "2026-09-26", "2026-09-26T08:00:00+08:00"
        store.commit_daily(day, [repository(n, stars=3000-n) for n in (1, 2, 3, 4)], [], [
            Recommendation(1, day, "growth", None, 10, None, at),
            Recommendation(2, day, "keyword", key.id, None, None, at),
            Recommendation(3, day, "keyword", key.id, None, None, at),
            Recommendation(4, day, "keyword", other.id, None, None, at),
        ])
        candidates = CandidateBatch(tuple(AIRepositoryInput(repository(n, stars), "README", False)
                                      for n, stars in ((2, 2998), (3, 2997), (5, 5000), (6, 4000))),
                                    1, 12)
        decisions = tuple(RelevanceVerdict(n, verdict, "Evidence") for n, verdict in (
            (2, "relevant"), (3, "irrelevant"), (5, "relevant"), (6, "uncertain")))
        store.commit_ai_batch(day, key.id, None, candidates, decisions, at)
        picks = store.daily_recommendations(day)
        self.assertEqual([item.repo_id for item in picks if item.section == "growth"], [1])
        self.assertEqual([item.repo_id for item in picks if item.keyword_id == other.id], [4])
        self.assertEqual([item.repo_id for item in picks if item.keyword_id == key.id], [5, 2])
        self.assertIn(3, store.seen_repo_ids())
        self.assertEqual(store.ai_progress(day, key.id, None).checked_count, 4)
        self.assertEqual(len(store.ai_verdicts(day, key.id, None)), 4)
        self.assertEqual(RadarStore(self.data_dir).daily_recommendations(day), picks)

    def test_ai_refill_preserves_search_ranks_after_history_exclusion(self):
        store = RadarStore(self.data_dir)
        key = store.add_keyword('local ai')
        old, day, at = '2026-09-25', '2026-09-26', '2026-09-26T08:00:00+08:00'
        store.commit_daily(old, [repository(n, 4000-n) for n in range(1, 6)], [],
                           [Recommendation(n, old, 'keyword', key.id, None, None, at, rank=n) for n in range(1, 6)])
        batch = CandidateBatch(tuple(AIRepositoryInput(repository(n, 4000-n), None, True, candidate_rank=n)
                                     for n in (6, 7)), 1, 7)
        store.commit_ai_batch(day, key.id, None, batch,
                              tuple(RelevanceVerdict(n, 'relevant', 'Evidence') for n in (6, 7)), at)
        self.assertEqual([(r.repo_id, r.rank) for r in RadarStore(self.data_dir).daily_recommendations(day)], [(6, 6), (7, 7)])

    def test_batch_failure_rolls_back(self):
        store = RadarStore(self.data_dir)
        key = store.add_keyword("local ai")
        day, at = "2026-09-26", "2026-09-26T08:00:00+08:00"
        store.commit_daily(day, [repository(2)], [], [
            Recommendation(2, day, "keyword", key.id, None, None, at)])
        with closing(sqlite3.connect(store.db_path)) as connection, connection:
            connection.execute("""CREATE TRIGGER reject_ai_verdict BEFORE INSERT ON ai_verdicts
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END""")
        batch = CandidateBatch((AIRepositoryInput(repository(2), None, True),), 1, 1)
        with self.assertRaises(sqlite3.DatabaseError):
            store.commit_ai_batch(day, key.id, None, batch,
                                  (RelevanceVerdict(2, "irrelevant", "Wrong keyword"),), at)
        self.assertEqual([item.repo_id for item in store.daily_recommendations(day)], [2])
        self.assertEqual(store.ai_progress(day, key.id, None).checked_count, 0)
        self.assertEqual(store.ai_verdicts(day, key.id, None), {})

    def test_explanation_cache_survives_reopen(self):
        store = RadarStore(self.data_dir)
        insight = InsightText("Summary", "Purpose", "Scenario", "Users", ("Feature",))
        explanation = ProjectExplanation(insight, insight, "relevant", ("Description",), False)
        store.save_ai_model("gpt-6-sol")
        store.save_explanation(7, 10, "gpt-6-sol", "input-v1", explanation)
        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.load_ai_model(), "gpt-6-sol")
        self.assertEqual(reopened.load_explanation(7, 10, "gpt-6-sol", "input-v1"), explanation)
        self.assertIsNone(reopened.load_explanation(7, 10, None, "input-v1"))

    def test_duplicate_repository_recommendation_is_atomic(self):
        store = RadarStore(self.data_dir)
        repo = repository()
        snapshot = StarSnapshot(7, "2026-09-26", 1500, "2026-09-26T08:00:00+08:00")
        growth = Recommendation(7, "2026-09-26", "growth", None, None, None, snapshot.observed_at)
        keyword = Recommendation(7, "2026-09-26", "keyword", 1, None, None, snapshot.observed_at)
        with self.assertRaises(ValueError):
            store.commit_daily("2026-09-26", [repo], [snapshot], [growth, keyword])
        self.assertEqual(store.seen_repo_ids(), set())
        self.assertEqual(store.tracked_repositories(), [])

    def test_snapshots_and_recommendations_survive_reopen(self):
        store = RadarStore(self.data_dir)
        repo = repository()
        first = StarSnapshot(7, "2026-09-25", 1400, "2026-09-25T08:00:00+08:00")
        store.commit_daily("2026-09-25", [repo], [first], [])
        second = StarSnapshot(7, "2026-09-26", 1500, "2026-09-26T08:00:00+08:00")
        recommendation = Recommendation(7, "2026-09-26", "growth", None, 100, first.observed_at, second.observed_at)
        store.commit_daily("2026-09-26", [repo], [second], [recommendation])

        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.snapshots_before("2026-09-26", [7])[7].stars, 1400)
        self.assertEqual(reopened.seen_repo_ids(), {7})
        self.assertEqual(reopened.daily_recommendations("2026-09-26"), [recommendation])
        self.assertEqual(reopened.latest_recommendations(), [recommendation])
        self.assertEqual(reopened.tracked_repositories(), [repo])
        self.assertEqual(reopened.repositories_for_ids([7]), {7: repo})

    def test_growth_repository_can_return_on_next_date_but_not_twice_same_date(self):
        store = RadarStore(self.data_dir)
        first = Recommendation(7, "2026-09-26", "growth", None, 30, None,
                               "2026-09-26T08:00:00+08:00", "github_daily_new", "2026-09-25", 1, "new")
        second = Recommendation(7, "2026-09-27", "growth", None, 70, None,
                                "2026-09-27T08:00:00+08:00", "github_daily_new", "2026-09-26", 1, "old")
        store.commit_daily(first.local_date, [repository()], [], [first])
        store.commit_daily(second.local_date, [repository(stars=1570)], [], [second])
        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.daily_recommendations(first.local_date), [first])
        self.assertEqual(reopened.daily_recommendations(second.local_date), [second])
        self.assertEqual(reopened.seen_repo_ids(), {7})
        with self.assertRaises(ValueError):
            reopened.commit_daily(second.local_date, [repository()], [], [second])
        self.assertEqual(reopened.daily_recommendations(first.local_date), [first])

    def test_recent_growth_repositories_use_previous_rank_not_total_stars(self):
        store = RadarStore(self.data_dir)
        day = "2026-09-26"
        at = "2026-09-26T08:00:00+08:00"
        store.commit_daily(day, [repository(7, 1200), repository(8, 8000)], [], [
            Recommendation(7, day, "growth", None, 90, None, at),
            Recommendation(8, day, "growth", None, 50, None, at),
        ])
        self.assertEqual([repo.id for repo in store.recent_growth_repositories("2026-09-27")],
                         [7, 8])

    def test_legacy_database_migration_keeps_recommendation_ai_and_snapshot(self):
        self.data_dir.mkdir(parents=True)
        db = self.data_dir / "radar.db"
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.executescript("""
                CREATE TABLE repositories (
                    id INTEGER PRIMARY KEY, full_name TEXT, html_url TEXT,
                    description TEXT, topics TEXT, language TEXT, stars INTEGER, archived INTEGER
                );
                CREATE TABLE recommendations (
                    repo_id INTEGER PRIMARY KEY, local_date TEXT, section TEXT,
                    keyword_id INTEGER, star_delta INTEGER, baseline_at TEXT,
                    observed_at TEXT, position INTEGER, metric_basis TEXT, metric_date TEXT
                );
                CREATE TABLE snapshots (
                    repo_id INTEGER, local_date TEXT, stars INTEGER, observed_at TEXT,
                    PRIMARY KEY(repo_id, local_date)
                );
                CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE ai_keyword_progress (
                    local_date TEXT, keyword_id INTEGER, model_key TEXT,
                    next_page INTEGER, next_offset INTEGER, checked_count INTEGER,
                    PRIMARY KEY(local_date, keyword_id, model_key)
                );
                CREATE TABLE ai_verdicts (
                    local_date TEXT, keyword_id INTEGER, model_key TEXT, repo_id INTEGER,
                    verdict TEXT, reason TEXT, source_limited INTEGER, checked_at TEXT,
                    PRIMARY KEY(local_date, keyword_id, model_key, repo_id)
                );
                CREATE TABLE ai_explanations (
                    repo_id INTEGER, keyword_key INTEGER, model_key TEXT,
                    source_hash TEXT, content TEXT, saved_at TEXT,
                    PRIMARY KEY(repo_id, keyword_key, model_key, source_hash)
                );
                INSERT INTO repositories VALUES
                    (7, 'example/project', 'https://github.com/example/project',
                     'A local AI project', '["local-ai"]', 'Python', 1500, 0);
                INSERT INTO recommendations VALUES
                    (7, '2026-09-26', 'growth', NULL, 30, NULL,
                     '2026-09-26T08:00:00+08:00', 1, 'github_daily_new', '2026-09-25');
                INSERT INTO snapshots VALUES
                    (7, '2026-09-26', 1500, '2026-09-26T08:00:00+08:00');
                INSERT INTO settings VALUES ('ai_model', 'gpt-6-luna');
                INSERT INTO ai_keyword_progress VALUES
                    ('2026-09-26', 10, 'gpt-6-luna', 2, 4, 20);
                INSERT INTO ai_verdicts VALUES
                    ('2026-09-26', 10, 'gpt-6-luna', 7, 'relevant', 'Matches', 1,
                     '2026-09-26T08:00:00+08:00');
                INSERT INTO ai_explanations VALUES
                    (7, 10, 'gpt-6-luna', 'saved-input', '{}',
                     '2026-09-26T08:00:00+08:00');
            """)
        store = RadarStore(self.data_dir)
        self.assertEqual(store.daily_recommendations("2026-09-26")[0].repo_id, 7)
        self.assertEqual(store.snapshots_before("2026-09-27", [7])[7].stars, 1500)
        self.assertEqual(store.load_ai_model(), "gpt-6-luna")
        self.assertEqual(store.ai_progress("2026-09-26", 10, "gpt-6-luna").checked_count, 20)
        self.assertEqual(store.ai_verdicts("2026-09-26", 10, "gpt-6-luna")[7].verdict,
                         "relevant")
        with closing(sqlite3.connect(db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ai_explanations").fetchone(),
                             (1,))
        store.commit_daily("2026-09-27", [repository(7, 1550)], [], [
            Recommendation(7, "2026-09-27", "growth", None, 50, None,
                           "2026-09-27T08:00:00+08:00")])
        self.assertEqual(len(RadarStore(self.data_dir).daily_recommendations("2026-09-26")), 1)
        self.assertEqual(len(RadarStore(self.data_dir).daily_recommendations("2026-09-27")), 1)

    def test_failed_legacy_migration_keeps_original_rows(self):
        self.data_dir.mkdir(parents=True)
        db = self.data_dir / "radar.db"
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.executescript("""
                CREATE TABLE repositories (
                    id INTEGER PRIMARY KEY, full_name TEXT, html_url TEXT,
                    description TEXT, topics TEXT, language TEXT, stars INTEGER, archived INTEGER
                );
                CREATE TABLE recommendations (
                    repo_id INTEGER PRIMARY KEY, local_date TEXT, section TEXT,
                    keyword_id INTEGER, star_delta INTEGER, baseline_at TEXT, observed_at TEXT
                );
                INSERT INTO recommendations VALUES (7, NULL, 'growth', NULL, 20, NULL,
                    '2026-09-26T08:00:00+08:00');
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            RadarStore(self.data_dir)
        with closing(sqlite3.connect(db)) as connection:
            self.assertEqual(connection.execute("SELECT repo_id FROM recommendations").fetchall(),
                             [(7,)])

    def test_two_store_instances_share_database(self):
        first = RadarStore(self.data_dir)
        second = RadarStore(self.data_dir)
        first.add_keyword("Local AI")
        second.add_keyword("MCP")
        self.assertEqual([item.term for item in first.list_keywords()], ["Local AI", "MCP"])

    def test_recommendations_keep_selection_order_not_repository_id_order(self):
        store = RadarStore(self.data_dir)
        high_id = repository(90, stars=3000)
        low_id = repository(10, stars=2000)
        records = [
            Recommendation(90, "2026-09-26", "growth", None, None, None, "2026-09-26T08:00:00+08:00"),
            Recommendation(10, "2026-09-26", "keyword", 1, None, None, "2026-09-26T08:00:00+08:00"),
        ]
        store.commit_daily("2026-09-26", [high_id, low_id], [], records)
        self.assertEqual(store.daily_recommendations("2026-09-26"), records)

    def test_existing_database_migrates_recommendation_order(self):
        self.data_dir.mkdir(parents=True)
        with closing(sqlite3.connect(self.data_dir / "radar.db")) as connection, connection:
            connection.executescript("""
                CREATE TABLE repositories (
                    id INTEGER PRIMARY KEY, full_name TEXT, html_url TEXT,
                    description TEXT, topics TEXT, language TEXT, stars INTEGER, archived INTEGER
                );
                CREATE TABLE recommendations (
                    repo_id INTEGER PRIMARY KEY, local_date TEXT, section TEXT,
                    keyword_id INTEGER, star_delta INTEGER, baseline_at TEXT, observed_at TEXT
                );
                INSERT INTO repositories VALUES
                    (90, 'owner/growth', 'https://github.com/owner/growth', '', '[]', NULL, 3000, 0),
                    (10, 'owner/keyword', 'https://github.com/owner/keyword', '', '[]', NULL, 2000, 0);
                INSERT INTO recommendations VALUES
                    (90, '2026-09-26', 'growth', NULL, NULL, NULL, '2026-09-26T08:00:00+08:00'),
                    (10, '2026-09-26', 'keyword', 1, NULL, NULL, '2026-09-26T08:00:00+08:00');
            """)
        records = RadarStore(self.data_dir).daily_recommendations("2026-09-26")
        self.assertEqual([record.repo_id for record in records], [90, 10])

    def test_empty_successful_issue_and_sections_survive_reopen(self):
        store = RadarStore(self.data_dir)
        store.commit_daily(
            "2026-09-26", [], [], [],
            completed_at="2026-09-26T08:00:00+08:00",
            completed_sections=[("growth", None), ("keyword", 3)],
        )
        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.latest_successful_date(), "2026-09-26")
        self.assertEqual(reopened.daily_updated_at("2026-09-26"), "2026-09-26T08:00:00+08:00")
        self.assertEqual(reopened.daily_sections("2026-09-26"), {("growth", None), ("keyword", 3)})

    def test_official_growth_and_coverage_survive_reopen(self):
        store = RadarStore(self.data_dir)
        observed = "2026-09-26T08:00:00+08:00"
        recommendation = Recommendation(
            7, "2026-09-26", "growth", None, 38, None, observed,
            "github_daily_new", "2026-09-25", 1, "new",
        )
        coverage = GrowthCoverage(32, 19, ("trending", "search", "tracked"),
                                  "2026-09-25", "github_daily_new")
        store.commit_daily("2026-09-26", [repository()], [], [recommendation],
                           growth_coverage=coverage)

        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.daily_recommendations("2026-09-26"), [recommendation])
        self.assertEqual(reopened.growth_coverage("2026-09-26"), coverage)

    def test_legacy_growth_record_remains_readable_after_new_migration(self):
        self.data_dir.mkdir(parents=True)
        with closing(sqlite3.connect(self.data_dir / "radar.db")) as connection, connection:
            connection.executescript("""
                CREATE TABLE repositories (
                    id INTEGER PRIMARY KEY, full_name TEXT, html_url TEXT,
                    description TEXT, topics TEXT, language TEXT, stars INTEGER, archived INTEGER
                );
                CREATE TABLE recommendations (
                    repo_id INTEGER PRIMARY KEY, local_date TEXT, section TEXT,
                    keyword_id INTEGER, star_delta INTEGER, baseline_at TEXT, observed_at TEXT
                );
                INSERT INTO repositories VALUES
                    (7, 'example/project', 'https://github.com/example/project', '', '[]', NULL, 1500, 0);
                INSERT INTO recommendations VALUES
                    (7, '2026-09-26', 'growth', NULL, 42,
                     '2026-09-25T08:00:00+08:00', '2026-09-26T08:00:00+08:00');
            """)
        store = RadarStore(self.data_dir)
        record = store.daily_recommendations("2026-09-26")[0]
        self.assertEqual(record.star_delta, 42)
        self.assertEqual(record.baseline_at, "2026-09-25T08:00:00+08:00")
        self.assertIsNone(record.metric_basis)


if __name__ == "__main__":
    unittest.main()
