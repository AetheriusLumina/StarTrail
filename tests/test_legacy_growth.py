"""Retained-data upgrades must distinguish a return from a first discovery."""
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from github_radar.models import Recommendation, StarSnapshot
from github_radar.storage import RadarStore
from github_radar.service import RadarService
from github_radar.github_client import GitHubRequestError
from tests.test_wider_refresh import repo, Client, Discovery


DAY = '2026-09-30'
AT = '2026-09-30T10:00:00+00:00'


def legacy_pick(i, day=DAY, position=None):
    return Recommendation(i, day, 'growth', None, i, None,
                          day + 'T10:00:00+00:00', 'github_daily_new',
                          '2026-09-28' if day == DAY else '2026-09-27')


class LegacyGrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.store = RadarStore(self.data_dir)

    def seed(self, prior=(1, 2), current=(1, 2, 3, 4, 5)):
        for day, ids in [('2026-09-29', prior), (DAY, current)]:
            self.store.commit_daily(day, [repo(i) for i in ids],
                [StarSnapshot(i, day, 1000+i, day+'T10:00:00+00:00') for i in ids],
                [legacy_pick(i, day) for i in ids])

    def test_old_issue_marks_returning_ids_without_marking_its_first_appearance_old(self):
        self.seed()
        upgraded = RadarStore(self.data_dir)
        records = upgraded.daily_recommendations(DAY)
        self.assertEqual([r.display_role for r in records], ['old','old','new','new','new'])
        self.assertEqual([r.rank for r in records], [1,2,3,4,5])
        self.assertEqual([r.display_role for r in upgraded.daily_recommendations('2026-09-29')],
                         ['new','new'])
        self.assertEqual([(r.star_delta,r.observed_at,r.metric_date) for r in records],
                         [(i,AT,'2026-09-28') for i in (1,2,3,4,5)])

    def test_upgrade_preserves_original_rows_and_pending_repair_through_reopen(self):
        self.seed()
        self.store.set_followed(1, True, AT)
        keyword = self.store.add_keyword('MCP')
        upgraded = RadarStore(self.data_dir)
        self.assertEqual(upgraded.daily_recommendations(DAY)[0].display_role, 'old')
        self.assertTrue(upgraded.growth_repair_pending(DAY))
        reopened = RadarStore(self.data_dir)
        self.assertTrue(reopened.growth_repair_pending(DAY))
        self.assertFalse(reopened.growth_repair_pending('2026-09-29'))
        self.assertTrue(reopened.is_followed(1))
        self.assertEqual(reopened.list_keywords(), [keyword])
        self.assertEqual(reopened.snapshots_on('2026-09-29',[1])[1].stars,1001)
        with closing(sqlite3.connect(reopened.db_path)) as c:
            archived = [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM legacy_growth_archive WHERE local_date=? "
                "AND record_kind='recommendation' ORDER BY repo_id", (DAY,))]
        self.assertEqual(len(archived), 5)
        self.assertTrue(all(r['display_role'] is None and r['rank'] is None for r in archived))

    def test_seen_before_includes_prior_ai_removed_but_excludes_same_day(self):
        self.seed()
        with closing(self.store._connect()) as c, c:
            c.execute('INSERT INTO ai_removed_recommendations VALUES (?,?,?,?,?)',
                      ('2026-09-29',10,99,AT,'not_relevant'))
            c.execute('INSERT INTO ai_removed_recommendations VALUES (?,?,?,?,?)',
                      (DAY,10,88,AT,'not_relevant'))
        upgraded = RadarStore(self.data_dir)
        self.assertEqual(upgraded.daily_recommendations(DAY)[0].display_role,'old')
        self.assertEqual(upgraded.seen_repo_ids_before(DAY), {1,2,99})

    def test_modern_issue_is_not_reclassified_or_archived(self):
        pick = replace(legacy_pick(1), rank=7, display_role='new')
        self.store.commit_daily(DAY,[repo(1)],[],[pick])
        upgraded = RadarStore(self.data_dir)
        self.assertEqual(upgraded.daily_recommendations(DAY), [pick])
        self.assertTrue(callable(getattr(upgraded,'growth_repair_pending',None)))
        self.assertFalse(upgraded.growth_repair_pending(DAY))

    def repair_service(self, keyword_repo=None):
        self.seed(prior=(8,7), current=(8,7,6,5,4))
        if keyword_repo:
            rule = self.store.add_keyword('MCP')
            self.store.commit_daily(DAY,[repo(keyword_repo)],[],[
                Recommendation(keyword_repo,DAY,'keyword',rule.id,None,None,AT)])
        self.store = RadarStore(self.data_dir)
        pool = [repo(i) for i in range(1,9)]
        return RadarService(Client(pool), self.store, discovery=Discovery(pool))

    def test_completed_legacy_issue_returns_old_top_five_plus_five_unseen(self):
        service = self.repair_service()
        before = self.store.daily_recommendations('2026-09-29')
        result = service.refresh(DAY,AT)
        self.assertEqual(result.status,'ok')
        self.assertEqual([r.repo_id for r in result.recommendations],[8,7,6,5,4,3,2])
        self.assertEqual([r.rank for r in result.recommendations],[1,2,3,4,5,6,7])
        self.assertEqual([r.display_role for r in result.recommendations],
                         ['old','old','new','new','new','new','new'])
        self.assertEqual({r.metric_date for r in result.recommendations},{'2026-09-28'})
        self.assertEqual(self.store.daily_recommendations('2026-09-29'),before)
        self.assertFalse(self.store.growth_repair_pending(DAY))

    def test_repaired_issue_freezes_after_success(self):
        service = self.repair_service()
        first = service.refresh(DAY,'2026-09-30T07:00:00+08:00')
        self.assertEqual(sum(r.display_role=='new' for r in first.recommendations),5)
        service.discovery = Discovery([repo(99)])
        again = service.refresh(DAY,'2026-09-30T07:30:00+08:00')
        self.assertEqual(again.recommendations,first.recommendations)
        self.assertEqual(again.growth_coverage,first.growth_coverage)

    def test_mismatched_stat_day_or_network_failure_keeps_legacy_issue(self):
        service = self.repair_service()
        before = self.store.daily_recommendations(DAY)
        with closing(self.store._connect()) as c, c:
            c.execute("UPDATE recommendations SET metric_date='2026-09-30' WHERE local_date=?",(DAY,))
        before = self.store.daily_recommendations(DAY)
        result = service.refresh(DAY,AT)
        self.assertEqual(result.status,'error')
        self.assertEqual(self.store.daily_recommendations(DAY),before)
        self.assertTrue(self.store.growth_repair_pending(DAY))
        with patch.object(service.client,'get_repository',side_effect=GitHubRequestError('offline')):
            self.assertEqual(service.refresh(DAY,AT).status,'error')
        self.assertEqual(self.store.daily_recommendations(DAY),before)

    def test_growth_repair_rolls_back_without_losing_archive_or_history(self):
        service = self.repair_service()
        before = self.store.daily_recommendations(DAY)
        with closing(self.store._connect()) as c, c:
            c.execute("CREATE TRIGGER deny_growth BEFORE INSERT ON recommendations "
                      "WHEN NEW.repo_id=3 BEGIN SELECT RAISE(ABORT,'forced'); END")
        # Inserts really run; the trigger makes an in-progress write fail.
        with self.assertRaisesRegex(ValueError,'重复'):
            service.refresh(DAY,AT)
        self.assertEqual(self.store.daily_recommendations(DAY),before)
        self.assertTrue(self.store.growth_repair_pending(DAY))
        self.assertEqual(self.store.snapshots_on('2026-09-29',[8])[8].stars,1008)
        with closing(self.store._connect()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM legacy_growth_archive "
                "WHERE local_date=? AND record_kind='snapshot'",(DAY,)).fetchone()[0],0)

    def test_repair_keeps_current_keyword_ids_reserved(self):
        service = self.repair_service(keyword_repo=3)
        result = service.refresh(DAY,AT)
        growth = [r for r in result.recommendations if r.section=='growth']
        self.assertEqual([r.repo_id for r in growth],[8,7,6,5,4,2,1])
        self.assertEqual([r.rank for r in growth],[1,2,3,4,5,7,8])
        self.assertEqual(sum(r.section=='keyword' and r.repo_id==3 for r in result.recommendations),1)
