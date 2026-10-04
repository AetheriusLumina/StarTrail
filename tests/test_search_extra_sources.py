import unittest
from github_radar.trendshift import TrendshiftClient

class TopicDirectoryTests(unittest.TestCase):
    def test_public_topic_names_are_refreshed_and_used_exactly(self):
        client=TrendshiftClient();pages=['<a href="/topics/agent-skills">Agent Skills</a>','<a href="/topics/models">Models</a>']
        client._read=lambda url:pages.pop(0)
        self.assertEqual(client.topics(refresh=True),('agent skills',))
        self.assertEqual(client.topics(refresh=True),('models',))
