import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import unittest

from github_radar.discovery_types import DiscoveryBatch, DiscoveryCandidate, DiscoveryReport
from github_radar.models import OfficialStarWeek, Recommendation, Repository
from github_radar.service import RadarService
from github_radar.storage import RadarStore
from github_radar.github_client import GitHubRequestError

NOW="2026-09-30T10:00:00+00:00"
WEEK=int(datetime(2026,9,27,tzinfo=timezone.utc).timestamp())


def repo(i): return Repository(i,f"a/repo-{i}",f"https://github.com/a/repo-{i}","MCP",(),None,1000+i,False)


class Client:
    def __init__(self, repos):
        self.by_name={r.full_name:r for r in repos}
        self.core_remaining=200;self.search_remaining=0;self.calls=[]
    def star_history_weeks(self,name):
        self.core_remaining-=1;self.calls.append(name)
        return [OfficialStarWeek(WEEK,(1,)+(self.by_name[name].id,)*6)]
    def get_repository(self,name):
        self.core_remaining-=1
        if name not in self.by_name:
            raise GitHubRequestError("仓库旧名称不存在")
        return self.by_name[name]


class Discovery:
    def __init__(self,repos,clock=None):self.repos=repos;self.clock=clock
    def discover(self,rules,day,budget):
        if self.clock is not None:self.clock[0]=budget.deadline+1
        candidates=tuple(DiscoveryCandidate(r.full_name,r.id,("test_source",),NOW,cached_repo=r)
                         for r in self.repos)
        batch=DiscoveryBatch("test_source",candidates,None,True,())
        return DiscoveryReport(candidates,(batch,),())


class WiderRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=RadarStore(Path(self.temp.name))

    def service(self,client,discovery,**kwargs):
        self.assertIn("discovery",__import__('inspect').signature(RadarService).parameters)
        return RadarService(client,self.store,discovery=discovery,**kwargs)

    def test_refresh_scores_more_than_25_when_budget_allows(self):
        repos=[repo(i) for i in range(1,41)];client=Client(repos)
        result=self.service(client,Discovery(repos)).refresh("2026-09-30",NOW)
        self.assertEqual(result.status,"ok")
        self.assertEqual(len(client.calls),40)
        self.assertEqual([r.repo_id for r in result.recommendations],[40,39,38,37,36])
        self.assertEqual([r.rank for r in result.recommendations],[1,2,3,4,5])
        self.assertEqual(result.growth_coverage.scored_count,40)

    def test_same_day_refresh_preserves_slots_and_coverage_snapshot(self):
        repos=[repo(i) for i in range(1,9)];client=Client(repos)
        service=self.service(client,Discovery(repos))
        first=service.refresh("2026-09-30",NOW)
        client.by_name={r.full_name:replace(r,stars=9999) for r in repos}
        service.discovery=Discovery([repo(99)])
        second=service.refresh("2026-09-30","2026-09-30T11:00:00+00:00")
        self.assertEqual(first.recommendations,second.recommendations)
        self.assertEqual(first.growth_coverage,second.growth_coverage)
        self.assertEqual(second.repositories[8].stars,9999)
        self.store=RadarStore(Path(self.temp.name))
        self.assertEqual(self.store.daily_recommendations("2026-09-30"),list(first.recommendations))

    def test_next_day_reselects_and_rename_keeps_identity(self):
        repos=[repo(i) for i in range(1,9)]
        self.service(Client(repos),Discovery(repos)).refresh("2026-09-30",NOW)
        changed=replace(repo(8),full_name="a/renamed",html_url="https://github.com/a/renamed")
        repos=[changed]+[repo(i) for i in range(1,8)]+[repo(99)]
        result=self.service(Client(repos),Discovery(repos)).refresh("2026-10-01","2026-10-01T10:00:00+00:00")
        self.assertEqual(result.recommendations[0].repo_id,99)
        self.assertEqual(next(r for r in result.recommendations if r.repo_id==8).display_role,"old")
        self.assertEqual(sum(r.display_role=="new" for r in result.recommendations),4)

    def test_recycled_name_does_not_replace_saved_repo(self):
        r=repo(1);self.service(Client([r]),Discovery([r])).refresh("2026-09-30",NOW)
        client=Client([replace(r,id=999)])
        result=self.service(client,Discovery([])).refresh("2026-09-30",NOW)
        self.assertEqual(result.status,"error")
        self.assertEqual(self.store.repositories_for_ids([1])[1].id,1)

    def test_deadline_retains_old_results(self):
        r=repo(1);self.service(Client([r]),Discovery([r])).refresh("2026-09-30",NOW)
        ticks=[0]
        class ExpiredDiscovery(Discovery):
            def discover(self,rules,day,budget):
                result=super().discover(rules,day,budget)
                ticks[0]=301
                return result
        result=self.service(Client([repo(2)]),ExpiredDiscovery([repo(2)]),clock=lambda:ticks[0]).refresh(
            "2026-10-01","2026-10-01T10:00:00+00:00")
        self.assertEqual(result.status,"error")
        self.assertEqual([r.repo_id for r in result.recommendations],[1])

    def test_discovery_leaves_time_for_official_growth_verification(self):
        ticks=[0];repos=[repo(i) for i in range(1,7)]
        result=self.service(Client(repos),Discovery(repos,ticks),clock=lambda:ticks[0]).refresh(
            "2026-09-30",NOW)
        self.assertEqual(result.status,"ok")
        self.assertEqual(result.growth_coverage.scored_count,6)
        self.assertEqual([r.repo_id for r in result.recommendations],[6,5,4,3,2])

    def test_deadline_commits_verified_growth_and_resumes_unscored_candidates(self):
        ticks=[0];repos=[repo(i) for i in range(1,9)]
        class SlowClient(Client):
            def star_history_weeks(self,name):
                weeks=super().star_history_weeks(name)
                ticks[0]+=50
                return weeks
        result=self.service(SlowClient(repos),Discovery(repos),clock=lambda:ticks[0]).refresh(
            "2026-09-30",NOW)
        self.assertEqual(result.status,"ok")
        self.assertEqual(result.growth_coverage.scored_count,6)
        self.assertEqual([r.repo_id for r in result.recommendations],[6,5,4,3,2])
        self.assertTrue(any("留待下一次" in note for note in result.notes))
        self.assertIsNone(self.store.latest_refresh_failure())
        self.assertEqual(self.store.latest_successful_date(),"2026-09-30")
        self.assertTrue({7,8}.issubset({c.repo_id for c in self.store.catalog_candidates(20)}))

    def test_refresh_budget_does_not_block_later_manual_ai_requests(self):
        client=Client([repo(1)]);client.budget=None;client.clock=lambda:0
        self.service(client,Discovery([repo(1)])).refresh("2026-09-30",NOW)
        self.assertIsNone(client.budget)

    def test_official_keyword_metadata_survives_growth_budget_shortage(self):
        self.store.add_keyword("MCP")
        repos=[repo(i) for i in range(1,15)]
        client=Client(repos);client.core_remaining=12
        result=self.service(client,Discovery(repos)).refresh("2026-09-30",NOW)
        keywords=[r for r in result.recommendations if r.section=="keyword"]
        growth_ids={r.repo_id for r in result.recommendations if r.section=="growth"}
        expected=[r.id for r in sorted(repos,key=lambda r:r.stars,reverse=True) if r.id not in growth_ids][:5]
        self.assertEqual([r.repo_id for r in keywords],expected)
        self.assertEqual(result.recommendations[0].matched_keyword_ids,(1,))
