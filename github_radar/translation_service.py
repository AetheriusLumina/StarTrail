"""Bounded, cancellable offline jobs; only the parent owns the translation cache."""
import hashlib
import json
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from .translation_content import translation_cache_key
from .translation_types import TranslationPart, TranslationResult, validate_request


class TranslationBusy(ValueError):
    pass


def file_runner(target, items, cancel):
    with tempfile.TemporaryDirectory(prefix='github-radar-translation-') as directory:
        source, output = Path(directory)/'input.json', Path(directory)/'output.json'
        source.write_text(json.dumps({'target':target,'items':[asdict(i) for i in items]},
                                    ensure_ascii=False), encoding='utf-8')
        args = [sys.executable]
        if not getattr(sys,'frozen',False):
            args += ['-m','github_radar']
        args += ['--translation-worker',str(source),str(output)]
        options = {'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
        if sys.platform == 'win32':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(args, cwd=Path(__file__).resolve().parent.parent, **options)
        try:
            while process.poll() is None:
                if cancel.wait(.03):
                    raise RuntimeError('Translation cancelled')
            if cancel.is_set() or not output.is_file() or output.stat().st_size > 2097152:
                raise RuntimeError('Translation unavailable')
            result = json.loads(output.read_text(encoding='utf-8'))
            if result['status'] != 'ready':
                raise RuntimeError('Translation unavailable')
            return [TranslationResult(r['id'],r['status'],
                                      tuple(TranslationPart(**p) for p in r['parts']),r.get('reason'))
                    for r in result['items']]
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=.5)


