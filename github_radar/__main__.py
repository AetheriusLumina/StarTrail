"""Browser entry point with two small data-debugging commands."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from .install_paths import default_data_dir, install_dir as installation_dir



def _print_message(message):
    """Feedback must not turn a handled error into a locale encoding crash."""
    if sys.stdout is None:
        return
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, 'encoding', None) or 'ascii'
        print(str(message).encode(encoding, errors='backslashreplace').decode(encoding))


def _scheduler_for(store: RadarStore):
    from .windows_scheduler import SchedulerController

    if getattr(sys, "frozen", False):
        command = Path(sys.executable).resolve()
        root = command.parent
        arguments = ("--scheduled-refresh", "--data-dir", str(store.data_dir))
    else:
        root = Path(__file__).resolve().parent.parent
        command = Path(sys.executable).with_name("pythonw.exe")
        if not command.exists():
            command = Path(sys.executable)
        arguments = ("-m", "github_radar", "--scheduled-refresh",
                     "--data-dir", str(store.data_dir))
    return SchedulerController(root, command, arguments, root, binding_store=store)


def _main(
    argv: list[str] | None = None,
    *,
    client: GitHubClient | None = None,
    local_date: str | None = None,
    observed_at: str | None = None,
    launcher=None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == '--translation-worker':
        if len(arguments) != 3:
            return 1
        from .translation_worker import run_worker
        return run_worker(Path(arguments[1]), Path(arguments[2]))
    from .github_client import GitHubClient
    from .presentation import card_text
    from .service import RadarService
    from .storage import RadarStore
    from .trending import TrendingClient

    parser = argparse.ArgumentParser(description="StarTrail")
    parser.add_argument(
        "--data-dir", type=Path,
        default=default_data_dir(Path(__file__), Path(sys.executable),
                                 getattr(sys, "frozen", False)),
        help="个人数据目录",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--add-keyword", metavar="TEXT", help="新增关注关键词")
    action.add_argument("--refresh", action="store_true", help="从 GitHub 更新")
    action.add_argument("--scheduled-refresh", action="store_true", help="Windows 每日无界面更新")
    action.add_argument("--prepare-uninstall", action="store_true", help="卸载前正常退出并移除每日任务")
    action.add_argument("--sync-scheduler", action="store_true", help="安装后恢复每日更新任务")
    args = parser.parse_args(arguments)

    if args.prepare_uninstall:
        from .diagnostic_log import record_error
        from .uninstall import UninstallError, prepare_uninstall

        try:
            prepare_uninstall(installation_dir(Path(sys.executable)), args.data_dir)
        except UninstallError as exc:
            message = str(exc)
            record_error(args.data_dir, "uninstall", message)
            try:
                (args.data_dir / "uninstall-error.txt").write_text(
                    message, encoding="utf-8")
            except OSError:
                pass
            _print_message(message)
            return 1
        (args.data_dir / "uninstall-error.txt").unlink(missing_ok=True)
        return 0

    if launcher is None and not args.refresh and args.add_keyword is None and not args.sync_scheduler:
        from .browser_launcher import handoff_existing
        if handoff_existing(args.data_dir, scheduled=args.scheduled_refresh):
            return 0
    import sqlite3
    try:
        store = RadarStore(args.data_dir)
    except sqlite3.OperationalError as exc:
        from .diagnostic_log import record_error
        message = ('本地数据库正被其他任务占用，请等待当前任务结束后再打开；原数据已保留。'
                   if 'locked' in str(exc).lower() or 'busy' in str(exc).lower()
                   else '无法打开本地数据库，请检查磁盘和目录权限；原数据已保留。')
        record_error(args.data_dir, 'startup-database', str(exc))
        _print_message(message)
        if getattr(sys, 'frozen', False) and not any((args.scheduled_refresh,args.refresh,args.sync_scheduler,args.add_keyword is not None)):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,message,'StarTrail',0x40)
        return 1
    if args.sync_scheduler:
        from .diagnostic_log import record_error
        from .windows_scheduler import SchedulerError

        try:
            settings = store.load_auto_update_settings()
            _scheduler_for(store).sync(settings.enabled, settings.time)
        except SchedulerError as exc:
            record_error(store.data_dir, "task-scheduler", str(exc))
            _print_message(f"每日更新任务未能注册：{exc}")
            return 1
        return 0

    if args.add_keyword is not None:
        rule = store.add_keyword(args.add_keyword)
        _print_message(f"已关注：{rule.term}（最低 {rule.min_stars} Star）")
        return 0

    from .github_account import GitHubAccount
    from .oauth_config import publisher_client_id
    account = GitHubAccount(store.data_dir, client_id=publisher_client_id())
    service = RadarService(client or GitHubClient(token_provider=account.access_token,on_auth_failure=account.reject_token,request_concurrency=25), store,
                           TrendingClient() if client is None else None)
    if client is None:
        from .search_jobs import install_search
        install_search(service)
    if args.scheduled_refresh:
        from .daily_update import run_scheduled_update
        from .browser_launcher import handoff_scheduled_refresh

        handed_off = handoff_scheduled_refresh(store)
        state = (handed_off if handed_off is not None else
                 run_scheduled_update(store, service, datetime.now().astimezone()))
        if state == 'success' and handed_off is None:
            from .project_translation import warm_selected
            warm_selected(store, service.client)
        return 0 if state in {"success", "skipped", "accepted"} else 1
    if not args.refresh:
        from .ai_provider import CodexProvider
        from .ai_service import AIService
        from .browser_launcher import launch_browser_app
        from .codex_connection import CodexConnection

        if launcher is not None:
            return launcher(service, store)
        connection = CodexConnection()
        ai_service = AIService(service.client, store, CodexProvider(connection))
        scheduler = _scheduler_for(store)
        return launch_browser_app(service, store,
                                  ai_service=ai_service, connection=connection,
                                  scheduler=scheduler)

    from .update_lock import UpdateBusyError, update_lock

    now = datetime.now().astimezone()
    try:
        with update_lock(store.data_dir):
            result = service.refresh(
                local_date or now.date().isoformat(),
                observed_at or now.isoformat(timespec="seconds"),
            )
    except UpdateBusyError:
        _print_message("已有更新或 AI 任务正在运行，请稍后再试")
        return 1
    _print_message(result.message)
    if result.updated_at:
        _print_message(f"数据时间：{result.updated_at}")
    if result.stale:
        _print_message("当前显示上次保存的结果")
    for note in result.notes:
        _print_message(note)
    if result.growth_coverage is not None:
        coverage = result.growth_coverage
        _print_message(f"增长候选：发现 {coverage.candidate_count}，成功核算 {coverage.scored_count}；"
              f"口径 {coverage.metric_basis}；统计日 {coverage.stat_date or '暂无'}；"
              f"来源 {', '.join(coverage.source_names) or '暂无'}")
    for item in result.recommendations:
        repo = result.repositories.get(item.repo_id)
        if repo is None:
            continue
        words = card_text(item, repo, {})
        growth = f"  {words.growth}" if words.growth else ""
        observation = f"  {words.observation}" if words.observation else ""
        _print_message(f"[{item.section}] {repo.full_name}  Star {repo.stars}{growth}{observation}")
    if result.status == 'ok':
        from .project_translation import warm_selected
        warm_selected(store, service.client)
    return 0 if result.status == "ok" else 1


def main(argv=None, *, client=None, local_date=None, observed_at=None, launcher=None) -> int:
    arguments=list(sys.argv[1:] if argv is None else argv)
    options=dict(client=client,local_date=local_date,observed_at=observed_at,launcher=launcher)
    # Maintenance helpers run under Inno's exclusive reservation; translation
    # children are owned by an already admitted parent, which quit cancels/joins.
    bypass=('--prepare-uninstall' in arguments or
            bool(arguments and arguments[0]=='--translation-worker'))
    if not getattr(sys,'frozen',False) or bypass:
        return _main(arguments,**options)
    from .maintenance import MaintenanceBusyError,runtime_access
    try:
        with runtime_access(installation_dir(Path(sys.executable))):
            return _main(arguments,**options)
    except MaintenanceBusyError as exc:
        message=str(exc)
        _print_message(message)
        headless=any(arg.startswith(('--scheduled-refresh','--refresh','--sync-scheduler',
                                     '--add-keyword','--help','-h')) for arg in arguments)
        if not headless:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,message,'StarTrail',0x40)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
