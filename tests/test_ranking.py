import unittest
from datetime import datetime, timezone

from github_radar import models, ranking

from github_radar.models import KeywordRule, Repository, StarDay, StarSnapshot
from github_radar.ranking import (GrowthPick, latest_complete_day, matches_keyword,
                                  select_daily_growth, select_growth, select_keyword)


def repo(repo_id, stars, description="Local AI tools", topics=("local-ai",), archived=False):
    return Repository(
        repo_id,
        f"owner/repo-{repo_id}",
        f"https://github.com/owner/repo-{repo_id}",
        description,
        topics,
        "Python",
        stars,
        archived,
    )


class RankingTests(unittest.TestCase):
    def test_keyword_preserves_candidate_rank_before_history_filter(self):
        rule = KeywordRule(1, "Local AI", 1000, True)
        pool = [repo(i, 20000-i) for i in range(1, 11)]
        result = self.allocate([rule], {1: list(reversed(pool))}, {1,2,3,4,6})
        self.assertEqual([r.id for r in result.groups[1]], [5,7,8,9,10])
        self.assertEqual(getattr(result, "ranks", None), {1:{5:5,7:7,8:8,9:9,10:10}})

    def test_keyword_rank_is_independent_of_input_order_duplicate_and_history_shape(self):
        from itertools import combinations
        rule = KeywordRule(1, "Local AI", 1000, True)
        pool = [repo(i, 20000-i) for i in range(1, 9)]
        for seen_count in range(9):
            for ids in combinations(range(1,9), seen_count):
                seen = set(ids)
                expected = [i for i in range(1,9) if i not in seen][:5]
                for candidates in (pool, list(reversed(pool)) + [repo(4, 1500)]):
                    result = self.allocate([rule], {1:candidates}, seen)
                    self.assertEqual([r.id for r in result.groups[1]], expected)
                    self.assertEqual(getattr(result, "ranks", None), {1:{i:i for i in expected}})

    def test_new_keyword_leader_wins_after_previous_leader_and_renamed_id_seen(self):
        from dataclasses import replace
        rule = KeywordRule(1, "Local AI", 1000, True)
        old = replace(repo(1, 100000),full_name='renamed/new-name')
        leader = repo(99, 200000)
        pool = [old, repo(2, 80000), leader, repo(99, 1500)]
        result = self.allocate([rule], {1:pool}, {1})
        self.assertEqual([r.id for r in result.groups[1]], [99,2])
        self.assertEqual(getattr(result, "ranks", None), {1:{99:1,2:3}})

    def test_keyword_rank_counts_only_eligible_unique_candidates_with_stable_ties(self):
        from dataclasses import replace
        rule = KeywordRule(1,'Local AI',1000,True)
        pool = [repo(8,1000),repo(7,1000),repo(1,10000,archived=True),repo(2,999),
                replace(repo(3,50000),description='Other tool',topics=())]
        result = self.allocate([rule],{1:pool},{7})
        self.assertEqual([r.id for r in result.groups[1]],[8])
        self.assertEqual(result.ranks,{1:{8:2}})
        self.assertEqual(self.allocate([replace(rule,enabled=False)],{1:pool}).groups,{})


    def allocate(self,rules,candidates,seen=set(),slots=()):
        self.assertTrue(hasattr(ranking,"allocate_keyword_groups"))
        return ranking.allocate_keyword_groups(rules,candidates,seen,list(slots))

    def test_later_discovered_higher_star_is_recommended(self):
        rule=KeywordRule(1,"Local AI",1000,True)
        result=self.allocate([rule],{1:[repo(1,100000),repo(3,50000),repo(2,200000)]},{1})
        self.assertEqual([r.id for r in result.groups[1]],[2,3])

    def test_growth_keyword_overlap_is_marked_and_backfilled(self):
        rule=KeywordRule(1,"Local AI",1000,True)
        pool=[repo(i,10000-i) for i in range(1,8)]
        slot=ranking.GrowthSlot(GrowthPick(pool[0],100,None,"github_daily_new","2026-09-28"),8,"new")
        result=self.allocate([rule],{1:pool},{2},[slot])
        self.assertEqual(result.growth_matches,{1:(1,)})
        self.assertEqual([r.id for r in result.groups[1]],[3,4,5,6,7])

    def test_multiple_keywords_do_not_duplicate_repositories(self):
        rules=[KeywordRule(2,"AI",1000,True),KeywordRule(1,"Local AI",1000,True)]
        pool=[repo(i,2000-i) for i in range(1,9)]
        result=self.allocate(rules,{1:pool,2:pool})
        self.assertEqual([r.id for r in result.groups[1]],[1,2,3,4,5])
        self.assertEqual([r.id for r in result.groups[2]],[6,7,8])

    def test_keyword_budget_shortage_reports_missing_count(self):
        result=self.allocate([KeywordRule(1,"MCP",1000,True)],{1:[]})
        self.assertEqual(result.groups[1],())
        self.assertTrue(any("不足 5" in n for n in result.notes))

    def test_topic_hint_does_not_claim_ai_verification(self):
        result=self.allocate([KeywordRule(1,"MCP",1000,True)],{1:[repo(9,3000)]})
        self.assertEqual(result.groups[1],())

    def test_nontrending_high_star_keyword_candidate_can_win(self):
        result=self.allocate([KeywordRule(1,"Local AI",1000,True)],{1:[repo(7,200000),repo(8,5000)]})
        self.assertEqual([r.id for r in result.groups[1]],[7,8])

    def weeks(self, start="2026-09-27", days=(1, 2, 3, 4, 5, 6, 7)):
        self.assertTrue(hasattr(models, "OfficialStarWeek"), "raw official weeks missing")
        stamp = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
        return [models.OfficialStarWeek(stamp, days)]

    def confirmed(self, weeks, observed):
        self.assertTrue(hasattr(ranking, "confirmed_star_days"), "complete-day guard missing")
        return ranking.confirmed_star_days(weeks, datetime.fromisoformat(observed))

    def slots(self, repos, daily, seen=set()):
        self.assertTrue(hasattr(ranking, "select_growth_slots"), "fresh growth slots missing")
        return ranking.select_growth_slots(repos, daily, "2026-09-28", seen)

    def test_confirmed_day_uses_timezone_guard(self):
        weeks = self.weeks()
        before = self.confirmed(weeks, "2026-09-28T07:59:59+08:00")
        self.assertEqual(before, [])
        at = self.confirmed(weeks, "2026-09-28T08:00:00+08:00")
        self.assertEqual(at, [StarDay("2026-09-27", 1)])
        self.assertEqual(self.confirmed(weeks, "2026-09-30T08:00:00+08:00")[-1],
                         StarDay("2026-09-29", 3))
        with self.assertRaises(ValueError):
            ranking.confirmed_star_days(weeks, datetime(2026, 9, 30))

    def test_october_second_uses_full_october_first_not_current_partial_day(self):
        weeks=self.weeks(days=(1,2,3,4,1540,999999,999999))
        self.assertEqual(self.confirmed(weeks,'2026-10-02T07:59:59+08:00')[-1],StarDay('2026-09-30',4))
        self.assertEqual(self.confirmed(weeks,'2026-10-02T08:00:00+08:00')[-1],StarDay('2026-10-01',1540))
        self.assertEqual(self.confirmed(weeks,'2026-10-02T20:00:00+08:00')[-1],StarDay('2026-10-01',1540))

    def test_non_sunday_week_is_unverified(self):
        self.assertEqual(self.confirmed(self.weeks("2026-09-28"),
                                       "2026-10-10T00:00:00+00:00"), [])
        self.assertEqual(self.confirmed(self.weeks("2026-09-27T01:00:00"),
                                       "2026-10-10T00:00:00+00:00"), [])

    def test_cross_year_day_labels(self):
        result = self.confirmed(self.weeks("2025-12-28"), "2026-01-05T00:00:00+00:00")
        self.assertEqual((result[0], result[-1]),
                         (StarDay("2025-12-28", 1), StarDay("2026-01-03", 7)))

    def test_confirmed_days_reject_malformed_counts_and_conflicting_dates(self):
        for days in ((1, 2), (True, 0, 0, 0, 0, 0, 0), (-1, 0, 0, 0, 0, 0, 0)):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.confirmed(self.weeks(days=days), "2026-10-10T00:00:00+00:00")
        duplicate = self.weeks() + self.weeks(days=(99, 2, 3, 4, 5, 6, 7))
        with self.assertRaises(ValueError):
            self.confirmed(duplicate, "2026-10-10T00:00:00+00:00")

    def test_old_top_five_plus_five_new_keep_real_rank(self):
        repos = [repo(i, 1000) for i in range(1, 9)]
        daily = {i: StarDay("2026-09-28", 100-i) for i in range(1, 9)}
        slots = self.slots(repos, daily, {1, 3, 5})
        self.assertEqual([s.rank for s in slots if s.display_role == "old"], [1, 3, 5])
        self.assertEqual([s.rank for s in slots if s.display_role == "new"], [2, 4, 6, 7, 8])
        self.assertEqual([s.pick.repo.id for s in slots], list(range(1, 9)))

    def test_growth_slots_all_old_or_all_new_and_short_results(self):
        repos = [repo(i, 1000) for i in range(1, 11)]
        daily = {i: StarDay("2026-09-28", 100-i) for i in range(1, 11)}
        old = self.slots(repos, daily, set(range(1, 11)))
        self.assertEqual([(s.rank, s.display_role) for s in old],
                         [(i, "old") for i in range(1, 6)])
        self.assertEqual([s.rank for s in self.slots(repos, daily)], list(range(1, 6)))
        self.assertEqual(len(self.slots(repos[:2], daily)), 2)

    def test_growth_slots_filter_dates_zero_archive_and_stable_ties(self):
        repos = [repo(1, 1000), repo(2, 2000), repo(3, 2000), repo(4, 9999),
                 repo(5, 9999), repo(6, 9999, archived=True), repo(2, 1900)]
        daily = {i: StarDay("2026-09-28", 10) for i in range(1, 7)}
        daily[4] = StarDay("2026-09-28", 0)
        daily[5] = StarDay("2026-09-27", 1000)
        slots = self.slots(repos, daily, {3})
        self.assertEqual([(s.pick.repo.id, s.rank, s.display_role) for s in slots],
                         [(2, 1, "new"), (3, 2, "old"), (1, 3, "new")])

    def test_first_snapshot_has_no_growth_and_keeps_previous_projects_as_candidates(self):
        result = select_growth([repo(1, 100), repo(2, 300), repo(3, 200, archived=True)], {})
        self.assertEqual(result, [GrowthPick(repo(2, 300), None, None),
                                  GrowthPick(repo(1, 100), None, None)])

    def test_growth_uses_positive_delta_then_current_stars(self):
        current = [repo(1, 120), repo(2, 220), repo(3, 400), repo(4, 600), repo(5, 800)]
        baselines = {
            1: StarSnapshot(1, "2026-09-25", 100, "2026-09-25T09:00:00+08:00"),
            2: StarSnapshot(2, "2026-09-25", 200, "2026-09-25T09:00:00+08:00"),
            3: StarSnapshot(3, "2026-09-25", 400, "2026-09-25T09:00:00+08:00"),
            4: StarSnapshot(4, "2026-09-25", 610, "2026-09-25T09:00:00+08:00"),
        }
        result = select_growth(current, baselines)
        self.assertEqual([item.repo.id for item in result], [2, 1])
        self.assertEqual([item.delta for item in result], [20, 20])
        self.assertEqual(result[0].baseline_at, "2026-09-25T09:00:00+08:00")
        self.assertEqual(result[0].metric_basis, "local_snapshot")

    def test_no_positive_local_delta_keeps_new_unmeasured_candidates_pending(self):
        old = repo(1, 2000)
        new = repo(2, 1500)
        baselines = {1: StarSnapshot(1, "2026-09-25", 2000,
                                    "2026-09-25T09:00:00+08:00")}
        self.assertEqual(select_growth([old, new], baselines),
                         [GrowthPick(new, None, None)])

    def test_latest_complete_day_skips_current_partial_day_across_week(self):
        days = [StarDay("2026-09-20", 200), StarDay("2026-09-19", 90)]
        self.assertEqual(latest_complete_day(days, "2026-09-20"), StarDay("2026-09-19", 90))
        self.assertEqual(latest_complete_day(days, "2026-09-19"), None)

    def test_daily_growth_uses_one_date_and_stable_ties(self):
        candidates = [repo(1, 1000), repo(2, 2000), repo(3, 2000), repo(4, 9000), repo(5, 7000)]
        daily = {
            1: StarDay("2026-09-25", 30),
            2: StarDay("2026-09-25", 30),
            3: StarDay("2026-09-25", 30),
            4: StarDay("2026-09-24", 500),
        }
        result = select_daily_growth(candidates, daily, "2026-09-25")
        self.assertEqual([item.repo.id for item in result], [2, 3, 1])
        self.assertEqual([item.delta for item in result], [30, 30, 30])
        self.assertTrue(all(item.metric_basis == "github_daily_new" for item in result))
        self.assertTrue(all(item.metric_date == "2026-09-25" for item in result))

    def test_keyword_requires_all_words_and_filters_minimum_archive_and_seen(self):
        rule = KeywordRule(1, " LOCAL AI ", 1000, True)
        matches = [
            repo(1, 1200, "Tools for local models", ("ai",)),
            repo(2, 3000, "AI in the cloud", ("cloud",)),
            repo(3, 900),
            repo(4, 5000, archived=True),
            repo(5, 4000),
            repo(6, 2000, "Local AI", ()),
        ]
        result = select_keyword(matches, rule, {5})
        self.assertEqual([item.id for item in result], [6, 1])

    def test_keyword_disabled_or_exhausted_returns_actual_count(self):
        enabled = KeywordRule(1, "Agent Skills", 1000, True)
        disabled = KeywordRule(1, "Agent Skills", 1000, False)
        candidates = [repo(1, 2000, "Agent Skills", ()), repo(1, 2000, "Agent Skills", ())]
        self.assertEqual(len(select_keyword(candidates, enabled, set(), limit=5)), 1)
        self.assertEqual(select_keyword(candidates, disabled, set()), [])

    def test_basic_keyword_match_is_independent_of_recommendation_history(self):
        rule = KeywordRule(1, "Local AI", 1000, True)
        self.assertTrue(matches_keyword(repo(7, 1200, "Local AI tools"), rule))
        self.assertFalse(matches_keyword(repo(7, 900, "Local AI tools"), rule))
        self.assertFalse(matches_keyword(repo(7, 1200, "Local AI tools", archived=True), rule))
        self.assertFalse(matches_keyword(repo(7, 1200, "Local AI tools"),
                                         KeywordRule(1, "Local AI", 1000, False)))


if __name__ == "__main__":
    unittest.main()
