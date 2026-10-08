import json,tempfile,unittest
from pathlib import Path
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from github_radar.storage import RadarStore
from github_radar.search_storage import SearchStore
from github_radar.search_types import SearchScope,ObservedRepository,Publication
from github_radar.models import Recommendation,StarSnapshot
from tests.test_storage import repository
from github_radar.update_diagnostics import read_diagnostics

class UpdateDiagnosticsTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.store=RadarStore(self.temp.name);self.search=SearchStore(self.store)
 def test_export_is_readonly_and_excludes_credentials(self):
  with closing(self.store._connect()) as db,db:db.execute("INSERT INTO settings VALUES('secret_token','PRIVATE_MARKER')")
  Path(self.temp.name,'github-credential.bin').write_text('PRIVATE_MARKER')
  before=Path(self.temp.name,'radar.db').read_bytes()
  value=read_diagnostics(self.temp.name)
  self.assertNotIn('PRIVATE_MARKER',json.dumps(value))
  self.assertEqual(before,Path(self.temp.name,'radar.db').read_bytes())
 def test_missing_database_is_not_created(self):
  with tempfile.TemporaryDirectory() as missing:
   with self.assertRaises(FileNotFoundError):read_diagnostics(missing)
   self.assertFalse(Path(missing,'radar.db').exists())
 def test_publication_snapshot_survives_later_cache_refresh(self):
  rule=self.store.add_keyword('local ai');now=datetime.fromisoformat('2026-10-04T12:01:00+00:00')
  scope=SearchScope('keyword',rule.id,'2026-10-04',None,'local ai',1000,None)
  repo=repository(8,2000);at='2026-10-04T12:00:00+00:00'
  self.search.save_match(scope,repo,'local ai','github_name_topic',at)
  lease=self.search.claim_run(scope,'test',now=now.timestamp())
  value=Publication(scope,lease.generation,(ObservedRepository(repo,at),),(StarSnapshot(8,scope.local_date,2000,at),),(Recommendation(8,scope.local_date,'keyword',rule.id,None,None,at),),None)
  self.search.publish(value,lease,now=now)
  first=read_diagnostics(self.temp.name)['publication_log']
  self.assertEqual(len(first),1)
  self.assertEqual(first[0]['selected'][0]['stars'],2000)
  self.assertEqual(first[0]['selected'][0]['matches'][0]['term'],'local ai')
  self.search.save_match(scope,replace(repo,stars=3000),'other','github_name_topic','2026-10-05T12:00:00Z')
  self.assertEqual(read_diagnostics(self.temp.name)['publication_log'],first)
