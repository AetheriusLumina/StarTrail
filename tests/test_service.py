import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path

from github_radar.github_client import GitHubClient, GitHubRateLimitError, GitHubRequestError
from github_radar.ai_types import AIRepositoryInput, CandidateBatch, RelevanceVerdict
from github_radar.models import GrowthCoverage, Recommendation, Repository, StarDay, StarSnapshot
from github_radar.service import RadarService
from github_radar.storage import RadarStore


def repo(repo_id, stars=1500, description="Local AI tools"):
    return Repository(
        repo_id, f"owner/repo-{repo_id}", f"https://github.com/owner/repo-{repo_id}",
        description, ("local-ai",), "Python", stars, False,
    )


class FakeClient:
    def __init__(self, created=(), pushed=(), keywords=None, error=None, remaining=100, repository_updates=(), get_error=None, histories=None, core_remaining=None, strict_search_limit=False):
        self.created = list(created)
        self.pushed = list(pushed)
        self.keywords = keywords or {}
        self.error = error
        self.get_error = get_error
        self.remaining = remaining
        self.core_remaining = core_remaining if core_remaining is not None else remaining
        self.search_remaining = 10
        self.strict_search_limit = strict_search_limit
        self.histories = histories or {}
        self.history_calls = []
        self.calls = []
        self.get_calls = []
        self.by_name = {item.full_name: item for item in (*created, *pushed, *repository_updates)}

    def search(self, query, page=1, per_page=100, sort="stars"):
        self.calls.append((query, page))
        if self.strict_search_limit:
            if self.search_remaining <= 0:
                raise GitHubRateLimitError(None)
            self.search_remaining -= 1
        if self.error:
            raise self.error
        if query.startswith("created:"):
            return self.created if page == 1 else []
        if query.startswith("pushed:"):
            return self.pushed if page == 1 else []
        term = query.split(" stars:")[0].strip('"')
        pages = self.keywords.get(term, [])
        return pages[page - 1] if page <= len(pages) else []

    def get_repository(self, full_name):
        self.get_calls.append(full_name)
        if self.get_error:
            raise self.get_error
        self.core_remaining -= 1
        return self.by_name[full_name]

    def star_history(self, full_name):
        self.history_calls.append(full_name)
        self.core_remaining -= 1
        return self.histories.get(full_name, [])


