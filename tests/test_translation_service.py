import importlib
import importlib.util
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from github_radar.storage import RadarStore
from github_radar.translation_content import translation_cache_key
from github_radar.translation_types import TranslationPart, TranslationItem, TranslationResult


def item(identity='a', text='Useful project'):
    return TranslationItem(identity, (TranslationPart('text', text),))

def translated(items):
    return [TranslationResult(i.id, 'translated', (TranslationPart('text', '有用的项目'),)) for i in items]


class TranslationServiceTests(unittest.TestCase):
    def test_successful_original_language_is_cached_without_repeated_cpu(self):
        calls=[]
        def runner(target,items,cancel):
            calls.append(1)
            return [TranslationResult(i.id,'original',i.parts) for i in items]
        service=self.service(runner);source=(item('first','已有中文内容'),)
        self.wait(service,service.submit('zh',source)['job_id'])
        second=service.submit('zh',(item('second','已有中文内容'),))
        self.assertEqual(second['status'],'ready')
        self.assertEqual(calls,[1])

    def test_retry_reexecutes_failed_items_in_a_ready_job(self):
        calls=[]
        def runner(target,items,cancel):
            calls.append(1)
            if len(calls)==1:return [TranslationResult(i.id,'original',i.parts,'temporary error') for i in items]
            return translated(items)
        service=self.service(runner);source=(item(),)
        first=service.submit('zh',source);self.wait(service,first['job_id'])
        second=service.submit('zh',source);result=self.wait(service,second['job_id'])
        self.assertEqual(result['items'][0]['status'],'translated')
        self.assertEqual(len(calls),2)

    def test_cancel_running_job_preserves_cache_and_allows_next_job(self):
        started=threading.Event()
        def runner(target,items,cancel):
            if items[0].id=='old':
                started.set();cancel.wait(2)
            return translated(items)
        service=self.service(runner)
        source=item('old','Obsolete paragraph')
        job=service.submit('zh',(source,))
        self.assertTrue(started.wait(1))
        self.assertTrue(hasattr(service,'cancel'),'Per-page cancellation is unavailable')
        cancelled=service.cancel(job['job_id'])
        self.assertEqual(cancelled['status'],'cancelled')
        next_job=service.submit('zh',(item('new','Current paragraph'),))
        self.assertEqual(self.wait(service,next_job['job_id'])['status'],'ready')
        self.assertIsNone(self.store.load_translation(translation_cache_key(source,'zh')))

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('github_radar.translation_service'),
                             'Translation queue not implemented')
        self.module = importlib.import_module('github_radar.translation_service')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = RadarStore(Path(temp.name))

    def service(self, runner, **kwargs):
        service = self.module.TranslationService(self.store, runner=runner, **kwargs)
        self.addCleanup(service.close)
        return service

    def wait(self, service, identity):
        end = time.monotonic()+2
        while time.monotonic()<end:
            result = service.get(identity)
            if result and result['status'] in ('ready','error','cancelled'):
                return result
            time.sleep(.005)
        self.fail('Translation did not finish')

    def test_cache_hit_uses_current_item_id_without_cpu(self):
        source = item('old')
        self.store.save_translation(translation_cache_key(source,'zh'),
                                   (TranslationPart('text','cached'),), 'now')
        service = self.service(lambda *_: self.fail('CPU called for cache hit'))
        result = service.submit('zh', (item('new'),))
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['items'][0]['id'],'new')
        self.assertEqual(result['items'][0]['parts'][0]['text'],'cached')

    def test_identical_pending_batch_reuses_job_and_one_runner(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def run(target, items, cancel):
            calls.append(items); started.set(); release.wait(2); return translated(items)
        service = self.service(run)
        self.addCleanup(release.set)
        first = service.submit('zh',(item(),))
        self.assertTrue(started.wait(1))
        self.assertEqual(service.submit('zh',(item(),))['job_id'],first['job_id'])
        release.set()
        self.assertEqual(self.wait(service,first['job_id'])['status'],'ready')
        self.assertEqual(len(calls),1)
        self.assertIsNotNone(self.store.load_translation(translation_cache_key(item(),'zh')))

    def test_waiting_queue_cap_and_completed_result_cap(self):
        started, release = threading.Event(), threading.Event()
        def run(target, items, cancel):
            started.set(); release.wait(2); return translated(items)
        service = self.service(run)
        self.addCleanup(release.set)
        first = service.submit('zh',(item(),))
        self.assertTrue(started.wait(1))
        pending = [service.submit('zh',(item(str(i),str(i)),)) for i in range(8)]
        with self.assertRaises(self.module.TranslationBusy):
            service.submit('zh',(item('overflow','overflow'),))
        release.set()
        for result in [first,*pending]: self.wait(service,result['job_id'])
        for i in range(33):
            result = service.submit('zh',(item(str(i+10),str(i+10)),))
            self.wait(service,result['job_id'])
        self.assertIsNone(service.get(first['job_id']))
        self.assertLessEqual(len(service._jobs),32)

    def test_timeout_discards_late_results_and_serializes_next_cpu(self):
        started, release = threading.Event(), threading.Event()
        active, max_active = 0, 0
        clock = [0]
        def run(target, items, cancel):
            nonlocal active, max_active
            active += 1; max_active=max(active,max_active); started.set()
            release.wait(2); active-=1; return translated(items)
        service = self.service(run, clock=lambda:clock[0])
        self.addCleanup(release.set)
        first = service.submit('zh',(item(),))
        self.assertTrue(started.wait(1))
        service.submit('zh',(item('b','Second project'),))
        clock[0]=46
        result=self.wait(service,first['job_id'])
        self.assertEqual(result['status'],'error')
        release.set(); time.sleep(.1)
        self.assertIsNone(self.store.load_translation(translation_cache_key(item(),'zh')))
        self.assertEqual(max_active,1)

    def test_close_cancels_and_never_writes_late_cache(self):
        started, release = threading.Event(), threading.Event()
        def run(target, items, cancel):
            started.set(); release.wait(3); return translated(items)
        service = self.service(run)
        self.addCleanup(release.set)
        job=service.submit('zh',(item(),))
        self.assertTrue(started.wait(1))
        before=time.monotonic(); service.close()
        self.assertLess(time.monotonic()-before,2.05)
        self.assertEqual(service.get(job['job_id'])['status'],'cancelled')
        release.set(); time.sleep(.03)
        self.assertIsNone(self.store.load_translation(translation_cache_key(item(),'zh')))
        with self.assertRaises(self.module.TranslationBusy): service.submit('zh',(item(),))

    def test_partial_fallback_and_cache_write_failure_are_explicit(self):
        def run(target, items, cancel):
            return [translated(items)[0],TranslationResult('b','original',items[1].parts,'显示原文')]
        service=self.service(run)
        with patch.object(self.store,'save_translation',side_effect=OSError('readonly')):
            job=service.submit('zh',(item(),item('b','Other prose')))
            result=self.wait(service,job['job_id'])
        self.assertEqual(result['status'],'ready')
        self.assertIn('缓存',result['reason'])
        self.assertEqual(result['items'][1]['status'],'original')
        self.assertIsNone(self.store.load_translation(translation_cache_key(item('b','Other prose'),'zh')))

    def test_bad_worker_result_cannot_change_literal(self):
        source=TranslationItem('a',(TranslationPart('text','See '),TranslationPart('literal','`safe`')))
        service=self.service(lambda *_:[TranslationResult('a','translated',(TranslationPart('literal','`bad`'),))])
        result=self.wait(service,service.submit('zh',(source,))['job_id'])
        self.assertEqual(result['status'],'error')
        self.assertIsNone(self.store.load_translation(translation_cache_key(source,'zh')))

    def test_locked_cache_does_not_repeat_blocking_writes_or_delay_exit(self):
        import sqlite3
        locked=sqlite3.connect(self.store.db_path)
        self.addCleanup(locked.close)
        locked.execute('BEGIN IMMEDIATE')
        service=self.service(lambda target,items,cancel:translated(items))
        job=service.submit('zh',tuple(item(str(i),str(i)) for i in range(128)))
        began=time.monotonic()
        result=self.wait(service,job['job_id'])
        self.assertLess(time.monotonic()-began,2,'A locked cache must not stall the whole batch')
        self.assertIn('缓存',result['reason'])
        before=time.monotonic(); service.close()
        self.assertLess(time.monotonic()-before,2)

    def test_close_waits_for_owned_runner_cleanup_before_parent_can_exit(self):
        started, cleaned=threading.Event(),threading.Event()
        def run(target,items,cancel):
            started.set();cancel.wait(2)
            time.sleep(.1)  # Simulate terminate/wait in the owned Popen cleanup.
            cleaned.set()
            return translated(items)
        service=self.service(run)
        service.submit('zh',(item(),))
        self.assertTrue(started.wait(1))
        service.close()
        self.assertTrue(cleaned.is_set(),'Parent exit must wait for owned process cleanup')
