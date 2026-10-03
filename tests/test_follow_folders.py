import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from github_radar.storage import RadarStore
from github_radar.models import Recommendation
from tests.test_storage import repository


class FollowFolderTests(unittest.TestCase):
    def test_manual_order_survives_rename_reopen_and_new_folder_appends(self):
        a,b,c=self.create('Alpha'),self.create('Beta'),self.create('Gamma')
        self.store.reorder_follow_folders([c.id,a.id,b.id])
        self.store.rename_follow_folder(c.id,'ZZZ')
        self.store=RadarStore(self.store.data_dir)
        d=self.create('AAA')
        self.assertEqual([f.id for f in self.store.list_follow_folders()],[c.id,a.id,b.id,d.id])
        for ids in ([a.id,a.id,b.id,d.id],[a.id,b.id],[a.id,b.id,c.id,999],[True,a.id,b.id,c.id], '1'):
            with self.subTest(ids=ids),self.assertRaises((ValueError,LookupError)):
                self.store.reorder_follow_folders(ids)
            self.assertEqual([f.id for f in self.store.list_follow_folders()],[c.id,a.id,b.id,d.id])

    def test_order_migration_keeps_legacy_order_and_follow_time(self):
        b,a=self.create('Beta'),self.create('Alpha')
        self.store.set_follow_folders(7,[b.id])
        with closing(sqlite3.connect(self.store.db_path)) as db,db:
            db.execute('ALTER TABLE follow_folders DROP COLUMN sort_position')
        self.store=RadarStore(self.store.data_dir)
        self.assertEqual([f.id for f in self.store.list_follow_folders()],[a.id,b.id])
        self.assertEqual(self.store.follow_folder_ids(7),[b.id])
        self.assertEqual(self.store.followed_at(7),self.at)

    def test_move_unfiled_checks_membership_transactionally_and_preserves_follow(self):
        a,b=self.create('Alpha'),self.create('Beta')
        self.store.move_unfiled_to_folder(7,a.id)
        self.assertEqual(self.store.follow_folder_ids(7),[a.id])
        with self.assertRaises(ValueError):self.store.move_unfiled_to_folder(7,b.id)
        self.assertEqual(self.store.follow_folder_ids(7),[a.id])
        self.assertEqual(self.store.followed_at(7),self.at)
        with self.assertRaises(LookupError):self.store.move_unfiled_to_folder(8,b.id)
        self.assertFalse(self.store.is_followed(8))

    def test_order_write_failure_rolls_back_positions(self):
        a,b=self.create('Alpha'),self.create('Beta')
        with closing(sqlite3.connect(self.store.db_path)) as db,db:
            db.execute("CREATE TRIGGER stop_order BEFORE UPDATE OF sort_position ON follow_folders WHEN NEW.id="+str(a.id)+" BEGIN SELECT RAISE(ABORT,'write failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.reorder_follow_folders([b.id,a.id])
        self.assertEqual([f.id for f in self.store.list_follow_folders()],[a.id,b.id])

    def test_two_simultaneous_unfiled_moves_only_one_can_claim_project(self):
        a,b=self.create('Alpha'),self.create('Beta');gate=threading.Barrier(2);results=[]
        def move(identity):
            gate.wait()
            try:self.store.move_unfiled_to_folder(7,identity);results.append('saved')
            except ValueError:results.append('already filed')
        threads=[threading.Thread(target=move,args=(f.id,)) for f in (a,b)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(3);self.assertFalse(thread.is_alive())
        self.assertCountEqual(results,['saved','already filed'])
        self.assertIn(self.store.follow_folder_ids(7),([a.id],[b.id]))
        self.assertEqual(self.store.followed_at(7),self.at)

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('github_radar.follow_folders'),'Following folders not implemented')
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=RadarStore(Path(temp.name))
        day='2026-09-26';self.at=day+'T08:00:00+08:00'
        self.store.commit_daily(day,[repository(7),repository(8)],[],
            [Recommendation(n,day,'growth',None,5,None,self.at) for n in (7,8)])
        self.store.set_followed(7,True,self.at)

    def create(self,name):return self.store.create_follow_folder(name,self.at)

    def test_legacy_migration_keeps_follow_time_and_history_unfiled(self):
        with closing(sqlite3.connect(self.store.db_path)) as db,db:
            db.execute('DROP TABLE follow_folder_items');db.execute('DROP TABLE follow_folders')
        store=RadarStore(self.store.data_dir);store=RadarStore(store.data_dir)
        self.assertEqual(store.list_follow_folders(),[])
        self.assertEqual([r.id for r,_,_ in store.followed_repositories('unfiled')],[7])
        self.assertEqual(store.followed_at(7),self.at)
        self.assertEqual(store.history_dates(),[('2026-09-26',2)])

    def test_name_trim_nfkc_casefold_unique_and_invalid_inputs(self):
        folder=self.create(' ＡＩ ')
        self.assertEqual(folder.name,'ＡＩ')
        with self.assertRaises(ValueError):self.create('ai')
        chinese=self.create('工具')
        self.assertEqual(self.store.rename_follow_folder(chinese.id,' 工具箱 ').name,'工具箱')
        for name in ('',' '*3,'x'*41,'a\nname','bad\x00','a\u202ename',None):
            with self.subTest(name=name),self.assertRaises(ValueError):self.create(name)
        with self.assertRaises(ValueError):self.store.rename_follow_folder(chinese.id,'AI')
        self.assertEqual(len(self.store.list_follow_folders()),2)

    def test_maximum_200_folders_cannot_be_exceeded(self):
        for n in range(200):self.create('group-'+str(n))
        with self.assertRaises(ValueError):self.create('one more')
        self.assertEqual(len(self.store.list_follow_folders()),200)

    def test_multiple_memberships_counts_and_filters_survive_reopen(self):
        a,b=self.create('Alpha'),self.create('Beta')
        self.store.set_followed(8,True,self.at)
        self.store.set_follow_folders(7,[a.id,b.id]);self.store.set_follow_folders(8,[b.id])
        store=RadarStore(self.store.data_dir)
        self.assertEqual(store.follow_folder_ids(7),[a.id,b.id])
        self.assertEqual([(f.name,f.count) for f in store.list_follow_folders()],[('Alpha',1),('Beta',2)])
        self.assertEqual([r.id for r,_,_ in store.followed_repositories(str(a.id))],[7])
        self.assertEqual(store.followed_repositories('unfiled'),[])
        self.assertEqual(len(store.followed_repositories()),2)

    def test_delete_folder_keeps_follow_and_other_memberships_and_time(self):
        a,b=self.create('Alpha'),self.create('Beta')
        self.store.set_follow_folders(7,[a.id,b.id]);self.store.delete_follow_folder(a.id)
        self.assertTrue(self.store.is_followed(7))
        self.assertEqual(self.store.follow_folder_ids(7),[b.id])
        self.assertEqual(self.store.followed_at(7),self.at)
        self.assertEqual(self.store.list_follow_folders()[0].count,1)

    def test_deleted_identity_never_reused_after_reopen_or_legacy_migration(self):
        old=self.create('Old');self.store.set_follow_folders(7,[old.id])
        # Emulate the previously shipped schema, with no allocator table.
        with closing(sqlite3.connect(self.store.db_path)) as db,db:
            db.execute('DROP TABLE IF EXISTS follow_folder_sequence')
        self.store=RadarStore(self.store.data_dir)
        self.store.delete_follow_folder(old.id)
        self.store=RadarStore(self.store.data_dir)
        new=self.create('Different')
        self.assertGreater(new.id,old.id,'Stale ID must never identify another folder')
        with self.assertRaises(LookupError):self.store.set_follow_folders(7,[old.id])
        with self.assertRaises(LookupError):self.store.rename_follow_folder(old.id,'Wrong')
        with self.assertRaises(LookupError):self.store.delete_follow_folder(old.id)
        self.assertEqual(self.store.follow_folder_ids(7),[])
        self.assertEqual(self.store.list_follow_folders()[0].name,'Different')

    def test_unfollow_cascades_and_refollow_starts_unfiled(self):
        a=self.create('Alpha');self.store.set_follow_folders(7,[a.id])
        self.store.set_followed(7,False,'later');self.store.set_followed(7,True,'again')
        self.assertEqual(self.store.follow_folder_ids(7),[])
        self.assertEqual(self.store.followed_at(7),'again')
        self.assertEqual(self.store.list_follow_folders()[0].count,0)

    def test_invalid_or_missing_ids_do_not_replace_saved_classification_or_autofollow(self):
        a=self.create('Alpha');self.store.set_follow_folders(7,[a.id])
        for ids,error in (([a.id,a.id],ValueError),([a.id,999],LookupError),([True],ValueError),('1',ValueError),([-1],ValueError)):
            with self.subTest(ids=ids),self.assertRaises(error):self.store.set_follow_folders(7,ids)
            self.assertEqual(self.store.follow_folder_ids(7),[a.id])
        with self.assertRaises(LookupError):self.store.set_follow_folders(8,[a.id])
        with self.assertRaises(LookupError):self.store.delete_follow_folder(999)
        with self.assertRaises(LookupError):self.store.rename_follow_folder(999,'Unused')
        with self.assertRaises(LookupError):self.store.followed_repositories('999')
        self.assertFalse(self.store.is_followed(8))

    def test_failed_replace_rolls_back_previous_membership(self):
        a,b=self.create('Alpha'),self.create('Beta');self.store.set_follow_folders(7,[a.id])
        with closing(sqlite3.connect(self.store.db_path)) as db,db:
            db.execute("CREATE TRIGGER block_folder BEFORE INSERT ON follow_folder_items BEGIN SELECT RAISE(ABORT,'write failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.set_follow_folders(7,[b.id])
        self.assertEqual(self.store.follow_folder_ids(7),[a.id])

    def test_concurrent_unfollow_and_classify_cannot_leave_orphan(self):
        a=self.create('Alpha');gate=threading.Barrier(2);errors=[]
        def classify():
            gate.wait()
            try:self.store.set_follow_folders(7,[a.id])
            except LookupError:pass
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=classify);thread.start();gate.wait()
        self.store.set_followed(7,False,'later');thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertFalse(self.store.is_followed(7));self.assertEqual(self.store.list_follow_folders()[0].count,0)
        with closing(sqlite3.connect(self.store.db_path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM follow_folder_items').fetchone()[0],0)
