import unittest

from github_radar.models import Recommendation, Repository
from github_radar.presentation import card_text


class PresentationTests(unittest.TestCase):
    def test_keyword_rank_does_not_claim_growth_rank(self):
        rec = Recommendation(1,"2026-10-01","keyword",7,None,None,"2026-10-01T09:00:00Z",rank=8)
        card = card_text(rec, self.repo, {7:"MCP"}, rank=1)
        self.assertEqual(getattr(card,"display_rank",None),8)
        self.assertEqual(getattr(card,"rank_source",None),"keyword")
        self.assertIsNone(card.growth_rank)

    def test_presentation_preserves_rank_above_five_and_role(self):
        rec=Recommendation(1,"2026-09-30","growth",None,10,None,"2026-09-30T10:00:00Z",
                           "github_daily_new","2026-09-28",8,"new",(7,))
        card=card_text(rec,self.repo,{7:"MCP"},rank=2)
        self.assertEqual(card.growth_rank,8)
        self.assertEqual(card.display_role,"new")
        self.assertEqual(card.matched_keywords,("MCP",))
    def setUp(self):
        self.repo = Repository(1, "owner/project", "https://github.com/owner/project", "", ("python", "desktop", "tool", "extra"), "Python", 1200, False)

    def test_first_sample_never_claims_growth(self):
        rec = Recommendation(1, "2026-09-26", "growth", None, None, None, "2026-09-26T09:00:00+08:00")
        card = card_text(rec, self.repo, {})
        self.assertEqual(card.growth, "正在建立增长记录")
        self.assertEqual(card.description, "GitHub 暂无项目简介")
        self.assertEqual(card.tags, ("python", "desktop", "tool"))

    def test_growth_displays_actual_observation_interval(self):
        rec = Recommendation(1, "2026-09-26", "growth", None, 42, "2026-09-25T09:00:00+08:00", "2026-09-26T09:00:00+08:00")
        card = card_text(rec, self.repo, {})
        self.assertEqual(card.growth, "+42 Star")
        self.assertIn("09-25 09:00", card.observation)
        self.assertIn("09-26 09:00", card.observation)

    def test_keyword_source_is_visible(self):
        rec = Recommendation(1, "2026-09-26", "keyword", 7, None, None, "2026-09-26T09:00:00+08:00")
        card = card_text(rec, self.repo, {7: "MCP"})
        self.assertEqual(card.source, "关键词：MCP")

    def test_official_growth_names_daily_additions_and_statistics_date(self):
        rec = Recommendation(1, "2026-09-26", "growth", None, 42, None,
                             "2026-09-26T09:00:00+08:00", "github_daily_new", "2026-09-25")
        card = card_text(rec, self.repo, {})
        self.assertEqual(card.growth, "新增 42 Star")
        self.assertIn("2026-09-25", card.observation)
        self.assertNotIn("观察区间", card.observation)

    def test_explicit_pending_growth_does_not_claim_zero(self):
        rec = Recommendation(1, "2026-09-26", "growth", None, None, None,
                             "2026-09-26T09:00:00+08:00", "pending", None)
        card = card_text(rec, self.repo, {})
        self.assertEqual(card.growth, "正在建立增长记录")

    def test_english_card_localizes_labels_but_keeps_github_text(self):
        rec = Recommendation(1, "2026-09-26", "growth", None, 42, None,
                             "2026-09-26T09:00:00+08:00", "github_daily_new", "2026-09-25")
        card = card_text(rec, self.repo, {}, language="en")
        self.assertEqual(card.growth, "+42 new Stars")
        self.assertEqual(card.source, "Recent Star growth")
        self.assertEqual(card.description, "No GitHub description provided")
        self.assertIn("GitHub statistical date", card.observation)
        self.assertEqual(card.tags, ("python", "desktop", "tool"))


if __name__ == "__main__":
    unittest.main()
