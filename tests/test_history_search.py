import importlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from contextlib import closing
from pathlib import Path
from github_radar.storage import RadarStore
from github_radar.models import Recommendation, StarSnapshot
from tests.test_storage import repository
from tests.test_readme_content import document


class HistorySearchTests(unittest.TestCase):
    def test_month_index_includes_all_31_days_without_search_pagination(self):
        for day in range(1,32):
            at=f'2026-10-{day:02}T09:00:00+08:00'
            self.store.commit_daily(at[:10],[repository(70)],[],[Recommendation(70,at[:10],'growth',None,day,None,at)])
        rows=self.store.history_month('2026-10')
        self.assertEqual(len(rows),31)
        self.assertEqual(rows[0],('2026-10-01',1))
        self.assertEqual(rows[-1],('2026-10-31',1))
        self.assertEqual(self.store.history_month('2026-02'),[])
        for month in ('0000-01','2026-13','2026-2','2026-01-01',True):
            with self.subTest(month=month),self.assertRaises(ValueError):self.store.history_month(month)

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('github_radar.history_search'),'History filtering not implemented')
        self.module=importlib.import_module('github_radar.history_search')
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=RadarStore(Path(temp.name))
        self.word=self.store.add_keyword('AI')
        self.old='2026-09-26';self.new='2026-09-27'
        for day,stars in ((self.old,1500),(self.new,9000)):
            at=day+'T08:00:00+08:00'
            repos=[replace(repository(7,stars),full_name='Straße/项目',description="literal %_ ' test",topics=('工具',)),
                   replace(repository(8),full_name='other/repo',description='Topic test',topics=('mcp-tools',)),
                   replace(repository(9),full_name='third/repo',description='Missing optional material',topics=())]
            picks=[Recommendation(7,day,'growth',None,40,None,at,rank=3,matched_keyword_ids=(self.word.id,)),
                   Recommendation(8,day,'keyword',self.word.id,None,None,at),
                   Recommendation(9,day,'growth',None,10,None,at,rank=7)]
            self.store.commit_daily(day,repos,[StarSnapshot(r.id,day,r.stars,at) for r in repos],picks)
        self.store.save_readme(document('# README\n\n本地独有内容 Fast SEARCH',8))
        with closing(sqlite3.connect(self.store.db_path)) as db,db:
            db.execute('INSERT INTO ai_explanations VALUES (?,?,?,?,?,?)',
                       (9,0,'auto','hash',json.dumps({'zh':{'summary':'智能体专用解读'},'en':{'summary':'Saved explanation'}}),'now'))

    def find(self,**values):
        return self.store.search_history(self.module.HistoryFilters(**values))

    def test_current_name_description_topics_and_unicode_casefold_are_literal(self):
        for q,ids in [('STRASSE',[7,7]),('项目',[7,7]),("%_ '",[7,7]),('工具',[7,7]),('MCP-TOOLS',[8,8])]:
            with self.subTest(q=q):self.assertEqual([r.repo_id for r in self.find(q=q)],ids)
        self.assertEqual(self.find(q='%does-not-match'),[])

    def test_saved_readme_and_decoded_ai_are_searchable_without_optional_material(self):
        for q,ids in [('本地独有',[8,8]),('fast search',[8,8]),('智能体专用',[9,9]),('saved explanation',[9,9])]:
            with self.subTest(q=q):self.assertEqual([r.repo_id for r in self.find(q=q)],ids)
        self.assertEqual(len(self.find()),6)

    def test_date_source_and_text_use_and_with_inclusive_boundaries(self):
        rows=self.find(q='项目',from_date=self.old,to_date=self.old,source=f'keyword:{self.word.id}')
        self.assertEqual([(r.repo_id,r.local_date,r.rank,r.star_delta) for r in rows],[(7,self.old,3,40)])
        self.assertEqual(self.store.snapshots_on(self.old,[7])[7].stars,1500)
        self.assertEqual(self.find(q='mcp',source='growth'),[])
        self.assertEqual([r.repo_id for r in self.find(source='growth')],[7,9,7,9])

    def test_deleted_keyword_remains_searchable_and_unknown_keyword_is_empty(self):
        self.store.soft_delete_keyword(self.word.id,'now')
        self.assertEqual([r.repo_id for r in self.find(source=f'keyword:{self.word.id}')],[7,8,7,8])
        self.assertEqual(self.find(source='keyword:999'),[])

    def test_original_order_and_snapshots_survive_reopen(self):
        self.assertEqual([(r.local_date,r.repo_id) for r in self.find()],
                         [(self.new,7),(self.new,8),(self.new,9),(self.old,7),(self.old,8),(self.old,9)])
        reopened=RadarStore(self.store.data_dir)
        self.assertEqual(reopened.search_history(self.module.HistoryFilters()),self.find())
        self.assertEqual(reopened.snapshots_on(self.old,[7])[7].stars,1500)

    def test_query_validation_rejects_duplicates_unknowns_bad_dates_and_sources(self):
        for query in ('q=a&q=b','other=x','from=2026-02-30','from=20260901','from=2026-09-28&to=2026-09-27',
                      'source=keyword:0','source=keyword:-1','source=bad','cursor=-1','cursor=1.5','q='+('a'*201)):
            with self.subTest(query=query),self.assertRaises(ValueError):self.module.parse_history_query(query,True)
        with self.assertRaises(ValueError):self.module.parse_history_query('cursor=0',False)

    def test_query_returns_shared_filters_and_nonnegative_cursor(self):
        filters,cursor=self.module.parse_history_query('q=%25_%27&from=2026-09-26&to=2026-09-27&source=keyword:1&cursor=30',True)
        self.assertEqual(filters,self.module.HistoryFilters("%_'",self.old,self.new,'keyword:1'))
        self.assertEqual(cursor,30)
        self.assertEqual(self.module.parse_history_query('',True),(self.module.HistoryFilters(),0))

    def test_invalid_direct_filters_are_rejected_without_mutation(self):
        with self.assertRaises(ValueError):self.find(from_date='wrong')
        self.assertEqual(self.store.history_dates(),[(self.new,3),(self.old,3)])
