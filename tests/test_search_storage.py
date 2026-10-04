import tempfile
import unittest
from github_radar.storage import RadarStore
from github_radar.search_storage import SearchStore
from github_radar.search_types import SearchScope, QueryExpansion, ObservedRepository, PreparedCandidate
from github_radar.ai_types import AIRepositoryInput, RelevanceVerdict
from tests.test_storage import repository

class SearchStorageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=RadarStore(temp.name);self.rule=self.store.add_keyword('local ai')
        self.scope=SearchScope('keyword',self.rule.id,'2026-10-04',None,'local ai',1000,None,'1')
        self.search=SearchStore(self.store)

    def test_migration_preserves_follows_and_history(self):
        from github_radar.models import Recommendation
        self.store.commit_daily(self.scope.local_date,[repository(7)],[],
            [Recommendation(7,self.scope.local_date,'keyword',self.rule.id,None,None,'2026-10-04T12:00:00Z')])
        self.store.set_followed(7,True,'2026-10-04T12:00:00Z')
        RadarStore(self.store.data_dir);RadarStore(self.store.data_dir)
        self.assertTrue(self.store.is_followed(7));self.assertIn(7,self.store.seen_repo_ids())

    def test_candidate_not_displayed_can_later_be_selected(self):
        observation=ObservedRepository(repository(8),'2026-10-04T12:00:00Z')
        self.search.save_candidates(self.scope,(observation,),())
        self.assertNotIn(8,self.store.seen_repo_ids())
        self.assertEqual(self.search.candidate_page(self.scope,None,10),(observation,))

    def test_older_observation_cannot_overwrite_new_metadata(self):
        self.search.save_candidates(self.scope,(ObservedRepository(repository(8,2000),'2026-10-04T12:00:00Z'),),())
        self.search.save_candidates(self.scope,(ObservedRepository(repository(8,1500),'2026-10-03T12:00:00Z'),),())
        self.assertEqual(self.search.candidate_page(self.scope,None)[0].repo.stars,2000)

    def test_partial_batch_restart_reuses_verdicts_across_dates(self):
        from dataclasses import replace
        observation=ObservedRepository(repository(8),'2026-10-04T12:00:00Z')
        prepared=PreparedCandidate(observation,AIRepositoryInput(observation.repo,None,True),(),'same-content','day-evidence',None)
        verdict=RelevanceVerdict(8,'relevant','README matches')
        self.search.save_judgments(self.scope,(prepared,),(verdict,),(),'2026-10-04T12:01:00Z')
        search=SearchStore(RadarStore(self.store.data_dir))
        tomorrow=replace(self.scope,local_date='2026-10-05')
        self.assertEqual(search.load_judgment(tomorrow,8,'same-content','semantic'),verdict)
        self.assertIsNone(search.load_judgment(replace(tomorrow,model_id='other'),8,'same-content','semantic'))
        self.assertIsNone(search.load_judgment(tomorrow,8,'changed','semantic'))

    def test_expansion_preserves_original_and_can_be_reopened(self):
        value=QueryExpansion('local ai',('offline AI',),(),'1')
        self.search.save_expansion(self.scope,value)
        self.assertEqual(SearchStore(RadarStore(self.store.data_dir)).load_expansion(self.scope),value)
