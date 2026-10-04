import json
import unittest
import threading
from pathlib import Path
from unittest.mock import patch
from github_radar.ai_provider import CodexProvider,AIOutputError
from github_radar.codex_runner import CodexRunner
from github_radar.search_provider import SearchProvider
from github_radar.search_types import SearchScope,QueryExpansion
from tests.test_ai_provider import ReadyConnection,FakeExec

class SearchProviderTests(unittest.TestCase):
    def setUp(self):
        self.scope=SearchScope('keyword',1,'2026-10-04',None,'智能体技能',1000,None)
        self.provider=SearchProvider(CodexProvider(ReadyConnection()))

    def test_search_enables_live_mode_and_requires_search_events(self):
        made=[]
        def launch(command,**kwargs):
            class Process(FakeExec):
                def communicate(self,input=None,timeout=None):
                    kwargs['stdout'].write(json.dumps({'type':'item.completed','item':{'type':'web_search','id':'w1','query':'agent skills'}})+'\n');kwargs['stdout'].flush()
                    return super().communicate(input,timeout)
            value=Process(command,json.dumps({'candidates':[],'coverage':['GitHub'],'notes':[]}));made.append(value);return value
        with patch('github_radar.codex_runner.subprocess.Popen',side_effect=launch):
            result=self.provider.search_repositories(self.scope,QueryExpansion(self.scope.term,('agent skills',),(),'1'),(),None,timeout=10,cancel_event=threading.Event())
        self.assertEqual(result.web_search_calls,1)
        self.assertIn('web_search="live"',made[0].command)
        self.assertIn('features.shell_tool=false',made[0].command)
        with patch('github_radar.codex_runner.subprocess.Popen',side_effect=lambda command,**kw:FakeExec(command,'{"candidates":[],"coverage":[],"notes":[]}')):
            with self.assertRaises(AIOutputError):self.provider.search_repositories(self.scope,None,(),None,timeout=10,cancel_event=threading.Event())

    def test_expansion_preserves_original_and_limits_six(self):
        runner=self.provider.runner
        with patch.object(runner,'run',return_value={'terms':['agent skills'],'topics':['AI Skills']}):
            value=self.provider.expand_keyword(self.scope.term,None,('AI Skills',))
        self.assertEqual(value.original,self.scope.term)
        for value in ({'terms':['a']*7,'topics':[]},{'terms':['a'],'topics':['invented topic']},{'terms':['a'],'topics':[],'shell':'exec'}):
            with patch.object(runner,'run',return_value=value),self.assertRaises(AIOutputError):self.provider.expand_keyword(self.scope.term,None,('AI Skills',))

    def test_wrong_host_and_invented_ids_are_rejected(self):
        self.provider.runner.web_search_calls=1
        for row in ({'full_name':'owner/repo','source_url':'https://evil.invalid/p','evidence':'x'},
                    {'full_name':'owner/repo','source_url':'https://github.com/owner/repo','evidence':'x','repo_id':123}):
            with patch.object(self.provider.runner,'run',return_value={'candidates':[row],'coverage':[],'notes':[]}),self.assertRaises(AIOutputError):
                self.provider.search_repositories(self.scope,None,(),None,timeout=10,cancel_event=threading.Event())

    def test_search_event_shapes_fail_closed(self):
        for event in ([],{'type':'item.completed','item':[]}, {'type':'item.completed','item':{'type':'web_search','id':'x','status':'failed'}}):
            def launch(command,**kw):
                class Process(FakeExec):
                    def communicate(self,input=None,timeout=None):
                        kw['stdout'].write(json.dumps(event)+'\n');kw['stdout'].flush()
                        return super().communicate(input,timeout)
                return Process(command,'{"candidates":[],"coverage":[],"notes":[]}')
            with patch('github_radar.codex_runner.subprocess.Popen',side_effect=launch):
                with self.assertRaises(AIOutputError):self.provider.search_repositories(self.scope,None,(),None,timeout=10,cancel_event=threading.Event())

    def test_search_evidence_is_retained_with_the_public_source(self):
        row={'full_name':'owner/repo','source_url':'https://github.com/owner/repo','evidence':'README contains installable agent skills'}
        self.provider.runner.web_search_calls=1
        with patch.object(self.provider.runner,'run',return_value={'candidates':[row],'coverage':[],'notes':[]}):
            result=self.provider.search_repositories(self.scope,None,(),None,timeout=10,cancel_event=threading.Event())
        self.assertEqual(result.candidates[0].evidence[0].evidence_text,row['evidence'])

    def test_cancel_new_job_uses_new_runner(self):
        old=CodexRunner(ReadyConnection());old.cancel()
        with self.assertRaises(AIOutputError):old.run('x',{}, {},None)
        new=CodexRunner(ReadyConnection())
        with patch('github_radar.codex_runner.subprocess.Popen',side_effect=lambda command,**kw:FakeExec(command,'{}')):
            self.assertEqual(new.run('x',{}, {},None),{})
