import json,unittest
from unittest.mock import patch
from github_radar.search_provider import SearchProvider
from github_radar.ai_provider import CodexProvider,AIOutputError
from github_radar.ai_types import AIRepositoryInput
from tests.test_ai_provider import ReadyConnection
from tests.test_storage import repository

class CatalogTests(unittest.TestCase):
 def setUp(self):self.provider=SearchProvider(CodexProvider(ReadyConnection()));self.inputs=tuple(AIRepositoryInput(repository(i,1000+i),'skills README',False) for i in range(1,251))
 def test_all_250_records_supplied_in_single_read_only_invocation(self):
  calls=[]
  def run(instruction,data,schema,model_id,**kw):
   file=kw['data_file'];payload=json.loads(file.read_text(encoding='utf-8'));calls.append(len(payload['repositories']))
   self.assertIn('技能',payload['terms']);self.assertEqual(payload['repositories'][0][0],1)
   return {'relevant_ids':[250,249,248,247,246],'irrelevant_ids':list(range(1,246)),'uncertain_ids':[]}
  with patch.object(self.provider.runner,'run',side_effect=run):results=self.provider.filter_catalog(self.inputs,'skills',('技能',),None)
  self.assertEqual(calls,[250]);self.assertEqual(len(results),250);self.assertEqual(results[-1].verdict,'relevant')
 def test_invented_duplicate_and_missing_ids_are_rejected(self):
  for output in ({'relevant_ids':[999],'irrelevant_ids':[],'uncertain_ids':[]},{'relevant_ids':[1],'irrelevant_ids':[1],'uncertain_ids':[]},{'relevant_ids':[1],'irrelevant_ids':[],'uncertain_ids':[]}):
   with patch.object(self.provider.runner,'run',return_value=output),self.assertRaises(AIOutputError):self.provider.filter_catalog(self.inputs,'skills',(),None)

class RunnerCatalogTests(unittest.TestCase):
 def test_file_is_sent_as_data_without_enabling_local_tools(self):
  from github_radar.codex_runner import CodexRunner
  from tests.test_ai_provider import FakeExec
  from pathlib import Path
  import tempfile
  made=[]
  with tempfile.TemporaryDirectory() as folder:
   file=Path(folder)/'candidates.json';file.write_text('{"repositories":[[17,"org/skills"]]}')
   def launch(command,**kwargs):
    process=FakeExec(command,'{}');made.append(process);return process
   with patch('github_radar.codex_runner.subprocess.Popen',side_effect=launch):
    result=CodexRunner(ReadyConnection()).run('classify all',None,{},None,data_file=file)
  self.assertEqual(result,{});self.assertIn('features.shell_tool=false',made[0].command)
  self.assertIn('features.unified_exec=false',made[0].command)
