"""Optional x86 emulation of the terminal bridge; never attaches to a game."""
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, patch
import io
import json
import re
import sys
import os
import subprocess
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESP, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EFLAGS, UC_X86_REG_XMM0
    import keystone
except ImportError:
    Uc = None

specification = importlib.util.spec_from_file_location('game_control_test', Path(__file__).resolve().parents[1] / 'scripts/game_control.py')
control = importlib.util.module_from_spec(specification)
specification.loader.exec_module(control)


class GameControlCommandTests(unittest.TestCase):
    def test_startup_wait_observes_heartbeat_without_submitting_commands(self):
        owner = {'pid': 123, 'created': 456}
        ready = {'pid': 123, 'heartbeat': 1, 'mailbox_status': 0}
        with patch.object(control, 'execute', side_effect=[dict(ready, heartbeat=0), ready]) as observe, \
                patch.object(control.time, 'sleep'):
            self.assertEqual(control.wait_for_game_loop(None, owner), ready)
        self.assertEqual(observe.call_count, 2)
        for call in observe.call_args_list:
            self.assertEqual(call.args, (None,))
            self.assertEqual(call.kwargs, {'expected_owner': owner})

    def test_startup_wait_refuses_changed_owner_and_has_a_deadline(self):
        owner = {'pid': 123, 'created': 456}
        with patch.object(control, 'execute', side_effect=RuntimeError('Owner changed')):
            with self.assertRaisesRegex(RuntimeError, 'Owner changed'):
                control.wait_for_game_loop(None, owner)
        with patch.object(control, 'execute', return_value={'heartbeat': 0}), \
                patch.object(control.time, 'monotonic', side_effect=[0, 2]), \
                patch.object(control.time, 'sleep') as sleep:
            with self.assertRaises(TimeoutError):
                control.wait_for_game_loop(None, owner, timeout=1)
            sleep.assert_not_called()

    def test_menu_uses_one_borrowed_command_and_checks_owner_before_dispatch(self):
        owner = {'pid': 123, 'created': 456}
        with patch.object(control, 'STATE') as state, \
                patch.object(control, '_probe_module', Mock()), \
                patch.object(control, 'execute') as mailbox, \
                patch('plugin_core.dispatch_owned_console', return_value={
                    'pid': 123, 'status': 'dispatched', 'dispatch_returned': True,
                    'effect_verified': False}) as dispatch:
            state.read_text.return_value = json.dumps(owner)
            result = control.main(['menu'])
            self.assertTrue(result['menu_requested'])
            self.assertFalse(result['effect_verified'])
            dispatch.assert_called_once_with(control.ROOT, owner, 'mainmenu')
            mailbox.assert_not_called()
            dispatch.reset_mock()
            token = control.expected_session.set({'pid': 999, 'created': 456})
            try:
                with self.assertRaisesRegex(RuntimeError, 'owner changed'):
                    control.main(['menu'])
            finally:
                control.expected_session.reset(token)
            dispatch.assert_not_called()

    def test_map_returns_native_dispatch_receipt_without_falling_through_to_status(self):
        owner = {'pid': 123, 'created': 456}
        with patch.object(control, 'STATE') as state, \
                patch.object(control, '_probe_module', Mock()), \
                patch.object(control, 'execute') as mailbox, \
                patch('game_launch.map_arguments', return_value=['-advmap', 'Maps/Test/map.xdb']), \
                patch('plugin_core.dispatch_owned_console', return_value={
                    'pid': 123, 'status': 'dispatched', 'dispatch_returned': True,
                    'effect_verified': False}) as dispatch:
            state.read_text.return_value = json.dumps(owner)
            result = control.main(['map', 'WorkshopPolygon'])
        self.assertEqual(result['map'], 'WorkshopPolygon')
        self.assertTrue(result['dispatch_returned'])
        self.assertFalse(result['effect_verified'])
        self.assertEqual([call.args[2] for call in dispatch.call_args_list],
            ['setvar pwl_press_any_key_enabled = 0', 'advmap Maps/Test/map.xdb'])
        mailbox.assert_not_called()

    def test_player_launch_uses_ordinary_creation_without_probe_patches(self):
        specification = importlib.util.spec_from_file_location('ordinary_player_test',
            Path(__file__).resolve().parents[1] / 'scripts/plugin-player-check.py')
        checker = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(checker)
        specification = importlib.util.spec_from_file_location('ordinary_probe_test',
            Path(__file__).resolve().parents[1] / 'scripts/native-probe.py')
        probe = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(probe)
        kernel = Mock()

        def create(*arguments):
            process = arguments[-1]._obj
            process.process, process.thread, process.pid = 11, 12, 123
            return 1

        kernel.CreateProcessW.side_effect = create
        game = Path('C:/fixture with spaces/bin/H5_Game.exe')
        with patch.object(checker.subprocess, 'run', return_value=Mock(stdout=b'INFO')), \
                patch.object(checker, 'map_arguments', return_value=['-advmap', 'Maps/Test/map.xdb']), \
                patch.object(probe, 'HASHES', {}), \
                patch.object(probe, 'creation_time', return_value=456), \
                patch.object(probe, 'launch') as patched_launch:
            pid, handle = checker.launch_player_game(probe, kernel, game)
        self.assertEqual((pid, handle), (123, 11))
        arguments = kernel.CreateProcessW.call_args.args
        self.assertEqual(arguments[5], 0)
        self.assertIn('-advmap Maps/Test/map.xdb', arguments[1].value)
        self.assertEqual(arguments[7], str(game.parent))
        kernel.CloseHandle.assert_called_once_with(12)
        kernel.WriteProcessMemory.assert_not_called()
        kernel.ResumeThread.assert_not_called()
        patched_launch.assert_not_called()

        kernel.reset_mock()
        with patch.object(checker.subprocess, 'run', return_value=Mock(stdout=b'"H5_Game.exe"')):
            with self.assertRaisesRegex(RuntimeError, 'Close Heroes'):
                checker.launch_player_game(probe, kernel, game)
        kernel.CreateProcessW.assert_not_called()

    def test_screenshot_chooses_newest_complete_frame_without_replaying_request(self):
        from tempfile import TemporaryDirectory
        from PIL import Image
        with TemporaryDirectory() as directory:
            root = Path(directory)
            captures = root / '.local/test-game/screenshots'
            captures.mkdir(parents=True)
            owner_path = root / 'owner.json'
            owner_path.write_text(json.dumps({'pid': 123, 'created': 456}), encoding='utf-8')
            Image.new('RGB', (3, 2), 'yellow').save(captures / 'ScrnShot_stale.tga')
            os.utime(captures / 'ScrnShot_stale.tga', ns=(999999999, 999999999))
            requests = []
            def dispatch(probe, command=None, timeout=10, mode=None, expected_owner=None):
                self.assertEqual(expected_owner, {'pid': 123, 'created': 456})
                if command:
                    requests.append((command, mode))
                    for name, color, stamp in [('first', 'red', 100000000), ('last', 'blue', 200000000)]:
                        path = captures / ('ScrnShot_' + name + '.tga')
                        Image.new('RGB', (3, 2), color).save(path)
                        os.utime(path, ns=(stamp, stamp))
                return {'pid': 123}
            with patch.object(control, 'ROOT', root), patch.object(control, 'STATE', owner_path), \
                    patch.object(control, 'execute', side_effect=dispatch):
                result = control.capture_screenshot(Mock(), 1)
            self.assertEqual(requests, [('screenshot', 24)])
            self.assertEqual(result['capture_count'], 2)
            self.assertEqual(Path(result['source_file']).name, 'ScrnShot_last.tga')
            with Image.open(result['image_file']) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))

    def test_screenshot_waits_for_partial_file_and_preserves_cancellation(self):
        from tempfile import TemporaryDirectory
        from PIL import Image
        for cancel in (False, True):
            with self.subTest(cancel=cancel), TemporaryDirectory() as directory:
                root = Path(directory)
                captures = root / '.local/test-game/screenshots'
                captures.mkdir(parents=True)
                owner_path = root / 'owner.json'
                owner_path.write_text(json.dumps({'pid': 123, 'created': 456}), encoding='utf-8')
                state = {'queries': 0, 'requests': 0}
                def dispatch(probe, command=None, timeout=10, mode=None, expected_owner=None):
                    self.assertEqual(expected_owner, {'pid': 123, 'created': 456})
                    if command:
                        state['requests'] += 1
                        (captures / 'ScrnShot_new.tga').write_bytes(b'partial')
                    else:
                        state['queries'] += 1
                        if state['queries'] == 4:
                            if cancel:
                                raise RuntimeError('disconnected owner')
                            Image.new('RGB', (3, 2), 'green').save(captures / 'ScrnShot_new.tga')
                    return {'pid': 123}
                with patch.object(control, 'ROOT', root), patch.object(control, 'STATE', owner_path), \
                        patch.object(control, 'execute', side_effect=dispatch), \
                        patch.object(control.time, 'sleep'):
                    if cancel:
                        with self.assertRaisesRegex(RuntimeError, 'disconnected owner'):
                            control.capture_screenshot(Mock(), 1)
                        self.assertFalse((root / '.local/xalkit/captures').exists())
                    else:
                        result = control.capture_screenshot(Mock(), 1)
                        self.assertTrue(Path(result['image_file']).is_file())
                self.assertEqual(state['requests'], 1)

    def test_screenshot_timeout_does_not_accept_stale_file_or_retry(self):
        from tempfile import TemporaryDirectory
        from PIL import Image
        with TemporaryDirectory() as directory:
            root = Path(directory)
            captures = root / '.local/test-game/screenshots'
            captures.mkdir(parents=True)
            owner_path = root / 'owner.json'
            owner_path.write_text(json.dumps({'pid': 123, 'created': 456}), encoding='utf-8')
            Image.new('RGB', (3, 2)).save(captures / 'ScrnShot_old.tga')
            request = Mock(return_value={'pid': 123})
            with patch.object(control, 'ROOT', root), patch.object(control, 'STATE', owner_path), \
                    patch.object(control, 'execute', request):
                with self.assertRaises(TimeoutError):
                    control.capture_screenshot(Mock(), 0.01)
            self.assertEqual(sum(call.args[1:] == ('screenshot', 0.01) for call in request.call_args_list), 1)
            self.assertFalse((root / '.local/xalkit/captures').exists())

    def test_screenshot_rejects_session_change_with_the_actual_owner_guard(self):
        from tempfile import TemporaryDirectory
        original_execute = control.execute
        for next_owner in ({'pid': 999, 'created': 888}, {'pid': 123, 'created': 789}):
            with self.subTest(next_owner=next_owner), TemporaryDirectory() as directory:
                root = Path(directory)
                captures = root / '.local/test-game/screenshots'
                captures.mkdir(parents=True)
                owner_path = root / 'owner.json'
                owner_path.write_text(json.dumps({'pid': 123, 'created': 456}), encoding='utf-8')
                state = {'requested': False, 'requests': 0}
                probe = Mock()
                def dispatch(probe, command=None, timeout=10, mode=None, expected_owner=None):
                    if command:
                        state['requested'] = True
                        state['requests'] += 1
                        owner_path.write_text(json.dumps(next_owner), encoding='utf-8')
                        (captures / 'ScrnShot_other_owner.tga').write_bytes(b'partial')
                        return {'pid': 123}
                    if state['requested']:
                        return original_execute(probe, expected_owner=expected_owner)
                    return {'pid': 123}
                with patch.object(control, 'ROOT', root), patch.object(control, 'STATE', owner_path), \
                        patch.object(control, 'execute', side_effect=dispatch):
                    with self.assertRaisesRegex(RuntimeError, 'session changed'):
                        control.capture_screenshot(probe, 1)
                self.assertEqual(state['requests'], 1)
                probe.api.assert_not_called()
                self.assertFalse((root / '.local/xalkit/captures').exists())

    def test_disconnected_console_context_cannot_submit_or_be_overridden(self):
        cancelled = threading.Event()
        cancelled.set()
        owner = {'pid': 123, 'created': 456, '_cancelled': cancelled}
        token = control.expected_session.set(owner)
        self.addCleanup(control.expected_session.reset, token)
        probe = Mock()
        with patch.object(control, 'STATE') as state:
            state.read_text.return_value = json.dumps({'pid': 123, 'created': 456})
            with self.assertRaisesRegex(RuntimeError, 'disconnected'):
                control.execute(probe, '@print(1)', expected_owner={'pid': 123, 'created': 456})
            with self.assertRaisesRegex(RuntimeError, 'conflicts'):
                control.execute(probe, '@print(1)', expected_owner={'pid': 999, 'created': 456})
        probe.api.assert_not_called()

    def test_expected_owner_change_is_rejected_before_opening_or_writing(self):
        probe = Mock()
        with patch.object(control, 'STATE') as state:
            state.read_text.return_value = json.dumps({'pid': 456, 'created': 789})
            with self.assertRaisesRegex(RuntimeError, 'session changed'):
                control.execute(probe, '@print(1)', expected_owner={'pid': 123, 'created': 456})
        probe.api.assert_not_called()

    def test_trace_limits_cursor_and_quotes_console_messages_as_data(self):
        owner = {'pid': 123, 'created': 456}
        message = "'\"; injected(); -- __workshop_rpc_result\nстрока"
        records = [{'sequence': index, 'milliseconds': 10, 'level': 'warning',
                    'module': 'alpha', 'message': message} for index in (1, 2, 3)]
        with patch.object(control, '_probe_module', Mock()), \
                patch.object(control, 'STATE') as state, \
                patch.object(control, 'execute', side_effect=[{'pid': 123},
                    {'pid': 123, 'status': 'completed', 'result': 'printed'}]) as execute, \
                patch('sdk_diagnostics.read_plugin_trace', return_value={
                    'records': records, 'cursor': 3, 'next_sequence': 4, 'oldest_sequence': 1, 'dropped': 0}):
            state.read_text.return_value = json.dumps(owner)
            result = control.main(['trace', '--console', '--limit', '1'])
        self.assertEqual(result['cursor'], 1)
        self.assertEqual(len(result['records']), 1)
        self.assertTrue(result['console_script_completed'])
        self.assertFalse(result['console_output_verified'])
        script = execute.call_args.args[1]
        self.assertEqual(execute.call_args.kwargs['expected_owner'], owner)
        self.assertNotIn(message, script)
        self.assertIn('\\039', script)
        self.assertIn('print(', script)
        self.assertTrue(script.isascii())

    def test_player_stage_failure_preserves_previous_file_and_removes_partial_temporary(self):
        from tempfile import TemporaryDirectory
        specification = importlib.util.spec_from_file_location('atomic_stage_test',
            Path(__file__).resolve().parents[1] / 'scripts/plugin-player-check.py')
        checker = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(checker)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'wsock32.dll'
            target.write_bytes(b'previous')
            original_write = Path.write_bytes
            def partial_write(path, contents):
                original_write(path, contents[:2])
                raise OSError('partial temporary write')
            installed = {}
            with patch.object(Path, 'write_bytes', partial_write):
                with self.assertRaisesRegex(OSError, 'partial temporary write'):
                    checker.stage_player_file(target, b'new-library', installed)
            self.assertEqual(target.read_bytes(), b'previous')
            self.assertEqual(installed, {})
            self.assertEqual(list(root.glob('*.tmp')), [])

    def test_player_restore_preserves_changed_shared_file_and_restores_other_owned_files(self):
        from tempfile import TemporaryDirectory
        import hashlib
        specification = importlib.util.spec_from_file_location('shared_restore_test',
            Path(__file__).resolve().parents[1] / 'scripts/plugin-player-check.py')
        checker = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(checker)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            loader = root / 'dinput8.dll'
            preload = root / 'wsock32.dll'
            plugin = root / 'one.dll'
            loader.write_bytes(b'owned-loader')
            preload.write_bytes(b'external-change')
            plugin.write_bytes(b'owned-plugin')
            errors = []
            checker.restore_player_files({plugin: hashlib.sha256(b'owned-plugin').hexdigest()},
                loader, hashlib.sha256(b'owned-loader').hexdigest(), b'previous-loader', errors,
                preload, hashlib.sha256(b'owned-preload').hexdigest(), b'previous-preload')
            self.assertEqual(preload.read_bytes(), b'external-change')
            self.assertEqual(loader.read_bytes(), b'previous-loader')
            self.assertFalse(plugin.exists())
            self.assertEqual([error['stage'] for error in errors], ['restore_preload'])

    def test_guarded_launch_rejects_identity_before_mutation_and_cancels_interrupted_handshake(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        specification = importlib.util.spec_from_file_location('guarded_probe_test',
            Path(__file__).resolve().parents[1] / 'scripts/native-probe.py')
        probe = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(probe)
        for failure in ('creation', 'path', 'interrupt', 'malformed', 'eof', 'cancel_failure'):
            with self.subTest(failure=failure), TemporaryDirectory() as directory:
                root = Path(directory)
                prepared = root / '.local/test-state/prepared.json'
                prepared.parent.mkdir(parents=True)
                prepared.write_text('{}', encoding='utf-8')
                helper = Mock()
                helper.stdout.readline.return_value = 'PREPARED 123 456 789\n'
                helper.communicate.return_value = ('', '')
                if failure == 'interrupt':
                    helper.stdout.readline.side_effect = KeyboardInterrupt
                if failure in ('malformed', 'cancel_failure'):
                    helper.stdout.readline.return_value = 'INVALID\n'
                if failure == 'eof':
                    helper.stdout.readline.return_value = ''
                if failure == 'cancel_failure':
                    helper.communicate.side_effect = OSError('cleanup failure')
                kernel = Mock()
                kernel.OpenProcess.return_value = 99
                def wrong_path(process, flags, filename, size):
                    filename.value = str(root / 'different.exe')
                    return True
                kernel.QueryFullProcessImageNameW.side_effect = wrong_path
                with patch.object(probe, 'ROOT', root), patch.object(probe, 'GAME', root), \
                        patch.object(probe, 'HASHES', {}), \
                        patch.object(probe.subprocess, 'run', return_value=Mock(stdout=b'')), \
                        patch.object(probe.subprocess, 'Popen', return_value=helper), \
                        patch.object(probe, 'creation_time', return_value=0 if failure == 'creation' else 789), \
                        patch.object(probe, 'read') as read, patch.object(probe, 'write') as write, \
                        patch.object(probe.sys, 'stderr', io.StringIO()):
                    with self.assertRaises(KeyboardInterrupt if failure == 'interrupt' else RuntimeError):
                        probe.launch(kernel, launch_gate=root / 'gate.exe')
                read.assert_not_called()
                write.assert_not_called()
                kernel.VirtualAllocEx.assert_not_called()
                kernel.TerminateProcess.assert_not_called()
                helper.communicate.assert_called_once_with('cancel\n', timeout=10)

    def test_live_checker_closes_owned_game_after_manager_cleanup_failures(self):
        from unittest.mock import Mock
        specification = importlib.util.spec_from_file_location('control_acceptance_test',
            Path(__file__).resolve().parents[1] / 'scripts/plugin-control-check.py')
        acceptance = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(acceptance)
        for failure in ('broken_pipe', 'manager_timeout', 'subscription_read', 'game_close'):
            with self.subTest(failure=failure):
                manager = Mock()
                manager.poll.side_effect = [None, 0]
                manager.wait.return_value = 0
                subscriptions = Mock(return_value=0)
                close_game = Mock(return_value={'game_close': {'status': 'game_closed'}, 'game_exit_code': 0})
                if failure == 'broken_pipe':
                    manager.stdin.write.side_effect = BrokenPipeError('stop pipe closed')
                elif failure == 'manager_timeout':
                    manager.wait.side_effect = subprocess.TimeoutExpired('owned-manager', 50)
                elif failure == 'subscription_read':
                    subscriptions.side_effect = RuntimeError('remote read failed')
                else:
                    close_game.side_effect = RuntimeError('owned exit unconfirmed')
                report = {'checks_passed': True, 'failure': 'original operation failed'}
                acceptance.cleanup_session(manager, close_game, subscriptions, report)
                close_game.assert_called_once_with()
                self.assertFalse(report['passed'])
                self.assertTrue(report['cleanup_errors'])
                self.assertEqual(report['failure'], 'original operation failed')
                if failure == 'subscription_read':
                    self.assertNotIn('subscriptions_after_stop', report)
                if failure != 'game_close':
                    self.assertEqual(report['game_exit_code'], 0)
                manager.terminate.assert_not_called()

    def test_levelup_install_failure_restores_code_and_releases_allocation(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import ctypes
        from ctypes import wintypes
        memory = {site: original for site, original in control.LEVELUP_SITES}
        allocation = 0x200000
        protection_calls = []
        def protect(process, address, size, protection, old):
            protection_calls.append((address, protection))
            ctypes.cast(old, ctypes.POINTER(wintypes.DWORD)).contents.value = 0x20
            return True
        def write(kernel, process, address, data):
            if address == control.LEVELUP_SITES[1][0] and data[0] == 0xe9:
                raise RuntimeError('injected patch write failure')
            memory[address] = data
        kernel = SimpleNamespace(VirtualAllocEx=Mock(return_value=allocation), VirtualProtectEx=protect,
                                 VirtualFreeEx=Mock(return_value=True), FlushInstructionCache=Mock(return_value=True))
        probe = SimpleNamespace(read=lambda kernel, process, address, size: memory[address], write=write,
                                checked=lambda result: result)
        with self.assertRaisesRegex(RuntimeError, 'patch write failure'):
            control.install_levelup_observer(probe, kernel, 1)
        for site, original in control.LEVELUP_SITES:
            self.assertEqual(memory[site], original)
            self.assertIn((site, 0x20), protection_calls)
        kernel.VirtualFreeEx.assert_called_once_with(1, allocation, 0, 0x8000)

    def test_levelup_decoder_requires_coherent_bounded_lifetimes(self):
        valid = control.LEVELUP_MAGIC + struct.pack('<10I', 2, 1, 0, 1, 0, 0, 1234, 0, 0, 0)
        self.assertTrue(control.decode_levelup_state(valid)['open'])
        empty = control.LEVELUP_MAGIC + bytes(40)
        self.assertFalse(control.decode_levelup_state(empty)['open'])
        for index, value in ((0, 3), (1, 5), (2, 1), (3, 0), (4, 2), (5, 1), (7, 1234)):
            words = list(struct.unpack('<10I', valid[8:])); words[index] = value
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                control.decode_levelup_state(control.LEVELUP_MAGIC + struct.pack('<10I', *words))
        with self.assertRaises(RuntimeError):
            control.decode_levelup_state(b'wrong')
        probe = Mock()
        probe.checked.side_effect = lambda value: value
        probe.creation_time.return_value = 456
        observer = {'data': 1000, 'patches': ['90909090909090', '909090909090']}
        memory = {1000: valid, 1234: struct.pack('<I', 0xe333bc),
                  1234 + 0x10c: struct.pack('<6I', 2000, 2008, 2008, 3000, 3016, 3016),
                  3000: struct.pack('<4I', 0x175e8690, 0x72747461, 0, 1)}
        for (site, _), code in zip(control.LEVELUP_SITES, observer['patches']):
            memory[site] = bytes.fromhex(code)
        probe.read.side_effect = lambda kernel, process, address, size: memory[address][:size]
        with patch.object(control, 'STATE') as state:
            state.read_text.return_value = json.dumps({'pid': 123, 'created': 456, 'levelup_observer': observer})
            self.assertEqual(control.levelup_state(probe)['selectable_choices'], [3, 4])
            memory[1234 + 0x10c] = struct.pack('<6I', 2000, 2008, 2008, 3000, 3000, 3000)
            self.assertEqual(control.levelup_state(probe)['selectable_choices'], [])
            memory[1234 + 0x10c] = struct.pack('<6I', 2000, 2008, 2008, 3000, 2996, 3000)
            with self.assertRaisesRegex(RuntimeError, 'offer vectors'):
                control.levelup_state(probe)

    @unittest.skipUnless(os.name == 'nt', 'Windows bitmap validation requires PowerShell/System.Drawing')
    def test_capture_rejects_blank_and_two_colour_surfaces_before_ocr(self):
        script_path = Path(__file__).resolve().parents[1] / 'scripts/game-ui.ps1'
        script = r'''
Add-Type -AssemblyName System.Drawing
$tokens = $null; $errors = $null
$syntax = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Capture script has parse errors' }
$function = $syntax.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-RenderedGameFrame' }, $true)
Invoke-Expression $function.Extent.Text
$bitmap = New-Object System.Drawing.Bitmap 64,64
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.Clear([System.Drawing.Color]::Black)
    $black = Test-RenderedGameFrame $bitmap
    $graphics.Clear([System.Drawing.Color]::White)
    $white = Test-RenderedGameFrame $bitmap
    $graphics.Clear([System.Drawing.Color]::Black)
    $graphics.FillRectangle([System.Drawing.Brushes]::White,0,0,8,64)
    $twoColour = Test-RenderedGameFrame $bitmap
    $graphics.FillRectangle([System.Drawing.Brushes]::Red,24,24,16,16)
    $hudOnly = Test-RenderedGameFrame $bitmap
    for ($row=0; $row -lt 32; $row++) {
        for ($column=0; $column -lt 32; $column++) {
            $bitmap.SetPixel($column*2,$row*2,[System.Drawing.Color]::FromArgb($row*7,$column*7,($row+$column)*3))
        }
    }
    $content = Test-RenderedGameFrame $bitmap
    @{black=$black;white=$white;two_colour=$twoColour;hud_only=$hudOnly;content=$content} | ConvertTo-Json -Compress
} finally { $graphics.Dispose(); $bitmap.Dispose() }
'''
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / 'capture-check.ps1'
            harness.write_text(script, encoding='utf-8')
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(harness), str(script_path)],
                                    capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout),
                         {'black': False, 'white': False, 'two_colour': False, 'hud_only': False, 'content': True})

    def test_runtime_decoder_distinguishes_pending_queues_and_rejects_corrupt_chains(self):
        memory = {}
        def put(address, *values):
            for index, value in enumerate(values):memory[address+index*4] = value
        def read(address, size):
            return struct.pack('<' + str(size//4) + 'I', *(memory[address+index*4] for index in range(size//4)))
        put(0xfd2d9c,8000);put(8000,8000)
        put(0x1022bb8,1000,1004);put(1000,2000);put(2000,0,0,3000)
        put(3000+0x28,4000,4004);put(4000,5000)
        put(5000,5100,1,6000,6004,6004,7000);put(7000,7000)
        put(5100,0,21,6100,6100,6100,7100);put(7100,7100)
        state=control.decode_runtime_state(read)
        self.assertFalse(state['event_commands_pending'])
        self.assertEqual(state['script_contexts']['adventure']['registered_listener_count'],1)
        self.assertEqual(state['script_contexts']['combat_console']['registered_listener_count'],0)
        put(8000,8004);put(7000,7010)
        state=control.decode_runtime_state(read)
        self.assertTrue(state['event_commands_pending'])
        self.assertTrue(state['script_contexts']['adventure']['script_commands_pending'])
        put(5000,5000,2)
        with self.assertRaisesRegex(RuntimeError,'bucket chain'):control.decode_runtime_state(read)

    def test_teleport_follows_owned_hero_only_after_stable_position(self):
        start = {'x': 0, 'y': 0, 'floor': 0}
        target = {'x': 6, 'y': 7, 'floor': 1}
        for settled in (target, {'x': 6, 'y': 8, 'floor': 1}):
            with self.subTest(settled=settled), \
                 patch('sys.argv', ['game_control', 'teleport', 'Brem', '6', '7', '--floor', '1']), \
                 patch.object(control, 'hero_state', side_effect=[start, target, dict(settled)]), \
                 patch.object(control, 'execute', return_value={}) as execute, \
                 patch.object(control.time, 'sleep'), \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                if settled == target:
                    control.main()
                    self.assertTrue(json.loads(output.getvalue())['camera_follow_requested'])
                    self.assertEqual(execute.call_count, 2)
                    self.assertIn("GetObjectOwner('Brem')==GetCurrentPlayer()", execute.call_args_list[1].args[1])
                    self.assertIn('MoveCamera(6,7,1,50,1.57079632679,0,1,1)', execute.call_args_list[1].args[1])
                else:
                    with self.assertRaisesRegex(RuntimeError, 'Hero position changed'):
                        control.main()
                    self.assertEqual(execute.call_count, 1)

    def test_serve_forwards_jsonl_without_reloading_the_game(self):
        with patch('sys.argv', ['game_control', 'serve']), \
             patch('sys.stdin', io.StringIO('{}\n{"command":"dev_probe"}\n')), \
             patch.object(control, 'execute', side_effect=[{'pid': 7, 'heartbeat': 4, 'mailbox_status': 3},
                                                           {'pid': 7, 'status': 'dispatched'}]) as execute, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            control.main()
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(replies[0], {'status': 'ready', 'protocol': 'game-control-jsonl-v1'})
        self.assertEqual(replies[1]['response']['heartbeat'], 4)
        self.assertEqual(replies[2]['response']['status'], 'dispatched')
        self.assertEqual(execute.call_count, 2)
        self.assertIsNone(execute.call_args_list[0].args[1])
        self.assertEqual(execute.call_args_list[1].args[1], 'dev_probe')

    def test_reused_command_dispatcher_returns_results_without_stdout_and_revalidates(self):
        with patch.object(control, 'execute', side_effect=[{'pid': 7}, RuntimeError('PID was reused')]) as execute, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(control.main(['status']), {'pid': 7})
            with self.assertRaisesRegex(RuntimeError, 'PID was reused'):
                control.main(['status'])
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(output.getvalue(), '')

    def test_creature_target_is_idempotent_and_rejection_is_not_success(self):
        for before, actual, target in ((5, 12, 12), (12, 12, 12), (10, 1, 0)):
            with self.subTest(before=before, actual=actual, target=target):
                replies = [{'status': 'completed', 'result': str(before)}]
                if before != target:
                    replies += [{'status': 'dispatched'}, {'status': 'completed', 'result': str(actual)}]
                with patch('sys.argv', ['game_control', 'creature', 'Brem', 'CREATURE_PEASANT', '--count', str(target)]), \
                     patch.object(control, 'hero_state', return_value={}), \
                     patch.object(control, 'execute', side_effect=replies) as execute, \
                     patch('sys.stdout', new_callable=io.StringIO) as output:
                    if actual != target:
                        with self.assertRaisesRegex(RuntimeError, 'actual=1'):
                            control.main()
                        self.assertEqual(output.getvalue(), '')
                    else:
                        control.main()
                        self.assertEqual(json.loads(output.getvalue())['count'], target)
                    self.assertEqual(execute.call_count, 1 if before == target else 3)

    def test_resource_requires_readback(self):
        with patch('sys.argv', ['game_control', 'resource', '1', '6', '--amount', '50']), \
             patch.object(control, 'execute', side_effect=[{'result': '100'}, {}, {'result': '100'}]), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaisesRegex(RuntimeError, 'actual=100'):
                control.main()
            self.assertEqual(output.getvalue(), '')


@unittest.skipIf(Uc is None, 'Optional Unicorn/Keystone not installed')
class GameControlTests(unittest.TestCase):
    def test_levelup_lifecycle_observer_balances_objects_and_preserves_original_instructions(self):
        machine = Uc(UC_ARCH_X86, UC_MODE_32)
        code, data = 0x100000, 0x200000
        for address, size in ((code, 4096), (data, 8192), (0x300000, 4096), (0x400000, 4096), (0x600000, 0x100000)):
            machine.mem_map(address, size)
        machine.mem_write(data, control.LEVELUP_MAGIC + bytes(40))
        for created in (True, False):
            machine.mem_write(code + (0 if created else 512), control.levelup_trampoline(code + (0 if created else 512), data, created))
        for created, pointer, expected in ((True, 0x400000, 1), (True, 0x400400, 2),
                                           (False, 0x400400, 1), (False, 0x400000, 0)):
            with self.subTest(created=created, pointer=pointer):
                registers = {UC_X86_REG_EAX: 11, UC_X86_REG_EBX: 22, UC_X86_REG_EDX: 33,
                             UC_X86_REG_EDI: 44, UC_X86_REG_ESI: 55, UC_X86_REG_EBP: pointer,
                             UC_X86_REG_ECX: pointer + 0x148, UC_X86_REG_ESP: 0x300800}
                for register, value in registers.items(): machine.reg_write(register, value)
                machine.reg_write(UC_X86_REG_EFLAGS, 0x246)
                machine.mem_write(pointer + 0x130, struct.pack('<I', 0xbeef))
                index = 0 if created else 1
                site, original = control.LEVELUP_SITES[index]
                machine.emu_start(code + index * 512, site + len(original), count=1000)
                snapshot = control.decode_levelup_state(bytes(machine.mem_read(data, 48)))
                self.assertEqual(len(snapshot['objects']), expected)
                self.assertEqual(machine.reg_read(UC_X86_REG_EFLAGS), 0x246)
                for register, value in registers.items():
                    if not created and register in (UC_X86_REG_EAX, UC_X86_REG_ESI, UC_X86_REG_ESP): continue
                    self.assertEqual(machine.reg_read(register), value)
                if created:
                    self.assertEqual(struct.unpack('<I', machine.mem_read(pointer, 4))[0], 0xe333bc)
                else:
                    self.assertEqual(machine.reg_read(UC_X86_REG_EAX), 0xbeef)
                    self.assertEqual(machine.reg_read(UC_X86_REG_ESI), pointer + 0x148)
                    self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x3007fc)

    def test_position_quantity_and_actor_observers_require_arming_and_bound_records(self):
        from unicorn.x86_const import UC_X86_REG_ESI, UC_X86_REG_EFLAGS
        source = (Path(__file__).resolve().parents[1] / 'scripts/game_control.py').read_text()
        assembly = source.split("        assembly = f'''", 1)[1].split("'''", 1)[0]
        assembly = re.sub(r'\{records(?: \+ (\d+))?\}',
                          lambda match: str(0x305000 + int(match.group(1) or 0)), assembly)
        code = bytes(keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_32).asm(assembly, 0x300000)[0])
        for armed, count, length in ((0, 0, 45), (1, 0, 45), (1, 6, 200), (1, 7, 45)):
            with self.subTest(armed=armed, count=count, name_length=length):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 65536)
                machine.mem_write(0x300000, code)
                machine.mem_write(0x304ff0, b'\xa5' * 832)
                machine.mem_write(0x305000, struct.pack('<4I', armed, count, 0, 123))
                machine.mem_write(0x204000, struct.pack('<2I', 0x205000, 0x205000 + length))
                machine.mem_write(0x205000, b'n' * length)
                machine.mem_write(0x20801c, struct.pack('<2i', 13, 8))
                machine.reg_write(UC_X86_REG_ESP, 0x208000)
                machine.reg_write(UC_X86_REG_ESI, 0x204000)
                machine.reg_write(UC_X86_REG_EDI, 0xabcd)
                machine.reg_write(UC_X86_REG_EFLAGS, 0x646)
                machine.emu_start(0x300000, 0x568239, count=1000)
                header = struct.unpack('<4I', machine.mem_read(0x305000, 16))
                self.assertEqual(header, (armed, count + int(armed and count < 7), int(armed and count >= 7), 123))
                if armed and count < 7:
                    record = 0x305010 + count * 112
                    self.assertEqual(struct.unpack('<2i', machine.mem_read(record, 8)), (13, 8))
                    self.assertEqual(bytes(machine.mem_read(record + 8, min(length, 96))), b'n' * min(length, 96))
                    self.assertEqual(struct.unpack('<I', machine.mem_read(record + 104, 4))[0], min(length, 96))
                else:
                    self.assertEqual(bytes(machine.mem_read(0x305010, 784)), b'\xa5' * 784)
                self.assertEqual(bytes(machine.mem_read(0x304ff0, 16)), b'\xa5' * 16)
                self.assertEqual(bytes(machine.mem_read(0x305320, 16)), b'\xa5' * 16)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x207ffc)
                self.assertEqual(machine.reg_read(UC_X86_REG_EAX), 0x208020)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESI), 0x204000)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDI), 0xabcd)
                self.assertEqual(machine.reg_read(UC_X86_REG_EFLAGS), 0x646)

        assembly = source.split("        quantity_assembly = f'''", 1)[1].split("'''", 1)[0]
        assembly = re.sub(r'\{records(?: \+ (\d+))?\}',
                          lambda match: str(0x305000 + int(match.group(1) or 0)), assembly)
        code = bytes(keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_32).asm(assembly, 0x300200)[0])
        for armed, count, length in ((0, 0, 45), (1, 0, 45), (1, 6, 200), (1, 7, 45)):
            with self.subTest(quantity_armed=armed, quantity_count=count, name_length=length):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 65536)
                machine.mem_write(0x300200, code)
                machine.mem_write(0x304ff0, b'\xa5' * 1632)
                machine.mem_write(0x305000, struct.pack('<4I', armed, 0, 0, 123))
                machine.mem_write(0x305320, struct.pack('<2I', count, 0))
                machine.mem_write(0x204000, struct.pack('<2I', 0x205000, 0x205000 + length))
                machine.mem_write(0x205000, b'n' * length)
                machine.reg_write(UC_X86_REG_ESP, 0x208000)
                machine.reg_write(UC_X86_REG_EBP, 0x204000)
                machine.reg_write(UC_X86_REG_EAX, 321)
                machine.reg_write(UC_X86_REG_ESI, 0x1111)
                machine.reg_write(UC_X86_REG_EDI, 0xabcd)
                machine.reg_write(UC_X86_REG_EFLAGS, 0x646)
                machine.emu_start(0x300200, 0x5688ec, count=1000)
                self.assertEqual(struct.unpack('<2I', machine.mem_read(0x305320, 8)),
                                 (count + int(armed and count < 7), int(armed and count >= 7)))
                if armed and count < 7:
                    record = 0x305330 + count * 112
                    self.assertEqual(struct.unpack('<I', machine.mem_read(record, 4))[0], 321)
                    self.assertEqual(bytes(machine.mem_read(record + 8, min(length, 96))), b'n' * min(length, 96))
                    self.assertEqual(struct.unpack('<I', machine.mem_read(record + 104, 4))[0], min(length, 96))
                else:
                    self.assertEqual(bytes(machine.mem_read(0x305330, 784)), b'\xa5' * 784)
                self.assertEqual(bytes(machine.mem_read(0x305010, 784)), b'\xa5' * 784)
                self.assertEqual(bytes(machine.mem_read(0x305640, 16)), b'\xa5' * 16)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x208000)
                self.assertEqual(machine.reg_read(UC_X86_REG_EAX), 0x208014)
                self.assertEqual(struct.unpack('<I', machine.mem_read(0x208014, 4))[0], 321)
                self.assertEqual(machine.reg_read(UC_X86_REG_EBP), 0x204000)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESI), 0x1111)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDI), 0xabcd)
                self.assertEqual(machine.reg_read(UC_X86_REG_EFLAGS), 0x646)

        assembly = source.split("        actor_assembly = f'''", 1)[1].split("'''", 1)[0]
        assembly = re.sub(r'\{records(?: \+ (\d+))?\}',
                          lambda match: str(0x305000 + int(match.group(1) or 0)), assembly)
        code = bytes(keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_32).asm(assembly, 0x300400)[0])
        for armed, count in ((0, 0), (1, 0), (1, 6), (1, 7)):
            with self.subTest(actor_armed=armed, actor_count=count):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 65536)
                machine.mem_write(0x300400, code)
                machine.mem_write(0x305000, struct.pack('<4I', armed, count, 0, 123))
                machine.mem_write(0x305630, b'\xa5' * 88)
                machine.mem_write(0x204000, struct.pack('<I', 0xe4fba4))
                machine.reg_write(UC_X86_REG_EAX, 0x204000)
                machine.reg_write(UC_X86_REG_EDI, 0xabcd)
                machine.reg_write(UC_X86_REG_ESP, 0x208000)
                machine.reg_write(UC_X86_REG_EFLAGS, 0x246)
                machine.emu_start(0x300400, 0x56822f, count=1000)
                if armed and count < 7:
                    self.assertEqual(struct.unpack('<2I', machine.mem_read(0x305640 + count * 8, 8)),
                                     (0x204000, 0xe4fba4))
                else:
                    self.assertEqual(bytes(machine.mem_read(0x305640, 56)), b'\xa5' * 56)
                self.assertEqual(bytes(machine.mem_read(0x305630, 16)), b'\xa5' * 16)
                self.assertEqual(bytes(machine.mem_read(0x305678, 16)), b'\xa5' * 16)
                self.assertEqual(machine.reg_read(UC_X86_REG_EAX), 0x204000)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDX), 0xe4fba4)
                self.assertEqual(machine.reg_read(UC_X86_REG_ECX), 0x20801c)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDI), 0xabcd)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x207ffc)
                self.assertEqual(machine.reg_read(UC_X86_REG_EFLAGS), 0x246)

    def test_main_thread_mailbox_dispatches_once_with_native_arguments(self):
        for mode, destination in ((0, 0xc125f0), (2, 0xbcf150), (24, 0x493ed0), (25, 0x838c10)):
            with self.subTest(mode=mode):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 16384)
                for page in (0x401000, 0x456000, 0x493000, 0x838000, 0x879000, 0xbcf000, 0xc12000, 0xd10000):
                    machine.mem_map(page, 4096)
                machine.mem_write(0x300000, control.trampoline(0x300000))
                machine.mem_write(0x30100c, struct.pack('<I', 1))
                machine.mem_write(0x301014, struct.pack('<I', mode))
                for function, code in ((0xd10980, b'\xc3'), (0x456220, b'\xc2\x04\x00'),
                                       (0x401ab0, b'\xc2\x04\x00'), (0xc125f0, b'\xc3'),
                                       (0xbcf150, b'\xc2\x08\x00'), (0x493ed0, b'\xc2\x04\x00'),
                                       (0x838c10, b'\xc3'), (0x879240, b'\xc3')):
                    machine.mem_write(function, code)
                calls = []
                def observe(machine, address, size, user_data):
                    if address == destination:
                        stack = machine.reg_read(UC_X86_REG_ESP)
                        calls.append((machine.reg_read(UC_X86_REG_ECX), machine.reg_read(UC_X86_REG_EDX),
                                      bytes(machine.mem_read(stack + 4, 8))))
                machine.hook_add(UC_HOOK_CODE, observe)
                for _ in range(2):
                    machine.mem_write(0x208000, struct.pack('<I', 0x200100))
                    machine.reg_write(UC_X86_REG_ESP, 0x208000)
                    machine.reg_write(UC_X86_REG_ECX, 0x1234)
                    machine.reg_write(UC_X86_REG_EDX, 0x5678)
                    machine.emu_start(0x300000, 0x200100, count=2000)
                    self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x208004)
                    self.assertEqual(machine.reg_read(UC_X86_REG_ECX), 0x1234)
                    self.assertEqual(machine.reg_read(UC_X86_REG_EDX), 0x5678)
                self.assertEqual(len(calls), 1)
                self.assertEqual(struct.unpack('<II', machine.mem_read(0x301008, 8)), (2, 3))
                if mode == 2:
                    self.assertEqual(calls[0][0], 1)
                    self.assertEqual(calls[0][2], struct.pack('<iI', -1, 0))
                elif mode == 24:
                    self.assertEqual(calls[0][1], 0)
                    self.assertEqual(calls[0][2][:4], bytes(4))
                elif mode == 25:
                    self.assertEqual(calls[0][0], 0)

    def test_observer_inspection_recognizes_mailbox_and_native_owner_and_rejects_corruption(self):
        from types import SimpleNamespace
        code = 0x300000
        prefix = control.observer_prefix(code)
        descriptor = code + control.OBSERVER_DESCRIPTOR_OFFSET
        for opcode in (0xe8, 0xe9):
            with self.subTest(opcode=opcode):
                image = prefix + bytes([opcode]) + struct.pack('<i', 0xd10980 - code - len(prefix) - 5)
                if opcode == 0xe8:
                    image += b'\xc3'
                header = struct.pack('<8s6I32s', control.OBSERVER_MAGIC, 1, 320, control.ENTRY,
                                     0xd10980, len(image), len(prefix), control.hashlib.sha256(image).digest())
                memory = {control.ENTRY: b'\xe8' + struct.pack('<i', code - control.ENTRY - 5),
                          code: image, descriptor: header,
                          descriptor + 64: struct.pack('<64I', 1111, *([0] * 62), 2222)}
                probe = SimpleNamespace(read=lambda kernel, process, address, size: memory[address][:size])
                self.assertEqual(control.read_script_observers(probe, None, 1), [1111, 2222])
                for damage in ('version', 'digest', 'prefix', 'bounds', 'transfer'):
                    with self.subTest(damage=damage):
                        broken_header = bytearray(header)
                        broken_image = bytearray(image)
                        if damage == 'version':
                            broken_header[8:12] = struct.pack('<I', 99)
                        elif damage == 'digest':
                            broken_header[32] ^= 1
                        elif damage == 'prefix':
                            broken_image[0] ^= 1
                            broken_header[32:64] = control.hashlib.sha256(broken_image).digest()
                        elif damage == 'bounds':
                            broken_header[24:28] = struct.pack('<I', 4096)
                        else:
                            broken_image[len(prefix)] = 0x90
                            broken_header[32:64] = control.hashlib.sha256(broken_image).digest()
                        memory[descriptor] = bytes(broken_header)
                        memory[code] = bytes(broken_image)
                        with self.assertRaises(RuntimeError):
                            control.read_script_observers(probe, None, 1)
                        memory[descriptor], memory[code] = header, image
                memory[control.ENTRY] = control.ORIGINAL
                self.assertEqual(control.read_script_observers(probe, None, 1), [])

    def test_shared_observers_preserve_machine_state_and_removal_is_independent(self):
        for remove_first in (False, True):
            with self.subTest(remove_first=remove_first):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 16384)
                prefix = control.observer_prefix(0x300000)
                machine.mem_write(0x300000, prefix + b'\xc3')
                assembler = keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_32)
                first = 'inc dword ptr [0x303000]; xor eax, eax; xor ecx, ecx; xor edx, edx; pxor xmm0, xmm0; fninit; fldz; std; ret'
                last = 'pushfd; pop eax; mov dword ptr [0x303004], eax; inc dword ptr [0x303000]; ret'
                machine.mem_write(0x300400, bytes(assembler.asm(first, 0x300400)[0]))
                machine.mem_write(0x300500, bytes(assembler.asm(last, 0x300500)[0]))
                slots = 0x300000 + control.OBSERVER_DESCRIPTOR_OFFSET + 64
                machine.mem_write(slots, struct.pack('<I', 0 if remove_first else 0x300400))
                machine.mem_write(slots + (control.OBSERVER_CAPACITY - 1) * 4, struct.pack('<I', 0x300500))
                # A value immediately beyond capacity must never be called.
                machine.mem_write(slots + control.OBSERVER_CAPACITY * 4, struct.pack('<I', 0xffffffff))
                registers = {UC_X86_REG_EAX: 111, UC_X86_REG_EBX: 222, UC_X86_REG_ECX: 333,
                             UC_X86_REG_EDX: 444, UC_X86_REG_ESI: 555, UC_X86_REG_EDI: 666,
                             UC_X86_REG_EBP: 777, UC_X86_REG_XMM0: 0x123456789abcdef}
                for register, value in registers.items():
                    machine.reg_write(register, value)
                machine.reg_write(UC_X86_REG_EFLAGS, 0x647)
                initial_flags = machine.reg_read(UC_X86_REG_EFLAGS)
                machine.mem_write(0x208000, struct.pack('<I', 0x200100))
                machine.reg_write(UC_X86_REG_ESP, 0x208000)
                machine.emu_start(0x300000, 0x200100, count=2000)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x208004)
                for register, value in registers.items():
                    self.assertEqual(machine.reg_read(register), value)
                self.assertEqual(machine.reg_read(UC_X86_REG_EFLAGS), initial_flags)
                count, callback_flags = struct.unpack('<2I', machine.mem_read(0x303000, 8))
                self.assertEqual(count, 1 if remove_first else 2)
                self.assertFalse(callback_flags & 0x400, 'Each observer must enter with DF clear')

    def test_result_hook_copies_only_owned_key_and_bounds_response(self):
        for key, value, accepted in ((control.RESULT_KEY + ':0123456789abcdef', b'14|49|0', True),
                                      (control.RESULT_KEY + ':0123456789abcdef', b'x' * 3000, True),
                                      (control.RESULT_KEY + ':fedcba9876543210', b'stale', False),
                                      ('another_game_variable', b'private', False)):
            with self.subTest(key=key, size=len(value)):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 16384)
                machine.mem_map(0x5de000, 4096)
                machine.mem_write(0x300400, control.result_trampoline(0x300400, 0x301000))
                machine.mem_write(0x301040, b'0123456789abcdef')
                encoded = key.encode('ascii')
                machine.mem_write(0x201000, encoded)
                machine.mem_write(0x202000, value)
                machine.mem_write(0x200200, struct.pack('<II', 0x201000, 0x201000 + len(encoded)))
                machine.mem_write(0x200220, struct.pack('<II', 0x202000, 0x202000 + len(value)))
                machine.reg_write(UC_X86_REG_EDI, 0x200200)
                machine.reg_write(UC_X86_REG_EBP, 0x200220)
                machine.reg_write(UC_X86_REG_ESP, 0x208000)
                reserved = key.startswith(control.RESULT_KEY + ':')
                machine.emu_start(0x300400, 0x5dee8e if reserved else 0x5dee7b, count=6000)
                size, ready = struct.unpack('<II', machine.mem_read(0x301018, 8))
                self.assertEqual(ready, int(accepted))
                if accepted:
                    self.assertEqual(size, min(2047, len(value)))
                    self.assertEqual(bytes(machine.mem_read(0x301400, size + 1)), value[:size] + b'\0')
                self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x208000)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDI), 0x200200)
                self.assertEqual(machine.reg_read(UC_X86_REG_EBP), 0x200220)
