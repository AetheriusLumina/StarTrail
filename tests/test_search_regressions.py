"""Real storage/API regressions with synthetic repository data."""
import tempfile
import unittest
from contextlib import closing
from github_radar.storage import RadarStore
from github_radar.models import Recommendation, StarSnapshot
from github_radar.ai_types import AIRepositoryInput, CandidateBatch, RelevanceVerdict
from github_radar.browser_server import BrowserServer
from github_radar.service import RadarService
from tests.test_storage import repository

class SearchPublicationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = RadarStore(temp.name)
        self.rule = self.store.add_keyword('local ai')
        self.day = '2026-10-04'
        self.at = self.day + 'T12:00:00+08:00'

    def publish_batch(self, repos):
        self.store.commit_ai_batch(self.day, self.rule.id, None,
            CandidateBatch(tuple(AIRepositoryInput(r, None, True, observed_at=self.at) for r in repos), 2, 0),
            tuple(RelevanceVerdict(r.id, 'relevant', 'README matches') for r in repos), self.at)

    def test_ai_selection_saves_real_current_snapshot_and_keeps_history(self):
        old = '2026-10-02'
        old_at = old + 'T12:00:00+08:00'
        self.store.commit_daily(old, [repository(7,1500)],
            [StarSnapshot(7,old,1500,old_at)], [])
        self.publish_batch([repository(7,2500)])
        snap = self.store.snapshots_on(self.day,[7]).get(7)
        self.assertIsNotNone(snap)
        self.assertEqual((snap.stars,snap.observed_at),(2500,self.at))
        server = BrowserServer(RadarService(object(),self.store),self.store)
        self.addCleanup(server.close)
        detail = server._project_payload(7,context_date=self.day)
        self.assertEqual(detail['saved_at'],self.at)
        self.assertEqual(self.store.snapshots_on(old,[7])[7].stars,1500)

    def test_better_candidate_replaces_low_star_but_display_history_survives(self):
        repos = [repository(i,1400+i) for i in range(1,6)]
        self.store.commit_daily(self.day,repos,
            [StarSnapshot(r.id,self.day,r.stars,self.at) for r in repos],
            [Recommendation(r.id,self.day,'keyword',self.rule.id,None,None,self.at) for r in repos])
        self.publish_batch([repository(9,62000)])
        cards = self.store.daily_recommendations(self.day)
        self.assertEqual([c.repo_id for c in cards],[9,5,4,3,2])
        self.assertIn(1,self.store.seen_repo_ids())
        self.assertIn(1,self.store.seen_repo_ids_before('2026-10-05'))
        self.publish_batch([repository(10,63000)])
        self.assertEqual(len(self.store.daily_recommendations(self.day)),5)

    def test_cached_old_snapshot_is_not_relabelled_today(self):
        old='2026-10-02'; at=old+'T12:00:00+08:00'
        self.store.commit_daily(old,[repository(7,1500)],[StarSnapshot(7,old,1500,at)],[])
        self.store.commit_daily(self.day,[repository(7,2500)],[],
            [Recommendation(7,self.day,'keyword',self.rule.id,None,None,self.at)])
        server=BrowserServer(RadarService(object(),self.store),self.store)
        self.addCleanup(server.close)
        self.assertIsNone(server._project_payload(7,context_date=self.day)['saved_at'])

    def test_batch_snapshot_failure_rolls_back_recommendations_and_metadata(self):
        with closing(self.store._connect()) as db,db:
            db.execute("CREATE TRIGGER fail_snapshot BEFORE INSERT ON snapshots BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        with self.assertRaises(Exception): self.publish_batch([repository(7,2500)])
        self.assertEqual(self.store.daily_recommendations(self.day),[])
        self.assertEqual(self.store.repositories_for_ids([7]),{})
