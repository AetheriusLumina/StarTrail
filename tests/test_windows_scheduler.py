"""Task Scheduler boundary tests use a fake command runner, never real tasks."""

import subprocess
import base64
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from github_radar import windows_scheduler
from github_radar.windows_scheduler import (SchedulerError, build_task_xml,
                                            query_task,
                                            register_task, remove_task,
                                            task_name)


def imported_xml(path):
    return ET.tostring(ET.fromstring(Path(path).read_bytes()), encoding="unicode")


class SchedulerTests(unittest.TestCase):
    def test_scheduler_commands_never_flash_a_console_window(self):
        captured = {}

        def runner(args, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        windows_scheduler._run(["whoami.exe", "/user"], runner)
        self.assertEqual(captured.get("creationflags"), subprocess.CREATE_NO_WINDOW)

    def test_scheduler_output_preserves_unicode_install_paths(self):
        def runner(args, **kwargs):
            self.assertFalse(kwargs["text"])
            return subprocess.CompletedProcess(
                args, 0, stdout=("<Command>" + str(self.root / "测试目录" / "GitHubRadar.exe") + "</Command>").encode("utf-8"),
                stderr=b"")

        result = windows_scheduler._run(["schtasks.exe", "/query"], runner)
        self.assertIn("测试目录", result.stdout)

    def test_task_query_recovers_unicode_when_schtasks_export_is_damaged(self):
        root = self.root / "自选安装 04"
        command = root / "GitHubRadar.exe"
        arguments = ("--scheduled-refresh", "--data-dir", str(root / "UserData"))
        correct = build_task_xml(command, arguments, root,
                                 "09:00", "S-1-5-21-123", date(2026, 9, 28))
        damaged = correct.encode("utf-8").replace("装".encode("utf-8"), b"\xe8\xa3?")
        calls = []

        def runner(args, **kwargs):
            calls.append(args[0].lower())
            if args[0].lower().endswith("schtasks.exe"):
                return subprocess.CompletedProcess(args, 0, stdout=damaged, stderr=b"")
            if args[0].lower().endswith("powershell.exe"):
                exported = '<?xml version="1.0" encoding="UTF-16"?>\r\n' + correct
                encoded = base64.b64encode(exported.encode("utf-16-le"))
                return subprocess.CompletedProcess(args, 0, stdout=encoded, stderr=b"")
            self.fail(f"Unexpected task reader: {args[0]}")

        state = query_task(task_name(root), runner=runner)
        self.assertEqual(state.command, str(command))
        self.assertEqual(state.arguments, subprocess.list2cmdline(list(arguments)))
        self.assertEqual(len(calls), 2)

    def test_task_import_xml_has_utf16_bom_for_chinese_path(self):
        root = self.root / "自选安装 04"
        command = root / "GitHubRadar.exe"
        arguments = ("--scheduled-refresh", "--data-dir", str(root / "UserData"))
        imported = []

        def runner(args, **kwargs):
            if args[1] == "/query":
                return subprocess.CompletedProcess(args, -2147024893,
                                                   stdout=b"", stderr=b"")
            payload = Path(args[args.index("/xml") + 1]).read_bytes()
            imported.append(payload)
            self.assertTrue(payload.startswith(b"\xff\xfe"))
            task = ET.fromstring(payload)
            ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
            self.assertEqual(task.findtext("t:Actions/t:Exec/t:Command", namespaces=ns),
                             str(command))
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        register_task(task_name(root), command, arguments, root,
                      "09:00", "S-1-5-21-123", runner=runner)
        self.assertEqual(len(imported), 1)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Radar test & space"
        self.root.mkdir()
        self.command = self.root / "GitHubRadar.exe"
        self.arguments = ("--scheduled-refresh", "--data-dir", str(self.root / "UserData"))

    def test_name_is_stable_and_isolated_by_install_directory(self):
        self.assertEqual(task_name(self.root), task_name(self.root))
        self.assertNotEqual(task_name(self.root), task_name(self.root / "other"))
        self.assertTrue(task_name(self.root).startswith("GitHubRadar-"))

    def test_xml_runs_current_user_without_waking_and_escapes_paths(self):
        xml = build_task_xml(self.command, self.arguments, self.root,
                             "09:00", "S-1-5-21-123", date(2026, 9, 28))
        root = ET.fromstring(xml)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(len(root.findall("t:Triggers/t:CalendarTrigger", ns)), 1)
        boundaries = [item.text for item in root.findall(
            "t:Triggers/t:CalendarTrigger/t:StartBoundary", ns)]
        self.assertEqual([value[11:16] for value in boundaries], ["09:00"])
        self.assertEqual(root.findtext("t:Settings/t:StartWhenAvailable", namespaces=ns), "true")
        self.assertEqual(root.findtext("t:Settings/t:WakeToRun", namespaces=ns), "false")
        self.assertEqual(root.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns),
                         "InteractiveToken")
        self.assertEqual(root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns),
                         str(self.command))
        self.assertIn('"' + str(self.root / "UserData") + '"',
                      root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns))
        self.assertEqual(root.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=ns),
                         str(self.root))

    def test_registration_rejects_foreign_task_and_does_not_overwrite(self):
        calls = []
        foreign = build_task_xml(self.root / "other.exe", self.arguments, self.root,
                                 "09:00", "S-1-5-21-123", date(2026, 9, 28))

        def runner(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout=foreign, stderr="")

        with self.assertRaises(SchedulerError):
            register_task(task_name(self.root), self.command, self.arguments,
                          self.root, "09:00", "S-1-5-21-123", runner=runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "/query")

    def test_register_and_remove_only_own_task_with_argument_arrays(self):
        calls = []
        own = build_task_xml(self.command, self.arguments, self.root,
                             "09:00", "S-1-5-21-123", date(2026, 9, 28))
        installed = False

        def runner(args, **kwargs):
            nonlocal installed
            calls.append(args)
            if args[1] == "/query":
                return subprocess.CompletedProcess(args, 0 if installed else -2147024893,
                                                   stdout=own if installed else "", stderr="")
            if args[1] == "/create":
                installed = True
                self.assertTrue(Path(args[args.index("/xml") + 1]).exists())
            if args[1] == "/delete":
                installed = False
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        name = task_name(self.root)
        register_task(name, self.command, self.arguments, self.root,
                      "09:00", "S-1-5-21-123", runner=runner)
        self.assertTrue(query_task(name, runner=runner).exists)
        remove_task(name, self.command, self.arguments, runner=runner)
        self.assertFalse(query_task(name, runner=runner).exists)
        self.assertEqual([item[1] for item in calls if item[1] in {"/create", "/delete"}],
                         ["/create", "/delete"])
        self.assertTrue(all(isinstance(item, list) for item in calls))

    def test_failed_create_reports_error_without_false_success(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Access denied")

        with self.assertRaises(SchedulerError):
            register_task(task_name(self.root), self.command, self.arguments,
                          self.root, "09:00", "S-1-5-21-123", runner=runner)

    def test_register_same_schedule_does_not_replace_existing_task(self):
        existing = build_task_xml(self.command, self.arguments, self.root,
                                  "09:00", "S-1-5-21-123", date(2026, 9, 28))

        def runner(args, **kwargs):
            if args[1] == "/query":
                return subprocess.CompletedProcess(args, 0, stdout=existing, stderr="")
            self.fail("An unchanged daily task must not be replaced")

        register_task(task_name(self.root), self.command, self.arguments,
                      self.root, "09:00", "S-1-5-21-123", runner=runner)

    def test_windows_omits_false_wake_setting_from_exported_xml(self):
        existing = build_task_xml(self.command, self.arguments, self.root,
                                  "09:00", "S-1-5-21-123", date(2026, 9, 28))
        existing = existing.replace("<WakeToRun>false</WakeToRun>", "")

        def runner(args, **kwargs):
            if args[1] == "/query":
                return subprocess.CompletedProcess(args, 0, stdout=existing, stderr="")
            self.fail("Windows default false should not cause endless re-registration")

        controller = windows_scheduler.SchedulerController(
            self.root, self.command, self.arguments, self.root,
            user_sid="S-1-5-21-123", runner=runner)
        self.assertTrue(controller.status(True, "09:00"))
        controller.sync(True, "09:00")

    def test_disabled_or_non_daily_task_is_not_reported_as_registered(self):
        original = build_task_xml(self.command, self.arguments, self.root,
                                  "09:00", "S-1-5-21-123", date(2026, 9, 28))
        mutations = (
            original.replace("<DaysInterval>1</DaysInterval>",
                             "<DaysInterval>2</DaysInterval>", 1),
            original.replace("<CalendarTrigger>",
                             "<CalendarTrigger><Enabled>false</Enabled>", 1),
            original.replace("<Settings>", "<Settings><Enabled>false</Enabled>", 1),
        )
        for changed in mutations:
            with self.subTest(changed=changed[:100]):
                def runner(args, **kwargs):
                    return subprocess.CompletedProcess(args, 0, stdout=changed, stderr="")
                controller = windows_scheduler.SchedulerController(
                    self.root, self.command, self.arguments, self.root,
                    user_sid="S-1-5-21-123", runner=runner)
                self.assertFalse(controller.status(True, "09:00"))

    def test_failed_time_change_restores_previous_task(self):
        previous = build_task_xml(self.command, self.arguments, self.root,
                                  "09:00", "S-1-5-21-123", date(2026, 9, 28))
        created = []

        def runner(args, **kwargs):
            if args[1] == "/query":
                return subprocess.CompletedProcess(args, 0, stdout=previous, stderr="")
            created.append(imported_xml(args[args.index("/xml") + 1]))
            if len(created) == 1:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="Access denied")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with self.assertRaisesRegex(SchedulerError, "创建失败"):
            register_task(task_name(self.root), self.command, self.arguments,
                          self.root, "12:00", "S-1-5-21-123", runner=runner)
        self.assertEqual(len(created), 2)
        self.assertEqual(created[1], previous)

    def test_query_service_failure_is_not_mistaken_for_missing_task(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Service unavailable")

        with self.assertRaises(SchedulerError):
            query_task(task_name(self.root), runner=runner)

    def test_controller_reports_registration_time_and_removes_own_task(self):
        self.assertTrue(hasattr(windows_scheduler, "SchedulerController"))
        installed = None

        def runner(args, **kwargs):
            nonlocal installed
            if args[1] == "/query":
                return subprocess.CompletedProcess(args, 0 if installed else -2147024893,
                                                   stdout=installed or "", stderr="")
            if args[1] == "/create":
                installed = imported_xml(args[args.index("/xml") + 1])
            elif args[1] == "/delete":
                installed = None
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        controller = windows_scheduler.SchedulerController(self.root, self.command, self.arguments,
                                         self.root, user_sid="S-1-5-21-123", runner=runner)
        self.assertFalse(controller.status(True, "09:00"))
        controller.sync(True, "09:00")
        self.assertTrue(controller.status(True, "09:00"))
        self.assertFalse(controller.status(True, "10:00"))
        controller.sync(False, "09:00")
        self.assertTrue(controller.status(False, "09:00"))
        self.assertIsNone(installed)

    def test_copying_data_to_new_installation_keeps_both_tasks_isolated(self):
        tasks = {}

        class BindingStore:
            binding = None

            def load_scheduler_binding(self):
                return self.binding

            def save_scheduler_binding(self, binding):
                self.binding = binding

        def runner(args, **kwargs):
            name = args[args.index("/tn") + 1]
            if args[1] == "/query":
                xml = tasks.get(name)
                return subprocess.CompletedProcess(args, 0 if xml else -2147024893,
                                                   stdout=xml or "", stderr="")
            if args[1] == "/create":
                tasks[name] = imported_xml(args[args.index("/xml") + 1])
            elif args[1] == "/delete":
                tasks.pop(name)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        binding_store = BindingStore()
        old = windows_scheduler.SchedulerController(
            self.root, self.command, self.arguments, self.root,
            user_sid="S-1-5-21-123", runner=runner, binding_store=binding_store)
        old.sync(True, "09:00")
        self.assertIn(old.name, tasks)

        moved_root = self.root / "moved"
        moved_command = moved_root / "GitHubRadar.exe"
        moved_arguments = ("--scheduled-refresh", "--data-dir", str(moved_root / "UserData"))
        moved = windows_scheduler.SchedulerController(
            moved_root, moved_command, moved_arguments, moved_root,
            user_sid="S-1-5-21-123", runner=runner, binding_store=binding_store)
        moved.sync(True, "09:00")
        self.assertEqual(set(tasks), {old.name, moved.name})
        self.assertTrue(moved.status(True, "09:00"))
        moved.sync(False, "09:00")
        self.assertEqual(set(tasks), {old.name})

    def test_new_installation_does_not_try_deleting_previous_task(self):
        old_xml = build_task_xml(self.command, self.arguments, self.root,
                                 "09:00", "S-1-5-21-123", date(2026, 9, 28))
        old_name = task_name(self.root)
        moved_root = self.root / "moved"
        moved_command = moved_root / "GitHubRadar.exe"
        moved_arguments = ("--scheduled-refresh", "--data-dir", str(moved_root / "UserData"))
        tasks = {old_name: old_xml}

        class BindingStore:
            binding = {"install_dir": str(self.root), "command": str(self.command),
                       "arguments": list(self.arguments)}

            def load_scheduler_binding(self):
                return self.binding

            def save_scheduler_binding(self, binding):
                self.binding = binding

        binding_store = BindingStore()

        def runner(args, **kwargs):
            name = args[args.index("/tn") + 1]
            if args[1] == "/query":
                xml = tasks.get(name)
                return subprocess.CompletedProcess(args, 0 if xml else -2147024893,
                                                   stdout=xml or "", stderr="")
            if args[1] == "/create":
                tasks[name] = imported_xml(args[args.index("/xml") + 1])
            elif args[1] == "/delete":
                if name == old_name:
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="Access denied")
                tasks.pop(name)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        moved = windows_scheduler.SchedulerController(
            moved_root, moved_command, moved_arguments, moved_root,
            user_sid="S-1-5-21-123", runner=runner, binding_store=binding_store)
        moved.sync(True, "09:00")
        self.assertEqual(set(tasks), {old_name, moved.name})
        self.assertEqual(binding_store.binding["install_dir"], str(moved_root))


if __name__ == "__main__":
    unittest.main()
