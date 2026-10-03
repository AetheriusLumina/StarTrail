import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from github_radar.storage import RadarStore
from github_radar.translation_types import TranslationItem, TranslationPart
from github_radar.translation_content import translation_cache_key


class TranslationStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = RadarStore(self.path)
        self.assertTrue(callable(getattr(self.store, 'load_translation', None)),
                        'Translation cache not implemented')
        self.assertTrue(callable(getattr(self.store, 'save_translation', None)))

    def test_parts_roundtrip_and_missing_key(self):
        parts = (TranslationPart('text', '译文'), TranslationPart('literal', '`x < y`'),
                 TranslationPart('text', ' https://example.com/#a'))
        self.assertIsNone(self.store.load_translation('a' * 64))
        self.store.save_translation('a' * 64, parts, '2026-10-01T00:00:00Z')
        self.assertEqual(RadarStore(self.path).load_translation('a' * 64), parts)

    def test_language_version_and_literal_boundary_have_distinct_keys(self):
        item = TranslationItem('1', (TranslationPart('text', 'GitHub project'),))
        first = translation_cache_key(item, 'zh')
        self.store.save_translation(first, (TranslationPart('text', '项目'),), '2026-10-01')
        self.assertIsNone(self.store.load_translation(translation_cache_key(item, 'en')))
        with patch('github_radar.translation_content.PROTECTION_VERSION', 'future-test-version'):
            self.assertIsNone(self.store.load_translation(translation_cache_key(item, 'zh')))
        other = TranslationItem('2', item.parts)
        self.assertEqual(self.store.load_translation(translation_cache_key(other, 'zh')),
                         (TranslationPart('text', '项目'),))
        literal = TranslationItem('1', (TranslationPart('literal', 'GitHub project'),))
        self.assertIsNone(self.store.load_translation(translation_cache_key(literal, 'zh')))

    def test_legacy_database_migration_is_idempotent_and_preserves_all_rows(self):
        # Simulate a prior release by dropping only the new cache from this isolated DB.
        keyword = self.store.add_keyword('项目检索', 1800)
        with closing(sqlite3.connect(self.store.db_path)) as db, db:
            db.execute("INSERT INTO repositories VALUES (1, 'a/b', 'https://github.com/a/b', '原文', '[]', NULL, 5, 0)")
            db.execute("INSERT INTO follows VALUES (1, '2026-09-30')")
            db.execute("INSERT INTO snapshots VALUES (1, '2026-09-30', 5, 'then')")
            db.execute("INSERT INTO readme_cache VALUES (1, 'a/b', '原 README', 'url', 'then', NULL, 'hash', 0, 1)")
            tables = ('keywords', 'repositories', 'follows', 'snapshots', 'readme_cache')
            before = {t: db.execute('SELECT * FROM ' + t).fetchall() for t in tables}
            db.execute('DROP TABLE translation_cache')
        for _ in range(2):
            RadarStore(self.path)
        with closing(sqlite3.connect(self.store.db_path)) as db:
            self.assertEqual({t: db.execute('SELECT * FROM ' + t).fetchall() for t in tables}, before)
        self.assertEqual(self.store.list_keywords()[0].id, keyword.id)

    def test_limit_evicts_oldest_cache_only(self):
        self.store.add_keyword('keep me')
        with closing(sqlite3.connect(self.store.db_path)) as db, db:
            db.executemany('INSERT INTO translation_cache VALUES (?, ?, ?)',
                           ((f'{i:064x}', '[]', f'{i:06d}') for i in range(20000)))
        self.store.save_translation('f' * 64, (TranslationPart('text', 'new'),), '999999')
        self.assertIsNone(self.store.load_translation('0' * 64))
        self.assertEqual(self.store.load_translation('f' * 64), (TranslationPart('text', 'new'),))
        with closing(sqlite3.connect(self.store.db_path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM translation_cache').fetchone()[0], 20000)
        self.assertEqual(self.store.list_keywords()[0].term, 'keep me')

    def test_corrupt_cached_parts_are_a_miss(self):
        for value in ('{', '{"text":"bad"}', '[{"kind":"html","text":"<b>x</b>"}]',
                      '[{"kind":"text","text":false}]'):
            with closing(sqlite3.connect(self.store.db_path)) as db, db:
                db.execute('INSERT OR REPLACE INTO translation_cache VALUES (?, ?, ?)',
                           ('a' * 64, value, 'now'))
            self.assertIsNone(self.store.load_translation('a' * 64))

    def test_write_failure_propagates_without_touching_source(self):
        self.store.add_keyword('keep')
        with patch.object(self.store, '_connect', side_effect=sqlite3.OperationalError('readonly')):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.save_translation('a' * 64, (TranslationPart('text', 'x'),), 'now')
        self.assertEqual(self.store.list_keywords()[0].term, 'keep')
