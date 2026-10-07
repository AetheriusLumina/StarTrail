import unittest
from github_radar.i18n import tr

class StatusTranslationTests(unittest.TestCase):
 def test_compound_product_status_preserves_dates_and_translates_each_clause(self):
  value='检索未完成，已保留上次结果：GitHub限流等待至 2026-10-07T22:15:21+08:00；已完成核算保留，可取消；检索已取消；实际读取 25 个项目；来源顺序与官方新增排名分别保存；主题首批候选，未核实全量分页；网站 AI 分类不是本软件 AI 精选；检索已取消，断点保留'
  text=tr('en',value)
  self.assertIn('2026-10-07T22:15:21+08:00',text)
  self.assertIn('25',text)
  self.assertFalse(any('\u4e00'<=c<='\u9fff' for c in text),text)
 def test_unknown_author_text_remains_verbatim(self):
  self.assertEqual(tr('en','作者的中文项目简介'),'作者的中文项目简介')
