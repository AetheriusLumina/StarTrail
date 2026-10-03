import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from github_radar.readme_types import ReadmeDocument
from github_radar.readme_content import extract_readme
from github_radar.storage import RadarStore


def document(text, repo_id=42):
    return ReadmeDocument(repo_id, 'owner/repo', text,
                          'https://github.com/owner/repo#readme', '2026-09-30T09:00:00Z',
                          '"etag"', hashlib.sha256(text.encode()).hexdigest(), False)


class ReadmeContentTests(unittest.TestCase):
    def test_author_html_table_keeps_header_rows_and_protected_commands(self):
        text='<table><tr><th>Name</th><th>Use</th></tr><tr><td>Engine</td><td><code>run | test</code></td></tr></table>'
        full=extract_readme(document(text)).full_markdown
        self.assertIn('| Name | Use |',full)
        self.assertIn('| --- | --- |',full)
        self.assertIn('| Engine | `run \\| test` |',full)

    def test_full_readme_keeps_long_middle_tail_tables_and_code_without_active_html(self):
        text = '# Start\n\nOpening.\n\n' + 'Long explanation. '*2000 + '\n\n## Last\n\nTail marker.\n\n| Name | Use |\n| --- | --- |\n| A | Local |\n\n```html\n<script>example()</script>\n```\n<script>evil()</script>'
        view = extract_readme(document(text))
        full = getattr(view,'full_markdown','')
        self.assertIn('Opening.',full)
        self.assertEqual(full.count('Long explanation.'),2000)
        self.assertIn('Tail marker.',full)
        self.assertIn('| A | Local |',full)
        self.assertIn('<script>example()</script>',full)
        self.assertNotIn('<script>evil()</script>',full)

    def test_html_pre_code_keeps_exact_commands_without_inline_delimiters(self):
        view=extract_readme(document('# Project\n\n## Installation\n<pre><code>npm install example\nnode app.js</code></pre>'))
        self.assertIn('```\nnpm install example\nnode app.js\n```',view.selected_markdown)

    def test_html_inline_code_uses_collision_free_backticks(self):
        view=extract_readme(document('# Project\n\nRun <code>echo `pwd`</code> safely.'))
        self.assertIn('`` echo `pwd` ``',view.selected_markdown)

    def test_inline_author_html_preserves_prose_code_and_link_as_safe_markdown(self):
        text='<p><strong>Codex CLI</strong> runs locally. <a href="https://example.com/guide#setup">Install here</a>.<br>Run <code>pip &lt;x&gt;</code>.</p>'
        view=extract_readme(document(text))
        self.assertIn('Codex CLI',view.selected_markdown)
        self.assertIn('[Install here](https://example.com/guide#setup)',view.selected_markdown)
        self.assertIn('`pip <x>`',view.selected_markdown)
        self.assertNotIn('<strong>',view.selected_markdown)
        self.assertNotIn('<p>',view.selected_markdown)
        # Literal inline HTML examples must also stay executable author text.
        example=extract_readme(document('# Project\n\nUse `<img src="example">` in templates.'))
        self.assertIn('`<img src="example">`',example.selected_markdown)

    def test_fenced_html_examples_keep_scripts_styles_and_comments(self):
        example = '```html\n<!-- required config -->\n<script src="app.js"></script>\n<style>body {color:red}</style>\n```'
        view = extract_readme(document('# Project\n\n## Installation\n' + example))
        self.assertIn(example, view.selected_markdown)
        self.assertIn('required config', next(s.text for s in view.sections if s.kind == 'prerequisites'))

    def test_alternate_markers_in_navigation_list_are_not_purpose(self):
        for marker in ('1)', '+', '1.', '-', '*'):
            with self.subTest(marker=marker):
                view = extract_readme(document('# Project\n\n' + marker + ' [Features](#features)\n\n## Features\nSearch projects.'))
                self.assertEqual(view.sections[0].text, '')

    def test_badges_and_toc_are_not_purpose_and_absent_categories_are_explicit(self):
        view = extract_readme(document('# Project\n\n[![Build](https://badge)](https://ci)\n\n'
            '## Table of Contents\n- [Features](#features)\n\n'
            '## Overview\nA local tool for browsing repositories.\n\n'
            '## Features\n- Offline cache\n- Search\n\n'
            '## Installation\npip install example\n'))
        sections = {s.kind: s for s in view.sections}
        self.assertEqual(len(sections), 6)
        self.assertIn('local tool', sections['purpose'].text)
        self.assertNotIn('Build', sections['purpose'].text)
        self.assertIn('Offline cache', sections['features'].text)
        self.assertEqual(sections['users'].text, '')
        self.assertIsNone(sections['users'].source_heading)
        self.assertEqual(sections['prerequisites'].source_heading, 'Installation')
        self.assertNotIn('Table of Contents', view.selected_markdown)

    def test_chinese_headings_and_fenced_fake_heading(self):
        view = extract_readme(document('# 项目\n\n## 项目介绍\n本地项目浏览工具。\n\n'
            '## 核心功能\n- 搜索与缓存\n\n## 快速开始\n```sh\n# not a heading\nrun\n```\n'))
        self.assertIn('本地', view.sections[0].text)
        self.assertIn('# not a heading', view.selected_markdown)
        self.assertIn('搜索', next(s.text for s in view.sections if s.kind == 'features'))

    def test_limits_keep_complete_blocks_and_code_fences(self):
        text = '# Project\n\n## Overview\n' + ('A full paragraph. ' * 24 + '\n\n') * 40
        text += '## Installation\n```sh\n' + 'long command\n' * 1500 + '```\n'
        view = extract_readme(document(text))
        self.assertLessEqual(len(view.sections[0].text), 800)
        self.assertTrue(view.sections[0].text.endswith('paragraph.'))
        self.assertLessEqual(len(view.selected_markdown), 12000)
        self.assertEqual(view.selected_markdown.count('```') % 2, 0)

    def test_html_only_cannot_become_invented_purpose(self):
        view = extract_readme(document('<script>alert(1)</script>\n<!-- tracking -->'))
        self.assertEqual(view.sections[0].text, '')

    def test_feature_only_readme_does_not_invent_purpose(self):
        view = extract_readme(document('# Project\n\n## Features\nFast search.'))
        self.assertEqual(view.sections[0].text, '')
        self.assertEqual(next(s.text for s in view.sections if s.kind == 'features'), 'Fast search.')

    def test_cache_migrates_reopens_and_detects_damage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RadarStore(Path(directory))
            store.add_keyword('MCP')
            saved = document('# Project\n\nA useful project.')
            store.save_readme(saved)
            reopened = RadarStore(Path(directory))
            self.assertEqual(reopened.load_readme(42), saved)
            self.assertEqual(reopened.list_keywords()[0].term, 'MCP')
            with closing(sqlite3.connect(store.db_path)) as db, db:
                db.execute("UPDATE readme_cache SET text='damaged' WHERE repo_id=42")
            with self.assertRaisesRegex(ValueError, '缓存'):
                reopened.load_readme(42)


if __name__ == '__main__':
    unittest.main()