class FakeTrending:
    def __init__(self, names=(), error=None):
        self.names = list(names)
        self.error = error

    def repo_names(self):
        if self.error:
            raise self.error
        return self.names


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RadarStore(Path(self.temp.name) / "UserData")
        self.today = "2026-09-26"
        self.now = "2026-09-26T08:00:00+08:00"

    def test_load_latest_reads_saved_issue_without_network_and_marks_old_date(self):
        self.store.commit_daily(
            "2026-09-25", [repo(1)],
            [StarSnapshot(1, "2026-09-25", 1500, "2026-09-25T08:00:00+08:00")],
            [Recommendation(1, "2026-09-25", "growth", None, None, None, "2026-09-25T08:00:00+08:00")],
            completed_at="2026-09-25T08:00:00+08:00",
            completed_sections=[("growth", None)],
        )
        client = FakeClient(error=AssertionError("network must not be used"))
        result = RadarService(client, self.store).load_latest(self.today)
        self.assertEqual(result.local_date, "2026-09-25")
        self.assertTrue(result.stale)
        self.assertEqual(result.updated_at, "2026-09-25T08:00:00+08:00")
        self.assertEqual([item.repo_id for item in result.recommendations], [1])
        self.assertEqual(client.calls, [])

    def test_growth_and_keyword_share_history_deduplication(self):
        self.store.add_keyword("Local AI")
        client = FakeClient(created=[repo(1)], keywords={"Local AI": [[repo(1), repo(2)]]})
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertEqual(result.status, "ok")
        self.assertEqual([(item.section, item.repo_id) for item in result.recommendations], [("growth", 1), ("keyword", 2)])
        self.assertEqual(result.repositories[2].full_name, "owner/repo-2")
        self.assertEqual(self.store.seen_repo_ids(), {1, 2})

    def test_next_day_keyword_resorts_and_persists_unfiltered_candidate_ranks(self):
        rule = self.store.add_keyword('Local AI')
        yesterday = '2026-09-25'
        old = [repo(i,20000-i) for i in (1,2,3,4,6)]
        self.store.commit_daily(yesterday,old,[],[
            Recommendation(r.id,yesterday,'keyword',rule.id,None,None,yesterday+'T09:00:00+08:00')
            for r in old])
        pool = [repo(i,20000-i) for i in range(1,11)]
        client = FakeClient(keywords={'Local AI':[pool]}, repository_updates=pool)
        result = RadarService(client,self.store).refresh(self.today,self.now)
        cards = [r for r in result.recommendations if r.section=='keyword']
        self.assertEqual([(r.repo_id,r.rank) for r in cards],[(5,5),(7,7),(8,8),(9,9),(10,10)])
        self.assertEqual([(r.repo_id,r.rank) for r in self.store.daily_recommendations(self.today)
                          if r.section=='keyword'],[(5,5),(7,7),(8,8),(9,9),(10,10)])

    def test_disabled_keyword_does_not_search_or_change_history(self):
        rule = self.store.add_keyword("MCP")
        self.store.set_keyword_enabled(rule.id, False)
        client = FakeClient(keywords={"MCP": [[repo(7, description="MCP")]]})
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertEqual(result.status, "ok")
        self.assertFalse(any("MCP stars:" in query for query, _ in client.calls))
        self.assertEqual(self.store.daily_recommendations(self.today), [])

    def test_keyword_deleted_while_search_runs_cannot_be_saved(self):
        rule = self.store.add_keyword("MCP")
        owner = self

        class DeletingClient(FakeClient):
            def search(self, query, **kwargs):
                result = super().search(query, **kwargs)
                if query.startswith("MCP stars:"):
                    owner.store.soft_delete_keyword(rule.id, owner.now)
                return result

        client = DeletingClient(keywords={"MCP": [[repo(7, description="MCP")]]})
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.recommendations, ())
        self.assertNotIn(("keyword", rule.id), self.store.daily_sections(self.today))
        self.assertEqual(self.store.seen_repo_ids(), set())

    def test_next_day_reorders_previous_leader_behind_new_fast_project(self):
        first = FakeClient(
            created=[repo(1, 2000), repo(2, 1800)],
            histories={repo(1).full_name: [StarDay("2026-09-25", 80)],
                       repo(2).full_name: [StarDay("2026-09-25", 40)]},
        )
        RadarService(first, self.store).refresh(self.today, self.now)
        next_day = "2026-09-27"
        later = FakeClient(
            created=[repo(3, 1500)],
            repository_updates=[repo(1, 2100), repo(2, 1850)],
            histories={repo(1).full_name: [StarDay("2026-09-26", 25)],
                       repo(2).full_name: [StarDay("2026-09-26", 5)],
                       repo(3).full_name: [StarDay("2026-09-26", 130)]},
        )
        result = RadarService(later, self.store).refresh(next_day,
                                                         "2026-09-27T08:00:00+08:00")
        self.assertEqual([item.repo_id for item in result.recommendations], [3, 1, 2])
        self.assertEqual([item.star_delta for item in result.recommendations], [130, 25, 5])
        self.assertEqual(result.growth_coverage.stat_date, "2026-09-26")
        self.assertEqual(result.growth_coverage.scored_count, 3)
        self.assertEqual([item.repo_id for item in self.store.daily_recommendations(self.today)],
                         [1, 2])
        repeat = RadarService(FakeClient(repository_updates=[repo(3, 1650), repo(1, 2110),
                                                             repo(2, 1860)]), self.store).refresh(
            next_day, "2026-09-27T09:00:00+08:00")
        self.assertEqual([item.repo_id for item in repeat.recommendations], [3, 1, 2])

    def test_ai_rejected_keyword_project_can_enter_later_growth_top_five(self):
        rule = self.store.add_keyword("Local AI")
        old_day = "2026-09-25"
        old_at = "2026-09-25T08:00:00+08:00"
        self.store.commit_daily(old_day, [repo(4)], [], [
            Recommendation(4, old_day, "keyword", rule.id, None, None, old_at)])
        self.store.commit_ai_batch(old_day, rule.id, None,
                                   CandidateBatch((AIRepositoryInput(repo(4), None, True),), 1, 1),
                                   (RelevanceVerdict(4, "irrelevant", "Wrong topic"),), old_at)
        client = FakeClient(created=[repo(4, 1800)],
                            histories={repo(4).full_name: [StarDay("2026-09-25", 90)]},
                            keywords={"Local AI": [[]]})
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertEqual([item.repo_id for item in result.recommendations
                          if item.section == "growth"], [4])
        self.assertEqual([item.repo_id for item in result.recommendations
                          if item.section == "keyword"], [])

    def test_history_budget_checks_previous_leader_among_many_new_projects(self):
        previous = "2026-09-25"
        self.store.commit_daily(previous, [repo(100, 1100)], [], [
            Recommendation(100, previous, "growth", None, 50, None,
                           "2026-09-25T08:00:00+08:00")])
        created = [repo(number) for number in range(1, 100)]
        leader = repo(100, 1300)
        histories = {item.full_name: [StarDay("2026-09-25", 1)] for item in created}
        histories[leader.full_name] = [StarDay("2026-09-25", 900)]
        client = FakeClient(created=created, repository_updates=[leader], histories=histories)
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertIn(leader.full_name, client.history_calls)
        self.assertEqual(result.recommendations[0].repo_id, leader.id)
        self.assertEqual(result.growth_coverage.candidate_count, 100)
        self.assertEqual(result.growth_coverage.stat_date, "2026-09-25")

    def test_low_core_budget_checks_previous_leader_before_trending_lookups(self):
        previous = "2026-09-25"
        leader = repo(100, 1300)
        self.store.commit_daily(previous, [leader], [], [
            Recommendation(100, previous, "growth", None, 50, None,
                           "2026-09-25T08:00:00+08:00")])
        trending = repo(101, 9000)
        client = FakeClient(
            repository_updates=[repo(100, 1400), trending], core_remaining=2,
            histories={leader.full_name: [StarDay("2026-09-25", 90)]},
        )
        result = RadarService(client, self.store, FakeTrending([trending.full_name])).refresh(
            self.today, self.now)
        self.assertEqual(result.status, "ok")
        self.assertEqual(client.history_calls, [leader.full_name])
        self.assertEqual([item.repo_id for item in result.recommendations], [leader.id])
        self.assertNotIn("trending", result.growth_coverage.source_names)
        self.assertIn("Trending", " ".join(result.notes))

    def test_keyword_search_continues_past_full_seen_page(self):
        rule = self.store.add_keyword("Local AI")
        old = [repo(repo_id) for repo_id in range(1, 101)]
        snapshots = [StarSnapshot(item.id, "2026-09-25", item.stars, "2026-09-25T08:00:00+08:00") for item in old]
        history = [Recommendation(item.id, "2026-09-25", "keyword", rule.id, None, None, "2026-09-25T08:00:00+08:00") for item in old]
        self.store.commit_daily("2026-09-25", old, snapshots, history)
        client = FakeClient(keywords={"Local AI": [old, [repo(101)]]}, remaining=0)
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertEqual([item.repo_id for item in result.recommendations if item.local_date == self.today], [101])
        self.assertIn(('Local AI stars:>=1000', 2), client.calls)

    def test_same_day_refresh_keeps_selection_and_adds_new_keyword(self):
        self.store.add_keyword("Local AI")
        first = FakeClient(created=[repo(1)], keywords={"Local AI": [[repo(2)]]})
        RadarService(first, self.store).refresh(self.today, self.now)

        self.store.add_keyword("MCP")
        second = FakeClient(
            created=[repo(3)],
            keywords={"Local AI": [[repo(4)]], "MCP": [[repo(5, description="MCP tools")]]},
            remaining=0,
            repository_updates=[repo(1), repo(2)],
        )
        result = RadarService(second, self.store).refresh(self.today, "2026-09-26T09:00:00+08:00")
        self.assertEqual([item.repo_id for item in result.recommendations], [1, 2, 5])
        self.assertEqual(self.store.seen_repo_ids(), {1, 2, 5})
        self.assertEqual(result.updated_at, "2026-09-26T09:00:00+08:00")
        self.assertIn("首次采样：正在建立增长记录", result.notes)

    def test_same_day_refresh_updates_saved_keyword_repository_metadata(self):
        self.store.add_keyword("Local AI")
        first = FakeClient(created=[repo(1)], keywords={"Local AI": [[repo(2, stars=1500)]]})
        RadarService(first, self.store).refresh(self.today, self.now)

        second = FakeClient(
            created=[repo(1)],
            repository_updates=[repo(2, stars=1800)],
        )
        result = RadarService(second, self.store).refresh(
            self.today, "2026-09-26T09:00:00+08:00"
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.repositories[2].stars, 1800)
        self.assertIn("owner/repo-2", second.get_calls)

    def test_same_day_refresh_updates_snapshot_time_without_new_recommendations(self):
        first = FakeClient(created=[repo(1, stars=1500)])
        RadarService(first, self.store).refresh(self.today, self.now)
        second = FakeClient(created=[repo(1, stars=1502)], remaining=0)
        result = RadarService(second, self.store).refresh(self.today, "2026-09-26T09:00:00+08:00")
        self.assertEqual([item.repo_id for item in result.recommendations], [1])
        self.assertEqual(result.updated_at, "2026-09-26T09:00:00+08:00")

    def test_network_failure_keeps_last_saved_issue_and_no_partial_day(self):
        self.store.add_keyword("Local AI")
        RadarService(FakeClient(created=[repo(1)]), self.store).refresh(self.today, self.now)
        failed = RadarService(FakeClient(error=GitHubRequestError("network down")), self.store)
        result = failed.refresh("2026-09-27", "2026-09-27T08:00:00+08:00")
        self.assertEqual(result.status, "error")
        self.assertTrue(result.stale)
        self.assertEqual([item.repo_id for item in result.recommendations], [1])
        self.assertEqual(self.store.daily_recommendations("2026-09-27"), [])
        self.assertEqual(self.store.snapshots_before("2026-09-28", [1])[1].local_date, self.today)

    def test_failure_reason_survives_reopen_with_saved_date(self):
        RadarService(FakeClient(created=[repo(1)]), self.store).refresh(self.today, self.now)
        RadarService(FakeClient(error=GitHubRequestError("network down")), self.store).refresh(
            "2026-09-27", "2026-09-27T08:00:00+08:00"
        )
        reopened = RadarStore(self.store.data_dir)
        result = RadarService(FakeClient(), reopened).load_latest("2026-09-27")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.local_date, self.today)
        self.assertIn("network down", result.message)
        self.assertEqual([item.repo_id for item in result.recommendations], [1])

    def test_truncated_http_body_returns_saved_issue(self):
        RadarService(FakeClient(created=[repo(1)]), self.store).refresh(self.today, self.now)

        class BrokenResponse:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                raise IncompleteRead(b'{"items":', 100)

        client = GitHubClient(opener=lambda request, timeout: BrokenResponse())
        result = RadarService(client, self.store).refresh("2026-09-27", "2026-09-27T08:00:00+08:00")
        self.assertEqual(result.status, "error")
        self.assertEqual([item.repo_id for item in result.recommendations], [1])
        self.assertEqual(self.store.daily_recommendations("2026-09-27"), [])

    def test_next_day_growth_uses_prior_snapshot(self):
        initial = [repo(repo_id, stars=2000 - repo_id * 100) for repo_id in range(1, 7)]
        RadarService(FakeClient(created=initial), self.store).refresh("2026-09-25", "2026-09-25T08:00:00+08:00")
        next_day = [repo(6, stars=1430)]
        result = RadarService(FakeClient(created=next_day, remaining=0), self.store).refresh(self.today, self.now)
        today_growth = [item for item in result.recommendations if item.local_date == self.today]
        self.assertEqual([(item.repo_id, item.star_delta) for item in today_growth], [(6, 30)])
        self.assertEqual(today_growth[0].baseline_at, "2026-09-25T08:00:00+08:00")

    def test_keyword_pagination_is_bounded_when_no_matches_exist(self):
        self.store.add_keyword("MCP")
        unrelated = [repo(repo_id) for repo_id in range(1, 101)]
        client = FakeClient(keywords={"MCP": [unrelated] * 11}, remaining=0)
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.recommendations, ())
        self.assertEqual(len([query for query, _ in client.calls if query.startswith("MCP ")]), 10)

    def test_empty_sections_are_not_reselected_later_the_same_day(self):
        self.store.add_keyword("Local AI")
        first = RadarService(FakeClient(), self.store).refresh(self.today, self.now)
        self.assertEqual(first.recommendations, ())

        second_client = FakeClient(created=[repo(1)], keywords={"Local AI": [[repo(2)]]})
        second = RadarService(second_client, self.store).refresh(
            self.today, "2026-09-26T09:00:00+08:00"
        )
        self.assertEqual(second.recommendations, ())
        self.assertEqual(self.store.seen_repo_ids(), set())

    def test_offline_after_newer_empty_day_does_not_show_older_issue(self):
        RadarService(FakeClient(created=[repo(1)]), self.store).refresh(
            "2026-09-25", "2026-09-25T08:00:00+08:00"
        )
        RadarService(FakeClient(repository_updates=[repo(1)]), self.store).refresh(self.today, self.now)
        failed = RadarService(FakeClient(error=GitHubRequestError("offline")), self.store).refresh(
            "2026-09-27", "2026-09-27T08:00:00+08:00"
        )
        self.assertTrue(failed.stale)
        self.assertEqual(failed.local_date, self.today)
        self.assertEqual(failed.recommendations, ())
        self.assertEqual(failed.updated_at, self.now)

    def test_combines_sources_by_id_and_ranks_one_verified_statistics_day(self):
        self.store.add_keyword("Local AI")
        client = FakeClient(
            created=[repo(1), repo(2)], pushed=[repo(2)],
            repository_updates=[repo(2), repo(3)],
            keywords={"Local AI": [[repo(2), repo(4)]]},
            histories={
                repo(1).full_name: [StarDay("2026-09-25", 5)],
                repo(2).full_name: [StarDay("2026-09-25", 7)],
                repo(3).full_name: [StarDay("2026-09-24", 40)],
            },
        )
        result = RadarService(client, self.store, FakeTrending([repo(2).full_name, repo(3).full_name])).refresh(self.today, self.now)
        self.assertEqual([(r.section, r.repo_id) for r in result.recommendations],
                         [("growth", 2), ("growth", 1), ("keyword", 4)])
        self.assertEqual([r.star_delta for r in result.recommendations[:2]], [7, 5])
        self.assertEqual({r.metric_basis for r in result.recommendations[:2]}, {"github_daily_new"})
        self.assertLessEqual(client.get_calls.count(repo(2).full_name), 1)
        self.assertEqual(result.growth_coverage.candidate_count, 3)
        self.assertEqual(result.growth_coverage.scored_count, 2)
        self.assertEqual(result.growth_coverage.stat_date, "2026-09-25")
        self.assertEqual(RadarService(client, self.store).load_latest(self.today).growth_coverage,
                         result.growth_coverage)

    def test_trending_failure_keeps_search_and_explains_reduced_coverage(self):
        from github_radar.trending import TrendingUnavailable
        client = FakeClient(created=[repo(1)], histories={repo(1).full_name: [StarDay("2026-09-25", 4)]})
        result = RadarService(client, self.store, FakeTrending(error=TrendingUnavailable("changed"))).refresh(self.today, self.now)
        self.assertEqual(result.status, "ok")
        self.assertEqual([r.repo_id for r in result.recommendations], [1])
        self.assertIn("Trending", " ".join(result.notes))
        self.assertNotIn("trending", result.growth_coverage.source_names)

    def test_public_refresh_uses_available_core_quota_without_reserve(self):
        candidates = [repo(i) for i in range(1, 6)]
        client = FakeClient(
            created=candidates, core_remaining=12,
            histories={item.full_name: [StarDay("2026-09-25", 6 - item.id)] for item in candidates},
        )
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertEqual(len(client.history_calls), 5)
        self.assertGreaterEqual(client.core_remaining, 0)
        self.assertEqual(result.growth_coverage.candidate_count, 5)
        self.assertEqual(result.growth_coverage.scored_count, 5)
        self.assertEqual(len(result.recommendations), 5)

    def test_first_day_without_official_history_keeps_pending_candidates(self):
        result = RadarService(FakeClient(created=[repo(1)]), self.store).refresh(self.today, self.now)
        self.assertEqual(result.recommendations[0].metric_basis, "pending")
        self.assertIsNone(result.recommendations[0].star_delta)
        self.assertEqual(result.growth_coverage.metric_basis, "pending")

    def test_next_day_without_official_history_uses_separate_local_observation(self):
        first = [repo(i, stars=2000 - i * 100) for i in range(1, 7)]
        RadarService(FakeClient(created=first), self.store).refresh(
            "2026-09-25", "2026-09-25T08:00:00+08:00"
        )
        result = RadarService(FakeClient(
            created=[repo(6, stars=1420)], repository_updates=first[:5],
        ), self.store).refresh(self.today, self.now)
        self.assertEqual(result.recommendations[0].star_delta, 20)
        self.assertEqual(result.recommendations[0].metric_basis, "local_snapshot")
        self.assertEqual(result.growth_coverage.metric_basis, "local_snapshot")

    def test_no_verified_growth_keeps_new_candidate_as_pending_card(self):
        previous = "2026-09-25"
        self.store.commit_daily(
            previous, [repo(1, 2000)],
            [StarSnapshot(1, previous, 2000, "2026-09-25T08:00:00+08:00")],
            [Recommendation(1, previous, "growth", None, None, None,
                            "2026-09-25T08:00:00+08:00")],
        )
        client = FakeClient(created=[repo(2, 1500)],
                            repository_updates=[repo(1, 2000)])
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertEqual([(item.repo_id, item.metric_basis, item.star_delta)
                          for item in result.recommendations], [(2, "pending", None)])

    def test_history_budget_samples_trending_even_with_many_created_results(self):
        created = [repo(i) for i in range(1, 101)]
        hot = repo(101, stars=10000)
        histories = {item.full_name: [StarDay("2026-09-25", 1)] for item in created}
        histories[hot.full_name] = [StarDay("2026-09-25", 9999)]
        client = FakeClient(created=created, repository_updates=[hot], histories=histories)
        result = RadarService(client, self.store, FakeTrending([hot.full_name])).refresh(self.today, self.now)
        self.assertIn(hot.full_name, client.history_calls)
        self.assertEqual(result.recommendations[0].repo_id, hot.id)

    def test_keyword_search_gets_quota_before_growth_discovery(self):
        keywords = {}
        for number in range(1, 10):
            term = f"term{number}"
            self.store.add_keyword(term)
            keywords[term] = [[repo(number, description=term)]]
        client = FakeClient(created=[repo(100)], keywords=keywords,
                            strict_search_limit=True, histories={})
        result = RadarService(client, self.store).refresh(self.today, self.now)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len([r for r in result.recommendations if r.section == "keyword"]), 9)
        self.assertLessEqual(len([query for query, _ in client.calls if query.startswith("created:") or query.startswith("pushed:")]), 1)
        self.assertGreaterEqual(client.search_remaining, 0)

    def test_truncated_trending_response_degrades_to_search(self):
        from github_radar.trending import TrendingClient

        class BrokenResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                raise IncompleteRead(b"<article", 100)

        client = FakeClient(created=[repo(1)], histories={repo(1).full_name: [StarDay("2026-09-25", 3)]})
        trending = TrendingClient(opener=lambda request, timeout: BrokenResponse())
        result = RadarService(client, self.store, trending).refresh(self.today, self.now)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.recommendations[0].repo_id, 1)
        self.assertIn("Trending", " ".join(result.notes))


if __name__ == "__main__":
    unittest.main()
