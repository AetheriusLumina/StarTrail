import importlib
import importlib.util
import tempfile
from pathlib import Path
import unittest

from github_radar.discovery_types import SourceEvidence
from github_radar.models import Repository, StarDay
from github_radar.storage import RadarStore

NOW="2026-09-30T10:00:00+00:00"


class CrossCheckTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("github_radar.cross_check"))
        return importlib.import_module("github_radar.cross_check")

    def compare(self, stars=1903, total="1.9k", added="10", boundary="UTC", source_at=NOW):
        evidence=SourceEvidence("trendshift_daily","a/repo","https://trendshift.io/",source_at,
                                 "daily","2026-09-28","trendshift_daily",1,total,added,
                                 day_boundary=boundary,repo_id=7)
        repo=Repository(7,"a/repo","https://github.com/a/repo","",(),None,stars,False)
        return self.module().compare_source(evidence,repo,StarDay("2026-09-28",10),NOW,"UTC")

    def test_rounded_star_is_not_treated_as_exact(self):
        result=self.compare()
        self.assertEqual(result[0].status,"consistent")
        self.assertEqual(result[0].source_value_text,"1.9k")
        self.assertEqual(result[0].official_value,1903)
        self.assertEqual(self.compare(stars=3000)[0].status,"conflict")
        self.assertEqual(self.compare(stars=3000,source_at="2026-09-30T09:00:00+00:00")[0].status,"unaligned")

    def test_daily_boundary_unknown_is_unaligned(self):
        result=self.compare(boundary=None)
        self.assertEqual(result[1].status,"unaligned")
        self.assertIn("日界",result[1].reason)

    def test_actual_same_day_conflict_is_preserved(self):
        result=self.compare(added="99")
        self.assertEqual(result[1].status,"conflict")
        self.assertEqual((result[1].official_value,result[1].source_value_text),(10,"99"))
        with tempfile.TemporaryDirectory() as d:
            store=RadarStore(Path(d)); self.assertTrue(hasattr(store,"save_cross_checks"))
            store.save_cross_checks("2026-09-30",list(result))
            store=RadarStore(Path(d))
            self.assertEqual(store.cross_checks("2026-09-30",7),list(result))

    def test_missing_source_keeps_official_rank(self):
        repo=Repository(7,"a/repo","https://github.com/a/repo","",(),None,1903,False)
        result=self.module().compare_source(None,repo,StarDay("2026-09-28",10),NOW,"UTC")
        self.assertTrue(all(c.status=="missing_source" for c in result))
        self.assertTrue(all(c.source_url is None and c.source_observed_at is None for c in result))
        self.assertEqual(result[1].official_value,10)
