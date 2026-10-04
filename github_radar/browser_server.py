"""Private loopback pages and JSON for the local browser interface."""

import json
import os
import secrets
import sqlite3
import threading
from dataclasses import asdict, replace
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .ai_provider import AIOutputError, CodexProvider
from .ai_service import AIService
from .codex_connection import CodexConnection
from .daily_update import auto_update_due, run_scheduled_update, valid_update_time
from .diagnostic_log import record_error
from .github_client import GitHubRequestError
from .i18n import tr
from .interpretation import BasicInterpreter
from .models import Recommendation
from .presentation import card_text, saved_display_ranks
from .service import RadarService, RefreshResult
from .storage import RadarStore
from .update_lock import UpdateBusyError, update_lock
from .windows_scheduler import SchedulerError
from .github_account import GitHubAccount
from .github_client import GitHubClient
from .readme_service import ReadmeService
from .readme_types import ReadmeView
from .translation_service import TranslationService, TranslationBusy
from .translation_types import validate_request
from .project_translation import ProjectPretranslator
from .history_search import HistoryFilters, parse_history_query
from .oauth_config import publisher_client_id
from collections import Counter


_ASSETS = Path(__file__).resolve().parent / "web_assets"


class BrowserServer:
    """One session; callers launch the loopback listener and own its lifetime."""

    def __init__(self, service: RadarService, store: RadarStore,
                 ai_service: AIService | None = None,
                 connection: CodexConnection | None = None, scheduler=None, github_account=None, software_updater=None):
        self.service = service
        self.store = store
        from .software_update import SoftwareUpdater
        self.software_updater = software_updater or SoftwareUpdater(store.data_dir)
        self._check_software_on_start = software_updater is not None or isinstance(service.client, GitHubClient)
        self.github_account = github_account or GitHubAccount(store.data_dir,
                                  client_id=publisher_client_id())
        if isinstance(service.client,GitHubClient):
            service.client.token_provider=self.github_account.access_token
            service.client.on_auth_failure=self.github_account.reject_token
        self.connection = connection or CodexConnection()
        self.scheduler = scheduler
        self._schedule_error = ""
        self.ai_service = ai_service or AIService(service.client, store,
                                                  CodexProvider(self.connection))
        if isinstance(service.client,GitHubClient) and service.search_jobs is None:
            from .search_jobs import install_search
            install_search(service,self.connection)
        self.token = secrets.token_urlsafe(32)
        self.instance_id = secrets.token_hex(16)
        self._thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._ai_worker_thread: threading.Thread | None = None
        self.readme_service = ReadmeService(service.client, store)
        self.translation_service = TranslationService(store)
        self.project_pretranslator = ProjectPretranslator(store, self.readme_service, self.translation_service)
        self._readme_worker: threading.Thread | None = None
        self._readme_states: dict[int, ReadmeView] = {}
        self._ai_state: dict | None = None
        self._login_pending = False
        self._lock = threading.Lock()
        self._quitting = False
        self._last_result: RefreshResult | None = None
        self._stopped = threading.Event()
        self._closed = False
        server_owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                server_owner._handle_get(self)

            def do_POST(self):
                server_owner._handle_post(self)

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._http.daemon_threads = True
        self._origin = f"http://127.0.0.1:{self._http.server_port}"

    @property
    def url(self) -> str:
        return f"{self._origin}/#token={self.token}"

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Closed browser server cannot be restarted")
        if self._thread is None:
            self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
            self._thread.start()
            self._pretranslate_latest()
            if self._check_software_on_start:self.software_updater.check()

    def start_due_update(self, now: datetime | None = None) -> bool:
        """Catch up today's due update on startup without blocking page display."""
        now = now or datetime.now().astimezone()
        day = now.date().isoformat()
        with self._lock:
            if (self._quitting or (self._worker is not None and self._worker.is_alive())
                    or (self._readme_worker is not None and self._readme_worker.is_alive())
                    or (self._ai_worker_thread is not None
                        and self._ai_worker_thread.is_alive())):
                return False
            if not auto_update_due(now, self.store.load_auto_update_settings(),
                                   self.store.auto_attempts(day),
                                   self.store.latest_successful_date()):
                return False
            self._last_result = None
            self._worker = threading.Thread(target=self._scheduled_worker,
                                            args=(now,), daemon=True)
            self._worker.start()
            return True

    @property
    def closed(self) -> bool:
        return self._closed

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._stopped.wait(timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.software_updater.close()
        if getattr(self.service, "search_jobs", None) is not None:self.service.search_jobs.cancel()
        self.readme_service.cancel()
        self.project_pretranslator.close()
        self.translation_service.close()
        if self._ai_worker_thread is not None and self._ai_worker_thread.is_alive():
            self.ai_service.cancel()
        self.connection.cancel_login()
        self.github_account.cancel()
        if self._readme_worker is not None and self._readme_worker.is_alive():
            self._readme_worker.join()
        if self._thread is not None:
            self._http.shutdown()
            self._thread.join(timeout=3)
        self._http.server_close()
        self._stopped.set()

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.headers.get("Host") != self._origin.removeprefix("http://"):
            self._send(handler, 403, {"error": "forbidden"})
            return
        path = urlsplit(handler.path).path
        if path.startswith("/api/"):
            if not secrets.compare_digest(handler.headers.get("X-Radar-Token", ""), self.token):
                self._send(handler, 403, {"error": "forbidden"})
                return
            if path == "/api/software-update":
                self._send(handler, 200, self.software_updater.check())
                return
            if path == "/api/health":
                self._send(handler, 200, {
                    "status": "ok", "instance_id": self.instance_id, "pid": os.getpid(),
                })
                return
            if path.startswith('/api/translation/'):
                result = self.translation_service.get(path.removeprefix('/api/translation/'))
                self._send(handler,200 if result else 404,result or {'error':'not_found'})
                return
            if path.startswith('/api/search/keywords/') and path.endswith('/expansion'):
                self._search_expansion(handler,path);return
            if path == "/api/issue":
                self._send(handler, 200, self._issue_payload())
                return
            if path == "/api/ai/status":
                self._send(handler, 200, self._ai_status_payload())
                return
            if path == "/api/github/status":
                previous=self.github_account.status().state
                state=self.github_account.poll()
                if previous != "connected" and state.state == "connected" and isinstance(self.service.client,GitHubClient):
                    self.service.client.core_remaining=None;self.service.client.search_remaining=None
                self._send(handler,200,asdict(state))
                return
            if path.startswith("/api/repository/") and path.endswith("/sources"):
                repo_id=self._repo_id(path[len("/api/repository/"):-len("/sources")])
                params=parse_qs(urlsplit(handler.path).query)
                dates=params.get("date",[])
                day=self._local_date(dates[0]) if len(dates)==1 else None
                if repo_id is None or day is None:
                    self._send(handler,400,{"error":"日期或项目无效"})
                    return
                rec=self.store.recommendation_on(day,repo_id)
                if rec is None:
                    self._send(handler,404,{"error":"not_found"})
                    return
                checks=self.store.cross_checks(day,repo_id)
                sources=[asdict(e) for e in self.store.source_evidence(repo_id)
                         if e.observed_at[:10]<=day]
                self._send(handler,200,{"growth_rank":rec.rank,"display_role":rec.display_role,
                    "sources":sources,"checks":[asdict(c) for c in checks]})
                return
            if path == "/api/history":
                try:
                    filters,cursor=parse_history_query(urlsplit(handler.path).query,True)
                    result=self._history_index_payload(filters,cursor)
                except ValueError as exc:
                    self._send(handler,400,{'error':str(exc)});return
                self._send(handler,200,result)
                return
            if path == '/api/history/calendar':
                try:
                    query=parse_qs(urlsplit(handler.path).query,keep_blank_values=True,max_num_fields=2)
                    if set(query)!={'month'} or len(query['month'])!=1:raise ValueError('月份查询无效')
                    month=query['month'][0]
                    dates=[{'date':day,'count':count} for day,count in self.store.history_month(month)]
                except ValueError as exc:self._send(handler,400,{'error':str(exc)});return
                self._send(handler,200,{'month':month,'dates':dates})
                return
            if path.startswith("/api/history/"):
                day = self._local_date(path.removeprefix("/api/history/"))
                if day is None:
                    self._send(handler, 400, {"error": "日期无效"})
                else:
                    try:
                        filters,_=parse_history_query(urlsplit(handler.path).query,False)
                        result=self._history_payload(day,filters)
                    except ValueError as exc:
                        self._send(handler,400,{'error':str(exc)});return
                    self._send(handler,200,result)
                return
            if path in ('/api/folders','/api/following') or (path.startswith('/api/following/') and path.endswith('/folders')):
                try:
                    query=parse_qs(urlsplit(handler.path).query,keep_blank_values=True)
                    if path=='/api/folders':
                        if query:raise ValueError('分类查询无效')
                        result={'folders':[asdict(f) for f in self.store.list_follow_folders()]}
                    elif path=='/api/following':
                        if set(query)-{'folder','q'} or any(len(v)!=1 for v in query.values()):raise ValueError('关注分类查询无效')
                        folder=query.get('folder',['all'])[0];q=query.get('q',[''])[0]
                        if len(q)>200 or (q and folder!='all'):raise ValueError('关注搜索参数无效')
                        result=self._following_payload(folder,q)
                    else:
                        if query:raise ValueError('分类查询无效')
                        repo_id=self._repo_id(path[len('/api/following/'):-len('/folders')])
                        if repo_id is None:raise ValueError('项目ID无效')
                        result={'ids':self.store.follow_folder_ids(repo_id),
                                'folders':[asdict(f) for f in self.store.list_follow_folders()]}
                except LookupError as exc:self._send(handler,404,{'error':str(exc)});return
                except ValueError as exc:self._send(handler,400,{'error':str(exc)});return
                except (sqlite3.Error,OSError):self._send(handler,503,{'error':'分类暂时无法读取，请稍后重试'});return
                self._send(handler,200,result)
                return
            if path == "/api/settings":
                self._send(handler, 200, {"keywords": [
                    asdict(item) for item in self.store.list_keywords()
                ]})
                return
            if path == "/api/auto-update":
                self._send(handler, 200, self._auto_update_payload())
                return
            if path == "/api/preferences":
                language, font_size = self.store.load_preferences()
                _, font_scale = self.store.load_display_preferences()
                self._send(handler, 200, {"language": language, "font_size": font_size,
                                          "font_scale": font_scale,
                                          "motion_preference":self.store.load_motion_preference()})
                return
            if path.startswith('/api/readme/'):
                repo_id = self._repo_id(path.removeprefix('/api/readme/'))
                if repo_id is None or not self.store.repositories_for_ids([repo_id]):
                    self._send(handler, 404, {'error':'not_found'}); return
                with self._lock: view = self._readme_states.get(repo_id)
                cached = self.readme_service.cached(repo_id)
                if view is None or (view.status != 'fetching' and cached.document and
                        (not view.document or cached.document.content_hash != view.document.content_hash)):
                    view = cached
                self._send(handler, 200, asdict(view))
                return
            if path.startswith("/api/project/"):
                repo_id = self._repo_id(path.removeprefix("/api/project/"))
                query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True)
                dates = query.get("date", [])
                contexts=query.get('context',[])
                if set(query)-{'date','context'} or (dates and contexts) or any(len(values)!=1 or self._local_date(values[0]) is None for values in (dates,contexts) if values):
                    self._send(handler, 400, {"error": "日期无效"})
                    return
                project = self._project_payload(repo_id, dates[0] if dates else None, contexts[0] if contexts else None) \
                    if repo_id is not None else None
                self._send(handler, 200 if project else 404,
                           project if project else {"error": "not_found"})
                return
            self._send(handler, 404, {"error": "not_found"})
            return
        if path in ("/", "/history", "/following", "/settings") or \
                (path.startswith("/project/") and self._repo_id(path[9:]) is not None):
            self._send(handler, 200, (_ASSETS / "index.html").read_bytes(),
                       "text/html; charset=utf-8")
        elif path in ('/assets/forest-mist.png', '/assets/startrail.png'):
            self._send(handler, 200, (_ASSETS / path.rsplit('/', 1)[-1]).read_bytes(), 'image/png')
        elif path in ('/fonts/cormorant-garamond.ttf', '/fonts/noto-serif-sc.ttf'):
            self._send(handler,200,(_ASSETS / 'fonts' / path.rsplit('/',1)[-1]).read_bytes(),'font/ttf')
        elif path in ("/assets/app.css", "/assets/app.js", "/assets/i18n.js", "/assets/translation.js", "/assets/history_following.js", "/assets/history_calendar.js", "/assets/following_board.js", "/assets/detail_classification.js", "/assets/card_transition.js", "/assets/software_update_ui.js"):
            filename = path.rsplit("/", 1)[-1]
            content_type = "text/css" if filename.endswith(".css") else "text/javascript"
            self._send(handler, 200, (_ASSETS / filename).read_bytes(),
                       f"{content_type}; charset=utf-8")
        else:
            self._send(handler, 404, {"error": "not_found"})

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if (handler.headers.get("Host") != self._origin.removeprefix("http://")
                or handler.headers.get("Origin") != self._origin
                or not secrets.compare_digest(handler.headers.get("X-Radar-Token", ""), self.token)):
            # Closing a Windows socket with an unread small body can reset the
            # connection before the client sees 403. Discard bytes only: never
            # parse untrusted JSON or run an action. Missing bodies remain bounded.
            previous_timeout=handler.connection.gettimeout()
            try:
                length=int(handler.headers.get('Content-Length','0'))
                if 0<length<=8192:
                    handler.connection.settimeout(.05)
                    handler.rfile.read(length)
            except (OSError,ValueError):pass
            finally:handler.connection.settimeout(previous_timeout)
            self._send(handler, 403, {"error": "forbidden"})
            return
        path = urlsplit(handler.path).path
        if path == "/api/quit":
            self.readme_service.cancel()
            self.project_pretranslator.close()
            self.translation_service.close()
            with self._lock:
                self._quitting = True
                worker = self._worker
                ai_worker = self._ai_worker_thread
                readme_worker = self._readme_worker
            self.ai_service.cancel()
            if getattr(self.service,'search_jobs',None) is not None:self.service.search_jobs.cancel()
            self._send(handler, 202, {"status": "quitting"})
            threading.Thread(target=self._finish_quit,
                             args=(worker, ai_worker, readme_worker), daemon=True).start()
            return
        if path == "/api/scheduled-refresh":
            now = datetime.now().astimezone()
            day = now.date().isoformat()
            with self._lock:
                if self._quitting:
                    self._send(handler, 409, {"error": "正在退出"})
                    return
                if ((self._worker is not None and self._worker.is_alive())
                    or (self._readme_worker is not None and self._readme_worker.is_alive())):
                    self._send(handler, 409, {"error": "已有任务正在运行"})
                    return
                if self._ai_worker_thread is not None and self._ai_worker_thread.is_alive():
                    self._send(handler, 409, {"error": "AI 分析正在进行"})
                    return
                if not auto_update_due(now, self.store.load_auto_update_settings(),
                                       self.store.auto_attempts(day),
                                       self.store.latest_successful_date()):
                    self._send(handler, 200, {"status": "skipped"})
                    return
                self._last_result = None
                self._worker = threading.Thread(target=self._scheduled_worker,
                                                args=(now,), daemon=True)
                self._worker.start()
            self._send(handler, 202, {"status": "accepted"})
            return
        if path not in ("/api/refresh", "/api/keywords", "/api/preferences",
                        "/api/auto-update", "/api/translation", "/api/folders",
                        "/api/software-update/check", "/api/software-update/install") and not path.startswith(
                ("/api/ai/", "/api/following/", "/api/keywords/", "/api/github/", "/api/readme/", "/api/folders/", "/api/translation/", "/api/search/")):
            self._send(handler, 404, {"error": "not_found"})
            return
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length < 0 or length > (524288 if path == '/api/translation' else 8192):
                raise ValueError("请求内容太长")
            payload = json.loads(handler.rfile.read(length)) if length else {}
            if not isinstance(payload, dict):
                raise ValueError("请求内容无效")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send(handler, 400, {"error": "请求内容无效"})
            return
        if path.startswith('/api/software-update/'):
            self._software_update_action(handler,path,payload);return
        if path.startswith('/api/search/keywords/') and path.endswith('/expansion'):
            self._search_expansion(handler,path,payload);return
        if path.startswith('/api/translation/'):
            identity=path.removeprefix('/api/translation/').removesuffix('/cancel')
            if not path.endswith('/cancel') or len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):
                self._send(handler,404,{'error':'not_found'});return
            if payload:
                self._send(handler,400,{'error':'请求内容无效'});return
            result=self.translation_service.cancel(identity)
            self._send(handler,200 if result else 404,result or {'error':'not_found'})
            return
        if path == '/api/translation':
            try:
                target, items = validate_request(payload)
                result = self.translation_service.submit(target, items)
            except TranslationBusy as exc:
                self._send(handler,409,{'error':str(exc)}); return
            except ValueError as exc:
                self._send(handler,400,{'error':str(exc)}); return
            self._send(handler,200 if result['status']=='ready' else 202,result)
            return
        if path=='/api/folders' or path.startswith('/api/folders/') or (path.startswith('/api/following/') and path.endswith(('/folders','/classify'))):
            self._handle_folder_post(handler,path,payload);return
        if path.startswith('/api/readme/'):
            repo_id = self._repo_id(path.removeprefix('/api/readme/'))
            if repo_id is None or not self.store.repositories_for_ids([repo_id]):
                self._send(handler, 404, {'error':'not_found'}); return
            refresh = payload.get('refresh', False)
            if type(refresh) is not bool:
                self._send(handler, 400, {'error':'README 刷新参数无效'}); return
            with self._lock:
                if self._quitting or any(t is not None and t.is_alive() for t in
                        (self._worker, self._ai_worker_thread, self._readme_worker)):
                    self._send(handler, 409, {'error':'已有任务正在运行，请稍后读取 README。'}); return
                cached = self.readme_service.cached(repo_id)
                if cached.document and not refresh:
                    self._send(handler, 200, asdict(cached)); return
                self._readme_states[repo_id] = replace(cached, status='fetching', reason=None)
                self._readme_worker = threading.Thread(target=self._fetch_readme,
                    args=(repo_id, refresh), daemon=True)
                self._readme_worker.start()
            self._send(handler, 202, asdict(replace(cached, status='fetching', reason=None)))
            return
        if path.startswith("/api/github/"):
            try:
                if path == "/api/github/connect":
                    authorization=self.github_account.begin()
                    self._send(handler,200,asdict(authorization))
                elif path == "/api/github/cancel":
                    self.github_account.cancel()
                    self._send(handler,200,asdict(self.github_account.status()))
                elif path == "/api/github/disconnect":
                    self.github_account.disconnect()
                    if isinstance(self.service.client,GitHubClient):
                        self.service.client.core_remaining=None;self.service.client.search_remaining=None
                    self._send(handler,200,asdict(self.github_account.status()))
                else:
                    self._send(handler,404,{"error":"not_found"})
            except (ValueError,OSError):
                self._send(handler,400,{"error":"GitHub 账号连接不可用，请查看账号状态"})
            return
        if path == "/api/auto-update":
            enabled, time = payload.get("enabled"), payload.get("time")
            if set(payload) != {"enabled", "time"} or type(enabled) is not bool \
                    or not valid_update_time(time):
                self._send(handler, 400, {"error": "每日更新时间或开关无效"})
                return
            if self.scheduler is None:
                self._send(handler, 503, {"error": "当前版本尚未配置 Windows 定时任务"})
                return
            previous = self.store.load_auto_update_settings()
            try:
                self.scheduler.sync(enabled, time)
                self.store.save_auto_update_settings(enabled, time)
                self._schedule_error = ""
            except (SchedulerError, OSError, sqlite3.Error) as exc:
                self._schedule_error = str(exc)
                record_error(self.store.data_dir, "task-scheduler", str(exc))
                try:
                    self.scheduler.sync(previous.enabled, previous.time)
                except (SchedulerError, OSError):
                    pass
                self._send(handler, 503, {"error": "Windows 定时任务未能保存，请检查权限后重试"})
                return
            self._send(handler, 200, self._auto_update_payload())
            return
        if path == "/api/preferences":
            if set(payload) == {'motion_preference'}:
                try: self.store.save_motion_preference(payload['motion_preference'])
                except ValueError:
                    self._send(handler, 400, {'error':'动效选项无效'}); return
                self._send(handler, 200, {'motion_preference':payload['motion_preference']}); return
            language, font_size = payload.get("language"), payload.get("font_size")
            if "font_scale" in payload:
                try:
                    self.store.save_display_preferences(language, payload["font_scale"])
                except ValueError:
                    self._send(handler,400,{"error":"语言或字号倍率无效"})
                    return
                _, legacy = self.store.load_preferences()
                self._pretranslate_latest()
                self._send(handler,200,{"language":language,"font_size":legacy,
                                       "font_scale":payload["font_scale"]})
                return
            if language not in ("zh", "en") or font_size not in ("small", "normal", "large"):
                self._send(handler, 400, {"error": "语言或字号无效"})
                return
            self.store.save_preferences(language, font_size)
            self._pretranslate_latest()
            self._send(handler, 200, {"language": language, "font_size": font_size})
            return
        if path.startswith("/api/following/"):
            repo_id = self._repo_id(path.removeprefix("/api/following/"))
            if repo_id is None or repo_id not in self.store.repositories_for_ids([repo_id]):
                self._send(handler, 404, {"error": "项目不存在"})
                return
            if type(payload.get("followed")) is not bool:
                self._send(handler, 400, {"error": "关注状态无效"})
                return
            at = datetime.now().astimezone().isoformat(timespec="seconds")
            self._send(handler, 200, {"followed": self.store.set_followed(
                repo_id, payload["followed"], at)})
            return
        if path.startswith("/api/keywords/"):
            self._handle_keyword_settings_post(handler, path, payload)
            return
        if path.startswith("/api/ai/"):
            self._handle_ai_post(handler, path, payload)
            return
        if path == "/api/keywords":
            term = payload.get("term", "")
            min_stars = payload.get("min_stars", 1000)
            if not isinstance(term, str) or not term.strip():
                self._send(handler, 400, {"error": "请输入关键词"})
                return
            if type(min_stars) is not int or min_stars < 0:
                self._send(handler, 400, {"error": "最低 Star 必须是非负整数"})
                return
            with self._lock:
                if (self._quitting or (self._worker is not None and self._worker.is_alive())
                    or (self._readme_worker is not None and self._readme_worker.is_alive())
                        or (self._ai_worker_thread is not None
                            and self._ai_worker_thread.is_alive())):
                    self._send(handler, 409, {"error": "正在更新，请稍后再添加关键词"})
                    return
                normalized = " ".join(term.split())
                if any(item.term.casefold() == normalized.casefold() for item in self.store.list_keywords()):
                    self._send(handler, 409, {"error": "这个关键词已经添加过了"})
                    return
                keyword = self.store.add_keyword(normalized, min_stars)
                self._start_worker_locked()
            self._send(handler, 202, {"busy": True, "keyword": asdict(keyword)})
            return
        with self._lock:
            if self._quitting:
                self._send(handler, 409, {"error": "正在退出"})
                return
            if self._readme_worker is not None and self._readme_worker.is_alive():
                self._send(handler, 409, {"error": "正在读取 README，请稍后更新。"})
                return
            if self._worker is not None and self._worker.is_alive():
                self._send(handler, 202, {"busy": True, "already_running": True})
                return
            if self._ai_worker_thread is not None and self._ai_worker_thread.is_alive():
                self._send(handler, 409, {"error": "AI 分析正在进行，请稍后再更新"})
                return
            self._start_worker_locked()
        self._send(handler, 202, {"busy": True, "already_running": False})

    def _search_expansion(self,handler,path,payload=None):
        from .search_storage import SearchStore
        from .search_types import SearchScope,QueryExpansion
        from hashlib import sha256
        identity=path.removeprefix('/api/search/keywords/').removesuffix('/expansion')
        if not identity.isdecimal():self._send(handler,404,{'error':'not_found'});return
        rule=next((r for r in self.store.list_keywords() if r.id==int(identity)),None)
        if rule is None:self._send(handler,404,{'error':'not_found'});return
        scope=SearchScope('keyword',rule.id,date.today().isoformat(),None,rule.term,rule.min_stars,
            self.store.load_ai_active_model() or self.store.load_ai_model())
        search=SearchStore(self.store);old=search.load_expansion(scope)
        if payload is not None:
            terms=payload.get('terms')
            if set(payload)!={'terms'} or not isinstance(terms,list) or len(terms)>6 or any(not isinstance(t,str) or not 1<=len(t.strip())<=120 for t in terms):
                self._send(handler,400,{'error':'扩展词最多6个，每个1到120字'});return
            terms=tuple(dict.fromkeys(t.strip() for t in terms if t.strip()!=rule.term))
            old=QueryExpansion(rule.term,terms,old.topics if old else (),'1')
            search.save_expansion(scope,old)
        self._send(handler,200,asdict(old) if old else {'original':rule.term,'terms':[],'topics':[],'version':'1'})

    def _handle_keyword_settings_post(self, handler: BaseHTTPRequestHandler,
                                      path: str, payload: dict) -> None:
        parts = path.removeprefix("/api/keywords/").split("/")
        if len(parts) != 2 or parts[1] not in ("settings", "delete"):
            self._send(handler, 404, {"error": "not_found"})
            return
        keyword_id = self._repo_id(parts[0])
        if keyword_id is None or not any(
                item.id == keyword_id for item in self.store.list_keywords()):
            self._send(handler, 404, {"error": "关键词不存在"})
            return
        if parts[1] == "delete":
            at = datetime.now().astimezone().isoformat(timespec="seconds")
            self.store.soft_delete_keyword(keyword_id, at)
            self._send(handler, 200, {"deleted": True})
            return
        if set(payload) == {"enabled"} and type(payload["enabled"]) is bool:
            updated = self.store.set_keyword_enabled(keyword_id, payload["enabled"])
        elif set(payload) == {"min_stars"} and type(payload["min_stars"]) is int \
                and payload["min_stars"] >= 0:
            updated = self.store.set_keyword_min_stars(keyword_id, payload["min_stars"])
        else:
            self._send(handler, 400, {"error": "关键词设置无效"})
            return
        self._send(handler, 200, {"keyword": asdict(updated)})

    def _auto_update_payload(self) -> dict:
        settings = self.store.load_auto_update_settings()
        error = self._schedule_error
        registered = False
        if self.scheduler is not None:
            try:
                registered = self.scheduler.status(settings.enabled, settings.time)
            except SchedulerError as exc:
                error = str(exc)
        last_success_date = self.store.latest_successful_date()
        return {
            "enabled": settings.enabled, "time": settings.time,
            "registered": registered, "task_error": error,
            "last_success_date": last_success_date,
            "last_success_at": (self.store.daily_updated_at(last_success_date)
                                if last_success_date else None),
            "last_attempt": asdict(self.store.latest_auto_attempt()),
        }

    def _ai_status_payload(self) -> dict:
        state = self.connection.probe()
        models = self.connection.list_models() if state.ready else ()
        selected = self.store.load_ai_model()
        ready, reason = state.ready, state.reason
        if selected is not None and selected not in {item.id for item in models}:
            ready, reason = False, "所选模型已不可用，请重新选择或使用自动选择"
        if ready and self._login_pending:
            self.connection.cancel_login()
            self._login_pending = False
        return {"ready": ready, "reason": reason,
                "models": [asdict(item) for item in models],
                "selected_model": selected, "login_pending": self._login_pending}

    def _handle_ai_post(self, handler: BaseHTTPRequestHandler,
                        path: str, payload: dict) -> None:
        context_date=None
        if path == "/api/ai/login":
            try:
                login = self.connection.begin_login()
            except Exception:
                self._send(handler, 503, {"error": "Codex 登录无法启动，请检查安装或账号环境"})
                return
            self._login_pending = True
            self._send(handler, 200, {"auth_url": login.url, "pending": login.pending})
            return
        if path == "/api/ai/model":
            model_id = payload.get("model_id")
            if model_id is not None and (not isinstance(model_id, str)
                                         or model_id not in {
                                             item.id for item in self.connection.list_models()}):
                self._send(handler, 400, {"error": "所选模型目前不可用"})
                return
            with self._lock:
                if self._quitting or (self._ai_worker_thread is not None
                                      and self._ai_worker_thread.is_alive()):
                    self._send(handler, 409, {"error": "分析运行中，暂不能切换模型"})
                    return
                self.store.save_ai_model(model_id)
            self._send(handler, 200, {"selected_model": model_id})
            return
        kind, target = None, None
        force = False
        if path.startswith("/api/ai/keywords/") and path.endswith("/refine"):
            kind = "refine"
            target = self._repo_id(path[len("/api/ai/keywords/"):-len("/refine")])
            if target is None or not any(rule.id == target and rule.enabled
                                         for rule in self.store.list_keywords()):
                self._send(handler, 404, {"error": "关键词不存在"})
                return
        elif path.startswith("/api/ai/projects/") and path.endswith("/explain"):
            kind = "explain"
            force = payload.get("force", False)
            if not isinstance(force, bool):
                self._send(handler, 400, {"error": "重新生成参数无效"})
                return
            target = self._repo_id(path[len("/api/ai/projects/"):-len("/explain")])
            context_date=payload.get('context_date')
            if context_date is not None and self._local_date(context_date) is None:
                self._send(handler,400,{'error':'日期无效'});return
            item=(self.store.recommendation_on(context_date,target) if context_date
                  else self.store.latest_recommendation_for_repo(target)) if target is not None else None
            if target is None or (item is None and (context_date or not self.store.is_followed(target))):
                self._send(handler, 404, {"error": "项目不存在"})
                return
            if item and item.keyword_id is not None and not any(
                    rule.id == item.keyword_id and rule.enabled
                    for rule in self.store.list_keywords()):
                self._send(handler, 409, {"error": "关键词已停用，不能生成新的 AI 解读"})
                return
        else:
            self._send(handler, 404, {"error": "not_found"})
            return
        with self._lock:
            if self._quitting:
                self._send(handler, 409, {"error": "正在退出"})
                return
            if self._ai_worker_thread is not None and self._ai_worker_thread.is_alive():
                same = (self._ai_state is not None and self._ai_state["kind"] == kind
                        and self._ai_state["target_id"] == target)
                self._send(handler, 202 if same else 409,
                           {"busy": True, "already_running": same} if same else
                           {"error": "另一项 AI 分析正在进行"})
                return
            if ((self._worker is not None and self._worker.is_alive())
                    or (self._readme_worker is not None and self._readme_worker.is_alive())):
                self._send(handler, 409, {"error": "数据更新中，请稍后开始 AI 分析"})
                return
        status = self._ai_status_payload()
        if not status["ready"]:
            self._send(handler, 503, {"error": status["reason"]})
            return
        if kind == "refine":
            day = self.store.latest_successful_date()
            if day is None or ("keyword", target) not in self.store.daily_sections(day):
                self._send(handler, 409, {"error": "请先更新这个关键词的数据"})
                return
        else:
            day = self.store.latest_successful_date()
        selected_model = self.store.load_ai_model()
        model_id = selected_model
        if model_id is None:
            available = status["models"]
            default = next((item for item in available if item["is_default"]),
                           available[0] if available else None)
            if default is None:
                self._send(handler, 503, {"error": "Codex 当前没有可用的文本模型"})
                return
            model_id = default["id"]
        with self._lock:
            if self._quitting or (self._ai_worker_thread is not None
                                  and self._ai_worker_thread.is_alive()):
                self._send(handler, 409, {"error": "AI 分析正在进行"})
                return
            if ((self._worker is not None and self._worker.is_alive())
                    or (self._readme_worker is not None and self._readme_worker.is_alive())):
                self._send(handler, 409, {"error": "数据更新中，请稍后开始 AI 分析"})
                return
            self._ai_state = {"kind": kind, "target_id": target,
                              "status": "running", "message": "AI 分析中"}
            self._ai_worker_thread = threading.Thread(
                target=self._run_ai_job,
                args=(kind, target, day, model_id, force, selected_model is None, context_date),
                daemon=True)
            self._ai_worker_thread.start()
        self._send(handler, 202, {"busy": True, "already_running": False})

    def _run_ai_job(self, kind: str, target: int, day: str,
                    model_id: str | None, force: bool = False,
                    auto_selected: bool = False, context_date: str | None = None) -> None:
        checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            search_jobs=getattr(self.service,'search_jobs',None)
            if kind=='refine' and search_jobs is not None:
                search_jobs.continue_keyword(target,day,model_id=model_id)
                if auto_selected:self.store.save_ai_active_model(model_id)
            else:
              with update_lock(self.store.data_dir):
                if kind == "refine":
                    self.ai_service.refine_keyword(day, target, model_id, checked_at)
                else:
                    self.ai_service.explain_project(target, model_id, checked_at,
                                                    force=force, **({'context_date':context_date} if context_date else {}))
                if auto_selected:
                    with self._lock:
                        if self._quitting:
                            raise RuntimeError("AI 分析已取消")
                        self.store.save_ai_active_model(model_id)
            status, message = "done", "AI 分析已保存"
            if kind == 'explain': self.project_pretranslator.enqueue([target])
            else: self._pretranslate_latest()
        except UpdateBusyError:
            status, message = "error", "已有任务正在运行，请稍后再试"
        except (AIOutputError, GitHubRequestError, ValueError) as exc:
            status, message = "error", str(exc)
        except Exception:
            status, message = "error", "AI 分析失败，已保留原有结果"
        with self._lock:
            self._ai_state = {"kind": kind, "target_id": target,
                              "status": status, "message": message}
            self._last_result = None

    def _start_worker_locked(self) -> None:
        self._last_result = None
        self._worker = threading.Thread(target=self._refresh_worker, daemon=True)
        self._worker.start()

    def _refresh_worker(self) -> None:
        observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        today = date.today().isoformat()
        try:
            if getattr(self.service,'search_jobs',None) is not None:
                result = self.service.refresh(today, observed_at)
            else:
                with update_lock(self.store.data_dir):
                    result = self.service.refresh(today, observed_at)
        except UpdateBusyError:
            result = replace(self.service.load_latest(today), status="error", stale=True,
                             message="已有任务正在运行，请稍后再试")
        except Exception as exc:
            self.store.save_refresh_failure(observed_at, f"更新失败：{exc}")
            result = self.service.load_latest(today)
        with self._lock:
            self._last_result = result
        if result.status=='error':record_error(self.store.data_dir,'manual-update',result.message+'; '+'; '.join(result.notes))
        if result.status == 'ok': self._pretranslate_latest()

    def _pretranslate_latest(self):
        try:
            self.project_pretranslator.enqueue(dict.fromkeys(r.repo_id for r in self.store.latest_recommendations()))
        except Exception as exc:
            record_error(self.store.data_dir, 'pretranslation', str(exc))

    def _scheduled_worker(self, now: datetime) -> None:
        run_scheduled_update(self.store, self.service, now)
        result = self.service.load_latest(now.date().isoformat())
        with self._lock:
            self._last_result = result
        if result.status in ('ok', 'cached'): self._pretranslate_latest()

    def _fetch_readme(self, repo_id, refresh):
        try:
            view = self.readme_service.load(repo_id, refresh)
        except Exception:
            view = replace(self.readme_service.cached(repo_id), status='error',
                           reason='README 读取未完成，请稍后重试。')
        with self._lock: self._readme_states[repo_id] = view
        if view.document: self.project_pretranslator.enqueue([repo_id])

    def _software_update_action(self, handler, path, payload):
        """Only the trusted release client selects a URL; webpages cannot supply one."""
        if path.endswith('/check'):
            if payload:self._send(handler,400,{'error':'请求内容无效'});return
            self._send(handler,202,self.software_updater.check(force=True));return
        if payload != {'confirmed':True} or type(payload.get('confirmed')) is not bool:
            self._send(handler,400,{'error':'请确认更新将退出软件，并由安装程序备份和保留原数据'});return
        with self._lock:
            if self._quitting or any(t and t.is_alive() for t in (self._worker,self._ai_worker_thread,self._readme_worker)):
                self._send(handler,409,{'error':'请等待当前更新或分析完成后再升级软件'});return
            try:state=self.software_updater.start(self._software_ready)
            except ValueError as exc:self._send(handler,400,{'error':str(exc)});return
        self._send(handler,202,state)

    def _software_ready(self, target, release):
        from .software_update import launch_installer
        # A launch failure keeps the running reader and its data available.
        with self._lock:
            if self._quitting:raise ValueError('软件正在退出，请下次重新更新')
            launch_installer(target,release,self.store.data_dir)
            self._quitting=True
            workers=(self._worker,self._ai_worker_thread,self._readme_worker)
        self.ai_service.cancel()
        if getattr(self.service,'search_jobs',None) is not None:self.service.search_jobs.cancel()
        self.project_pretranslator.close()
        self.translation_service.close()
        threading.Thread(target=self._finish_quit,args=workers,daemon=True).start()

    def _finish_quit(self, worker: threading.Thread | None,
                     ai_worker: threading.Thread | None,
                     readme_worker: threading.Thread | None = None) -> None:
        self.readme_service.cancel()
        if worker is not None:
            worker.join()
        if ai_worker is not None:
            ai_worker.join()
        if readme_worker is not None:
            readme_worker.join()
        self.close()

    @staticmethod
    def _repo_id(value: str) -> int | None:
        return int(value) if value.isascii() and value.isdecimal() and int(value) > 0 else None

    @staticmethod
    def _local_date(value: str) -> str | None:
        try:
            return value if date.fromisoformat(value).isoformat() == value else None
        except (ValueError,TypeError):
            return None

    def _history_index_payload(self,filters,cursor):
        records=self.store.search_history(filters)
        counts=Counter(r.local_date for r in records)
        groups=[{'date':day,'count':count} for day,count in counts.items()]
        known=set()
        from .history_search import HistoryFilters
        for item in self.store.search_history(HistoryFilters()):
            if item.keyword_id:known.add(item.keyword_id)
            known.update(item.matched_keyword_ids)
        sources=[{'value':'all','label':'全部来源'},{'value':'growth','label':'Star增长'}]
        sources += [{'value':f'keyword:{rule.id}','label':rule.term} for rule in self.store.all_keywords() if rule.id in known]
        return {'dates':groups[cursor:cursor+30],
                'next_cursor':cursor+30 if cursor+30<len(groups) else None,
                'matched_total':len(records),'history_total':sum(count for _,count in self.store.history_dates()),
                'sources':sources,'filters':{'q':filters.q,'from':filters.from_date,'to':filters.to_date,'source':filters.source}}

    def _handle_folder_post(self,handler,path,payload):
        try:
            with self._lock:
                if self._quitting:
                    self._send(handler,409,{'error':'正在退出'});return
            if path=='/api/folders':
                if set(payload)!={'name'}:raise ValueError('文件夹参数无效')
                folder=self.store.create_follow_folder(payload['name'],datetime.now().astimezone().isoformat(timespec='seconds'))
                result={'folder':asdict(folder)}
            elif path=='/api/folders/order':
                if set(payload)!={'ids'}:raise ValueError('分类排序参数无效')
                self.store.reorder_follow_folders(payload['ids']);result={'ordered':True}
            elif path.startswith('/api/folders/'):
                identity=self._repo_id(path.removeprefix('/api/folders/'))
                if identity is None:raise ValueError('分类ID无效')
                if payload.get('action')=='rename' and set(payload)=={'action','name'}:
                    result={'folder':asdict(self.store.rename_follow_folder(identity,payload['name']))}
                elif payload.get('action')=='delete' and set(payload)=={'action'}:
                    self.store.delete_follow_folder(identity);result={'deleted':True}
                else:raise ValueError('分类操作无效')
            elif path.endswith('/classify'):
                repo_id=self._repo_id(path[len('/api/following/'):-len('/classify')])
                if repo_id is None or set(payload)!={'ids','create_name'}:raise ValueError('归类参数无效')
                result=self.store.classify_followed_project(repo_id,payload['ids'],payload['create_name'],
                    datetime.now().astimezone().isoformat(timespec='seconds'))
            else:
                repo_id=self._repo_id(path[len('/api/following/'):-len('/folders')])
                if repo_id is None:raise ValueError('项目分类参数无效')
                if set(payload)=={'action','folder_id'} and payload['action']=='move_unfiled':
                    self.store.move_unfiled_to_folder(repo_id,payload['folder_id'])
                elif set(payload)=={'action','source','target'} and payload['action']=='move':
                    self.store.move_followed_project(repo_id,payload['source'],payload['target'])
                elif set(payload)=={'ids'}:self.store.set_follow_folders(repo_id,payload['ids'])
                else:raise ValueError('项目分类参数无效')
                result={'ids':self.store.follow_folder_ids(repo_id)}
        except LookupError as exc:self._send(handler,404,{'error':str(exc)});return
        except ValueError as exc:self._send(handler,400,{'error':str(exc)});return
        except (sqlite3.Error,OSError):self._send(handler,503,{'error':'分类未能保存，请稍后重试'});return
        self._send(handler,200,result)

    def _history_payload(self, day: str, filters=None) -> dict:
        language = self.store.load_preferences()[0]
        records = self.store.daily_recommendations(day)
        ranks = saved_display_ranks(records)
        if filters is not None:
            if (filters.from_date and filters.from_date>day) or (filters.to_date and filters.to_date<day):records=[]
            else:records=self.store.search_history(replace(filters,from_date=day,to_date=day))
        repositories = self.store.repositories_for_ids([item.repo_id for item in records])
        snapshots = self.store.snapshots_on(day, [item.repo_id for item in records])
        rules = self.store.all_keywords()
        terms = {item.id: item.term for item in rules}
        sections = {"growth": [], "keyword": []}
        keyword_groups: dict[int, list[dict]] = {}
        for item in records:
            repository = repositories.get(item.repo_id)
            if repository is None:
                continue
            snapshot = snapshots.get(item.repo_id)
            shown = replace(repository, stars=snapshot.stars) if snapshot else repository
            card = asdict(card_text(item, shown, terms, rank=ranks.get(item.repo_id),
                                    keyword_rules=tuple(rules), language=language))
            card.update(repo_id=item.repo_id, history_date=day)
            if snapshot is None:
                card["stars"] = tr(language, "当时 Star 未记录")
            sections[item.section].append(card)
            if item.section == "keyword":
                keyword_groups.setdefault(item.keyword_id or 0, []).append(card)
        coverage = self.store.growth_coverage(day)
        return {"date": day, "sections": [
            {"id": section, "cards": sections[section]}
            for section in ("growth", "keyword")],
            "keyword_groups": [
                {"term": terms.get(key, tr(language, "已保存关键词")), "cards": cards}
                for key, cards in keyword_groups.items()],
            "coverage": asdict(coverage) if coverage else None}

    def _following_payload(self,folder='all',q='') -> dict:
        language = self.store.load_preferences()[0]
        cards = []
        terms = {item.id: item.term for item in self.store.all_keywords()}
        folders={f.id:asdict(f) for f in self.store.list_follow_folders()}
        for repository, followed_at, snapshot in self.store.followed_repositories(folder):
            if q.strip().casefold() not in (repository.full_name+'\n'+repository.description).casefold():continue
            try:ids=self.store.follow_folder_ids(repository.id)
            except LookupError:continue  # It was concurrently unfollowed.
            item = self.store.latest_recommendation_for_repo(repository.id)
            if item is None:
                item = Recommendation(repository.id, snapshot.local_date if snapshot else "",
                                      "following", None, None, None,
                                      snapshot.observed_at if snapshot else followed_at)
            shown = replace(repository, stars=snapshot.stars) if snapshot else repository
            ranks = saved_display_ranks(self.store.daily_recommendations(item.local_date))
            card = asdict(card_text(item, shown, terms, rank=ranks.get(item.repo_id), language=language))
            card.update(repo_id=repository.id, followed_at=followed_at,
                        saved_at=snapshot.observed_at if snapshot else None,
                        folders=[folders[i] for i in ids if i in folders])
            cards.append(card)
        return {'cards':cards,'folders':list(folders.values()),'folder':folder,
                'all_count':len(self.store.followed_repositories()),'unfiled_count':len(self.store.followed_repositories('unfiled'))}

    def _issue_payload(self) -> dict:
        language = self.store.load_preferences()[0]
        with self._lock:
            result = self._last_result
            busy = self._worker is not None and self._worker.is_alive()
            quitting = self._quitting
            ai_state = dict(self._ai_state) if self._ai_state else None
        saved_date = self.store.latest_successful_date()
        saved_at = self.store.daily_updated_at(saved_date) if saved_date else None
        failure = self.store.latest_refresh_failure()
        cache_changed = (result is not None and (
            result.local_date != (saved_date or date.today().isoformat())
            or result.updated_at != saved_at
            or (failure is not None and
                (result.status != "error" or result.message != failure[1]))))
        if result is None or busy or cache_changed:
            result = self.service.load_latest(date.today().isoformat())
        keyword_rules = self.store.list_keywords()
        keyword_terms = {item.id: item.term for item in keyword_rules}
        growth_ranks = saved_display_ranks(list(result.recommendations))
        selected_model = self.store.load_ai_model() or self.store.load_ai_active_model()
        verdicts = {rule.id: self.store.ai_verdicts(result.local_date, rule.id, selected_model)
                    for rule in keyword_rules}
        from .search_storage import SearchStore
        search_store=SearchStore(self.store)
        search_progress=search_store.current_progress(date.today().isoformat())
        for rule in keyword_rules:
            verdicts[rule.id].update(search_store.keyword_verdicts(rule.id,selected_model,{r.repo_id for r in result.recommendations if r.keyword_id==rule.id}))
        sections = []
        keyword_cards: dict[int, list[dict]] = {rule.id: [] for rule in keyword_rules}
        for section_id, title in (("growth", "Star 增长"), ("keyword", "关键词推荐")):
            cards = []
            for recommendation in result.recommendations:
                if recommendation.section != section_id:
                    continue
                if section_id == "keyword" and recommendation.keyword_id not in keyword_terms:
                    continue
                repository = result.repositories.get(recommendation.repo_id)
                if repository is None:
                    continue
                card = asdict(card_text(
                    recommendation, repository, keyword_terms,
                    rank=growth_ranks.get(repository.id), keyword_rules=tuple(keyword_rules),
                    language=language,
                ))
                card["repo_id"] = repository.id
                if section_id == "keyword":
                    verdict = verdicts.get(recommendation.keyword_id or 0, {}).get(repository.id)
                    card["ai_status"] = verdict.verdict if verdict else "basic"
                    card["ai_reason"] = verdict.reason if verdict else ""
                    keyword_cards.setdefault(recommendation.keyword_id or 0, []).append(card)
                cards.append(card)
            sections.append({"id": section_id, "title": tr(language, title), "cards": cards})
        keyword_groups = []
        for rule in keyword_rules:
            progress = self.store.ai_progress(result.local_date, rule.id, selected_model)
            group_job = (ai_state if ai_state and ai_state["kind"] == "refine"
                         and ai_state["target_id"] == rule.id else None)
            current_search=next((p for p in search_progress if p.keyword_id==rule.id and p.section=='keyword'),None)
            keyword_groups.append({
                "keyword_id": rule.id, "term": rule.term,
                "cards": keyword_cards.get(rule.id, []),
                "checked_count": current_search.newly_checked+current_search.cache_hits if current_search else progress.checked_count,
                "search_progress": asdict(current_search) if current_search else None,
                "ai_status": group_job["status"] if group_job else
                             "checked" if progress.checked_count else "idle",
                "ai_message": tr(language, group_job["message"]) if group_job else "",
            })
        return {
            "local_date": result.local_date,
            "updated_at": result.updated_at,
            "status": "quitting" if quitting else "updating" if busy else result.status,
            "busy": busy,
            "stale": result.stale,
            "message": tr(language, "正在结束更新" if quitting else
                          "正在更新，已保存项目仍可浏览" if busy else result.message),
            "notes": [tr(language, note) for note in result.notes],
            "coverage": asdict(result.growth_coverage) if result.growth_coverage else None,
            "keywords": [asdict(item) for item in keyword_rules],
            "sections": sections,
            "keyword_groups": keyword_groups,
            "search_progress": [asdict(p) for p in search_progress],
            "ai_busy": ai_state is not None and ai_state["status"] == "running",
        }

    def _project_payload(self, repo_id: int, history_date: str | None = None, context_date: str | None = None) -> dict | None:
        language = self.store.load_preferences()[0]
        source_date=history_date or context_date
        recommendation = (self.store.recommendation_on(source_date, repo_id)
                          if source_date else self.store.latest_recommendation_for_repo(repo_id))
        repository = self.store.repositories_for_ids([repo_id]).get(repo_id)
        if repository is None:
            return None
        if recommendation is None and source_date is None:
            followed_at = self.store.followed_at(repo_id)
            if followed_at is not None:
                recommendation = Recommendation(repo_id, "", "following", None, None,
                                                None, followed_at)
        if recommendation is None:
            return None
        snapshot = (self.store.snapshots_on(source_date, [repo_id])
                    if source_date else self.store.snapshots_before("9999-12-31", [repo_id])).get(repo_id)
        shown = replace(repository, stars=snapshot.stars) if snapshot else repository
        keyword_rules = self.store.all_keywords()
        keyword_terms = {item.id: item.term for item in keyword_rules}
        growth_ranks = saved_display_ranks(self.store.daily_recommendations(recommendation.local_date))
        words = card_text(
            recommendation, shown, keyword_terms,
            rank=growth_ranks.get(repo_id), keyword_rules=tuple(keyword_rules),
            language=language,
        )
        term = (keyword_terms.get(recommendation.keyword_id)
                if recommendation.keyword_id else
                words.matched_keywords[0] if words.matched_keywords else None)
        insight = BasicInterpreter().interpret(repository, term)
        insight_text = asdict(insight)
        insight_text["summary"] = tr(language, insight_text["summary"])
        insight_text["relevance"] = tr(language, insight_text["relevance"])
        for evidence in insight_text["evidence"]:
            evidence["fields"] = [tr(language, field) for field in evidence["fields"]]
        selected_model = self.store.load_ai_model() or self.store.load_ai_active_model()
        saved = self.store.latest_explanation(repo_id, recommendation.keyword_id,
                                              selected_model)
        saved_model_id = selected_model
        if saved is None:
            previous = self.store.latest_explanation_any_model(
                repo_id, recommendation.keyword_id)
            if previous is not None:
                saved = previous[:2]
                saved_model_id = previous[2]
        if saved is None and recommendation.section == "following":
            previous = self.store.latest_explanation_for_repo(repo_id, selected_model)
            if previous is not None:
                saved = previous[:2]
                saved_model_id = previous[2]
        with self._lock:
            ai_state = dict(self._ai_state) if self._ai_state else None
        job = ai_state if ai_state and ai_state["kind"] == "explain" \
            and ai_state["target_id"] == repo_id else None
        try:document=self.store.load_readme(repo_id)
        except (ValueError,TypeError,OSError,sqlite3.Error):document=None
        stale=bool(saved and (saved[0].schema_version<3 or
                   (document and saved[0].readme_hash!=document.content_hash)))
        return {
            "repo_id": repo_id,
            "history_date": history_date,
            "local_date": recommendation.local_date,
            "followed": self.store.is_followed(repo_id),
            "saved_at": snapshot.observed_at if snapshot else None,
            "url": repository.html_url,
            "language": repository.language,
            **asdict(words),
            "stars": words.stars if not history_date or snapshot else tr(language, "当时 Star 未记录"),
            "insight": insight_text,
            "ai_explanation": ({**asdict(saved[0]), "saved_at": saved[1],
                                "model_id": saved_model_id} if saved else None),
            "ai_job": job,
            "analysis_stale":stale,
        }

    @staticmethod
    def _send(handler: BaseHTTPRequestHandler, status: int, payload, content_type=None) -> None:
        if isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", content_type or "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        handler.end_headers()
        handler.wfile.write(body)
