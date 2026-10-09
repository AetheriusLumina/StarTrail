import importlib
import importlib.util
import tempfile
from pathlib import Path
import unittest

from github_radar import discovery_types as types
from github_radar.models import KeywordRule, Repository
from github_radar.storage import RadarStore


class DiscoveryTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("github_radar.discovery"))
        return importlib.import_module("github_radar.discovery")

    def budget(self, core=None, search=None, deadline=300):
        self.assertTrue(hasattr(types, "RequestBudget"))
        return types.RequestBudget(core, search, deadline)

    def test_unobserved_budget_only_allows_probe(self):
        budget = self.budget()
        self.assertTrue(budget.can_spend("core", 1, 0))
        self.assertFalse(budget.can_spend("core", 2, 0))
        budget.spend("core", 1, 0)
        self.assertFalse(budget.can_spend("core", 1, 0))
        budget.observe({"X-RateLimit-Resource":"core", "X-RateLimit-Remaining":"12"})
        self.assertTrue(budget.can_spend("core", 2, 0))
        self.assertTrue(budget.can_spend("core", 12, 0))
        self.assertFalse(budget.can_spend("core", 13, 0))
        self.assertFalse(budget.can_spend("core", 1, 300))

    def test_source_failure_keeps_other_sources(self):
        class Failure:
            def discover(self, *args): raise OSError("offline")
        class Trend:
            def daily(self, observed):
                c=types.DiscoveryCandidate("a/project",None,("trendshift",),observed)
                return (types.DiscoveryBatch("trendshift",(c,),None,True,()),)
            def topic(self,*args): return types.DiscoveryBatch("topic",(),None,False,())
        with tempfile.TemporaryDirectory() as d:
            store=RadarStore(Path(d));
            report=self.module().DiscoveryCoordinator(None,store,free_events=Failure(),trendshift=Trend(),
                                                      clock=lambda:0).discover([],"2026-09-28",self.budget(10,0))
            self.assertEqual([c.full_name for c in report.candidates],["a/project"])
            self.assertTrue(any("offline" in n for n in report.notes))
            self.assertEqual(store.seen_repo_ids(),set())

    def test_search_partition_over_1000_is_split(self):
        self.assertTrue(hasattr(types,"SearchPage"))
        queries=[]
        class Client:
            core_remaining=10;search_remaining=9
            def search_page(self,query,page=1):
                queries.append(query)
                return types.SearchPage((), 2000 if len(queries)==1 else 0, False)
        with tempfile.TemporaryDirectory() as d:
            coord=self.module().DiscoveryCoordinator(Client(),RadarStore(Path(d)),free_events=False,
                                                      trendshift=False,clock=lambda:0)
            coord.discover([KeywordRule(1,"MCP",1000,True)],"2026-09-28",self.budget(10,9))
            self.assertGreater(len(queries),1)
            self.assertTrue(all("created:" in q for q in queries[1:]))

    def test_cursor_resumes_after_budget_stop(self):
        self.assertTrue(hasattr(types,"SearchPage"))
        pages=[]
        class Client:
            core_remaining=10;search_remaining=0
            def search_page(self,query,page=1):
                pages.append(page)
                r=Repository(1,"a/repo","https://github.com/a/repo","MCP",(),None,10,False)
                return types.SearchPage((r,)*100,200,False)
        with tempfile.TemporaryDirectory() as d:
            store=RadarStore(Path(d));client=Client()
            coord=self.module().DiscoveryCoordinator(client,store,free_events=False,trendshift=False,clock=lambda:0)
            coord.discover([],"2026-09-28",self.budget(10,1))
            coord=self.module().DiscoveryCoordinator(client,RadarStore(Path(d)),free_events=False,trendshift=False,clock=lambda:0)
            coord.discover([],"2026-09-28",self.budget(10,1))
            self.assertEqual(pages,[1,2])

    def test_incomplete_search_preserves_candidates_without_consuming_history(self):
        self.assertTrue(hasattr(types,"SearchPage"))
        class Client:
            core_remaining=10;search_remaining=0
            def search_page(self,query,page=1):
                r=Repository(7,"a/repo","https://github.com/a/repo","MCP",(),None,1000,False)
                return types.SearchPage((r,),100,True)
        with tempfile.TemporaryDirectory() as d:
            store=RadarStore(Path(d))
            report=self.module().DiscoveryCoordinator(Client(),store,free_events=False,trendshift=False,
                 clock=lambda:0).discover([],"2026-09-28",self.budget(10,1))
            self.assertEqual([c.repo_id for c in report.candidates],[7])
            self.assertEqual(store.seen_repo_ids(),set())
            self.assertTrue(report.notes)
