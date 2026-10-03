import tempfile
import unittest
from pathlib import Path
from github_radar.storage import RadarStore


class FontScaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = RadarStore(self.path)

    def test_legacy_sizes_keep_their_actual_scale(self):
        self.assertTrue(hasattr(self.store, 'load_display_preferences'))
        for name, expected in [('small', .9375), ('normal', 1), ('large', 1.125)]:
            self.store.save_preferences('en', name)
            self.assertEqual(self.store.load_display_preferences(), ('en', expected))

    def test_fractional_and_boundary_values_survive_reopen(self):
        self.assertTrue(hasattr(self.store, 'save_display_preferences'))
        for scale in [.8, 1.37, 2]:
            self.store.save_display_preferences('zh', scale)
            self.assertEqual(RadarStore(self.path).load_display_preferences(), ('zh', scale))

    def test_invalid_scale_does_not_change_language_or_value(self):
        self.assertTrue(hasattr(self.store, 'save_display_preferences'))
        self.store.save_display_preferences('zh', 1.37)
        for scale in [True, False, float('nan'), float('inf'), .79, 2.01, '1.2', None]:
            with self.subTest(scale=scale):
                with self.assertRaises(ValueError):
                    self.store.save_display_preferences('en', scale)
                self.assertEqual(self.store.load_display_preferences(), ('zh', 1.37))

    def test_invalid_language_keeps_original(self):
        self.assertTrue(hasattr(self.store, 'save_display_preferences'))
        self.store.save_display_preferences('en', 1.2)
        with self.assertRaises(ValueError):
            self.store.save_display_preferences('xx', 1.7)
        self.assertEqual(self.store.load_display_preferences(), ('en', 1.2))
