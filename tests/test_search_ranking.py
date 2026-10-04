import unittest
from dataclasses import replace
from github_radar.search_types import SearchScope,PreparedCandidate,ObservedRepository,GrowthAssessment
from github_radar.search_ranking import select_keyword_candidates,select_growth_candidates
from github_radar.ai_types import AIRepositoryInput,RelevanceVerdict
from github_radar.models import StarDay
from tests.test_storage import repository

def prepared(identity,stars,added=1):
    r=repository(identity,stars);day=StarDay('2026-10-03',added)
    return PreparedCandidate(ObservedRepository(r,'2026-10-04T12:00:00+08:00'),AIRepositoryInput(r,None,True),(),'s','e',day)

class SearchRankingTests(unittest.TestCase):
    def test_high_star_frontier_and_same_day_do_not_rotate(self):
        scope=SearchScope('keyword',1,'2026-10-04',None,'skills',1000,None)
        items=tuple(prepared(i,1400+i) for i in range(1,6))+(prepared(9,62000),)
        verdicts={p.observation.repo.id:RelevanceVerdict(p.observation.repo.id,'relevant','matches') for p in items}
        picks=select_keyword_candidates(scope,items,verdicts,set(),{1,2,3,4,5},{1,2,3,4,5})
        self.assertEqual([p.repo_id for p in picks],[9,5,4,3,2])
        self.assertEqual(picks,select_keyword_candidates(scope,items,verdicts,set(),{p.repo_id for p in picks},{p.repo_id for p in picks}))
    def test_growth_old_five_do_not_consume_new_slots_and_ties_are_real(self):
        scope=SearchScope('growth',None,'2026-10-04','2026-10-03','',100,None)
        items=tuple(prepared(i,10000-i,100-i) for i in range(1,11))
        daily={p.observation.repo.id:p.official_day for p in items}
        checked={i:GrowthAssessment(i,'supported','official day') for i in range(1,11)}
        picks=select_growth_candidates(scope,items,daily,checked,{1,2,3,4,5},set())
        self.assertEqual(len(picks),10)
        self.assertEqual(sum(p.display_role=='new' for p in picks),5)
        self.assertTrue(all(p.metric_date=='2026-10-03' for p in picks))
