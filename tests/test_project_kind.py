import json
import unittest
from unittest.mock import patch
from github_radar.ai_provider import CodexProvider, AIOutputError
from github_radar.storage import RadarStore
from tests.test_ai_provider import ReadyConnection, entry, explanation

class ProjectKindTests(unittest.TestCase):
    def payload(self):
        value=json.loads(explanation())
        value['project_kind']={'primary':'skill','secondary':[],
            'zh':'这是供AI使用的Skill，不是独立软件。','en':'This is a skill used by AI, rather than a standalone app.',
            'evidence':['README: SKILL.md'],'uncertain':False}
        return value

    def test_type_is_returned_by_same_explanation_call(self):
        provider=CodexProvider(ReadyConnection())
        with patch.object(provider,'_run',return_value=self.payload()) as run:
            value=provider.explain(entry(7,'SKILL.md instructions'),'agent skills',None)
        self.assertEqual(run.call_count,1)
        self.assertEqual(value.schema_version,3)
        self.assertEqual(value.project_kind.primary,'skill')

    def test_invalid_type_or_false_confidence_is_rejected(self):
        provider=CodexProvider(ReadyConnection())
        for field,bad in [('primary','executable-command'),('secondary',['skill']*3),('zh',''),('uncertain','false')]:
            value=self.payload();value['project_kind'][field]=bad
            with self.subTest(field=field),patch.object(provider,'_run',return_value=value),self.assertRaises(AIOutputError):
                provider.explain(entry(7),'agent skills',None)

    def test_legacy_cached_explanation_loads_without_a_type(self):
        value=self.payload();value.pop('project_kind');value['schema_version']=2
        cached=RadarStore._explanation(json.dumps(value))
        self.assertIsNone(cached.project_kind)
