import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from github_radar.storage import RadarStore
from github_radar.translation_service import TranslationService
from github_radar.translation_types import TranslationPart, TranslationResult
from github_radar.project_translation import ProjectPretranslator, markdown_items, warm_selected


class ProjectTranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RadarStore(Path(self.temp.name))

    def test_markdown_keeps_code_links_tables_and_full_prose(self):
        source = '# Intro\n\nUse `npm install` and [Guide](./guide.md).\n\n| Name | Use |\n| --- | --- |\n| A | Run |\n\n```sh\nnever translate command\n```'
        items = list(markdown_items(source, 'https://github.com/owner/repo/blob/main/README.md', 'zh'))
        parts = [p for i in items for p in i.parts]
        self.assertIn(TranslationPart('literal', 'npm install'), parts)
        self.assertIn(TranslationPart('literal', 'Guide ↗'), parts)
        self.assertIn(TranslationPart('text', 'Guide'), parts)
        self.assertIn(TranslationPart('text', 'Run'), parts)
        self.assertNotIn('never translate command', ''.join(p.text for p in parts))

    def make_worker(self, runner, description='Useful local project'):
        translation = TranslationService(self.store, runner=runner)
        self.addCleanup(translation.close)
        loaded = []
        class Readme:
            def load(inner, repo_id):
                loaded.append(repo_id)
                return SimpleNamespace(sections=(), full_markdown='', document=None)
        repository = SimpleNamespace(description=description, topics=())
        self.store.repositories_for_ids = lambda ids: {i: repository for i in ids}
        self.store.latest_explanation_for_repo = lambda *args: None
        worker = ProjectPretranslator(self.store, Readme(), translation)
        self.addCleanup(worker.close)
        return worker, loaded

    def test_duplicate_projects_and_restarts_reuse_disk_cache_without_cpu(self):
        calls = []
        def runner(target, items, cancel):
            calls.append(items)
            return [TranslationResult(i.id, 'translated', (TranslationPart('text', '有用的本地项目'),)) for i in items]
        worker, loaded = self.make_worker(runner)
        worker.run([1, 1, 2])
        self.assertEqual(loaded, [1, 2])
        self.assertEqual(len(calls), 1)
        other, _ = self.make_worker(lambda *_: self.fail('Unchanged content must use disk cache'))
        other.run([1])
        changed, _ = self.make_worker(runner, 'Changed project description')
        changed.run([1])
        self.assertEqual(len(calls), 2)

    def test_background_enqueue_is_nonblocking_serial_and_shutdown_cancels(self):
        started = threading.Event()
        active = []
        def runner(target, items, cancel):
            active.append(1)
            self.assertEqual(len(active), 1)
            started.set()
            cancel.wait(2)
            active.pop()
            return [TranslationResult(i.id, 'original', i.parts) for i in items]
        worker, _ = self.make_worker(runner)
        worker.enqueue([1, 1, 2])
        self.assertTrue(started.wait(1))
        worker.enqueue([3])
        worker.close()
        self.assertFalse(worker._thread.is_alive())
        self.assertEqual(active, [])

    def test_failure_does_not_stop_next_selected_repository(self):
        calls = []
        worker, loaded = self.make_worker(lambda target, items, cancel: (_ for _ in ()).throw(RuntimeError('model unavailable')))
        worker.run([1, 2])
        self.assertEqual(loaded, [1, 2])

    def test_readme_transport_exception_still_translates_saved_description(self):
        calls=[]
        def runner(target,items,cancel):
            calls.append(1)
            return [TranslationResult(i.id,'translated',(TranslationPart('text','有用的项目'),)) for i in items]
        worker,_=self.make_worker(runner)
        worker.readme.cached=lambda repo_id:SimpleNamespace(sections=(),full_markdown='',document=None)
        worker.readme.load=lambda *args:(_ for _ in ()).throw(RuntimeError('transport unavailable'))
        worker.run([1])
        self.assertEqual(calls,[1])

    def test_batches_are_bounded_and_long_text_is_complete(self):
        source = 'Long English prose. ' * 5000
        items = list(markdown_items(source, None, 'zh'))
        self.assertGreater(len(items), 1)
        self.assertEqual(''.join(p.text for i in items for p in i.parts), source)
        self.assertTrue(all(sum(len(p.text) for p in i.parts) <= 8192 for i in items))

    def test_selected_scope_excludes_catalog_and_never_generates_ai(self):
        self.store.latest_recommendations = lambda: [SimpleNamespace(repo_id=7), SimpleNamespace(repo_id=7), SimpleNamespace(repo_id=9)]
        seen = []
        class Worker:
            def run(inner, ids): seen.extend(ids)
        warm_selected(self.store, None, worker=Worker())
        self.assertEqual(seen, [7, 9])


if __name__ == '__main__':
    unittest.main()
