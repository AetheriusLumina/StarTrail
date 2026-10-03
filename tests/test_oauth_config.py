import json
import tempfile
import unittest
from pathlib import Path

class PublisherConfigTests(unittest.TestCase):
    def test_bundled_public_id_works_without_developer_environment(self):
        from github_radar.oauth_config import publisher_client_id
        with tempfile.TemporaryDirectory() as d:
            config=Path(d)/'public_config.json'
            config.write_text(json.dumps({'github_oauth_client_id':'Ov23li1234567890abcd'}),encoding='utf-8')
            self.assertEqual(publisher_client_id(config,{}),'Ov23li1234567890abcd')

    def test_environment_override_and_invalid_missing_config_are_honest(self):
        from github_radar.oauth_config import publisher_client_id
        with tempfile.TemporaryDirectory() as d:
            config=Path(d)/'public_config.json'
            self.assertIsNone(publisher_client_id(config,{}))
            config.write_text('{broken',encoding='utf-8');self.assertIsNone(publisher_client_id(config,{}))
            config.write_text(json.dumps({'github_oauth_client_id':'not a valid ID'}),encoding='utf-8')
            self.assertIsNone(publisher_client_id(config,{}))
            self.assertEqual(publisher_client_id(config,{'GITHUB_RADAR_OAUTH_CLIENT_ID':'Ov23li1234567890abcd'}),'Ov23li1234567890abcd')
            config.write_text(json.dumps({'github_oauth_client_id':'Ov23li1234567890abcd'}),encoding='utf-8')
            self.assertIsNone(publisher_client_id(config,{'GITHUB_RADAR_OAUTH_CLIENT_ID':'invalid'}))