class TranslationService:
    def __init__(self, store, runner=None, clock=time.monotonic):
        self.store, self.runner, self.clock = store, runner or file_runner, clock
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._jobs, self._pending = {}, deque()
        self._closed, self._thread, self._active_cancel = False, None, None
        self._runner_thread=None
        self._active_job=None

    def _view(self, job):
        return {k:job[k] for k in ('job_id','status','items','reason')}

    def _trim(self):
        done = [key for key,j in self._jobs.items() if j['status'] in ('ready','error','cancelled')]
        for key in done[:-32]:
            del self._jobs[key]

    def submit(self, target, items):
        target, items = validate_request({'target':target,'items':[
            {'id':i.id,'parts':[asdict(p) for p in i.parts]} for i in items]})
        signature = hashlib.sha256(json.dumps({'target':target,'items':[asdict(i) for i in items]},
                                              sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self._lock:
            if self._closed:
                raise TranslationBusy('翻译服务已退出，显示原文')
            for job in self._jobs.values():
                if job['signature']==signature and job['status'] not in ('error','cancelled') and not (
                        job['status']=='ready' and any(r.get('reason') for r in job['items'])):
                    return self._view(job)
            results, missing, reason, cache_available = [], [], None, True
            for item in items:
                try:
                    cached = self.store.load_translation(translation_cache_key(item,target)) if cache_available else None
                except Exception:
                    cached = None; cache_available=False; reason = '译文缓存不可用，显示原文或本次译文'
                if cached is None:
                    missing.append(item)
                else:
                    results.append(TranslationResult(item.id,'translated',cached))
            if missing and len(self._pending)>=8:
                raise TranslationBusy('离线翻译忙碌，请稍后显示译文')
            identity = secrets.token_hex(16)
            job = {'job_id':identity,'status':'queued' if missing else 'ready',
                   'items':[asdict(r) for r in results],'reason':reason,'signature':signature,
                   'target':target,'source':items,'missing':tuple(missing)}
            self._jobs[identity] = job
            if missing:
                self._pending.append(identity)
                if self._thread is None:
                    self._thread = threading.Thread(target=self._work,daemon=True)
                    self._thread.start()
                self._wake.set()
            else:
                self._trim()
            return self._view(job)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return self._view(job) if job else None

    def cancel(self, job_id):
        with self._lock:
            job=self._jobs.get(job_id)
            if job is None:return None
            if job['status'] in ('queued','running'):
                job['status']='cancelled';job['reason']='翻译已取消，显示原文'
                self._pending=deque(key for key in self._pending if key!=job_id)
                if self._active_job==job_id and self._active_cancel:self._active_cancel.set()
            return self._view(job)

    @staticmethod
    def _verify(results, source):
        if not isinstance(results,(list,tuple)) or len(results)!=len(source):
            raise ValueError('Incomplete translation output')
        for result,item in zip(results,source):
            if not isinstance(result,TranslationResult) or result.id!=item.id or result.status not in ('translated','original'):
                raise ValueError('Invalid translation output')
            if any(not isinstance(p,TranslationPart) or p.kind not in ('text','literal')
                   or not isinstance(p.text,str) for p in result.parts):
                raise ValueError('Invalid translation parts')
            if result.status=='original' and result.parts!=item.parts:
                raise ValueError('Original changed')
            if result.status=='translated' and ([p.text for p in result.parts if p.kind=='literal'] !=
                                                [p.text for p in item.parts if p.kind=='literal']):
                raise ValueError('Literal changed')
        return results

    def _work(self):
        while True:
            self._wake.wait(.1)
            with self._lock:
                if self._closed: return
                if not self._pending:
                    self._wake.clear(); continue
                job = self._jobs[self._pending.popleft()]
                if job['status']=='cancelled':continue
                job['status'] = 'running'
                cancel = threading.Event()
                self._active_cancel = cancel
                self._active_job=job['job_id']
            box, finished = [], threading.Event()
            def invoke():
                try: box.append(self._verify(self.runner(job['target'],job['missing'],cancel),job['missing']))
                except Exception: box.append(None)
                finally: finished.set()
            running = threading.Thread(target=invoke,daemon=True)
            deadline = self.clock()+45
            with self._lock:
                if self._closed: return
                self._runner_thread=running
                running.start()
            while not finished.wait(.02):
                if self._closed or cancel.is_set() or self.clock()>=deadline:
                    cancel.set(); break
            with self._lock:
                if job['status']=='cancelled':pass
                elif self._closed or cancel.is_set() or self.clock()>=deadline:
                    job['status']='cancelled' if self._closed else 'error'
                    job['reason']='翻译已取消，显示原文' if self._closed else '翻译超时，显示原文'
                elif not box or box[0] is None:
                    job['status']='error'; job['reason']='离线翻译未完成，显示原文'
                else:
                    results = {r['id']:r for r in job['items']}
                    cache_available=True
                    for item,result in zip(job['missing'],box[0]):
                        results[result.id]=asdict(result)
                        if result.status in ('translated','original') and not result.reason and cache_available:
                            try:
                                self.store.save_translation(translation_cache_key(item,job['target']),result.parts,
                                                            datetime.now(timezone.utc).isoformat())
                            except Exception:
                                cache_available=False
                                job['reason']='本次译文已显示，缓存未保存'
                    job['items']=[results[i.id] for i in job['source']]
                    job['status']='ready'
                self._trim()
            # Even an uncooperative injected runner cannot overlap the next CPU job.
            while not finished.wait(.02):
                if self._closed: return
            with self._lock:self._active_cancel=None;self._active_job=None

    def close(self):
        deadline=time.monotonic()+1.5
        with self._lock:
            self._closed=True
            if self._active_cancel: self._active_cancel.set()
            for job in self._jobs.values():
                if job['status'] in ('queued','running'):
                    job['status']='cancelled'; job['reason']='翻译已取消，显示原文'
            self._pending.clear(); self._wake.set()
            runner_thread=self._runner_thread
        if self._thread:
            self._thread.join(timeout=max(0,deadline-time.monotonic()))
        if runner_thread:
            runner_thread.join(timeout=max(0,deadline-time.monotonic()))
