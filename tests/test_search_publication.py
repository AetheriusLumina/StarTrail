import unittest
import tempfile
from datetime import datetime,timezone,timedelta
from dataclasses import replace
from github_radar.storage import RadarStore
from github_radar.search_storage import SearchStore
from github_radar.search_types import SearchScope,Publication,ObservedRepository
from github_radar.models import Recommendation,StarSnapshot
from tests.test_storage import repository

class SearchPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=RadarStore(self.tmp.name);self.search=SearchStore(self.store)
        rule=self.store.add_keyword('skills',1000)
        self.now=datetime.now().astimezone();self.day=self.now.date().isoformat()
        self.scope=SearchScope('keyword',rule.id,self.day,None,'skills',1000,None)
    def publication(self,lease):
        at=self.now.isoformat();r=repository(1)
        return Publication(self.scope,lease.generation,(ObservedRepository(r,at),),
            (StarSnapshot(r.id,self.day,r.stars,at),),
            (Recommendation(r.id,self.day,'keyword',self.scope.keyword_id,None,None,at),),None)
    def test_model_change_invalidates_active_publication(self):
        a=self.search.claim_run(self.scope,'a',now=self.now.timestamp())
        self.store.save_ai_model('another-model')
        with self.assertRaises(ValueError):self.search.publish(self.publication(a),a,now=self.now)
        self.assertEqual(self.store.daily_recommendations(self.day),[])

    def test_concurrent_owner_reuses_lease_and_has_no_write_right(self):
        a=self.search.claim_run(self.scope,'owner-a',now=self.now.timestamp())
        b=self.search.claim_run(self.scope,'owner-b',now=self.now.timestamp())
        self.assertTrue(a.acquired);self.assertFalse(b.acquired);self.assertEqual(a.job_id,b.job_id)
    def test_stale_generation_cannot_publish(self):
        a=self.search.claim_run(self.scope,'a',now=self.now.timestamp()-2000,ttl=10)
        b=self.search.claim_run(self.scope,'b',now=self.now.timestamp())
        self.assertGreater(b.generation,a.generation)
        with self.assertRaises(ValueError):self.search.publish(self.publication(a),a,now=self.now)
        self.assertEqual(self.store.daily_recommendations(self.day),[])
    def test_snapshot_date_and_keyword_change_reject_atomically(self):
        a=self.search.claim_run(self.scope,'a',now=self.now.timestamp())
        bad=replace(self.publication(a),snapshots=(StarSnapshot(1,self.day,1500,'2020-01-01T00:00:00Z'),))
        with self.assertRaises(ValueError):self.search.publish(bad,a,now=self.now)
        self.assertEqual(self.store.repositories_for_ids([1]),{})
        self.store.set_keyword_min_stars(self.scope.keyword_id,2000)
        with self.assertRaises(ValueError):self.search.publish(self.publication(a),a,now=self.now)
    def test_publish_keeps_visibility_history_and_other_modules(self):
        a=self.search.claim_run(self.scope,'a',now=self.now.timestamp())
        self.search.publish(self.publication(a),a,now=self.now)
        self.assertIn(1,self.store.seen_repo_ids())
        self.search.finish_run(a)
        b=self.search.claim_run(self.scope,'b',now=self.now.timestamp())
        self.search.publish(replace(self.publication(b),recommendations=()),b,now=self.now)
        self.assertEqual(self.store.daily_recommendations(self.day),[])
        self.assertIn(1,self.store.seen_repo_ids())
    def test_renewal_requires_owner_and_same_generation(self):
        a=self.search.claim_run(self.scope,'a',now=self.now.timestamp())
        self.assertFalse(self.search.renew_run(replace(a,owner='wrong'),now=self.now.timestamp()))
        self.assertTrue(self.search.renew_run(a,now=self.now.timestamp()))

    def test_expired_owner_cannot_overwrite_successor_progress(self):
        old=self.search.claim_run(self.scope,'old',now=self.now.timestamp()-2000,ttl=10)
        stale=replace(self.search.progress(old.job_id),newly_checked=1,status='paused')
        new=self.search.claim_run(self.scope,'new',now=self.now.timestamp())
        current=replace(self.search.progress(new.job_id),newly_checked=20,status='running')
        self.assertTrue(self.search.save_progress(current,lease=new))
        self.assertFalse(self.search.save_progress(stale,lease=old))
        self.assertEqual(self.search.progress(new.job_id).newly_checked,20)
