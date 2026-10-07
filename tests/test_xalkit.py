"""Human CLI project safety and standard gettext catalogs, no game launch."""
import json
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from typer.testing import CliRunner
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import xalkit
from xalkit_ui import text, translations


class XalKitTests(unittest.TestCase):
    def test_resource_pointer_validation_retries_unchanged_pointer_until_files_are_ready(self):
        import xalkit_runtime
        with tempfile.TemporaryDirectory() as directory:
            sdk = Path(directory)
            (sdk / 'runtime').mkdir()
            pointer = sdk / 'runtime/current.json'
            pointer.write_text('{"version": 1, "generation": "new"}', encoding='utf-8')
            active = {'bridge': 'old/core.dll'}
            ready = {'bridge': 'new/core.dll'}
            previous = b'old-pointer'
            with patch.object(xalkit_runtime, 'DEVKIT', sdk), \
                    patch('plugin_core.load_sdk_runtime', side_effect=[FileNotFoundError('still extracting'), ready]) as load, \
                    patch.object(xalkit_runtime, 'stage_resource_runtime', return_value=ready):
                with self.assertRaises(FileNotFoundError):
                    previous, candidate = xalkit_runtime.resource_runtime_candidate(sdk, previous, active)
                self.assertEqual(previous, b'old-pointer')
                previous, candidate = xalkit_runtime.resource_runtime_candidate(sdk, previous, active)
                self.assertEqual(candidate, ready)
                self.assertEqual(load.call_count, 2)
                self.assertEqual(xalkit_runtime.resource_runtime_candidate(sdk, previous, active), (previous, None))
                self.assertEqual(load.call_count, 2)

    def test_resource_bundle_console_rejection_rolls_back_core_and_timeout_never_rolls_back(self):
        import xalkit_runtime
        previous = {'bridge': 'old.dll', 'controller': 'old.exe', 'console': 'old-ui.dll'}
        candidate = {'bridge': 'new.dll', 'controller': 'new.exe', 'console': 'new-ui.dll'}
        client = Mock()
        client.reload_core.return_value = {'status': 'core_applied'}
        client.request.return_value = {'status': 1114}
        result = xalkit_runtime.replace_resource_runtime(client, previous, candidate)
        self.assertEqual(result['status'], 'sdk_runtime_rejected')
        self.assertEqual(client.reload_core.call_args_list[-1].args, ('old.dll', None, 0, 'main'))
        for status in (1279, 1460):
            client.reset_mock()
            client.request.return_value = {'status': status}
            with self.assertRaisesRegex(RuntimeError, 'recovery_unconfirmed'):
                xalkit_runtime.replace_resource_runtime(client, previous, candidate)
            self.assertEqual(client.reload_core.call_count, 1)

    def test_army_shell_completion_reads_public_creature_constants_without_a_game_request(self):
        from zipfile import ZipFile
        import typer
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / '.local/test-game/data'
            data.mkdir(parents=True)
            with ZipFile(data / 'data.pak', 'w') as archive:
                archive.writestr('scripts/common.lua', 'CREATURE_ARCHER=3\nCREATURE_PEASANT=1\nCREATURE_UNKNOWN=0\n')
            group = typer.main.get_command(xalkit.game_app)
            command = group.commands['army']
            parameter = next(value for value in command.params if value.name == 'creature')
            with patch.object(xalkit, 'settings', return_value={'workspace': str(root)}), \
                    patch.object(xalkit, 'game_action') as request:
                with command.make_context('army', ['Brem'], resilient_parsing=True) as context:
                    for prefix in ('CREATURE_AR', 'CREATURE_ARCH'):
                        self.assertEqual([item.value for item in parameter.shell_complete(context, prefix)],
                                         ['CREATURE_ARCHER'])
                    self.assertEqual(parameter.shell_complete(context, 'missing'), [])
                    self.assertEqual([item.value for item in parameter.shell_complete(context, '')],
                                     ['CREATURE_ARCHER', 'CREATURE_PEASANT'])
                request.assert_not_called()

    def test_profile_diagnostics_detect_missing_input_and_do_not_follow_escaped_profile_names(self):
        from sdk_diagnostics import input_profile_files
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sandbox = root / '.local/test-game/UniverseTeam/Universe Mod/Profiles'
            (sandbox / 'WorkshopDev').mkdir(parents=True)
            (sandbox / 'global_a2.cfg').write_text('setvar profile_name = WorkshopDev\n', encoding='utf-8')
            (sandbox / 'WorkshopDev/input_a2.cfg').write_text('fixture bindings', encoding='utf-8')
            documents = root / 'Documents'
            player = documents / 'My Games/Heroes of Might and Magic V - Tribes of the East/Profiles'
            (player / 'Player').mkdir(parents=True)
            settings = player / 'global_a2.cfg'
            settings.write_text('setvar profile_name = Player\n', encoding='utf-8')
            records = input_profile_files(root, documents)
            self.assertEqual([record['present'] for record in records], [True, False])
            self.assertFalse((player / 'Player/input_a2.cfg').exists())
            settings.write_text('setvar profile_name = ../../outside\n', encoding='utf-8')
            self.assertEqual(len(input_profile_files(root, documents)), 1)

    def test_hero_shell_completion_uses_exact_live_owner_without_game_commands(self):
        import typer
        owner = {'pid': 123, 'created': 456}
        state = self.root / '.local/test-state'
        state.mkdir(parents=True)
        (state / 'native-probe.json').write_text(json.dumps(owner), encoding='utf-8')
        log = self.root / '.local/xalkit/logs/events.jsonl'
        log.parent.mkdir(parents=True)
        def roster(created, names):
            return json.dumps({'status': 'game_request_completed', 'action': 'heroes', 'game_pid': 123,
                               'game_created': created, 'result': {'status': 'completed', 'result': names}})
        log.write_text('\n'.join([roster(456, 'OldHero'), roster(456, 'Brem Calid'),
                                  roster(999, 'WrongSession'), 'null', '{partial']), encoding='utf-8')
        probe = Mock()
        kernel = probe.api.return_value
        kernel.OpenProcess.return_value = 42
        kernel.WaitForSingleObject.return_value = 258
        probe.creation_time.return_value = 456
        group = typer.main.get_command(xalkit.game_app)
        with patch.object(xalkit, 'load_backend', return_value=probe), patch.object(xalkit, 'game_action') as request:
            for name in ('army', 'level', 'teleport', 'interact'):
                command = group.commands[name]
                parameter = next(value for value in command.params if value.name == 'hero')
                with command.make_context(name, [], resilient_parsing=True) as context:
                    self.assertEqual([item.value for item in parameter.shell_complete(context, '')], ['Brem', 'Calid'])
                    self.assertEqual([item.value for item in parameter.shell_complete(context, 'Br')], ['Brem'])
            probe.creation_time.return_value = 999
            self.assertEqual(xalkit.complete_heroes(''), [])
            probe.creation_time.return_value = 456
            kernel.WaitForSingleObject.return_value = 0
            self.assertEqual(xalkit.complete_heroes(''), [])
            kernel.WaitForSingleObject.return_value = 258
            kernel.OpenProcess.return_value = 0
            self.assertEqual(xalkit.complete_heroes(''), [])
            request.assert_not_called()
            probe.main.assert_not_called()
        self.assertEqual(kernel.CloseHandle.call_count, 10)

    def test_trace_has_readable_default_and_clean_json_option(self):
        backend = Mock()
        backend.main.return_value = {'records': [{'sequence': 1, 'level': 'warning', 'module': 'alpha',
                                                'message': '[literal markup]'}], 'cursor': 1, 'dropped': 0}
        with patch.object(xalkit, 'load_backend', return_value=backend):
            readable = self.runner.invoke(xalkit.app, ['game', 'trace', '--level', 'warning', '--console'])
            machine = self.runner.invoke(xalkit.app, ['game', 'trace', '--json'])
        self.assertEqual(readable.exit_code, 0, readable.output)
        self.assertIn('[WARNING] alpha: [literal markup]', readable.stdout)
        self.assertIn('--after 1', readable.stdout)
        self.assertEqual(machine.exit_code, 0, machine.output)
        self.assertEqual(json.loads(machine.stdout)['records'][0]['module'], 'alpha')
        self.assertIn('--console', backend.main.call_args_list[0].args[0])

    def test_missing_map_names_file_and_preserves_other_missing_dependency_error(self):
        from xalkit_ui import CommandFailure
        backend = Mock()
        for filename, reason in (
            (self.root / '.local/test-game/Maps/missing.h5m', 'SDK_GAME_MAP_NOT_FOUND'),
            (self.root / '.local/test-state/game-control.json', 'SDK_GAME_COMMAND_FAILED'),
        ):
            backend.main.side_effect = FileNotFoundError(2, 'not found', str(filename))
            with patch.object(xalkit, 'load_backend', return_value=backend):
                result = self.runner.invoke(xalkit.app, ['game', 'map', 'missing'])
            self.assertIsInstance(result.exception, CommandFailure)
            payload = result.exception.payload
            self.assertEqual(payload['details'][0]['reason'], reason)
            if reason == 'SDK_GAME_MAP_NOT_FOUND':
                self.assertEqual(payload['status'], 'NOT_FOUND')
                self.assertIn('missing.h5m', payload['message'])
                self.assertIn('H5M', payload['message'])
        self.assertEqual(backend.main.call_count, 2)

    def test_game_commands_show_domain_results_and_preserve_explicit_json(self):
        long_capture_path = 'C:/Project with spaces/' + 'capture-' + 'a' * 100 + '.png'
        cases = [
            (['heroes'], {'result': 'Brem Calid'}, 'Brem'),
            (['status'], {}, 'Test game connected.'),
            (['screenshot'], {'image_file': long_capture_path}, 'Game screenshot saved: ' + long_capture_path),
            (['level', 'Brem', '20'], {'hero': 'Brem', 'level': 20}, 'Brem: level 20.'),
            (['army', 'Brem', 'CREATURE_ARCHER'],
             {'hero': 'Brem', 'creature': 'CREATURE_ARCHER', 'before': 8, 'count': 8}, 'current count 8'),
            (['resource', '1', '6'], {'player': 1, 'resource': 6, 'before': 100, 'amount': 100}, 'current amount 100'),
            (['teleport', 'Brem', '10', '12'], {'hero': 'Brem', 'x': 10, 'y': 12, 'floor': 0}, 'tile (10, 12)'),
            (['interact', 'Brem', 'Town1'], {}, 'Command completed.'),
        ]
        for arguments, domain_result, expected in cases:
            with self.subTest(command=arguments[0]):
                backend = Mock()
                result = {'pid': 987654, 'status': 'completed', **domain_result}
                backend.main.return_value = result
                with patch.object(xalkit, 'load_backend', return_value=backend):
                    readable = self.runner.invoke(xalkit.app, ['game', *arguments])
                    machine = self.runner.invoke(xalkit.app, ['game', *arguments, '--json'])
                self.assertEqual(readable.exit_code, 0, readable.output)
                self.assertIn(expected, readable.stdout)
                self.assertNotIn('987654', readable.stdout)
                self.assertEqual(machine.exit_code, 0, machine.output)
                self.assertEqual(json.loads(machine.stdout), result)
                self.assertEqual(backend.main.call_count, 2)
                self.assertEqual(backend.main.call_args_list[0], backend.main.call_args_list[1])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {'H5_WORKSPACE': str(self.root),
            'XALKIT_CONFIG': str(self.root / 'settings.json'), 'XALKIT_LANG': 'en'})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.runner = CliRunner()

    def test_native_new_preserves_existing_project_and_records_selection(self):
        result = self.runner.invoke(xalkit.app, ['new', 'demo'])
        self.assertEqual(result.exit_code, 0, result.output)
        source = self.root / 'plugins/demo/plugin.cpp'
        self.assertIn('ModuleName[] = "demo"', source.read_text(encoding='utf-8'))
        self.assertIn('diagnostic_bus.hpp', source.read_text(encoding='utf-8'))
        source.write_text('owner edit', encoding='utf-8')
        result = self.runner.invoke(xalkit.app, ['new', 'demo'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(source.read_text(encoding='utf-8'), 'owner edit')
        saved = json.loads((self.root / 'settings.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['project'], 'demo')
        tasks = json.loads((self.root / 'plugins/demo/.vscode/tasks.json').read_text(encoding='utf-8'))['tasks']
        self.assertEqual(next(task['args'] for task in tasks if task['args'][0] == 'start'), ['start', 'demo'])
        build_task = next(task for task in tasks if task['args'][0] == 'build')
        self.assertEqual(build_task['type'], 'process')
        self.assertEqual(build_task['options']['env']['H5_WORKSPACE'], '${workspaceFolder}/../..')
        self.assertEqual(build_task['problemMatcher']['fileLocation'], 'absolute')
        self.assertEqual(build_task['problemMatcher']['owner'], 'xalkit')
        self.assertEqual(build_task['problemMatcher']['pattern']['line'], 2)
        self.assertTrue(build_task['group']['isDefault'])

    def test_new_rejects_path_traversal(self):
        result = self.runner.invoke(xalkit.app, ['new', '../escape'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse((self.root / 'plugins').exists())

    def test_game_command_records_correlated_success_and_original_failure(self):
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds):
                backend = Mock()
                backend.main.return_value = {'pid': 123, 'status': 'ready'}
                if not succeeds:
                    backend.main.side_effect = RuntimeError('private guarded failure')
                with patch.object(xalkit, 'load_backend', return_value=backend):
                    result = self.runner.invoke(xalkit.app, ['game', 'status'])
                records = [json.loads(line) for line in (self.root / '.local/xalkit/logs/events.jsonl')
                           .read_text(encoding='utf-8').splitlines()]
                terminal = records[-1]
                records = [event for event in records if event['operation_id'] == terminal['operation_id'] and
                           event['status'].startswith('game_request_')]
                self.assertEqual(records[0]['operation_id'], records[1]['operation_id'])
                self.assertEqual(records[1]['stage'], 'game')
                self.assertGreaterEqual(records[1]['duration_seconds'], 0)
                if succeeds:
                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertIn('Test game connected.', result.stdout)
                    self.assertNotIn('123', result.stdout)
                    self.assertEqual(records[1]['result']['pid'], 123)
                else:
                    self.assertNotEqual(result.exit_code, 0)
                    self.assertEqual(records[1]['error']['details'][0]['reason'], 'SDK_GAME_COMMAND_FAILED')
                    self.assertEqual(records[1]['failure_detail'], 'private guarded failure')

    def test_resource_template_has_the_selected_mod_id(self):
        result = self.runner.invoke(xalkit.app, ['new', 'marker', '--resources'])
        self.assertEqual(result.exit_code, 0, result.output)
        recipe = json.loads((self.root / 'mods/marker/mod.json').read_text(encoding='utf-8'))
        self.assertEqual(recipe['id'], 'marker')
        self.assertIn('marker', recipe['append'])
        readme = (self.root / 'mods/marker/README.md').read_text(encoding='utf-8')
        self.assertIn('mod.json', readme)
        self.assertNotIn('plugin.cpp', readme)
        resource_tasks = json.loads((self.root / 'mods/marker/.vscode/tasks.json').read_text(encoding='utf-8'))['tasks']
        self.assertTrue(all(task['problemMatcher'] == [] for task in resource_tasks))

    def test_named_build_and_release_accept_positional_project_and_override_selected_project(self):
        self.runner.invoke(xalkit.app, ['new', 'first', '--resources'])
        self.runner.invoke(xalkit.app, ['new', 'second', '--resources'])
        for action in ('build', 'release'):
            with self.subTest(action=action), patch.object(xalkit, 'build_project', return_value='first.h5u') as build:
                result = self.runner.invoke(xalkit.app, [action, 'first'])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(build.call_args.args[1:4], ('first', self.root / 'mods/first', False))

    def test_resource_build_prepares_missing_sandbox_without_launching(self):
        backend = Mock()
        workshop = backend.Workshop.return_value
        workshop.local = self.root / '.local/test-state'
        workshop.build.return_value = {'artifact': 'mod.h5u'}
        backend.exclusive.return_value.__enter__ = Mock()
        backend.exclusive.return_value.__exit__ = Mock(return_value=False)
        with patch.object(xalkit, 'load_backend', return_value=backend):
            result = xalkit.resource_operation({'workspace': str(self.root)}, 'marker', self.root / 'mods/marker', 'build')
        workshop.prepare.assert_called_once()
        workshop.build.assert_called_once()
        workshop.launch.assert_not_called()
        workshop.deploy.assert_not_called()
        backend.require_stopped.assert_called_once()
        self.assertEqual(result['artifact'], 'mod.h5u')

    def test_hmr_readiness_waits_for_selected_plugin_and_is_reported_once(self):
        import xalkit_runtime
        prepared = self.root / '.local/test-state/prepared.json'
        prepared.parent.mkdir(parents=True)
        prepared.write_text('{}', encoding='utf-8')
        (self.root / 'xalkit.json').write_text('{"display_event": true}', encoding='utf-8')
        kernel = Mock()
        kernel.OpenProcess.return_value = 99
        kernel.GetExitCodeProcess.return_value = True
        probe = Mock()
        probe.api.return_value = kernel
        probe.checked.side_effect = lambda result: result
        probe.creation_time.return_value = 456
        probe.launch.side_effect = lambda process_kernel, **arguments: arguments['before_resume'](
            {'pid': 123, 'created': 456}, process_kernel)
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        process.stdout = [json.dumps(event) for event in (
            {'status': 'applied', 'plugin': 'other'},
            {'status': 'watching', 'plugin': 'native-demo'},
            {'status': 'applied', 'plugin': 'native-demo'},
            {'status': 'core_applied', 'plugin': 'native-demo'},
            {'status': 'core_update_completed', 'plugins': ['native-demo']},
            {'status': 'core_update_completed', 'plugins': ['other']},
            {'status': 'core_update_rejected', 'rolled_back_plugins': ['native-demo']},
            {'status': 'applied', 'plugin': 'native-demo'})]
        tools = (self.root / 'vcvarsall.bat', None, {},
                 {'controller': 'controller.exe', 'bridge': 'bridge.dll', 'launch_gate': 'gate.exe'}, None)
        observed = []
        display_statuses = []
        process.stdin.write.side_effect = lambda value: (
            display_statuses.append(observed[-1]['status']) if json.loads(value).get('command') == 'event' else None)
        with patch.object(xalkit, 'load_backend', side_effect=lambda filename: probe if filename == 'native-probe.py' else Mock()), \
                patch.object(xalkit, 'native_tools', return_value=tools), \
                patch.object(xalkit_runtime, 'EventLog') as event_log, \
                patch.object(xalkit_runtime.subprocess, 'Popen', return_value=process):
            event_log.return_value.emit.side_effect = observed.append
            kernel.WaitForSingleObject.side_effect = lambda *arguments: (
                0 if any(event['status'] == 'session_stop_requested' for event in observed) else 258)
            xalkit_runtime.start_native({'workspace': str(self.root)}, 'native-demo', self.root)
        ready = [index for index, event in enumerate(observed)
                 if event['status'] == 'watching' and 'message' in event]
        self.assertEqual(len(ready), 1)
        selected_applied = next(index for index, event in enumerate(observed)
                                if event['status'] == 'applied' and event.get('plugin') == 'native-demo')
        self.assertEqual(ready[0], selected_applied + 1)
        self.assertEqual(display_statuses, ['watching', 'core_update_completed', 'core_update_rejected', 'applied'])

    def test_game_exit_stops_supervisor_before_waiting(self):
        import xalkit_runtime
        owner_path = self.root / '.local/test-state/native-probe.json'
        owner_path.parent.mkdir(parents=True)
        (owner_path.parent / 'prepared.json').write_text('{}', encoding='utf-8')
        owner_path.write_text(json.dumps({'pid': 123, 'created': 456}), encoding='utf-8')
        kernel = Mock()
        kernel.OpenProcess.side_effect = [99, 0]
        kernel.WaitForSingleObject.return_value = 0
        kernel.GetExitCodeProcess.return_value = True
        probe = Mock()
        probe.api.return_value = kernel
        probe.checked.side_effect = lambda result: result
        probe.creation_time.return_value = 456
        probe.launch.side_effect = lambda process_kernel, **arguments: arguments['before_resume'](
            {'pid': 123, 'created': 456}, process_kernel)
        process = Mock()
        process.stdout = []
        process.poll.return_value = None
        def wait_after_stop(**arguments):
            self.assertIn('"command":"stop"', process.stdin.write.call_args.args[0])
            self.assertEqual(xalkit_runtime.signal.getsignal(xalkit_runtime.signal.SIGINT), xalkit_runtime.signal.SIG_IGN)
            return 0
        process.wait.side_effect = wait_after_stop
        tools = (self.root / 'vcvarsall.bat', None, {},
                 {'controller': 'controller.exe', 'bridge': 'bridge.dll', 'launch_gate': 'gate.exe'}, None)
        previous_interrupt = xalkit_runtime.signal.getsignal(xalkit_runtime.signal.SIGINT)
        with patch.object(xalkit, 'load_backend', side_effect=lambda filename: probe if filename == 'native-probe.py' else Mock()), \
                patch.object(xalkit, 'native_tools', return_value=tools), \
                patch.object(xalkit_runtime, 'EventLog') as event_log, \
                patch.object(xalkit_runtime.subprocess, 'Popen', return_value=process):
            xalkit_runtime.start_native({'workspace': str(self.root)}, 'native-demo', self.root)
        self.assertEqual(probe.launch.call_args.kwargs['launch_gate'], 'gate.exe')
        statuses = [call.args[0]['status'] for call in event_log.return_value.emit.call_args_list]
        self.assertIn('plugins_starting', statuses)
        self.assertNotIn('watching', statuses)
        process.wait.assert_called_once_with(timeout=55)
        self.assertEqual(xalkit_runtime.signal.getsignal(xalkit_runtime.signal.SIGINT), previous_interrupt)
        kernel.OpenProcess.assert_called_once()
        self.assertTrue(any(call.args[0]['status'] == 'game_exited'
                            for call in event_log.return_value.emit.call_args_list))
        kernel.OpenProcess.side_effect = [99]
        runtime = {}
        for role, filename in [('bridge', 'sdk.dll'), ('controller', 'sdk-client.exe'),
                               ('console', 'XalKitConsole.dll'), ('launch_gate', 'sdk-gate.exe'), ('licenses', 'notices.txt')]:
            path = self.root / filename
            path.write_bytes(role.encode())
            runtime[role] = str(path)
        runtime['manifest'] = {'files': {Path(path).name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                       for path in runtime.values()}}
        with patch.object(xalkit, 'load_backend', side_effect=lambda filename: probe if filename == 'native-probe.py' else Mock()), \
                patch.object(xalkit, 'native_tools') as native_build, \
                patch.object(xalkit, 'resource_operation', return_value={'status': 'deployed'}) as deploy, \
                patch('plugin_core.load_sdk_runtime', return_value=runtime), \
                patch('plugin_core.CoreClient') as sdk_client, \
                patch('sdk_console.ConsoleBroker') as broker, \
                patch.object(xalkit_runtime, 'OwnedBackgroundBudget'), \
                patch.object(xalkit_runtime, 'EventLog'), \
                patch.object(xalkit_runtime.subprocess, 'Popen') as supervisor:
            sdk_client.return_value.request.return_value = {'status': 0}
            sdk_client.return_value.poll.return_value = None
            broker.return_value.port = 1234
            broker.return_value.token = 'fixture-token'
            xalkit_runtime.start_session({'workspace': str(self.root)}, 'marker', self.root, resources=True)
        native_build.assert_not_called()
        supervisor.assert_not_called()
        deploy.assert_called_once_with({'workspace': str(self.root)}, 'marker', self.root, 'deploy')
        sdk_client.return_value.request.assert_called_once_with('connect')
        sdk_client.return_value.close.assert_called_once()
        broker.return_value.close.assert_called_once()
        report = json.loads((self.root / '.local/xalkit/logs/session-latest.json').read_text(encoding='utf-8'))
        self.assertEqual(report['kind'], 'resources')
        self.assertTrue(report['passed'])
        self.assertEqual(report['game_exit_code'], 0)

        kernel.OpenProcess.side_effect = [99]
        failed_response = {'status': 111, 'result': 9, 'generation': 0}
        with patch.object(xalkit, 'load_backend', side_effect=lambda filename: probe if filename == 'native-probe.py' else Mock()), \
                patch.object(xalkit, 'resource_operation', return_value={'status': 'deployed'}), \
                patch('plugin_core.load_sdk_runtime', return_value=runtime), \
                patch('plugin_core.CoreClient') as sdk_client, \
                patch('plugin_core.decode_diagnostics', return_value={'records': [
                    {'level': 'error', 'message': 'console_device_failed', 'module': 'xkit.console', 'sequence': 1}
                ]}), \
                patch('sdk_console.ConsoleBroker') as broker, \
                patch.object(xalkit_runtime, 'EventLog') as event_log:
            sdk_client.return_value.request.side_effect = [failed_response, {'status': 0, 'diagnostics': 'fixture'}]
            broker.return_value.port = 1234
            broker.return_value.token = 'fixture-token'
            with self.assertRaises(RuntimeError):
                xalkit_runtime.start_session({'workspace': str(self.root)}, 'marker', self.root, resources=True)
        failures = [call.args[0] for call in event_log.return_value.emit.call_args_list
                    if call.args[0].get('status') == 'sdk_connect_failed']
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['response'], failed_response)
        diagnostics = [call.args[0] for call in event_log.return_value.emit.call_args_list
                       if call.args[0].get('status') == 'sdk_startup_diagnostic']
        self.assertEqual(diagnostics[0]['message'], 'console_device_failed')
        self.assertEqual(diagnostics[0]['level'], 'error')
        self.assertEqual(diagnostics[0]['game_pid'], 123)
        self.assertEqual((failures[0]['game_pid'], failures[0]['game_created']), (123, 456))
        self.assertEqual([call.args[0] for call in sdk_client.return_value.request.call_args_list],
                         ['connect', 'trace 0 2'])
        sdk_client.return_value.close.assert_called_once()

        kernel.OpenProcess.side_effect = [99]
        with patch.object(xalkit, 'load_backend', side_effect=lambda filename: probe if filename == 'native-probe.py' else Mock()), \
                patch.object(xalkit, 'resource_operation', return_value={'status': 'deployed'}), \
                patch('plugin_core.load_sdk_runtime', return_value=runtime), \
                patch('plugin_core.CoreClient') as sdk_client, \
                patch('sdk_console.ConsoleBroker') as broker, \
                patch.object(xalkit_runtime, 'EventLog') as event_log:
            sdk_client.return_value.request.side_effect = [failed_response, RuntimeError('diagnostics disconnected')]
            broker.return_value.port = 1234
            broker.return_value.token = 'fixture-token'
            with self.assertRaises(RuntimeError):
                xalkit_runtime.start_session({'workspace': str(self.root)}, 'marker', self.root, resources=True)
        statuses = [call.args[0]['status'] for call in event_log.return_value.emit.call_args_list]
        self.assertIn('sdk_connect_failed', statuses)
        self.assertIn('sdk_startup_diagnostics_unavailable', statuses)
        sdk_client.return_value.close.assert_called_once()

        monitor_path = self.root / '.local/tools/procdump/procdump.exe'
        monitor_path.parent.mkdir(parents=True)
        monitor_path.write_bytes(b'fixture')
        for scenario in ('game_error', 'supervisor_error', 'supervisor_already_failed', 'stop_timeout', 'dump', 'monitor_error'):
            with self.subTest(scenario=scenario):
                kernel.OpenProcess.side_effect = [99]
                process.reset_mock()
                process.poll.return_value = 1 if scenario == 'supervisor_already_failed' else None
                process.wait.side_effect = (
                    xalkit.subprocess.TimeoutExpired('fixture', 55) if scenario == 'stop_timeout' else None)
                process.wait.return_value = 1 if scenario == 'supervisor_error' else 0
                def get_exit_code(handle, pointer):
                    pointer._obj.value = 7 if scenario == 'game_error' else 0
                    return True
                kernel.GetExitCodeProcess.side_effect = get_exit_code
                monitor = Mock()
                monitor.snapshot.return_value = {}
                monitor.close.return_value = {'capture_status': 'dump_captured' if scenario == 'dump' else 'stopped'}
                if scenario == 'monitor_error':
                    monitor.close.side_effect = RuntimeError('monitor stop uncertain')
                with patch.object(xalkit, 'load_backend', side_effect=lambda filename: probe if filename == 'native-probe.py' else Mock()), \
                        patch.object(xalkit, 'native_tools', return_value=tools), \
                        patch.object(xalkit_runtime, 'EventLog') as event_log, \
                        patch.object(xalkit_runtime, 'OwnedCrashMonitor', return_value=monitor), \
                        patch.object(xalkit_runtime, 'read_owned_windows_events', return_value=[]), \
                        patch.object(xalkit_runtime.subprocess, 'Popen', return_value=process):
                    with self.assertRaisesRegex(RuntimeError, 'development session failed'):
                        xalkit_runtime.start_native({'workspace': str(self.root)}, 'native-demo', self.root)
                monitor.close.assert_called_once()
                self.assertEqual(xalkit_runtime.signal.getsignal(xalkit_runtime.signal.SIGINT), previous_interrupt)
                failure = next(call.args[0] for call in event_log.return_value.emit.call_args_list
                               if call.args[0]['status'] == 'session_failed')
                self.assertEqual(failure['error']['details'][0]['reason'], 'SDK_SESSION_FAILED')

        kernel.OpenProcess.side_effect = [99]
        kernel.GetExitCodeProcess.side_effect = None
        def fail_before_resume(process_kernel, **arguments):
            arguments['before_resume']({'pid': 123, 'created': 456}, process_kernel)
            raise RuntimeError('launch interrupted before resume')
        probe.launch.side_effect = fail_before_resume
        with patch.object(xalkit, 'load_backend', side_effect=lambda filename: probe if filename == 'native-probe.py' else Mock()), \
                patch.object(xalkit, 'native_tools', return_value=tools), \
                patch.object(xalkit_runtime, 'EventLog') as event_log, \
                patch.object(xalkit_runtime, 'OwnedCrashMonitor') as monitor_factory, \
                patch.object(xalkit_runtime.Path, 'replace', side_effect=OSError('report persistence failed')), \
                patch.object(xalkit_runtime, 'read_owned_windows_events', return_value=[]), \
                patch.object(xalkit_runtime.subprocess, 'Popen') as supervisor_factory:
            with self.assertRaisesRegex(RuntimeError, 'launch interrupted before resume'):
                xalkit_runtime.start_native({'workspace': str(self.root)}, 'native-demo', self.root)
        monitor_factory.assert_not_called()
        supervisor_factory.assert_not_called()
        event_log.return_value.close.assert_called_once()
        kernel.CloseHandle.assert_called_with(99)
        self.assertTrue(any(call.args[0]['status'] == 'game_owned_before_resume'
                            for call in event_log.return_value.emit.call_args_list))

    def test_diagnostics_empty_has_recovery_and_never_launches_backend(self):
        with patch.object(xalkit, 'load_backend') as backend:
            result = self.runner.invoke(xalkit.app, ['diagnostics'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Run xkit start or xkit check first', result.output)
        self.assertIn('Download ProcDump from Microsoft', result.output)
        backend.assert_not_called()

    def test_diagnostics_json_selects_latest_monitor_without_parsing_private_log_contents(self):
        local = self.root / '.local/xalkit'
        for relative in ('logs/events.jsonl', 'check/report.json', 'check/logs/backend.log',
                         'logs/events.jsonl.1.gz', 'check/logs/events.jsonl.2.gz',
                         'crashes/older/procdump.log', 'crashes/latest/procdump.log',
                         'crashes/older/old.dmp', 'crashes/latest/new.dmp'):
            artifact = local / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b'private fixture contents, deliberately not JSON')
        os.utime(local / 'crashes/older/procdump.log', (10, 10))
        os.utime(local / 'crashes/latest/procdump.log', (20, 20))
        (local / 'crashes/latest/windows-events.json').write_text(json.dumps({
            'owner': {'entry': 'private-address'},
            'events': [{'module': 'granny2.dll', 'timestamp': 'fixture', 'fault_address': 'private-address'}]
        }), encoding='utf-8')
        with patch.object(xalkit, 'load_backend') as backend:
            result = self.runner.invoke(xalkit.app, ['diagnostics', '--json'])
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.output)
        self.assertEqual(len(report['artifacts']), 8)
        self.assertEqual(len([item for item in report['artifacts'] if item['kind'] == 'log_archive']), 2)
        self.assertFalse(report['monitor_signature_checked'])
        self.assertFalse(report['monitor_file_present'])
        self.assertEqual([Path(item['path']).name for item in report['artifacts']
                          if item['kind'] == 'exception_dump'], ['new.dmp'])
        self.assertNotIn('private fixture contents', result.output)
        self.assertNotIn('private-address', result.output)
        self.assertEqual(report['windows_event_summary'], [{'module': 'granny2.dll', 'timestamp': 'fixture'}])
        backend.assert_not_called()

    def test_project_completion_is_sorted_filters_prefix_and_ignores_unmanaged_directories(self):
        self.runner.invoke(xalkit.app, ['new', 'native-demo'])
        self.runner.invoke(xalkit.app, ['new', 'marker', '--resources'])
        (self.root / 'plugins' / 'unfinished').mkdir()
        (self.root / 'mods' / 'Bad Name').mkdir()
        (self.root / 'mods/Bad Name/mod.json').write_text('{}', encoding='utf-8')
        self.assertEqual(xalkit.complete_projects(''), ['marker', 'native-demo'])
        self.assertEqual(xalkit.complete_projects('NAT'), ['native-demo'])
        self.assertEqual(xalkit.complete_projects('missing'), [])

    def test_gettext_ru_en_and_unknown_locale_fallback(self):
        with patch.dict(os.environ, {'XALKIT_LANG': 'ru'}):
            self.assertIn('тестовую', text('start'))
        with patch.dict(os.environ, {'XALKIT_LANG': 'en'}):
            self.assertIn('test game', text('start'))
        with patch.dict(os.environ, {'XALKIT_LANG': 'xx'}):
            self.assertEqual(text('start'), translations('en').gettext('xalkit.start'))

    def test_check_runs_canonical_backend_without_selecting_a_project_and_reports_result(self):
        prepared = self.root / '.local/test-state/prepared.json'
        prepared.parent.mkdir(parents=True)
        prepared.write_text('{}', encoding='utf-8')
        process = Mock()
        report_path = str(self.root / 'report.json')
        process.stdout = iter([json.dumps({'step': 'owned_launch'}) + '\n',
                               json.dumps({'report': report_path, 'passed': True}) + '\n'])
        process.wait.return_value = 0
        backend = Mock()
        built = {'launch_gate': str(self.root / 'sdk_launch_gate.exe'), 'graphics': str(self.root / 'd3d9.dll')}
        tools = (self.root / 'vcvarsall.bat', self.root / 'cl.exe', {}, built, self.root / 'loader.dll')
        with patch.object(xalkit, 'load_backend', return_value=backend), \
                patch.object(xalkit, 'native_tools', return_value=tools), \
                patch.object(xalkit.subprocess, 'Popen', return_value=process) as spawn:
            result = self.runner.invoke(xalkit.app, ['check'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('SDK check passed', result.output)
        self.assertIn(report_path, result.output.replace('\n', ''))
        backend.require_stopped.assert_called_once()
        backend.build.assert_called_once_with()
        arguments = spawn.call_args.args[0]
        self.assertEqual(Path(arguments[3]), xalkit.DEVKIT / 'scripts/plugin-control-check.py')
        self.assertIn('--native-owner', arguments)
        self.assertEqual(arguments[arguments.index('--launch-gate') + 1], built['launch_gate'])
        self.assertEqual(arguments[arguments.index('--graphics-facade') + 1], built['graphics'])
        self.assertFalse((self.root / 'plugins').exists())

    def test_console_check_uses_owned_session_without_deploying_a_project(self):
        prepared = self.root / '.local/test-state/prepared.json'
        prepared.parent.mkdir(parents=True)
        prepared.write_text('{}', encoding='utf-8')
        polygon = self.root / '.local/test-game/Maps/WorkshopPolygon.h5m'
        polygon.parent.mkdir(parents=True)
        polygon.touch()
        session = {'passed': True, 'duration_seconds': 12.5,
                   'console_validation': {'passed': True, 'report': str(self.root / 'console-report.json')}}
        with patch.object(xalkit, 'load_backend') as backend, \
                patch('xalkit_runtime.start_session', return_value=session) as start:
            result = self.runner.invoke(xalkit.app, ['check', '--console'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Console check passed', result.output)
        self.assertEqual(start.call_args.args[1:], ('sdk-console-check', None, 'WorkshopPolygon'))
        self.assertEqual(start.call_args.kwargs, {'resources': True, 'console_check': True})
        backend.return_value.build.assert_not_called()
        self.assertFalse((self.root / 'plugins').exists())
        with patch('xalkit_runtime.start_session') as start:
            result = self.runner.invoke(xalkit.app, ['check', '--console', '--player'])
        self.assertEqual(result.exit_code, 2, result.output)
        start.assert_not_called()

    def test_check_keeps_existing_test_map(self):
        prepared = self.root / '.local/test-state/prepared.json'
        prepared.parent.mkdir(parents=True)
        prepared.write_text('{}', encoding='utf-8')
        polygon = self.root / '.local/test-game/Maps/WorkshopPolygon.h5m'
        polygon.parent.mkdir(parents=True)
        polygon.write_bytes(b'existing map')
        process = Mock()
        process.stdout = iter([json.dumps({'report': 'report.json', 'passed': True}) + '\n'])
        process.wait.return_value = 0
        backend = Mock()
        tools = (self.root / 'vcvarsall.bat', self.root / 'cl.exe', {},
                 {'launch_gate': 'gate.exe', 'graphics': 'd3d9.dll'}, self.root / 'loader.dll')
        with patch.object(xalkit, 'load_backend', return_value=backend), \
                patch.object(xalkit, 'native_tools', return_value=tools), \
                patch.object(xalkit.subprocess, 'Popen', return_value=process):
            result = self.runner.invoke(xalkit.app, ['check'])
        self.assertEqual(result.exit_code, 0, result.output)
        backend.build.assert_not_called()
        self.assertEqual(polygon.read_bytes(), b'existing map')

    def test_check_failure_keeps_raw_backend_details_out_of_console_and_emits_error_info(self):
        prepared = self.root / '.local/test-state/prepared.json'
        prepared.parent.mkdir(parents=True)
        prepared.write_text('{}', encoding='utf-8')
        process = Mock()
        process.stdout = iter(['private-backend-detail\n', json.dumps({'report': 'failed.json', 'passed': False}) + '\n'])
        process.wait.return_value = 1
        tools = (self.root / 'vcvarsall.bat', self.root / 'cl.exe', {},
                 {'launch_gate': 'gate.exe', 'graphics': 'd3d9.dll'}, self.root / 'loader.dll')
        with patch.object(xalkit, 'load_backend', return_value=Mock()), \
                patch.object(xalkit, 'native_tools', return_value=tools), \
                patch.object(xalkit.subprocess, 'Popen', return_value=process):
            result = self.runner.invoke(xalkit.app, ['check'])
        self.assertEqual(result.exit_code, 1)
        self.assertNotIn('private-backend-detail', result.output)
        logs = self.root / '.local/xalkit/check/logs'
        self.assertIn('private-backend-detail', (logs / 'backend.log').read_text(encoding='utf-8'))
        events = [json.loads(line) for line in (logs / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
        failure = next(event for event in events if event['status'] == 'check_failed')['error']
        self.assertEqual(failure['status'], 'INTERNAL')
        self.assertEqual(failure['details'][0]['reason'], 'SDK_CHECK_FAILED')
        self.assertIn('diagnostic', failure['details'][0]['metadata'])

    def test_check_cancellation_drains_report_and_distinguishes_confirmed_cleanup(self):
        prepared = self.root / '.local/test-state/prepared.json'
        prepared.parent.mkdir(parents=True)
        prepared.write_text('{}', encoding='utf-8')
        for cleanup_failed in (False, True):
            with self.subTest(cleanup_failed=cleanup_failed):
                def interrupted_output():
                    yield json.dumps({'step': 'owned_launch'}) + '\n'
                    raise KeyboardInterrupt
                process = Mock()
                process.stdout = interrupted_output()
                process.poll.return_value = None
                report = {'report': 'cancel-report.json', 'passed': False, 'cancelled': True,
                          'cleanup_errors': [{'stage': 'game_close'}] if cleanup_failed else [],
                          'manager_exit_code': 0, 'game_exit_code': 0, 'subscriptions_after_stop': 0}
                process.communicate.return_value = (json.dumps(report) + '\n', None)
                tools = (self.root / 'vcvarsall.bat', self.root / 'cl.exe', {},
                         {'launch_gate': 'gate.exe', 'graphics': 'd3d9.dll'}, self.root / 'loader.dll')
                with patch.object(xalkit, 'load_backend', return_value=Mock()), \
                        patch.object(xalkit, 'native_tools', return_value=tools), \
                        patch.object(xalkit.subprocess, 'Popen', return_value=process):
                    result = self.runner.invoke(xalkit.app, ['check'])
                self.assertEqual(result.exit_code, 1 if cleanup_failed else 130, result.output)
                process.send_signal.assert_called_once()
                process.communicate.assert_called_once_with(timeout=50)
                logs = self.root / '.local/xalkit/check/logs'
                self.assertIn('cancel-report.json', (logs / 'backend.log').read_text(encoding='utf-8'))
                events = [json.loads(line) for line in (logs / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
                cancelled = next(event for event in reversed(events) if event['status'] in
                                 ('check_rejected', 'check_cleanup_unconfirmed'))
                self.assertEqual(cancelled['cleanup_confirmed'], not cleanup_failed)
                self.assertEqual(cancelled['report'], 'cancel-report.json')
                self.assertEqual(cancelled['error']['details'][0]['reason'],
                                 'SDK_CHECK_CLEANUP_UNCONFIRMED' if cleanup_failed else 'SDK_CHECK_CANCELLED')

    def test_player_check_uses_fresh_development_report_and_never_promotes_failed_delivery(self):
        prepared = self.root / '.local/test-state/prepared.json'
        prepared.parent.mkdir(parents=True)
        prepared.write_text('{}', encoding='utf-8')
        for player_passed in (True, False):
            with self.subTest(player_passed=player_passed):
                development = Mock()
                development.stdout = iter([json.dumps({'report': 'fresh-development.json', 'passed': True}) + '\n'])
                development.wait.return_value = 0
                player = Mock()
                player.stdout = iter([json.dumps({'report': 'player.json', 'passed': player_passed}) + '\n'])
                player.wait.return_value = 0 if player_passed else 1
                tools = (self.root / 'vcvarsall.bat', self.root / 'cl.exe', {},
                         {'controller': 'controller.exe', 'bridge': 'bridge.dll',
                          'launch_gate': 'gate.exe', 'graphics': 'd3d9.dll'}, self.root / 'loader.dll')
                with patch.object(xalkit, 'load_backend', return_value=Mock()), \
                        patch.object(xalkit, 'native_tools', return_value=tools), \
                        patch.object(xalkit.subprocess, 'Popen', side_effect=[development, player]) as spawn:
                    result = self.runner.invoke(xalkit.app, ['check', '--player'])
                self.assertEqual(result.exit_code, 0 if player_passed else 1, result.output)
                self.assertEqual(spawn.call_count, 2)
                arguments = spawn.call_args_list[1].args[0]
                self.assertEqual(Path(arguments[3]), xalkit.DEVKIT / 'scripts/plugin-player-check.py')
                self.assertEqual(arguments[arguments.index('--development-report') + 1], 'fresh-development.json')
                if player_passed:
                    combined = json.loads((self.root / '.local/xalkit/check/report.json').read_text(encoding='utf-8'))
                    self.assertEqual(combined['development_report'], 'fresh-development.json')
                    self.assertEqual(combined['player_report'], 'player.json')
                else:
                    self.assertNotIn('SDK check passed', result.output)

    def test_player_backend_rejects_changed_api_dependency_and_restores_remaining_files_after_one_failure(self):
        specification = importlib.util.spec_from_file_location('player_acceptance_test',
            xalkit.DEVKIT / 'scripts/plugin-player-check.py')
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        sdk = self.root / 'sdk'
        api = sdk / 'game-api/include/h5'
        api.mkdir(parents=True)
        header = api / 'hooks.hpp'
        header.write_bytes(b'accepted API')
        proof = {'source_hashes': {'game-api/hooks.hpp': hashlib.sha256(header.read_bytes()).hexdigest()}}
        module.verify_development_sources(proof, sdk)
        accepted_core = {'CMakeLists.txt': 'accepted-project', 'extra.cpp': 'accepted-helper'}
        core_history = [{'status': 'core_built', 'source_hashes': accepted_core},
                        {'status': 'core_update_completed'},
                        {'status': 'core_built', 'source_hashes': {'extra.cpp': 'rejected-helper'}},
                        {'status': 'core_update_rejected'}]
        module.verify_accepted_core(core_history, dict(accepted_core))
        from plugin_core import CoreBuild
        core_source = self.root / 'core-source'
        core_source.mkdir()
        project_file = core_source / 'CMakeLists.txt'
        original_project = b'target_compile_definitions(heroes5_plugin_bridge PRIVATE H5_CORE_VERSION=2)\r\n'
        project_file.write_bytes(original_project)
        (core_source / 'extra.cpp').write_bytes(b'unsigned SdkValue() { return 99; }\r\n')
        with patch('plugin_core.shutil.which', return_value=sys.executable):
            builder = CoreBuild(core_source, self.root / 'guard', {'PATH': ''})
        actual_accepted = builder.snapshot()
        export_file = core_source / 'exports.def'
        export_file.write_text('EXPORTS\nFirstFunction\n', encoding='utf-8')
        with_export = builder.snapshot()
        self.assertIn('exports.def', with_export)
        export_file.write_text('EXPORTS\nFirstFunction\nNewFunction\n', encoding='utf-8')
        self.assertNotEqual(with_export, builder.snapshot())
        export_file.unlink()
        rejected_project = original_project.replace(b'VERSION=2', b'VERSION=3') + \
            b'target_compile_definitions(heroes5_plugin_bridge PRIVATE H5_CORE_REJECT_SLOT=1)\r\n'
        project_file.write_bytes(rejected_project)
        actual_snapshot = builder.snapshot()
        actual_snapshot['CMakeLists.txt'] = hashlib.sha256(module.restore_accepted_core_project(
            project_file.read_bytes())).hexdigest()
        module.verify_accepted_core([{'status': 'core_built', 'source_hashes': actual_accepted},
                                     {'status': 'core_update_completed'}], actual_snapshot)
        for drift in ({'CMakeLists.txt': 'accepted-project', 'extra.cpp': 'changed-helper'},
                      {'CMakeLists.txt': 'accepted-project'},
                      {**accepted_core, 'unexpected.cpp': 'new-helper'}):
            with self.assertRaisesRegex(RuntimeError, 'last accepted core'):
                module.verify_accepted_core(core_history, drift)
        header.write_bytes(b'changed API')
        with self.assertRaisesRegex(RuntimeError, 'source changed'):
            module.verify_development_sources(proof, sdk)
        first = self.root / 'alpha.dll'; second = self.root / 'beta.dll'; loader = self.root / 'dinput8.dll'
        first.write_bytes(b'changed externally'); second.write_bytes(b'owned beta'); loader.write_bytes(b'owned loader')
        installed = {first: hashlib.sha256(b'owned alpha').hexdigest(), second: hashlib.sha256(second.read_bytes()).hexdigest()}
        errors = []
        module.restore_player_files(installed, loader, hashlib.sha256(loader.read_bytes()).hexdigest(), b'original loader', errors)
        self.assertTrue(first.exists(), 'Externally changed DLL must be retained')
        self.assertFalse(second.exists(), 'One failed restore must not skip the other owned DLL')
        self.assertEqual(loader.read_bytes(), b'original loader')
        self.assertEqual(errors[0]['stage'], 'restore_plugin')


if __name__ == '__main__':
    unittest.main()
