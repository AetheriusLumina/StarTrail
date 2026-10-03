import unittest

from github_radar.interpretation import BasicInterpreter, ProjectInterpreter
from github_radar.models import Repository


class InterpretationTests(unittest.TestCase):
    def test_basic_uses_original_description_and_explains_each_keyword_word(self):
        repo = Repository(1, "owner/local-ai", "https://github.com/owner/local-ai",
                          "Runs local models", ("ai", "desktop"), "Python", 2400, False)
        insight = BasicInterpreter().interpret(repo, "Local AI")
        self.assertEqual(insight.summary, "Runs local models")
        self.assertEqual(insight.provider, "basic")
        self.assertFalse(insight.ai_enabled)
        self.assertEqual(insight.relevance, "基础匹配")
        self.assertEqual(insight.evidence[0].term, "local")
        self.assertIn("名称", insight.evidence[0].fields)
        self.assertEqual(insight.evidence[1].term, "ai")
        self.assertIn("标签", insight.evidence[1].fields)

    def test_missing_description_and_partial_match_do_not_invent_a_summary(self):
        repo = Repository(2, "owner/tool", "https://github.com/owner/tool",
                          "", (), None, 1500, False)
        insight = BasicInterpreter().interpret(repo, "Local AI")
        self.assertEqual(insight.summary, "GitHub 暂无项目简介")
        self.assertEqual(insight.relevance, "基础匹配不足")
        self.assertEqual(insight.evidence, ())

    def test_provider_contract_can_be_replaced(self):
        self.assertIsInstance(BasicInterpreter(), ProjectInterpreter)


if __name__ == "__main__":
    unittest.main()
