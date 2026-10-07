import json
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from github_radar.browser_server import BrowserServer
from github_radar.ai_types import (AIRepositoryInput, CandidateBatch, InsightText,
                                   ProjectExplanation, RelevanceVerdict)
from github_radar.codex_connection import ConnectionState, LoginSession, ModelOption
from github_radar.github_client import GitHubRequestError
from github_radar.models import GrowthCoverage, Recommendation, Repository, StarSnapshot
from github_radar.service import RadarService
from github_radar.storage import RadarStore


class NoNetworkClient:
    def __getattr__(self, name):
        raise AssertionError(f"Reading a saved page must not call GitHub: {name}")


class ReadyConnection:
    def probe(self):
        return ConnectionState(True, "Codex 已连接")

    def list_models(self):
        return (ModelOption("gpt-6-sol", "GPT-6 Sol", True),)

    def begin_login(self):
        return LoginSession("https://chatgpt.com/auth/start")

    def cancel_login(self):
        pass


class BrowserServerTests(unittest.TestCase):
    def test_installer_launch_stops_preparation_before_opening(self):
        from unittest.mock import patch
        order=[]
        def launch(*args):
            order.append('launch')
            raise ValueError('test launch failed')
        with patch.object(self.server,'_stop_preparation',side_effect=lambda:order.append('stop')), patch('github_radar.software_update.launch_installer',side_effect=launch):
            with self.assertRaises(ValueError):self.server._software_ready(None,None)
        self.assertEqual(order,['stop','launch'])

    def test_close_stops_preparation_before_closing_services(self):
        from unittest.mock import patch
        order=[]
        with patch.object(self.server,'_stop_preparation',side_effect=lambda:order.append('stop')), patch.object(self.server.software_updater,'close',side_effect=lambda:order.append('updater')):
            self.server.close()
        self.assertEqual(order[:2],['stop','updater'])

    def test_search_expansion_is_editable_without_ai_and_origin_protected(self):
        rule=self.store.add_keyword('skills',1000)
        path=f'/api/search/keywords/{rule.id}/expansion'
        self.assertEqual(self.get(path,token=False)[0],403)
        self.assertEqual(self.post(path,{'terms':['agent skills']},token=False)[0],403)
        self.assertEqual(self.post(path,{'terms':['agent skills']})[0],200)
        status,_,raw=self.get(path)
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['terms'],['agent skills'])
        self.assertEqual(self.post(path,{'terms':['x']*7})[0],400)
        self.assertEqual(self.post(path,{'terms':['ignore instructions'], 'model':'other'})[0],400)

    def test_classification_script_is_served(self):
        status,kind,data=self.get('/assets/detail_classification.js')
        self.assertEqual(status,200)
        self.assertIn(b'RadarDetailClassification',data)
        self.assertIn('javascript',kind['Content-Type'])

    def test_refresh_and_readme_completion_enqueue_only_selected_ids(self):
        self.save_issue()
        from types import SimpleNamespace
        queued=[]
        self.server.project_pretranslator=SimpleNamespace(enqueue=lambda ids:queued.append(list(ids)),close=lambda:None)
        self.service.refresh=lambda *args:SimpleNamespace(status='ok')
        self.server._refresh_worker()
        self.assertEqual(queued,[[1,2,3,4]])
        from dataclasses import replace
        self.server.readme_service.load=lambda *args:replace(self.server.readme_service.cached(1),document=SimpleNamespace(repo_id=1))
        self.server._fetch_readme(1,False)
        self.assertEqual(queued[-1],[1])

    def test_folder_order_move_unfiled_and_follow_search_are_bounded_and_local(self):
        self.save_issue()
        self.store.set_followed(1,True,'original');self.store.set_followed(2,True,'second')
        a=self.post('/api/folders',{'name':'Alpha'})[1]['folder']['id']
        b=self.post('/api/folders',{'name':'Beta'})[1]['folder']['id']
        self.assertEqual(self.post('/api/folders/order',{'ids':[b,a]},token=False)[0],403)
        self.assertEqual(self.post('/api/folders/order',{'ids':[b,a]})[0],200)
        self.assertEqual([f['id'] for f in json.loads(self.get('/api/folders')[2])['folders']],[b,a])
        self.assertEqual(self.post('/api/folders/order',{'ids':[a]})[0],400)
        self.assertEqual(self.post('/api/following/1/folders',{'action':'move_unfiled','folder_id':a})[0],200)
        self.assertEqual(self.post('/api/following/1/folders',{'action':'move_unfiled','folder_id':b})[0],400)
        self.assertEqual(self.store.follow_folder_ids(1),[a]);self.assertEqual(self.store.followed_at(1),'original')
        found=json.loads(self.get('/api/following?q=PROJECT-1')[2])
        self.assertEqual([c['repo_id'] for c in found['cards']],[1]);self.assertEqual(found['all_count'],2)
        self.assertEqual(json.loads(self.get('/api/following?q=%25_')[2])['cards'],[])
        for query in ('q=a&q=b','q='+('a'*201),'folder=unfiled&q=repo','q=a&bad=1'):
            self.assertEqual(self.get('/api/following?'+query)[0],400)

    def test_calendar_month_index_is_authenticated_validated_and_local(self):
        day=self.save_issue();path='/api/history/calendar?month='+day[:7]
        self.assertEqual(self.get(path,token=False)[0],403)
        status,_,raw=self.get(path)
        self.assertEqual(status,200)
        result=json.loads(raw)
        self.assertEqual(result['month'],day[:7])
        self.assertIn({'date':day,'count':4},result['dates'])
        for query in ('','month=2026-13','month=2026-01&month=2026-02','month=2026-01&q=repo'):
            self.assertEqual(self.get('/api/history/calendar?'+query)[0],400)

    def test_detail_keeps_explicit_saved_source_rank_after_newer_recommendation(self):
        self.save_issue()
        old=self.store.latest_successful_date()
        tomorrow=(date.fromisoformat(old)+timedelta(days=1)).isoformat()
        repository=self.store.repositories_for_ids([1])[1]
        self.store.commit_daily(tomorrow,[repository],[],[
            Recommendation(1,tomorrow,'keyword',None,None,None,tomorrow+'T09:00:00Z',rank=9)])
        before=json.loads(self.get('/api/project/1?date='+old)[2])
        status,_,raw=self.get('/api/project/1?context='+old)
        self.assertEqual(status,200)
        after=json.loads(raw)
        self.assertEqual(after['display_rank'],before['display_rank'])
        self.assertEqual(after['rank_source'],before['rank_source'])
        self.assertEqual(self.get('/api/project/1?context=invalid')[0],400)
        self.assertEqual(self.get('/api/project/1?context=1900-01-01')[0],404)

    def test_manual_ai_receives_historical_source_context_and_invalid_date_is_rejected(self):
        self.save_issue();day=self.store.latest_successful_date();self.server.connection=ReadyConnection()
        calls=[]
        class RecordingAI:
            def explain_project(inner,repo_id,model_id,checked_at,force=False,context_date=None):calls.append((repo_id,context_date))
            def cancel(inner):pass
        self.server.ai_service=RecordingAI()
        path='/api/ai/projects/4/explain'
        self.assertEqual(self.post(path,{'context_date':42})[0],400)
        self.assertEqual(self.post(path,{'context_date':'1900-01-01'})[0],404)
        self.assertEqual(self.post(path,{'context_date':day})[0],202)
        self.wait_until(lambda:bool(calls));self.assertEqual(calls,[(4,day)])

    def test_title_fonts_are_local_complete_and_allowlisted(self):
        import struct
        for name in ('cormorant-garamond.ttf','noto-serif-sc.ttf'):
            status, headers, raw = self.get('/fonts/'+name,token=False)
            self.assertEqual(status,200)
            self.assertEqual(headers['Content-Type'],'font/ttf')
            self.assertIn("font-src 'self'",headers['Content-Security-Policy'])
            self.assertEqual(raw[:4],b'\x00\x01\x00\x00')
            tables = struct.unpack_from('>H',raw,4)[0]
            for offset in range(12,12+16*tables,16):
                tag,checksum,start,length = struct.unpack_from('>4sIII',raw,offset)
                self.assertLessEqual(start+length,len(raw),tag)
        for path in ('/fonts/../storage.py','/fonts/unknown.ttf','/fonts/%2e%2e/storage.py'):
            self.assertEqual(self.get(path,token=False)[0],404)

    def test_saved_keyword_candidate_rank_in_home_history_following_and_detail(self):
        today = date.today().isoformat()
        rule = self.store.add_keyword('local ai')
        repo = Repository(80,'owner/ranked','https://github.com/owner/ranked','local ai',(),None,8000,False)
        at = today+'T09:00:00+08:00'
        self.store.commit_daily(today,[repo],[StarSnapshot(80,today,8000,at)],
            [Recommendation(80,today,'keyword',rule.id,None,None,at,rank=8)])
        self.store.set_followed(80,True,at)
        issue = json.loads(self.get('/api/issue')[2])
        cards = [issue['keyword_groups'][0]['cards'][0],
                 json.loads(self.get('/api/history/'+today+'?q=ranked')[2])['sections'][1]['cards'][0],
                 json.loads(self.get('/api/following')[2])['cards'][0],
                 json.loads(self.get('/api/project/80?date='+today)[2])]
        self.assertEqual([(c.get('display_rank'),c.get('rank_source'),c['growth_rank']) for c in cards],
                         [(8,'keyword',None)]*4)

    def test_old_keyword_group_rank_is_fixed_before_history_search(self):
        today = date.today().isoformat()
        rule = self.store.add_keyword('local ai')
        at = today+'T09:00:00+08:00'
        repos = [Repository(i,f'owner/old-{i}',f'https://github.com/owner/old-{i}','local ai',(),None,8000,False)
                 for i in (81,82)]
        self.store.commit_daily(today,repos,[],[
            Recommendation(r.id,today,'keyword',rule.id,None,None,at) for r in repos])
        payload = json.loads(self.get('/api/history/'+today+'?q=old-82')[2])
        card = payload['sections'][1]['cards'][0]
        self.assertEqual(card.get('display_rank'),2)
        self.assertIsNone(card['growth_rank'])
        detail = json.loads(self.get('/api/project/82?date='+today)[2])
        self.assertEqual(detail.get('display_rank'),2)

    def test_unauthorized_missing_body_returns_bounded_403_without_writes(self):
        from http.client import HTTPConnection
        connection=HTTPConnection(urlsplit(self.base).netloc,timeout=1)
        try:
            connection.putrequest('POST','/api/folders')
            connection.putheader('Origin',self.base)
            connection.putheader('Content-Length','8192')
            connection.endheaders()
            started=time.monotonic()
            self.assertEqual(connection.getresponse().status,403)
            self.assertLess(time.monotonic()-started,.5)
            self.assertEqual(self.store.list_follow_folders(),[])
        finally:connection.close()
    def test_history_filters_page_dates_and_preserve_real_rank(self):
        self.save_issue()
        start=date(2026,8,1)
        repo=Repository(7,'test/history','https://github.com/test/history','Needle',(),None,500,False)
        for n in range(35):
            day=(start+timedelta(days=n)).isoformat();at=day+'T09:00:00+08:00'
            self.store.commit_daily(day,[repo],[StarSnapshot(7,day,100+n,at)],
                [Recommendation(7,day,'growth',None,12,None,at,metric_basis='github_daily_new',metric_date=day,rank=9)])
        data=json.loads(self.get('/api/history?q=needle&source=growth')[2])
        self.assertEqual(len(data['dates']),30);self.assertEqual(data['matched_total'],35)
        self.assertEqual(data['next_cursor'],30)
        next_page=json.loads(self.get('/api/history?q=needle&source=growth&cursor=30')[2])
        self.assertEqual(len(next_page['dates']),5);self.assertIsNone(next_page['next_cursor'])
        saved=json.loads(self.get('/api/history/2026-08-01?q=needle&source=growth')[2])
        card=saved['sections'][0]['cards'][0]
        self.assertEqual((card['growth_rank'],card['stars'],card['history_date']),(9,'★ 100','2026-08-01'))
        self.assertEqual(json.loads(self.get('/api/history/2026-08-01?q=missing')[2])['sections'][0]['cards'],[])

    def test_history_filter_does_not_renumber_legacy_rank_without_explicit_rank(self):
        today=self.save_issue()
        saved=json.loads(self.get('/api/history/'+today+'?q=project-2&source=growth')[2])
        self.assertEqual([(c['repo_id'],c['growth_rank']) for c in saved['sections'][0]['cards']],[(2,2)])

    def test_history_invalid_queries_and_deleted_sources_are_explicit(self):
        today=self.save_issue();rule=self.store.all_keywords()[0]
        self.store.soft_delete_keyword(rule.id,'now')
        for suffix in ('?q=a&q=b','?bad=x','?from=2026-02-30','?cursor=-1','?source=bad'):
            self.assertEqual(self.get('/api/history'+suffix)[0],400,suffix)
        self.assertEqual(self.get('/api/history/'+today+'?cursor=0')[0],400)
        data=json.loads(self.get('/api/history?source=keyword:'+str(rule.id))[2])
        self.assertEqual(data['matched_total'],1)
        self.assertIn('keyword:'+str(rule.id),[s['value'] for s in data['sources']])
        self.assertEqual(json.loads(self.get('/api/history?source=keyword:999')[2])['matched_total'],0)

    def test_folder_api_multi_membership_delete_preserves_follow_and_bounds(self):
        self.save_issue();self.post('/api/following/1',{'followed':True})
        self.assertEqual(self.get('/api/folders',token=False)[0],403)
        self.assertEqual(self.post('/api/folders',{'name':'A'},origin=False)[0],403)
        status,a=self.post('/api/folders',{'name':'Alpha'});self.assertEqual(status,200)
        _,b=self.post('/api/folders',{'name':'Beta'})
        aid,bid=a['folder']['id'],b['folder']['id']
        self.assertEqual(self.post('/api/following/1/folders',{'ids':[aid,bid]})[0],200)
        self.assertEqual(json.loads(self.get('/api/following/1/folders')[2])['ids'],[aid,bid])
        data=json.loads(self.get('/api/following?folder='+str(aid))[2])
        self.assertEqual([c['repo_id'] for c in data['cards']],[1]);self.assertEqual(len(data['cards'][0]['folders']),2)
        self.assertEqual(self.post('/api/folders/'+str(aid),{'action':'rename','name':' New '})[0],200)
        self.assertEqual(self.post('/api/folders/'+str(aid),{'action':'delete'})[0],200)
        self.assertTrue(self.store.is_followed(1));self.assertEqual(self.store.follow_folder_ids(1),[bid])
        self.assertEqual(self.post('/api/following/1/folders',{'ids':[999]})[0],404)
        self.assertEqual(self.post('/api/following/1/folders',{'ids':[bid,bid]})[0],400)
        self.assertEqual(self.post('/api/following/2/folders',{'ids':[bid]})[0],404)
        self.assertFalse(self.store.is_followed(2))
        self.assertEqual(self.post('/api/folders',{'name':'x'*9000})[0],400)
        self.assertEqual(self.get('/api/following?folder=999')[0],404)
        self.assertEqual(self.get('/api/following?folder=all&folder=all')[0],400)

    def test_folder_write_failure_is_not_false_success(self):
        from unittest.mock import patch
        import sqlite3
        with patch.object(self.store,'create_follow_folder',side_effect=sqlite3.OperationalError('disk failure')):
            status,result=self.post('/api/folders',{'name':'Keep input'})
        self.assertEqual(status,503);self.assertIn('error',result)
        self.assertEqual(self.store.list_follow_folders(),[])

    def test_translation_api_auth_limits_and_invalid_requests(self):
        from github_radar.translation_types import TranslationPart, TranslationResult
        payload = {'target':'zh','items':[{'id':'a','parts':[{'kind':'text','text':'Useful project'}]}]}
        self.assertEqual(self.post('/api/translation',payload,token=False)[0],403)
        self.assertEqual(self.post('/api/translation',payload,origin=False)[0],403)
        self.assertEqual(self.post('/api/translation',{'target':True,'items':[]})[0],400)
        self.assertEqual(self.post('/api/translation',{'target':'zh','items':[
            {'id':'a','parts':[{'kind':'text','text':'x'*16385}]}]})[0],400)
        # Send only the oversized length: rejection must precede reading a body.
        from http.client import HTTPConnection
        connection=HTTPConnection(urlsplit(self.base).netloc, timeout=3)
        try:
            connection.putrequest('POST','/api/translation')
            connection.putheader('Origin',self.base)
            connection.putheader('X-Radar-Token',self.token)
            connection.putheader('Content-Length','524289')
            connection.endheaders()
            self.assertEqual(connection.getresponse().status,400)
        finally: connection.close()
        self.assertEqual(self.post('/api/preferences',{'extra':'x'*9000})[0],400)
        def run(target, items, cancel):
            return [TranslationResult(i.id,'translated',(TranslationPart('text','译文'),)) for i in items]
        self.server.translation_service.runner=run
        # Greater than the existing 8 KiB boundary is allowed only here.
        payload['items'][0]['parts'][0]['text']='Useful project '*700
        status,result=self.post('/api/translation',payload)
        self.assertEqual(status,202)
        path='/api/translation/'+result['job_id']
        self.assertEqual(self.get(path,token=False)[0],403)
        self.wait_until(lambda:json.loads(self.get(path)[2])['status']=='ready')
        self.assertEqual(self.get('/api/translation/unknown')[0],404)

    def test_translation_cancel_is_authenticated_and_releases_running_job(self):
        started=threading.Event()
        def run(target,items,cancel):
            started.set();cancel.wait(2);return []
        self.server.translation_service.runner=run
        status,job=self.post('/api/translation',{'target':'zh','items':[
            {'id':'old','parts':[{'kind':'text','text':'Old page paragraph'}]}]})
        self.assertEqual(status,202);self.assertTrue(started.wait(1))
        path='/api/translation/'+job['job_id']+'/cancel'
        self.assertEqual(self.post(path,token=False)[0],403)
        self.assertEqual(self.post(path,{'unexpected':True})[0],400)
        status,result=self.post(path)
        self.assertEqual(status,200);self.assertEqual(result['status'],'cancelled')
        self.assertEqual(self.post('/api/translation/'+'a'*32+'/cancel')[0],404)

    def test_forest_background_is_served_as_local_image_only(self):
        status, headers, raw = self.get('/assets/forest-mist.png', token=False)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(self.get('/assets/../storage.py', token=False)[0], 404)

    def test_motion_preference_saved_independently_of_font_and_language(self):
        self.store.save_display_preferences('en', 1.37)
        self.assertEqual(self.post('/api/preferences', {'motion_preference':'on'})[0], 200)
        self.assertEqual(json.loads(self.get('/api/preferences')[2])['motion_preference'], 'on')
        self.assertEqual(self.store.load_display_preferences(), ('en',1.37))
        self.assertEqual(self.post('/api/preferences', {'motion_preference':'invalid'})[0], 400)
        self.assertEqual(RadarStore(self.store.data_dir).load_motion_preference(), 'on')
        self.assertEqual(self.post('/api/preferences', {'motion_preference':'off'}, token=False)[0], 403)
    def test_readme_api_is_local_authenticated_and_quit_cancels_blocked_read(self):
        from github_radar.readme_types import ReadmeFetch
        self.save_issue()
        started, release = threading.Event(), threading.Event()
        repository = self.store.repositories_for_ids([1])[1]
        class ReadmeClient:
            def get_repository_by_id(inner, repo_id):
                started.set(); release.wait(3)
                # A cancelled fake network call may outlive server shutdown.
                # It must not reopen a fixture database during its cleanup.
                return repository
            def fetch_readme_by_id(inner, repo_id, etag=None):
                return ReadmeFetch('# Repo\n\nAuthor introduction.', None, False, False)
        self.server.readme_service.client = ReadmeClient()
        try:
            self.assertEqual(self.get('/api/readme/1', token=False)[0], 403)
            self.assertEqual(self.post('/api/readme/1', token=False)[0], 403)
            self.assertEqual(self.post('/api/readme/1', origin=False)[0], 403)
            self.assertEqual(json.loads(self.get('/api/readme/1')[2])['status'], 'empty')
            self.assertFalse(started.is_set(), 'GET must not start requests')
            self.assertEqual(self.post('/api/readme/1', {'refresh':False})[0], 202)
            self.assertTrue(started.wait(1))
            self.assertEqual(json.loads(self.get('/api/readme/1')[2])['status'], 'fetching')
            self.assertEqual(self.post('/api/readme/2')[0], 409)
            self.assertEqual(self.post('/api/refresh')[0], 409)
            self.assertEqual(self.post('/api/quit')[0], 202)
            self.assertTrue(self.server.wait_closed(1.5), 'Exit must not wait for blocked README')
        finally:
            release.set()
        self.assertTrue(self.server.wait_closed(3))
        self.assertIsNone(self.store.load_readme(1))

    def test_continuous_font_scale_api_preserves_settings_on_bad_input(self):
        self.assertEqual(self.post('/api/preferences', {'language':'en', 'font_scale':1.37})[0],200)
        self.assertEqual(json.loads(self.get('/api/preferences')[2])['font_scale'],1.37)
        for invalid in (True, '1.4', .7, 2.1, float('nan'), float('inf'), 10**1000):
            self.assertEqual(self.post('/api/preferences', {'language':'zh', 'font_scale':invalid})[0],400)
        self.assertEqual(self.store.load_display_preferences(), ('en',1.37))
        self.assertEqual(self.post('/api/preferences', {'language':'zh','font_scale':1},token=False)[0],403)

    def test_missing_token_cannot_manage_github_account(self):
        from github_radar.github_account import GitHubAccount
        # Explicitly exercise an unconfigured publisher; release now has a real ID.
        self.server.github_account=GitHubAccount(self.store.data_dir,client_id=None)
        self.assertEqual(self.post('/api/github/connect',token=False)[0],403)
        code,_,raw=self.get('/api/github/status')
        self.assertEqual(code,200)
        self.assertEqual(json.loads(raw)['state'],'unavailable')
        self.assertEqual(self.post('/api/github/connect')[0],400)

    def test_source_rank_is_not_growth_rank(self):
        from github_radar.discovery_types import SourceEvidence
        from github_radar.cross_check import compare_source
        from github_radar.models import StarDay
        repo=Repository(7,'a/repo','https://github.com/a/repo','MCP',(),None,1903,False)
        rec=Recommendation(7,'2026-09-30','growth',None,10,None,'2026-09-30T10:00:00Z',
                           'github_daily_new','2026-09-28',8,'new')
        self.store.commit_daily('2026-09-30',[repo],[],[rec])
        evidence=SourceEvidence('trendshift_daily','a/repo','https://trendshift.io/',
            '2026-09-30T10:00:00Z','daily','2026-09-28','trendshift_daily',2,'1.9k','10',repo_id=7)
        self.store.save_source_evidence(evidence)
        self.store.save_cross_checks('2026-09-30',list(compare_source(evidence,repo,
                                    StarDay('2026-09-28',10),'2026-09-30T10:00:00Z','UTC')))
        code,_,raw=self.get('/api/repository/7/sources?date=2026-09-30')
        self.assertEqual(code,200);payload=json.loads(raw)
        self.assertEqual(payload['growth_rank'],8)
        self.assertEqual(payload['sources'][0]['source_rank'],2)
        self.assertNotIn('access_token',raw.decode())
        self.assertEqual(self.get('/api/repository/7/sources?date=2026-09-30',token=False)[0],403)
    def test_classify_from_unfollowed_detail_and_drag_between_custom_folders(self):
        self.save_issue()
        a=self.store.create_follow_folder('A','2026-10-04T12:00:00Z')
        b=self.store.create_follow_folder('B','2026-10-04T12:00:00Z')
        status,result=self.post('/api/following/1/classify',{'ids':[a.id],'create_name':'New'})
        self.assertEqual(status,200);self.assertTrue(result['followed'])
        status,result=self.post('/api/following/1/folders',{'action':'move','source':str(a.id),'target':str(b.id)})
        self.assertEqual(status,200);self.assertIn(b.id,result['ids']);self.assertNotIn(a.id,result['ids'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RadarStore(Path(self.temp.name) / "UserData")
        self.service = RadarService(NoNetworkClient(), self.store)
        self.server = BrowserServer(self.service, self.store)
        self.server.start()
        self.addCleanup(self.server.close)
        parts = urlsplit(self.server.url)
        self.base = f"{parts.scheme}://{parts.netloc}"
        self.token = parse_qs(parts.fragment)["token"][0]

    def get(self, path, *, token=True, host=None):
        headers = {"X-Radar-Token": self.token} if token else {}
        if host is not None:
            headers["Host"] = host
        request = Request(self.base + path, headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def post(self, path, payload=None, *, token=True, origin=True, host=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Radar-Token"] = self.token
        if origin:
            headers["Origin"] = self.base if origin is True else origin
        if host is not None:
            headers["Host"] = host
        request = Request(self.base + path, data=json.dumps(payload or {}).encode(), headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            body = error.read()
            return error.code, json.loads(body) if body.startswith(b"{") else {}

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("Timed out waiting for browser server state")

    def save_issue(self):
        today = date.today().isoformat()
        keyword = self.store.add_keyword("local ai")
        repos = [
            Repository(i, f"owner/project-{i}", f"https://github.com/owner/project-{i}",
                       "<script>alert(1)</script> local ai", ("local-ai",), "Python", 1400 + i, False)
            for i in range(1, 5)
        ]
        observed = f"{today}T09:00:00+08:00"
        records = [
            Recommendation(1, today, "growth", None, 24, None, observed, "github_daily_new", today),
            Recommendation(2, today, "growth", None, 8, f"{today}T08:00:00+08:00", observed, "local_snapshot"),
            Recommendation(3, today, "growth", None, None, None, observed),
            Recommendation(4, today, "keyword", keyword.id, None, None, observed),
        ]
        self.store.commit_daily(
            today, repos, [StarSnapshot(item.id, today, item.stars, observed) for item in repos],
            records, completed_at=observed,
            growth_coverage=GrowthCoverage(210, 25, ("GitHub Search", "Trending"), today,
                                           "github_daily_new"),
        )
        return today

    def test_saved_issue_has_true_metric_labels_and_coverage_without_network(self):
        today = self.save_issue()
        status, headers, body = self.get("/api/issue")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        issue = json.loads(body)
        self.assertEqual(issue["local_date"], today)
        self.assertEqual(issue["status"], "cached")
        self.assertEqual(issue["coverage"]["candidate_count"], 210)
        self.assertEqual(issue["coverage"]["scored_count"], 25)
        growth = issue["sections"][0]["cards"]
        self.assertEqual([card["growth"] for card in growth],
                         ["新增 24 Star", "+8 Star", "正在建立增长记录"])
        self.assertIn("GitHub 统计日", growth[0]["observation"])
        self.assertIn("观察区间", growth[1]["observation"])
        self.assertEqual([card["growth_rank"] for card in growth], [1, 2, None])
        self.assertEqual(growth[0]["matched_keywords"], ["local ai"])
        self.assertEqual(issue["sections"][1]["cards"][0]["matched_keywords"], [])
        self.assertEqual([card["repo_id"] for card in issue["sections"][1]["cards"]], [4])
        self.assertEqual(issue["sections"][1]["cards"][0]["source"], "关键词：local ai")

    def test_english_saved_issue_translates_status_but_preserves_github_description(self):
        self.save_issue()
        self.store.save_preferences("en", "large")
        issue = json.loads(self.get("/api/issue")[2])
        self.assertEqual(issue["message"], "Showing today's saved results")
        growth = issue["sections"][0]["cards"]
        self.assertEqual(growth[0]["source"], "Recent Star growth")
        self.assertEqual(growth[0]["growth"], "+24 new Stars")
        self.assertEqual(growth[0]["description"], "<script>alert(1)</script> local ai")
        detail = json.loads(self.get("/api/project/1")[2])
        self.assertEqual(detail["insight"]["relevance"], "Basic match")
        self.assertEqual(detail["description"], "<script>alert(1)</script> local ai")
        self.assertEqual(issue["keywords"][0]["term"], "local ai")

    def test_followed_project_survives_ai_removal_of_last_recommendation(self):
        today = self.save_issue()
        rule = self.store.list_keywords()[0]
        self.assertEqual(self.post("/api/following/4", {"followed": True})[0], 200)
        repo = self.store.repositories_for_ids([4])[4]
        text = InsightText("Kept AI summary", "Purpose", "Scenario", "Users", ("Feature",))
        self.store.save_explanation(4, rule.id, "gpt-6-luna", "follow-source",
                                    ProjectExplanation(text, text, "relevant", (), False))
        batch = CandidateBatch((AIRepositoryInput(repo, None, True),), 1, 1)
        self.store.commit_ai_batch(today, rule.id, None, batch,
                                   (RelevanceVerdict(4, "irrelevant", "Does not match"),),
                                   today + "T10:00:00+08:00")
        self.assertIsNone(self.store.latest_recommendation_for_repo(4))
        cards = json.loads(self.get("/api/following")[2])["cards"]
        self.assertEqual([card["repo_id"] for card in cards], [4])
        self.assertEqual(cards[0]["stars"], "★ 1,404")
        detail = json.loads(self.get("/api/project/4")[2])
        self.assertEqual(detail["title"], repo.full_name)
        self.assertTrue(detail["followed"])
        self.assertEqual(detail["stars"], "★ 1,404")
        self.assertEqual(detail["source"], "我的关注")
        self.assertEqual(detail["ai_explanation"]["zh"]["summary"], "Kept AI summary")

    def test_english_network_error_and_missing_description_are_localized(self):
        today = self.save_issue()
        self.store.save_preferences("en", "normal")
        at = today + "T10:00:00+08:00"
        empty = Repository(9, "owner/no-description", "https://github.com/owner/no-description",
                           "", (), None, 1500, False)
        self.store.commit_daily(today, [empty], [StarSnapshot(9, today, 1500, at)],
                                [Recommendation(9, today, "growth", None, 1, None, at)])
        detail = json.loads(self.get("/api/project/9")[2])
        self.assertEqual(detail["insight"]["summary"], "No GitHub description provided")

        class OfflineClient:
            search_remaining = 10

            def search(self, query, **kwargs):
                raise GitHubRequestError("连接 GitHub 失败，请检查网络")

            def get_repository(self, full_name):
                raise GitHubRequestError("连接 GitHub 失败，请检查网络")

        self.server.service = RadarService(OfflineClient(), self.store)
        self.assertEqual(self.post("/api/refresh")[0], 202)
        self.wait_until(lambda: not json.loads(self.get("/api/issue")[2])["busy"])
        issue = json.loads(self.get("/api/issue")[2])
        self.assertNotIn("连接 GitHub 失败", issue["message"])
        self.assertIn("Cannot connect to GitHub", issue["message"])

    def test_history_api_uses_dated_snapshot_and_rank(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        at = yesterday + "T09:00:00+08:00"
        older = Repository(1, "owner/project-1", "https://github.com/owner/project-1",
                           "Old project", (), "Python", 900, False)
        self.store.commit_daily(yesterday, [older], [StarSnapshot(1, yesterday, 900, at)],
                                [Recommendation(1, yesterday, "growth", None, 10,
                                                None, at, "github_daily_new", yesterday)])
        today = self.save_issue()
        text = InsightText("Saved summary", "Purpose", "Scenario", "Users", ("Feature",))
        self.store.save_explanation(1, None, "gpt-6-luna", "history-source",
                                    ProjectExplanation(text, text, "relevant", ("GitHub",), False))
        dates = json.loads(self.get("/api/history")[2])["dates"]
        self.assertEqual([(row["date"], row["count"]) for row in dates],
                         [(today, 4), (yesterday, 1)])
        saved = json.loads(self.get(f"/api/history/{yesterday}")[2])
        card = saved["sections"][0]["cards"][0]
        self.assertEqual(card["stars"], "★ 900")
        self.assertEqual(card["growth_rank"], 1)
        detail = json.loads(self.get(f"/api/project/1?date={yesterday}")[2])
        self.assertEqual(detail["stars"], "★ 900")
        self.assertEqual(detail["growth_rank"], 1)
        self.assertEqual(detail["history_date"], yesterday)
        self.assertEqual(detail["ai_explanation"]["zh"]["summary"], "Saved summary")
        current = json.loads(self.get(f"/api/project/1?date={today}")[2])
        self.assertEqual(current["stars"], "★ 1,401")
        self.assertEqual(self.get("/api/history/2026-02-30")[0], 400)
        self.assertEqual(self.get("/api/project/1?date=2026-02-30")[0], 400)
        self.assertEqual(self.get("/api/history", token=False)[0], 403)
        self.assertEqual(self.get("/api/history", host="evil.test")[0], 403)

    def test_history_missing_snapshot_is_explicit_and_following_persists(self):
        today = date.today().isoformat()
        at = today + "T09:00:00+08:00"
        self.store.commit_daily(today, [Repository(7, "owner/old", "https://github.com/owner/old",
                                                  "Old", (), None, 1000, False)], [],
                                [Recommendation(7, today, "growth", None, 5, None, at)])
        card = json.loads(self.get(f"/api/history/{today}")[2])["sections"][0]["cards"][0]
        self.assertEqual(card["stars"], "当时 Star 未记录")
        self.assertEqual(json.loads(self.get(f"/api/project/7?date={today}")[2])["stars"],
                         "当时 Star 未记录")
        self.assertEqual(self.post("/api/following/7", {"followed": "true"})[0], 400)
        self.assertEqual(self.post("/api/following/999", {"followed": True})[0], 404)
        self.assertEqual(self.post("/api/following/7", {"followed": True})[0], 200)
        following = json.loads(self.get("/api/following")[2])["cards"]
        self.assertEqual([item["repo_id"] for item in following], [7])
        self.assertIn("saved_at", following[0])
        self.assertTrue(json.loads(self.get("/api/project/7")[2])["followed"])
        self.assertEqual(self.post("/api/following/7", {"followed": False})[0], 200)
        self.assertEqual(json.loads(self.get("/api/following")[2])["cards"], [])
        self.assertEqual(self.post("/api/following/7", {"followed": True}, token=False)[0], 403)

    def test_followed_detail_shows_latest_local_save_time(self):
        today = self.save_issue()
        self.assertEqual(self.post("/api/following/1", {"followed": True})[0], 200)
        detail = json.loads(self.get("/api/project/1")[2])
        self.assertTrue(detail["followed"])
        self.assertEqual(detail["saved_at"], f"{today}T09:00:00+08:00")

    def test_following_keyword_card_keeps_its_saved_source(self):
        self.save_issue()
        self.assertEqual(self.post("/api/following/4", {"followed": True})[0], 200)
        cards = json.loads(self.get("/api/following")[2])["cards"]
        self.assertEqual(cards[0]["source"], "关键词：local ai")

    def test_preferences_api_validates_and_persists(self):
        self.assertEqual(json.loads(self.get("/api/preferences")[2]),
                         {"language": "zh", "font_size": "normal", "font_scale": 1.0, "motion_preference": "system"})
        self.assertEqual(self.post("/api/preferences", {"language": "en",
                                                        "font_size": "large"})[0], 200)
        self.assertEqual(json.loads(self.get("/api/preferences")[2]),
                         {"language": "en", "font_size": "large", "font_scale": 1.125, "motion_preference": "system"})
        self.assertEqual(self.post("/api/preferences", {"language": "es",
                                                        "font_size": "small"})[0], 400)
        self.assertEqual(self.post("/api/preferences", {"language": "zh",
                                                        "font_size": "giant"})[0], 400)
        self.assertEqual(self.post("/api/preferences", {"language": "zh",
                                                        "font_size": "small"}, token=False)[0], 403)
        self.assertEqual(json.loads(self.get("/api/preferences")[2]),
                         {"language": "en", "font_size": "large", "font_scale": 1.125, "motion_preference": "system"})

    def test_keyword_settings_and_delete_hide_home_but_keep_history(self):
        today = self.save_issue()
        rule = self.store.list_keywords()[0]
        settings = json.loads(self.get("/api/settings")[2])
        self.assertEqual(settings["keywords"][0]["term"], "local ai")
        self.assertEqual(self.post(f"/api/keywords/{rule.id}/settings",
                                   {"enabled": False})[1]["keyword"]["enabled"], False)
        self.assertEqual(self.post(f"/api/keywords/{rule.id}/settings",
                                   {"min_stars": 2500})[1]["keyword"]["min_stars"], 2500)
        self.assertEqual(self.post(f"/api/keywords/{rule.id}/settings",
                                   {"min_stars": -1})[0], 400)
        self.assertEqual(self.post(f"/api/keywords/{rule.id}/settings",
                                   {"enabled": "false"})[0], 400)
        self.assertEqual(self.post(f"/api/keywords/{rule.id}/settings",
                                   {"enabled": True, "min_stars": 1000})[0], 400)
        self.assertEqual(self.post("/api/keywords/999/settings", {"enabled": True})[0], 404)
        self.assertEqual(self.post(f"/api/keywords/{rule.id}/delete")[0], 200)
        self.assertEqual(json.loads(self.get("/api/settings")[2])["keywords"], [])
        issue = json.loads(self.get("/api/issue")[2])
        self.assertEqual(issue["keyword_groups"], [])
        self.assertEqual(issue["sections"][1]["cards"], [])
        saved = json.loads(self.get(f"/api/history/{today}")[2])
        self.assertEqual(saved["keyword_groups"][0]["term"], "local ai")
        self.assertEqual(saved["keyword_groups"][0]["cards"][0]["source"],
                         "关键词：local ai")
        self.assertEqual(self.store.seen_repo_ids(), {1, 2, 3, 4})
        self.assertEqual(self.post(f"/api/keywords/{rule.id}/settings",
                                   {"enabled": True})[0], 404)
        self.server.connection = ReadyConnection()
        self.assertEqual(self.post("/api/ai/projects/4/explain")[0], 409)
        self.assertEqual(self.get("/api/settings", token=False)[0], 403)
        self.assertEqual(self.post(f"/api/keywords/{rule.id}/delete", token=False)[0], 403)

        class QuickService:
            def load_latest(inner, current_day):
                return self.service.load_latest(current_day)

            def refresh(inner, current_day, observed_at):
                return inner.load_latest(current_day)

        self.server.service = QuickService()
        status, restored = self.post("/api/keywords",
                                     {"term": "LOCAL AI", "min_stars": 9999})
        self.assertEqual(status, 202)
        self.assertEqual(restored["keyword"]["id"], rule.id)
        self.assertEqual(restored["keyword"]["min_stars"], 2500)
        self.assertEqual(json.loads(self.get("/api/issue")[2])["keyword_groups"][0]["term"],
                         "local ai")

    def test_detail_uses_basic_interpretation_and_unknown_project_is_404(self):
        self.save_issue()
        status, _, body = self.get("/api/project/4")
        self.assertEqual(status, 200)
        detail = json.loads(body)
        self.assertEqual(detail["title"], "owner/project-4")
        self.assertEqual(detail["insight"]["provider"], "basic")
        self.assertFalse(detail["insight"]["ai_enabled"])
        self.assertEqual(detail["insight"]["relevance"], "基础匹配")
        self.assertIsNone(detail["growth_rank"])
        self.assertEqual(detail["matched_keywords"], [])
        growth_detail = json.loads(self.get("/api/project/1")[2])
        self.assertEqual(growth_detail["growth_rank"], 1)
        self.assertEqual(growth_detail["matched_keywords"], ["local ai"])
        self.assertEqual(growth_detail["insight"]["relevance"], "基础匹配")
        self.assertEqual(self.get("/api/project/999")[0], 404)
        self.assertEqual(self.get("/api/project/not-a-number")[0], 404)

    def test_growth_card_reports_multiple_basic_keyword_matches_without_ai_call(self):
        self.save_issue()
        self.store.add_keyword("script")
        issue = json.loads(self.get("/api/issue")[2])
        growth = issue["sections"][0]["cards"][0]
        self.assertEqual(growth["matched_keywords"], ["local ai", "script"])
        self.assertEqual(growth["growth_rank"], 1)
        detail = json.loads(self.get("/api/project/1")[2])
        self.assertEqual(detail["matched_keywords"], ["local ai", "script"])

    def test_api_rejects_missing_token_and_wrong_host_but_html_shell_has_no_data(self):
        self.save_issue()
        self.assertEqual(self.get("/api/issue", token=False)[0], 403)
        self.assertEqual(self.get("/api/health", host="evil.test")[0], 403)
        status, headers, body = self.get("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertNotIn(b"owner/project-1", body)
        self.assertEqual(self.get("/project/1", token=False)[0], 200)

    def test_post_rejects_missing_token_foreign_origin_and_wrong_host(self):
        self.assertEqual(self.post("/api/refresh", token=False)[0], 403)
        self.assertEqual(self.post("/api/refresh", origin="https://example.com")[0], 403)
        self.assertEqual(self.post("/api/refresh", origin=False)[0], 403)
        self.assertEqual(self.post("/api/refresh", host="evil.test")[0], 403)

    def test_repeated_refresh_runs_once_and_keeps_saved_cards_during_update(self):
        self.save_issue()

        class ControlledService:
            def __init__(inner):
                inner.started = threading.Event()
                inner.release = threading.Event()
                inner.calls = 0

            def load_latest(inner, today):
                return self.service.load_latest(today)

            def refresh(inner, today, observed_at):
                inner.calls += 1
                inner.started.set()
                inner.release.wait(3)
                return inner.load_latest(today)

        controlled = ControlledService()
        self.server.service = controlled
        self.addCleanup(controlled.release.set)
        self.assertEqual(self.post("/api/refresh")[0], 202)
        self.assertTrue(controlled.started.wait(2))
        self.assertEqual(self.post("/api/refresh")[1]["already_running"], True)
        issue = json.loads(self.get("/api/issue")[2])
        self.assertEqual(issue["status"], "updating")
        self.assertTrue(issue["busy"])
        self.assertEqual(issue["sections"][0]["cards"][0]["title"], "owner/project-1")
        controlled.release.set()
        self.wait_until(lambda: not json.loads(self.get("/api/issue")[2])["busy"])
        self.assertEqual(controlled.calls, 1)

    def test_manual_refresh_does_not_write_while_another_process_holds_update_lock(self):
        from github_radar.update_lock import update_lock

        class CountingService:
            calls = 0

            def load_latest(inner, today):
                return self.service.load_latest(today)

            def refresh(inner, today, observed_at):
                inner.calls += 1
                return inner.load_latest(today)

        controlled = CountingService()
        self.server.service = controlled
        with update_lock(self.store.data_dir):
            self.assertEqual(self.post("/api/refresh")[0], 202)
            self.wait_until(lambda: not json.loads(self.get("/api/issue")[2])["busy"])
        self.assertEqual(controlled.calls, 0)
        self.assertIn("已有任务", json.loads(self.get("/api/issue")[2])["message"])

    def test_private_scheduled_endpoint_updates_once_without_ai(self):
        from datetime import datetime
        from types import SimpleNamespace

        now = datetime.now().astimezone()
        self.store.save_auto_update_settings(True, "00:00")
        calls = []

        class QuickService:
            def load_latest(inner, today):
                return self.service.load_latest(today)

            def refresh(inner, today, observed_at):
                calls.append(today)
                self.store.commit_daily(today, [], [], [], completed_at=observed_at)
                return SimpleNamespace(status="ok", message="更新完成")

        self.server.service = QuickService()
        self.assertEqual(self.post("/api/scheduled-refresh", token=False)[0], 403)
        self.assertEqual(self.post("/api/scheduled-refresh")[0], 202)
        self.wait_until(lambda: self.store.auto_attempts(now.date().isoformat()).status == "success")
        # A committed result precedes worker cleanup; the next request must wait for both.
        self.server._worker.join(3)
        self.assertFalse(self.server._worker.is_alive())
        self.assertEqual(self.post("/api/scheduled-refresh")[0], 200)
        self.assertEqual(calls, [now.date().isoformat()])

    def test_open_app_catches_up_only_after_daily_time_for_current_day(self):
        from datetime import datetime
        from types import SimpleNamespace

        self.assertTrue(hasattr(self.server, "start_due_update"))
        calls = []

        class QuickService:
            def load_latest(inner, today):
                return self.service.load_latest(today)

            def refresh(inner, today, observed_at):
                calls.append(today)
                self.store.commit_daily(today, [], [], [], completed_at=observed_at)
                return SimpleNamespace(status="ok", message="更新完成")

        self.server.service = QuickService()
        self.store.begin_auto_attempt("2026-09-27", "2026-09-27T09:00:00+08:00")
        self.store.finish_auto_attempt("2026-09-27", "error", "offline")
        before = datetime.fromisoformat("2026-09-28T08:59:00+08:00")
        after = datetime.fromisoformat("2026-09-28T09:00:00+08:00")
        self.assertFalse(self.server.start_due_update(before))
        self.assertTrue(self.server.start_due_update(after))
        self.wait_until(lambda: self.store.auto_attempts("2026-09-28").status == "success")
        self.assertFalse(self.server.start_due_update(after))
        self.assertEqual(calls, ["2026-09-28"])

    def test_ai_does_not_run_while_background_update_holds_data_lock(self):
        from github_radar.update_lock import update_lock

        self.save_issue()
        self.server.connection = ReadyConnection()

        class CountingAI:
            calls = 0

            def refine_keyword(inner, *args):
                inner.calls += 1

            def cancel(inner):
                pass

        ai = CountingAI()
        self.server.ai_service = ai
        keyword_id = self.store.list_keywords()[0].id
        with update_lock(self.store.data_dir):
            self.assertEqual(self.post(f"/api/ai/keywords/{keyword_id}/refine")[0], 202)
            self.wait_until(lambda: json.loads(self.get("/api/issue")[2])
                            ["keyword_groups"][0]["ai_status"] == "error")
        self.assertEqual(ai.calls, 0)

    def test_auto_update_api_saves_schedule_only_after_windows_accepts_it(self):
        class Scheduler:
            def __init__(inner):
                inner.calls = []

            def sync(inner, enabled, time):
                inner.calls.append((enabled, time))

            def status(inner, enabled, time):
                return enabled and inner.calls[-1:] == [(enabled, time)]

        scheduler = Scheduler()
        self.server.scheduler = scheduler
        self.assertEqual(self.get("/api/auto-update", token=False)[0], 403)
        self.assertEqual(self.post("/api/auto-update", {"enabled": False,
                                                       "time": "10:30"}, token=False)[0], 403)
        self.assertEqual(self.post("/api/auto-update", {"enabled": True,
                                                       "time": "22:00"})[0], 400)
        self.assertEqual(self.post("/api/auto-update", {"enabled": 1,
                                                       "time": "10:30"})[0], 400)
        self.assertEqual(self.post("/api/auto-update", {"enabled": True,
                                                       "time": "10:30"})[0], 200)
        self.assertEqual(scheduler.calls, [(True, "10:30")])
        self.assertEqual(self.store.load_auto_update_settings().time, "10:30")
        status, _, body = self.get("/api/auto-update")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["registered"], True)

    def test_auto_update_api_keeps_old_preference_when_task_registration_fails(self):
        from github_radar.windows_scheduler import SchedulerError

        class Scheduler:
            def sync(inner, enabled, time):
                raise SchedulerError("Access denied")

            def status(inner, enabled, time):
                return False

        self.server.scheduler = Scheduler()
        status, payload = self.post("/api/auto-update", {"enabled": False,
                                                         "time": "10:30"})
        self.assertEqual(status, 503)
        self.assertTrue(payload["error"])
        self.assertEqual(self.store.load_auto_update_settings().time, "09:00")
        self.assertTrue(self.store.load_auto_update_settings().enabled)

    def test_auto_update_api_restores_old_task_when_setting_write_fails(self):
        import sqlite3
        from unittest.mock import patch

        class Scheduler:
            def __init__(inner):
                inner.calls = []

            def sync(inner, enabled, time):
                inner.calls.append((enabled, time))

            def status(inner, enabled, time):
                return True

        scheduler = Scheduler()
        self.server.scheduler = scheduler
        with patch.object(self.store, "save_auto_update_settings",
                          side_effect=sqlite3.OperationalError("disk full")):
            status, response = self.post("/api/auto-update", {"enabled": True,
                                                               "time": "11:00"})
        self.assertEqual(status, 503)
        self.assertTrue(response["error"])
        self.assertEqual(scheduler.calls, [(True, "11:00"), (True, "09:00")])
        self.assertEqual(self.store.load_auto_update_settings().time, "09:00")

    def test_keyword_post_saves_rule_and_starts_refresh(self):
        class QuickService:
            def load_latest(inner, today):
                return self.service.load_latest(today)

            def refresh(inner, today, observed_at):
                return inner.load_latest(today)

        self.server.service = QuickService()
        status, payload = self.post("/api/keywords", {"term": "  local ai  ", "min_stars": 1200})
        self.assertEqual(status, 202)
        self.assertEqual(payload["keyword"]["term"], "local ai")
        self.assertEqual(self.store.list_keywords()[0].min_stars, 1200)
        self.assertEqual(self.post("/api/keywords", {"term": "   "})[0], 400)
        self.assertEqual(self.post("/api/keywords", {"term": "LOCAL AI"})[0], 409)

    def test_offline_refresh_preserves_cards_and_explains_failure(self):
        saved_date = self.save_issue()

        class OfflineClient:
            search_remaining = 10

            def search(self, query, **kwargs):
                raise GitHubRequestError("当前无法连接 GitHub")

            def get_repository(self, full_name):
                raise GitHubRequestError("当前无法连接 GitHub")

        self.server.service = RadarService(OfflineClient(), self.store)
        self.assertEqual(self.post("/api/refresh")[0], 202)
        self.wait_until(lambda: not json.loads(self.get("/api/issue")[2])["busy"])
        issue = json.loads(self.get("/api/issue")[2])
        self.assertEqual(issue["status"], "error")
        self.assertEqual(issue["local_date"], saved_date)
        self.assertIn("无法连接", issue["message"])
        self.assertEqual(issue["sections"][0]["cards"][0]["title"], "owner/project-1")

    def test_issue_poll_reads_external_scheduled_failure_after_cached_refresh(self):
        today = self.save_issue()
        self.server._last_result = self.service.load_latest(today)
        self.store.save_refresh_failure(f"{today}T12:00:00+08:00", "后台更新连接失败")
        issue = json.loads(self.get("/api/issue")[2])
        self.assertEqual(issue["status"], "error")
        self.assertIn("后台更新连接失败", issue["message"])

    def test_quit_waits_for_in_progress_refresh_then_stops_server(self):
        class ControlledService:
            def __init__(inner):
                inner.started = threading.Event()
                inner.release = threading.Event()

            def load_latest(inner, today):
                return self.service.load_latest(today)

            def refresh(inner, today, observed_at):
                inner.started.set()
                inner.release.wait(3)
                return inner.load_latest(today)

        controlled = ControlledService()
        self.server.service = controlled
        self.addCleanup(controlled.release.set)
        self.post("/api/refresh")
        self.assertTrue(controlled.started.wait(2))
        status, payload = self.post("/api/quit")
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "quitting")
        self.assertFalse(self.server.closed)
        controlled.release.set()
        self.wait_until(lambda: self.server.closed)

    def test_ai_post_requires_local_token_and_origin(self):
        self.save_issue()
        self.server.connection = ReadyConnection()
        self.assertEqual(self.post("/api/ai/model", {"model_id": None}, token=False)[0], 403)
        self.assertEqual(self.post("/api/ai/model", {"model_id": None},
                                   origin="https://evil.example")[0], 403)
        self.assertEqual(self.post("/api/ai/model", {"model_id": "invented"})[0], 400)
        self.assertEqual(self.post("/api/ai/model", {"model_id": "gpt-6-sol"})[0], 200)
        self.assertEqual(self.store.load_ai_model(), "gpt-6-sol")
        status, _, body = self.get("/api/ai/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["models"][0]["id"], "gpt-6-sol")
        self.assertEqual(self.post("/api/ai/login")[1]["auth_url"],
                         "https://chatgpt.com/auth/start")
        self.assertEqual(self.post("/api/ai/keywords/999/refine")[0], 404)
        self.assertEqual(self.post("/api/ai/projects/999/explain")[0], 404)

    def test_ai_login_failure_is_a_visible_status(self):
        class FailedConnection(ReadyConnection):
            def begin_login(self):
                raise Exception("Could not find home directory")

        self.server.connection = FailedConnection()
        status, response = self.post("/api/ai/login")
        self.assertEqual(status, 503)
        self.assertTrue(response["error"])

    def test_ai_job_is_single_flight(self):
        self.save_issue()
        self.server.connection = ReadyConnection()

        class ControlledAI:
            def __init__(inner):
                inner.started = threading.Event()
                inner.release = threading.Event()
                inner.calls = 0
                inner.provider = inner

            def refine_keyword(inner, *args):
                inner.calls += 1
                inner.started.set()
                inner.release.wait(3)

            def cancel(inner):
                inner.release.set()

        ai = ControlledAI()
        self.server.ai_service = ai
        self.addCleanup(ai.release.set)
        keyword_id = self.store.list_keywords()[0].id
        path = f"/api/ai/keywords/{keyword_id}/refine"
        self.assertEqual(self.post(path)[0], 202)
        self.assertTrue(ai.started.wait(2))
        repeat_status, repeat = self.post(path)
        self.assertEqual(repeat_status, 202)
        self.assertTrue(repeat["already_running"])
        self.assertEqual(self.post("/api/refresh")[0], 409)
        issue = json.loads(self.get("/api/issue")[2])
        self.assertEqual(issue["keyword_groups"][0]["ai_status"], "running")
        ai.release.set()
        self.wait_until(lambda: json.loads(self.get("/api/issue")[2])
                        ["keyword_groups"][0]["ai_status"] != "running")
        self.assertEqual(ai.calls, 1)

    def test_quit_cancels_ai_without_partial_results(self):
        self.save_issue()
        self.server.connection = ReadyConnection()
        original = [item.repo_id for item in self.store.latest_recommendations()]

        class CancellableAI:
            def __init__(inner):
                inner.started = threading.Event()
                inner.release = threading.Event()
                inner.cancelled = False
                inner.provider = inner

            def refine_keyword(inner, *args):
                inner.started.set()
                inner.release.wait(3)
                if inner.cancelled:
                    raise RuntimeError("cancelled")

            def cancel(inner):
                inner.cancelled = True
                inner.release.set()

        ai = CancellableAI()
        self.server.ai_service = ai
        self.addCleanup(ai.release.set)
        keyword_id = self.store.list_keywords()[0].id
        self.assertEqual(self.post(f"/api/ai/keywords/{keyword_id}/refine")[0], 202)
        self.assertTrue(ai.started.wait(2))
        self.assertEqual(self.post("/api/quit")[0], 202)
        self.wait_until(lambda: self.server.closed)
        self.assertTrue(ai.cancelled)
        self.assertEqual([item.repo_id for item in self.store.latest_recommendations()], original)

    def test_issue_reads_ai_cache_only(self):
        self.save_issue()

        class ForbiddenAI:
            def __getattr__(self, method):
                raise AssertionError(f"GET must not call AI: {method}")

        self.server.ai_service = ForbiddenAI()
        issue = json.loads(self.get("/api/issue")[2])
        self.assertEqual(issue["keyword_groups"][0]["term"], "local ai")
        self.assertEqual(len(issue["keyword_groups"][0]["cards"]), 1)
        detail = json.loads(self.get("/api/project/4")[2])
        self.assertIsNone(detail["ai_explanation"])

    def test_detail_reads_saved_explanation_without_running_ai(self):
        self.save_issue()
        keyword_id = self.store.list_keywords()[0].id
        insight = InsightText("中文简介", "用途", "场景", "人群", ("特点",))
        saved = ProjectExplanation(insight, insight, "relevant", ("简介",), False)
        self.store.save_explanation(4, keyword_id, None, "source-v1", saved)

        class ForbiddenAI:
            def __getattr__(self, method):
                raise AssertionError(f"GET must not call AI: {method}")

        self.server.ai_service = ForbiddenAI()
        detail = json.loads(self.get("/api/project/4")[2])
        self.assertEqual(detail["ai_explanation"]["zh"]["summary"], "中文简介")
        self.assertEqual(detail["ai_explanation"]["relevance"], "relevant")

    def test_model_switch_still_shows_prior_labeled_explanation(self):
        self.save_issue()
        keyword_id = self.store.list_keywords()[0].id
        insight = InsightText("旧模型简介", "用途", "场景", "人群", ("特点",))
        saved = ProjectExplanation(insight, insight, "relevant", ("简介",), False)
        self.store.save_explanation(4, keyword_id, "gpt-old", "source-v1", saved)
        self.store.save_ai_model("gpt-6-sol")
        detail = json.loads(self.get("/api/project/4")[2])
        self.assertEqual(detail["ai_explanation"]["zh"]["summary"], "旧模型简介")
        self.assertEqual(detail["ai_explanation"]["model_id"], "gpt-old")

    def test_detail_regeneration_requires_explicit_boolean_and_forwards_it(self):
        self.save_issue()
        self.server.connection = ReadyConnection()

        class RecordingAI:
            def __init__(inner):
                inner.calls = []
                inner.provider = inner

            def explain_project(inner, repo_id, model_id, checked_at, force=False):
                inner.calls.append((repo_id, force))

            def cancel(inner):
                pass

        ai = RecordingAI()
        self.server.ai_service = ai
        path = "/api/ai/projects/4/explain"
        self.assertEqual(self.post(path, {"force": "true"})[0], 400)
        self.assertEqual(self.post(path, {"force": True})[0], 202)
        self.wait_until(lambda: len(ai.calls) == 1)
        self.wait_until(lambda: not self.server._ai_worker_thread.is_alive())
        self.assertEqual(ai.calls, [(4, True)])

    def test_auto_model_uses_and_saves_actual_default_model(self):
        self.save_issue()
        self.server.connection = ReadyConnection()

        class RecordingAI:
            def __init__(inner):
                inner.calls = []

            def explain_project(inner, repo_id, model_id, checked_at, force=False):
                inner.calls.append(model_id)

            def cancel(inner):
                pass

        ai = RecordingAI()
        self.server.ai_service = ai
        self.assertEqual(self.post("/api/ai/projects/4/explain")[0], 202)
        self.wait_until(lambda: bool(ai.calls))
        self.wait_until(lambda: not self.server._ai_worker_thread.is_alive())
        self.assertEqual(ai.calls, ["gpt-6-sol"])
        self.assertIsNone(self.store.load_ai_model())
        self.assertEqual(self.store.load_ai_active_model(), "gpt-6-sol")
        self.assertEqual(RadarStore(self.store.data_dir).load_ai_active_model(), "gpt-6-sol")

    def test_ai_start_rechecks_refresh_after_slow_connection_probe(self):
        self.save_issue()
        entered, release_probe, release_refresh = (threading.Event(),
                                                   threading.Event(), threading.Event())

        class SlowConnection(ReadyConnection):
            def probe(self):
                entered.set()
                release_probe.wait(3)
                return super().probe()

        class RecordingAI:
            def __init__(inner):
                inner.calls = 0

            def explain_project(inner, *args, **kwargs):
                inner.calls += 1

        ai = RecordingAI()
        self.server.connection = SlowConnection()
        self.server.ai_service = ai
        reply = []
        caller = threading.Thread(target=lambda: reply.append(
            self.post("/api/ai/projects/4/explain")))
        caller.start()
        self.assertTrue(entered.wait(2))
        refresh = threading.Thread(target=lambda: release_refresh.wait(3))
        refresh.start()
        self.server._worker = refresh
        release_probe.set()
        caller.join(3)
        release_refresh.set()
        refresh.join(3)
        self.assertFalse(caller.is_alive())
        self.assertEqual(reply[0][0], 409)
        self.assertEqual(ai.calls, 0)


if __name__ == "__main__":
    unittest.main()
