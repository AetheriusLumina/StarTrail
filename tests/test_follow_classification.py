import sqlite3
import tempfile
import unittest
from contextlib import closing
from github_radar.storage import RadarStore
from github_radar.models import Recommendation
from tests.test_storage import repository

class ClassificationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=RadarStore(temp.name);self.at='2026-10-04T12:00:00Z'
        self.store.commit_daily('2026-10-04',[repository(7)],[],[Recommendation(7,'2026-10-04','growth',None,40,None,self.at)])
        self.a=self.store.create_follow_folder('A',self.at)
        self.b=self.store.create_follow_folder('B',self.at)
        self.c=self.store.create_follow_folder('C',self.at)

    def test_create_and_classify_follows_atomically(self):
        result=self.store.classify_followed_project(7,[self.a.id],'New',self.at)
        self.assertTrue(result['followed'])
        self.assertEqual(len(self.store.follow_folder_ids(7)),2)
        self.assertTrue(self.store.is_followed(7))

    def test_classification_write_failure_rolls_back_folder_and_follow(self):
        with closing(self.store._connect()) as db,db:
            db.execute("CREATE TRIGGER no_item BEFORE INSERT ON follow_folder_items BEGIN SELECT RAISE(ABORT,'disk full'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.classify_followed_project(7,[self.a.id],'New',self.at)
        self.assertFalse(self.store.is_followed(7))
        self.assertEqual(len(self.store.list_follow_folders()),3)

    def test_move_custom_preserves_other_memberships_and_follow_time(self):
        self.store.classify_followed_project(7,[self.a.id,self.b.id],None,self.at)
        self.store.move_followed_project(7,str(self.a.id),str(self.c.id))
        self.assertEqual(self.store.follow_folder_ids(7),[self.b.id,self.c.id])
        self.assertEqual(self.store.followed_at(7),self.at)
        self.store.move_followed_project(7,str(self.b.id),'unfiled')
        self.assertEqual(self.store.follow_folder_ids(7),[])
        self.assertTrue(self.store.is_followed(7))

    def test_all_is_not_draggable_or_destination_and_stale_source_fails(self):
        self.store.classify_followed_project(7,[self.a.id],None,self.at)
        for source,target in [('all',str(self.b.id)),(str(self.a.id),'all'),('unfiled',str(self.b.id)),(str(self.c.id),str(self.b.id))]:
            with self.subTest(source=source,target=target),self.assertRaises((ValueError,LookupError)):
                self.store.move_followed_project(7,source,target)
        self.assertEqual(self.store.follow_folder_ids(7),[self.a.id])

    def test_empty_unfollowed_selection_does_not_follow(self):
        with self.assertRaises(ValueError):self.store.classify_followed_project(7,[],None,self.at)
        self.assertFalse(self.store.is_followed(7))
        self.store.classify_followed_project(7,[self.a.id],None,self.at)
        self.store.classify_followed_project(7,[],None,self.at)
        self.assertTrue(self.store.is_followed(7));self.assertEqual(self.store.follow_folder_ids(7),[])
