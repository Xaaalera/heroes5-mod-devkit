"""ProcDump ownership/signature and resource boundaries, no game launch."""
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sdk_diagnostics


class CrashMonitorTests(unittest.TestCase):
    def test_unused_background_budget_closes_without_disabling_unconfigured_cpu_control(self):
        for foreground in (None, 123):
            with self.subTest(foreground=foreground):
                probe, kernel = Mock(), Mock()
                probe.checked.side_effect = lambda value: value
                probe.creation_time.return_value = 456
                kernel.OpenProcess.return_value = 11
                kernel.CreateJobObjectW.return_value = 22
                budget = sdk_diagnostics.OwnedBackgroundBudget(probe, kernel, {'pid': 123, 'created': 456})
                if foreground is not None:
                    budget.update(foreground)
                budget.close()
                budget.close()
                kernel.SetInformationJobObject.assert_not_called()
                self.assertEqual(kernel.CloseHandle.call_args_list.count(unittest.mock.call(22)), 1)

    def test_background_budget_is_owned_reversible_and_only_changes_on_focus_transition(self):
        probe, kernel = Mock(), Mock()
        probe.checked.side_effect = lambda value: value
        probe.creation_time.return_value = 456
        kernel.OpenProcess.return_value = 11
        kernel.CreateJobObjectW.return_value = 22
        budget = sdk_diagnostics.OwnedBackgroundBudget(probe, kernel, {'pid': 123, 'created': 456})
        budget.update(999)
        information = kernel.SetInformationJobObject.call_args.args[2]
        self.assertEqual(list(information), [5, 200])
        budget.update(999)
        self.assertEqual(kernel.SetInformationJobObject.call_count, 1)
        budget.update(123)
        self.assertEqual(list(kernel.SetInformationJobObject.call_args.args[2]), [0, 10000])
        budget.close()
        self.assertIsNone(budget.job)
        kernel.CloseHandle.assert_any_call(22)
        kernel.AssignProcessToJobObject.reset_mock()
        probe.creation_time.return_value = 789
        with self.assertRaisesRegex(RuntimeError, 'owner changed'):
            sdk_diagnostics.OwnedBackgroundBudget(probe, kernel, {'pid': 123, 'created': 456})
        kernel.AssignProcessToJobObject.assert_not_called()
    def test_active_game_operation_temporarily_releases_and_restores_idle_quota(self):
        probe, kernel = Mock(), Mock()
        probe.checked.side_effect = lambda value: value
        probe.creation_time.return_value = 456
        kernel.OpenProcess.return_value = 11
        kernel.CreateJobObjectW.return_value = 22
        budget = sdk_diagnostics.OwnedBackgroundBudget(probe, kernel, {'pid': 123, 'created': 456})
        budget.update(999)
        budget.update(999, active=True)
        self.assertEqual(list(kernel.SetInformationJobObject.call_args.args[2]), [0, 10000])
        budget.update(999, active=True)
        self.assertEqual(kernel.SetInformationJobObject.call_count, 2)
        budget.update(999)
        self.assertEqual(list(kernel.SetInformationJobObject.call_args.args[2]), [5, 200])
        budget.close()

    def test_trace_borrows_pinned_core_and_rejects_changed_endpoint_before_attach(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watch = root / '.local/xalkit/watch'
            watch.mkdir(parents=True)
            owner = {'pid': 123, 'created': 456}
            endpoint = {'version': 1, **owner}
            for name in ('controller', 'bridge'):
                path = watch / name
                path.write_bytes(b'fixture')
                endpoint[name] = str(path)
                endpoint[name + '_sha256'] = hashlib.sha256(b'fixture').hexdigest()
            endpoint_path = watch / 'diagnostic-owner.json'
            endpoint_path.write_text(json.dumps(endpoint), encoding='utf-8')
            response = Mock(returncode=0, stdout='{"ready":true}\n{"status":0,"diagnostics":"fixture"}\n', stderr='')
            with patch.object(sdk_diagnostics.subprocess, 'run', return_value=response) as run, \
                    patch('plugin_core.decode_diagnostics', return_value={'records': []}):
                result = sdk_diagnostics.read_plugin_trace(root, owner, 10, 2)
            self.assertEqual(result, {'records': []})
            self.assertEqual(run.call_args.args[0][1], '--owned-read')
            self.assertEqual(run.call_args.kwargs['input'], 'trace 10 2\nquit\n')
            for mutation in ('owner', 'binary', 'outside'):
                changed = dict(endpoint)
                if mutation == 'owner':
                    changed['created'] = 999
                elif mutation == 'binary':
                    changed['controller_sha256'] = 'wrong'
                else:
                    outside = root / 'outside.exe'
                    outside.write_bytes(b'fixture')
                    changed['controller'] = str(outside)
                endpoint_path.write_text(json.dumps(changed), encoding='utf-8')
                with patch.object(sdk_diagnostics.subprocess, 'run') as attach:
                    with self.assertRaises(RuntimeError):
                        sdk_diagnostics.read_plugin_trace(root, owner, 0, 0)
                attach.assert_not_called()

    def test_windows_events_require_creation_path_and_pid_and_hide_fault_address(self):
        executable = Path('game/H5_Game.exe').resolve()
        owner = {'pid': 123, 'created': 456}
        data = {'ProcessId': '0x7b', 'ProcessCreationTime': '0x1c8', 'AppPath': str(executable),
                'ModuleName': 'granny2.dll', 'ExceptionCode': 'c0000005', 'FaultingOffset': 'private-address'}
        records = [{'record_id': 1, 'timestamp': 'fixture', 'data': data}]
        for changes in ({'ProcessId': '0x7c'}, {'ProcessCreationTime': '0x1c9'},
                        {'AppPath': str(executable.parent / 'other.exe')}, {'ProcessCreationTime': ''}):
            records.append({'record_id': 2, 'timestamp': 'fixture', 'data': {**data, **changes}})
        result = Mock(stdout=json.dumps(records))
        with patch.object(sdk_diagnostics.subprocess, 'run', return_value=result):
            found = sdk_diagnostics.read_owned_windows_events(owner, executable)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['module'], 'granny2.dll')
        self.assertNotIn('FaultingOffset', found[0])
        self.assertNotIn('private-address', json.dumps(found))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.tool = self.root / 'procdump.exe'
        self.tool.write_bytes(b'fixture executable')

    def test_invalid_signature_and_changed_tool_are_rejected(self):
        invalid = Mock(stdout='{"status":"NotSigned","signer":"CN=Microsoft Corporation,"}')
        with patch.object(sdk_diagnostics.subprocess, 'run', return_value=invalid):
            with self.assertRaisesRegex(RuntimeError, 'valid Microsoft signature'):
                sdk_diagnostics.verify_microsoft_tool(self.tool)
        def change_while_verifying(*arguments, **keywords):
            self.tool.write_bytes(b'replaced executable')
            return Mock(stdout='{"status":"Valid","signer":"CN=Microsoft Corporation,"}')
        with patch.object(sdk_diagnostics.subprocess, 'run', side_effect=change_while_verifying):
            with self.assertRaisesRegex(RuntimeError, 'changed during'):
                sdk_diagnostics.verify_microsoft_tool(self.tool)

    def test_wrong_creation_or_exited_process_never_launches_monitor(self):
        for created, wait_result in ((222, 258), (111, 0)):
            with self.subTest(created=created, wait_result=wait_result):
                kernel = Mock()
                kernel.WaitForSingleObject.return_value = wait_result
                probe = Mock()
                probe.creation_time.return_value = created
                with patch.object(sdk_diagnostics, 'verify_microsoft_tool', return_value=(self.tool, 'hash')), \
                        patch.object(sdk_diagnostics.subprocess, 'Popen') as spawn:
                    with self.assertRaisesRegex(RuntimeError, 'live immutable owned game'):
                        sdk_diagnostics.OwnedCrashMonitor(probe, kernel, 1, {'pid': 44, 'created': 111},
                            self.root / 'H5_Game.exe', self.tool, self.root / 'dumps')
                spawn.assert_not_called()
                kernel.DuplicateHandle.assert_not_called()

    def test_uncertain_stop_keeps_pid_pinned_and_completed_stop_normalizes_log(self):
        monitor = sdk_diagnostics.OwnedCrashMonitor.__new__(sdk_diagnostics.OwnedCrashMonitor)
        monitor.kernel = Mock()
        monitor.handle = 999
        monitor.owner = {'pid': 44, 'created': 111}
        monitor.output = self.root
        monitor.executable = self.tool
        monitor.tool_digest = 'hash'
        monitor.log = Mock()
        monitor.process = Mock()
        monitor.process.pid = 55
        monitor.process.poll.return_value = None
        monitor.process.wait.side_effect = subprocess.TimeoutExpired('procdump', 5)
        with patch.object(sdk_diagnostics.subprocess, 'run', return_value=Mock(returncode=0)):
            with self.assertRaises(subprocess.TimeoutExpired):
                monitor.close()
        monitor.kernel.CloseHandle.assert_not_called()
        self.assertEqual(monitor.handle, 999)
        (self.root / 'procdump.raw.log').write_bytes('Process exited.\n'.encode('utf-16'))
        monitor.process.poll.return_value = 0
        result = monitor.close()
        self.assertEqual(result['monitor_exit_code'], 0)
        self.assertEqual((self.root / 'procdump.log').read_text(encoding='utf-8'), 'Process exited.\n')
        monitor.kernel.CloseHandle.assert_called_once_with(999)
        monitor.log.close.assert_called_once()

    def test_mixed_log_is_readable_and_nonzero_monitor_exit_is_a_failure(self):
        monitor = sdk_diagnostics.OwnedCrashMonitor.__new__(sdk_diagnostics.OwnedCrashMonitor)
        monitor.kernel = Mock()
        monitor.handle = 999
        monitor.owner = {'pid': 44, 'created': 111}
        monitor.output = self.root
        monitor.tool_digest = 'hash'
        monitor.log = Mock()
        monitor.process = Mock()
        monitor.process.pid = 55
        monitor.process.poll.return_value = 1
        raw = b'ProcDump banner\r\n' + 'Process attach failed.\r\n'.encode('utf-16-le')
        (self.root / 'procdump.raw.log').write_bytes(raw)
        with self.assertRaisesRegex(RuntimeError, 'exit code 1'):
            monitor.close()
        text = (self.root / 'procdump.log').read_text(encoding='utf-8')
        self.assertIn('Process attach failed.', text)
        self.assertNotIn('\x00', text)
        monitor.kernel.CloseHandle.assert_called_once_with(999)

    def test_owned_fatal_dump_is_capture_success_but_foreign_or_truncated_dump_is_rejected(self):
        created = 1700000000
        owner = {'pid': 44, 'created': (created + 11644473600) * 10000000}
        header = bytearray(32)
        header[:4] = b'MDMP'
        struct.pack_into('<II', header, 8, 2, 32)
        directory = struct.pack('<6I', 15, 24, 56, 6, 168, 80)
        misc = struct.pack('<6I', 24, 3, 44, created, 0, 0)
        exception = bytearray(168)
        struct.pack_into('<3I', exception, 0, 55, 0, 0xe0424242)
        struct.pack_into('<II', exception, 160, 716, 248)
        dump = self.root / 'owned.dmp'
        data = bytes(header) + directory + misc + bytes(exception) + bytes(716)
        dump.write_bytes(data)
        self.assertEqual(sdk_diagnostics.read_dump_identity(dump, owner)['exception_code'], 0xe0424242)
        self.assertIsNone(sdk_diagnostics.read_dump_identity(dump, {**owner, 'pid': 99}))
        truncated = bytearray(data)
        struct.pack_into('<I', truncated, 48, 12)
        dump.write_bytes(truncated)
        self.assertIsNone(sdk_diagnostics.read_dump_identity(dump, owner))
        bad_context = bytearray(data)
        struct.pack_into('<I', bad_context, 80 + 164, len(data) + 1)
        dump.write_bytes(bad_context)
        self.assertIsNone(sdk_diagnostics.read_dump_identity(dump, owner))
        bad_parameters = bytearray(data)
        struct.pack_into('<I', bad_parameters, 80 + 32, 16)
        dump.write_bytes(bad_parameters)
        self.assertIsNone(sdk_diagnostics.read_dump_identity(dump, owner))
        dump.write_bytes(data[:-1])
        self.assertIsNone(sdk_diagnostics.read_dump_identity(dump, owner))
        dump.write_bytes(data)
        monitor = sdk_diagnostics.OwnedCrashMonitor.__new__(sdk_diagnostics.OwnedCrashMonitor)
        monitor.output = self.root; monitor.owner = owner; monitor.tool_digest = 'hash'; monitor.handle = None
        monitor.process = Mock(pid=77)
        monitor.process.poll.return_value = 1
        (self.root / 'procdump.raw.log').write_bytes(b'Dump count reached.\n')
        result = monitor.close()
        self.assertEqual(result['capture_status'], 'dump_captured')
        self.assertEqual(result['monitor_exit_code'], 1)


if __name__ == '__main__':
    unittest.main()
