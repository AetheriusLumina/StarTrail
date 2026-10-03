import sqlite3,tempfile,unittest
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from github_radar.models import GrowthCoverage,OfficialStarWeek,Recommendation,StarSnapshot
from github_radar.service import RadarService
from github_radar.storage import RadarStore
from tests.test_wider_refresh import Client,Discovery,repo

DAY='2026-10-02';AT=DAY+'T20:00:00+08:00'
WEEK=int(datetime(2026,9,27,tzinfo=timezone.utc).timestamp())

class YesterdayClient(Client):
    def star_history_weeks(self,name):
        self.calls.append(name);self.core_remaining-=1
        i=self.by_name[name].id
        return [OfficialStarWeek(WEEK,(0,0,0,100,100+i,999999,999999))]

class StatDateCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=RadarStore(Path(self.temp.name))
        self.repos=[repo(i) for i in range(1,10)]+[repo(99)]
        self.rule=self.store.add_keyword('MCP')
        self.store.commit_daily('2026-10-01',self.repos,[],[
            Recommendation(1,'2026-10-01','growth',None,5,None,AT,'github_daily_new','2026-09-30',1,'new')],
            completed_at=AT,completed_sections=[('growth',None)])
        self.store.commit_daily(DAY,self.repos,[StarSnapshot(i,DAY,1000+i,AT) for i in range(2,7)],
            [Recommendation(i,DAY,'growth',None,100-i,None,AT,'github_daily_new','2026-09-30',i-1,'new') for i in range(2,7)]+
            [Recommendation(99,DAY,'keyword',self.rule.id,None,None,AT,rank=1)],
            completed_at=AT,completed_sections=[('growth',None),('keyword',self.rule.id)],
            growth_coverage=GrowthCoverage(10,10,('test',),'2026-09-30','github_daily_new'))

    def service(self,client=None):
        return RadarService(client or YesterdayClient(self.repos),self.store,discovery=Discovery(self.repos))

    def rows(self,sql):
        with closing(sqlite3.connect(self.store.db_path)) as db:return db.execute(sql).fetchall()

    def test_manual_corrects_today_to_full_yesterday_despite_disabled_schedule(self):
        self.store.save_auto_update_settings(False,'21:59')
        with closing(sqlite3.connect(self.store.db_path)) as db,db:
            db.execute("INSERT INTO auto_update_runs VALUES (?,?,?,?,?)",(DAY,3,DAY+'T11:00:00+08:00','error','offline'))
        old=self.store.daily_recommendations('2026-10-01')
        keywords=[r for r in self.store.daily_recommendations(DAY) if r.section=='keyword']
        result=self.service().refresh(DAY,AT)
        self.assertEqual(result.status,'ok')
        self.assertEqual(result.growth_coverage.stat_date,'2026-10-01')
        growth=[r for r in result.recommendations if r.section=='growth']
        self.assertEqual([r.repo_id for r in growth],[9,8,7,6,5])
        self.assertEqual([r.rank for r in growth],[2,3,4,5,6], 'Reserved keyword retains its real first rank')
        self.assertTrue(all(r.metric_date=='2026-10-01' and r.star_delta==100+r.repo_id for r in growth))
        self.assertEqual(keywords,[r for r in result.recommendations if r.section=='keyword'])
        self.assertEqual(old,self.store.daily_recommendations('2026-10-01'))
        self.assertEqual(self.store.auto_attempts(DAY).attempts,3)
        archive=self.rows("SELECT record_kind,payload FROM legacy_growth_archive WHERE record_kind LIKE 'stat-date:%'")
        self.assertEqual(len(archive),11,'Archive five old rows, five snapshots and previous coverage')
        frozen=result.recommendations
        again=self.service().refresh(DAY,AT)
        self.assertEqual(frozen,again.recommendations,'Corrected same-day issue remains stable')
        self.assertEqual(len(archive),len(self.rows("SELECT * FROM legacy_growth_archive WHERE record_kind LIKE 'stat-date:%'")))

    def test_missing_yesterday_preserves_old_issue_and_history(self):
        class Missing(YesterdayClient):
            def star_history_weeks(self,name):
                return [OfficialStarWeek(WEEK-7*86400,(1,2,3,4,5,6,7))]
        before=self.store.daily_recommendations(DAY)
        result=self.service(Missing(self.repos)).refresh(DAY,AT)
        self.assertEqual(result.status,'error')
        self.assertIn('2026-10-01',result.message)
        self.assertEqual(before,self.store.daily_recommendations(DAY))
        self.assertEqual(self.store.growth_coverage(DAY).stat_date,'2026-09-30')
        self.assertEqual(self.rows("SELECT * FROM legacy_growth_archive WHERE record_kind LIKE 'stat-date:%'"),[])

    def test_date_correction_preserves_current_keyword_snapshot(self):
        with closing(sqlite3.connect(self.store.db_path)) as db,db:
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?)',(99,DAY,555,DAY+'T09:00:00+08:00'))
        before=self.rows("SELECT * FROM snapshots WHERE repo_id=99")
        self.assertEqual(self.service().refresh(DAY,AT).status,'ok')
        self.assertEqual(before,self.rows("SELECT * FROM snapshots WHERE repo_id=99"))

    def test_incomplete_new_slots_preserve_old_growth_and_can_retry(self):
        clock=[0.0]
        class Slow(YesterdayClient):
            def star_history_weeks(self,name):
                result=super().star_history_weeks(name)
                clock[0]+=150.0
                return result
        before=self.store.daily_recommendations(DAY)
        result=RadarService(Slow(self.repos),self.store,discovery=Discovery(self.repos),clock=lambda:clock[0]).refresh(DAY,AT)
        self.assertEqual(result.status,'error')
        self.assertEqual(before,self.store.daily_recommendations(DAY))
        self.assertEqual(self.rows("SELECT * FROM legacy_growth_archive WHERE record_kind LIKE 'stat-date:%'"),[])
        retry=self.service().refresh(DAY,AT)
        self.assertEqual(retry.status,'ok')
        self.assertEqual(sum(r.display_role=='new' for r in retry.recommendations if r.section=='growth'),5)

    def test_atomic_failure_rolls_back_date_correction(self):
        before=self.store.daily_recommendations(DAY)
        self.assertTrue(hasattr(self.store,'commit_growth_date_update'))
        with self.assertRaises(ValueError):
            self.store.commit_growth_date_update(DAY,self.repos,[],[
                Recommendation(i,DAY,'growth',None,100+i,None,AT,'github_daily_new','2026-10-01',rank,'new')
                for rank,i in enumerate((99,8,7,6,5),1)],
                completed_at=AT,growth_coverage=GrowthCoverage(10,10,('test',),'2026-10-01','github_daily_new'),
                expected_stat_date='2026-09-30',official_weeks={})
        self.assertEqual(before,self.store.daily_recommendations(DAY))
        self.assertEqual(self.rows("SELECT * FROM legacy_growth_archive WHERE record_kind LIKE 'stat-date:%'"),[])

    def test_stale_expected_date_and_past_issue_are_rejected_without_writes(self):
        before=self.store.daily_recommendations(DAY)
        for day,expected in ((DAY,'2026-09-29'),('2026-10-01','2026-09-30')):
            with self.subTest(day=day,expected=expected),self.assertRaises(ValueError):
                self.store.commit_growth_date_update(day,self.repos,[],[
                    Recommendation(9,day,'growth',None,109,None,AT,'github_daily_new','2026-10-01',2,'new')],
                    completed_at=AT,growth_coverage=GrowthCoverage(10,10,('test',),'2026-10-01','github_daily_new'),
                    expected_stat_date=expected,official_weeks={})
        self.assertEqual(before,self.store.daily_recommendations(DAY))
        self.assertEqual(self.rows("SELECT * FROM legacy_growth_archive WHERE record_kind LIKE 'stat-date:%'"),[])
