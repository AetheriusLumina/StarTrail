import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from github_radar.discovery import DiscoveryCoordinator
from github_radar.discovery_types import DiscoveryBatch, DiscoveryCandidate, RequestBudget, SearchPage
from github_radar.github_client import GitHubClient
from github_radar.models import Recommendation, StarSnapshot
from github_radar.storage import RadarStore
from github_radar.cross_check import compare_source
from github_radar.discovery_types import SourceEvidence
from tests.test_wider_refresh import Client, Discovery, NOW, WEEK, repo
from github_radar.service import RadarService


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.store = RadarStore(self.data)

    def test_parent_search_candidates_survive_split_at_last_request(self):
        class Search:
            search_remaining = 0
            core_remaining = 10
            def search_page(self, query, page):
                return SearchPage((repo(99),), 2001, False)
        report = DiscoveryCoordinator(Search(), self.store, free_events=False,
            trendshift=False, clock=lambda: 0).discover([], '2026-09-30', RequestBudget(10, 1, 300))
        self.assertEqual([r.repo_id for r in report.candidates], [99])
        import json
        queue = json.loads(self.store.load_discovery_cursor('github_partition_search'))
        self.assertEqual(len(queue), 2)
        self.assertLess(queue[0]['end'], queue[1]['start'])
        self.assertEqual(self.store.seen_repo_ids(), set())

    def test_quota_reset_resources_are_independent_and_reprobed(self):
        client = GitHubClient()
        client._capture_limit({'X-RateLimit-Resource':'core', 'X-RateLimit-Remaining':'10',
                               'X-RateLimit-Reset':'100'}, '/repositories')
        client._capture_limit({'X-RateLimit-Resource':'search', 'X-RateLimit-Remaining':'0',
                               'X-RateLimit-Reset':'200'}, '/search/repositories')
        self.assertTrue(hasattr(client, 'renew_expired_quotas'))
        client.renew_expired_quotas(101)
        self.assertIsNone(client.core_remaining)
        self.assertEqual(client.search_remaining, 0)
        budget = RequestBudget(client.core_remaining, client.search_remaining, 300)
        self.assertTrue(budget.can_spend('core', 1, 0))
        client.renew_expired_quotas(201)
        self.assertIsNone(client.search_remaining)

    def test_same_client_refresh_resumes_after_observed_reset(self):
        from github_radar.models import OfficialStarWeek
        class RealContractClient(GitHubClient):
            def __init__(self):
                super().__init__()
                self.core_remaining = 10
                self.core_reset_at = int(datetime.fromisoformat(NOW).timestamp()) - 1
                self.calls = []
            def star_history_weeks(self, name):
                self.calls.append(name)
                self.budget.spend('core', 1, self.clock())
                self._capture_limit({'x-ratelimit-resource':'core', 'x-ratelimit-remaining':'59'}, '/repos/a/r')
                return [OfficialStarWeek(WEEK, (0, 1, 0, 0, 0, 0, 0))]
        client = RealContractClient()
        result = RadarService(client, self.store, discovery=Discovery([repo(1)])).refresh('2026-09-30', NOW)
        self.assertEqual(result.status, 'ok')
        self.assertEqual(client.calls, [repo(1).full_name])

    def test_rotation_scores_an_old_unscored_candidate_despite_repeated_hot_prefix(self):
        old = repo(99)
        self.store.save_discovery_batch(DiscoveryBatch('prior', (
            DiscoveryCandidate(old.full_name, old.id, ('prior',), '2026-09-01T10:00:00+00:00'),
        ), None, True, ()))
        hot = [repo(i) for i in range(1, 30)]
        client = Client(hot + [old]); client.core_remaining = 14
        result = RadarService(client, self.store, discovery=Discovery(hot)).refresh('2026-09-30', NOW)
        self.assertEqual(result.status, 'ok')
        self.assertIn(old.full_name, client.calls)

    def test_upgrade_includes_followed_and_previous_growth_without_rediscovery(self):
        old = repo(99)
        self.store.commit_daily('2026-09-29', [old],
            [StarSnapshot(99, '2026-09-29', old.stars, NOW)],
            [Recommendation(99, '2026-09-29', 'growth', None, 99, None, NOW)])
        self.store.set_followed(99, True, NOW)
        client = Client([old, repo(1)])
        result = RadarService(client, self.store, discovery=Discovery([repo(1)])).refresh('2026-09-30', NOW)
        self.assertIn(old.full_name, client.calls)
        self.assertEqual(result.recommendations[0].repo_id, 99)

    def test_official_raw_day_evidence_survives_reopen(self):
        client = Client([repo(7)])
        RadarService(client, self.store, discovery=Discovery([repo(7)])).refresh('2026-09-30', NOW)
        reopened = RadarStore(self.data)
        self.assertTrue(hasattr(reopened, 'official_star_evidence'))
        rows = reopened.official_star_evidence(7, NOW)
        self.assertEqual(len(rows), 7)
        row = next(r for r in rows if r['day_index'] == 1)
        self.assertEqual((row['week'], row['added'], row['observed_at']), (WEEK, 7, NOW))
        self.assertIn('end threshold label + 1 day', row['interpretation_rule'])
        self.assertIn('unfinished UTC day excluded', row['interpretation_rule'])

    def test_nearby_total_samples_can_be_compared_but_old_samples_cannot(self):
        evidence = SourceEvidence('test', repo(7).full_name, 'https://trendshift.io/',
            '2026-09-30T10:00:00.000001+00:00', 'daily', None, 'none', None, str(repo(7).stars), None)
        self.assertEqual(compare_source(evidence, repo(7), None, NOW, None)[0].status, 'consistent')
        from dataclasses import replace
        old = replace(evidence, observed_at='2026-09-30T09:00:00+00:00')
        self.assertEqual(compare_source(old, repo(7), None, NOW, None)[0].status, 'unaligned')
        changed = replace(evidence, total_stars_text='999999')
        result = compare_source(changed, repo(7), None, NOW, None)[0]
        self.assertEqual(result.status, 'conflict')
        self.assertIn('延迟', result.reason)
