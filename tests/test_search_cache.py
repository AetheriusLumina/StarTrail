import unittest
from dataclasses import replace
from types import SimpleNamespace
from github_radar.search_coordinator import semantic_fingerprint,evidence_fingerprint,prepare_candidate
from github_radar.search_types import SearchScope,ObservedRepository
from github_radar.models import StarDay
from tests.test_storage import repository

class SearchCacheTests(unittest.TestCase):
    def setUp(self):self.scope=SearchScope('keyword',1,'2026-10-04',None,'skills',1000,'model-a')
    def test_star_only_change_reuses_semantic_verdict(self):
        self.assertEqual(semantic_fingerprint(self.scope,repository(7,1500),'readme','1'),semantic_fingerprint(self.scope,repository(7,3000),'readme','1'))
    def test_content_or_model_change_requires_review(self):
        before=semantic_fingerprint(self.scope,repository(),'same','1')
        self.assertNotEqual(before,semantic_fingerprint(replace(self.scope,model_id='other'),repository(),'same','1'))
        self.assertNotEqual(before,semantic_fingerprint(self.scope,repository(),'changed','1'))
    def test_growth_stat_day_is_not_semantic_cache_key(self):
        a=replace(self.scope,section='growth',keyword_id=None,stat_date='2026-10-03')
        b=replace(a,local_date='2026-10-05',stat_date='2026-10-04')
        self.assertEqual(semantic_fingerprint(a,repository(),'same','1'),semantic_fingerprint(b,repository(),'same','1'))
        self.assertNotEqual(evidence_fingerprint(a,(),StarDay(a.stat_date,50)),evidence_fingerprint(b,(),StarDay(b.stat_date,50)))
    def test_large_readme_bounded_and_old_observation_stays_old(self):
        observed=ObservedRepository(repository(),'2026-10-02T12:00:00Z')
        service=SimpleNamespace(client=SimpleNamespace(readme_excerpt=lambda *args,**kwargs:'x'*20000))
        prepared=prepare_candidate(observed,self.scope,service,(),None)
        self.assertLessEqual(len(prepared.source.readme_excerpt),6000)
        self.assertTrue(prepared.source.source_limited)
        self.assertEqual(prepared.observation.observed_at,observed.observed_at)
