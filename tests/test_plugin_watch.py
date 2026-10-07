"""Game-free watcher failure boundaries; real native lifecycle uses CTest."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import struct
import tempfile
import time
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
specification = importlib.util.spec_from_file_location(
    'plugin_watch_test', Path(__file__).resolve().parents[1] / 'scripts/plugin-watch.py')
watch_module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(watch_module)


class PluginWatchBoundaries(unittest.TestCase):
    def test_core_controller_isolates_parent_console_signal_group(self):
        import plugin_core
        with tempfile.TemporaryDirectory() as directory:
            client = object.__new__(plugin_core.CoreClient)
            client.directory = Path(directory)
            client.arguments = ['controller.exe', 'owned-pid', 'bridge.dll']
            client.response = Mock(return_value={'ready': True})
            process = Mock()
            process.poll.return_value = None
            process.wait.return_value = 0
            with patch.object(plugin_core.shutil, 'copyfile'), \
                    patch.object(plugin_core.subprocess, 'Popen', return_value=process) as spawn, \
                    patch.object(plugin_core.threading, 'Thread'):
                self.assertIs(client.start(Path('bridge.dll')), process)
            expected = plugin_core.subprocess.CREATE_NEW_PROCESS_GROUP if plugin_core.os.name == 'nt' else 0
            self.assertEqual(spawn.call_args.kwargs['creationflags'], expected)
            client.close(process)
            process.stdin.write.assert_called_once_with('quit\n')

    def test_graphics_staging_restores_original_and_preserves_foreign_changes(self):
        import hashlib
        import xalkit_runtime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            game = root / '.local/test-game'
            (game / 'bin').mkdir(parents=True)
            state = root / '.local/test-state'
            state.mkdir()
            (state / 'prepared.json').write_text(json.dumps({'game': str(game), 'status': 'complete', 'profile': 'WorkshopDev'}))
            graphics = game / 'bin/d3d9.dll'
            graphics.write_bytes(b'original')
            facade = root / 'facade.dll'
            facade.write_bytes(b'facade')
            original_digest = '5eb152357f99d53397b764384d5cf9a0f6aece733ced30a34186ac57fb15be25'
            boundary_hash = SimpleNamespace(sha256=lambda content: SimpleNamespace(hexdigest=lambda: original_digest)
                if content == b'original' else hashlib.sha256(content))
            with patch('sdk_storage.require_games_closed'), patch.object(xalkit_runtime, 'hashlib', boundary_hash):
                staged = xalkit_runtime.stage_owned_graphics(root, facade)
                self.assertEqual(graphics.read_bytes(), b'facade')
                self.assertEqual(staged['retained'].read_bytes(), b'original')
                graphics.write_bytes(b'foreign')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    xalkit_runtime.restore_owned_graphics(staged)
                self.assertEqual(graphics.read_bytes(), b'foreign')
                self.assertEqual(staged['retained'].read_bytes(), b'original')
                graphics.write_bytes(b'facade')
                xalkit_runtime.restore_owned_graphics(staged)
                self.assertEqual(graphics.read_bytes(), b'original')
                self.assertFalse(staged['retained'].exists())
                receipt = {}
                real_rename = xalkit_runtime.os.rename
                def interrupt_after_rename(source, target):
                    real_rename(source, target)
                    raise KeyboardInterrupt()
                with patch.object(xalkit_runtime.os, 'rename', side_effect=interrupt_after_rename):
                    with self.assertRaises(KeyboardInterrupt):
                        xalkit_runtime.stage_owned_graphics(root, facade, receipt=receipt)
                self.assertFalse(graphics.exists())
                self.assertEqual(receipt['retained'].read_bytes(), b'original')
                xalkit_runtime.restore_owned_graphics(receipt)
                self.assertEqual(graphics.read_bytes(), b'original')
                self.assertFalse(receipt['retained'].exists())
                with patch.object(xalkit_runtime.os, 'replace', side_effect=OSError('injected replace failure')):
                    with self.assertRaisesRegex(OSError, 'injected'):
                        xalkit_runtime.stage_owned_graphics(root, facade)
                self.assertEqual(graphics.read_bytes(), b'original')
                self.assertFalse(staged['retained'].exists())

    def test_owned_console_borrows_sealed_endpoint_without_replay_or_payload_stop(self):
        import hashlib
        import threading
        import plugin_core
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watch = root / '.local/xalkit/watch'
            watch.mkdir(parents=True)
            endpoint = {'version': 1, 'pid': 123, 'created': 456}
            for key in ('controller', 'bridge'):
                path = watch / key
                path.write_bytes(key.encode())
                endpoint[key] = str(path)
                endpoint[key + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            metadata = watch / 'diagnostic-owner.json'
            metadata.write_text(json.dumps(endpoint), encoding='utf-8')
            response = Mock(returncode=0, stdout='{"ready":true}\n{"status":0,"dispatch_returned":true}\n', stderr='')
            with patch.object(plugin_core.subprocess, 'run', return_value=response) as run:
                result = plugin_core.dispatch_owned_console(root, {'pid': 123, 'created': 456}, 'help')
                self.assertFalse(result['effect_verified'])
                self.assertEqual(run.call_args.args[0][1], '--owned-command')
                self.assertEqual(run.call_args.kwargs['input'], 'console help\nquit\n')
                run.assert_called_once()
            for command, owner in [('help\nexit', {'pid': 123, 'created': 456}),
                                   ('help', {'pid': 124, 'created': 456})]:
                with self.subTest(command=command, owner=owner), patch.object(plugin_core.subprocess, 'run') as run:
                    with self.assertRaises((ValueError, RuntimeError)):
                        plugin_core.dispatch_owned_console(root, owner, command)
                    run.assert_not_called()
            cancelled = threading.Event()
            cancelled.set()
            with patch.object(plugin_core.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'disconnected'):
                    plugin_core.dispatch_owned_console(root, {'pid': 123, 'created': 456, '_cancelled': cancelled}, 'help')
                run.assert_not_called()
            (watch / 'bridge').write_bytes(b'changed')
            with patch.object(plugin_core.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'binary changed'):
                    plugin_core.dispatch_owned_console(root, {'pid': 123, 'created': 456}, 'help')
                run.assert_not_called()
            response.stdout = '{"ready":true}\n{"status":104,"dispatch_returned":false}\n'
            with patch.object(plugin_core, 'owned_core_endpoint', return_value={
                    key: watch / key for key in ('controller', 'bridge')}), \
                    patch.object(plugin_core.subprocess, 'run', return_value=response) as run:
                with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                    plugin_core.dispatch_owned_console(root, {'pid': 123, 'created': 456}, 'help')
                run.assert_called_once()

    def test_empty_source_failure_has_full_timing_without_build_or_game_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            watcher = watch_module.PluginWatch(source, root / 'output', Path('cl.exe'), {}, None)
            with patch.object(watcher, 'build') as build, patch.object(watcher, 'request') as request, \
                    patch.object(watch_module.time, 'perf_counter', side_effect=[12.0, 12.2, 12.3]):
                result = watcher.update({}, 10.0)
            self.assertEqual(result['reason'], 'no_cpp_sources')
            self.assertAlmostEqual(result['duration_seconds'], 0.2)
            self.assertAlmostEqual(result['elapsed'], 2.3)
            build.assert_not_called()
            request.assert_not_called()

    def test_failed_first_probe_stops_candidate_and_keeps_watcher_available(self):
        self.watcher.request.side_effect = [{'status': 0}, {'status': 6}, {'status': 0}]
        with patch.object(self.watcher, 'build', return_value=(SimpleNamespace(returncode=0, stdout='', stderr=''), 1)):
            result = self.watcher.update(self.watcher.snapshot(), time.perf_counter())
        self.assertEqual(result['status'], 'observation_failed')
        self.assertIsNone(self.watcher.ready)
        self.assertEqual(self.watcher.request.call_args_list[-1].args, ('stop',))
        self.watcher.request.side_effect = [{'status': 0}, {'status': 6}, {'status': 104}]
        with patch.object(self.watcher, 'build', return_value=(SimpleNamespace(returncode=0, stdout='', stderr=''), 1)):
            with self.assertRaisesRegex(RuntimeError, 'teardown is unconfirmed'):
                self.watcher.update(self.watcher.snapshot(), time.perf_counter())

    def test_failed_recovery_keeps_primary_observation_and_original_transport_cause(self):
        for previous in (None, self.root / 'previous.dll'):
            with self.subTest(previous=previous):
                self.watcher.ready = previous
                recovery_error = RuntimeError('core_client_disconnected')
                self.watcher.request.side_effect = [{'status': 0}, {'status': 104, 'result': 9}, recovery_error]
                with patch.object(self.watcher, 'build', return_value=(SimpleNamespace(returncode=0, stdout='', stderr=''), 1)):
                    with self.assertRaises(RuntimeError) as raised:
                        self.watcher.update(self.watcher.snapshot(), time.perf_counter())
                self.assertIn('"status": 104', str(raised.exception))
                self.assertIn('"result": 9', str(raised.exception))
                self.assertIn('core_client_disconnected', str(raised.exception))
                self.assertIs(raised.exception.__cause__, recovery_error)
                self.assertEqual(self.watcher.request.call_count, 3)
                self.assertEqual(self.watcher.ready, previous)
                self.watcher.request.reset_mock()

    def test_shipped_runtime_loads_without_tools_and_refuses_mutation_or_stale_sources(self):
        import hashlib
        from plugin_core import load_sdk_runtime, package_sdk_runtime, package_sdk_release
        from zipfile import ZipFile
        with tempfile.TemporaryDirectory() as directory:
            sdk = Path(directory) / 'sdk'
            (sdk / 'native').mkdir(parents=True)
            source = sdk / 'native/core.cpp'
            source.write_bytes(b'core\r\n')
            artifacts = Path(directory) / 'artifacts'
            artifacts.mkdir()
            built = {}
            for role, name in [('bridge', 'core.dll'), ('controller', 'controller.exe'),
                               ('launch_gate', 'gate.exe'), ('console', 'XalKitConsole.dll'),
                               ('graphics', 'd3d9.dll')]:
                path = artifacts / name
                path.write_bytes(role.encode())
                built[role] = str(path)
            (artifacts / 'XalKitConsole.LICENSES.txt').write_text('notices', encoding='utf-8')
            built['source_hashes'] = {'core.cpp': hashlib.sha256(source.read_bytes()).hexdigest()}
            built['console_source_hashes'] = dict(built['source_hashes'])
            archive = package_sdk_runtime(sdk, built, Path(directory) / 'runtime.zip')
            with ZipFile(archive) as package:
                self.assertEqual(len(package.namelist()), 9)
                self.assertIn('runtime/current.json', package.namelist())
                graphics_entry = next(name for name in package.namelist() if name.endswith('/d3d9.dll'))
                self.assertEqual(package.read(graphics_entry), b'graphics')
            self.assertEqual(Path(load_sdk_runtime(sdk)['graphics']).read_bytes(), b'graphics')
            source.write_bytes(b'core\n')
            with patch('plugin_core.subprocess.run') as compiler:
                loaded = load_sdk_runtime(sdk)
                compiler.assert_not_called()
            for name in ('README.md', 'pyproject.toml', 'scripts/xalkit.py', 'game-api/include/h5/hooks.hpp'):
                path = sdk / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('source', encoding='utf-8')
            def git_inventory(arguments, **options):
                names = ('README.md', 'pyproject.toml', 'scripts/xalkit.py', 'native/core.cpp')
                if Path(arguments[2]).name == 'game-api':
                    names = ('include/h5/hooks.hpp',)
                return SimpleNamespace(stdout=b'\0'.join(name.encode() for name in names) + b'\0')
            with patch('plugin_core.subprocess.run', side_effect=git_inventory):
                release = package_sdk_release(sdk, archive, Path(directory) / 'sdk.zip')
                with ZipFile(release) as package:
                    self.assertIn('xkit/game-api/include/h5/hooks.hpp', package.namelist())
                source.write_bytes(b'changed\n')
                with self.assertRaisesRegex(ValueError, 'outdated'):
                    package_sdk_release(sdk, archive, release)
                source.write_bytes(b'core\n')
                with ZipFile(archive) as package:
                    entries = {name: package.read(name) for name in package.namelist()}
                bridge_entry = next(name for name in entries if name.endswith('/heroes5_plugin_bridge.dll'))
                entries[bridge_entry] = b'tampered'
                with ZipFile(archive, 'w') as package:
                    for name, content in entries.items():
                        package.writestr(name, content)
                with self.assertRaisesRegex(ValueError, 'archive file changed'):
                    package_sdk_release(sdk, archive, release)
            runtime = Path(loaded['bridge'])
            original = runtime.read_bytes()
            runtime.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError, 'runtime file changed'):
                load_sdk_runtime(sdk)
            runtime.write_bytes(original)
            source.write_bytes(b'new core\n')
            with self.assertRaisesRegex(ValueError, 'runtime is outdated'):
                load_sdk_runtime(sdk)

    def test_payload_free_hmr_refuses_a_loaded_mod_before_candidate_or_suspend(self):
        from plugin_core import CoreClient
        client = object.__new__(CoreClient)
        client.process = object()
        client.send = Mock(return_value={'status': 108})
        client.start = Mock()
        result = client.reload_core(Path('candidate.dll'), None, 0, 'main')
        self.assertEqual(result['status'], 'core_rejected')
        self.assertEqual(result['error']['details'][0]['metadata']['stage'], 'host_mode')
        client.send.assert_called_once_with(client.process, 'connect-check')
        client.start.assert_not_called()

    def test_console_controller_deadline_covers_native_budget_and_unconfirmed_cleanup(self):
        from plugin_core import CoreClient
        client = object.__new__(CoreClient)
        process = SimpleNamespace(stdin=io.StringIO(), responses=Mock(), poll=Mock(return_value=None), wait=Mock(return_value=0))
        def delayed_response(timeout):
            if timeout < 46:
                raise queue.Empty()
            return '{"status":0}'
        process.responses.get.side_effect = delayed_response
        self.assertEqual(client.send(process, 'console-replace candidate.dll'), {'status': 0})
        self.assertFalse(process.console_replacement_pending)
        process.responses.get.side_effect = queue.Empty
        with self.assertRaisesRegex(RuntimeError, 'unconfirmed_keep_remote_code'):
            client.send(process, 'console-replace candidate.dll')
        self.assertTrue(process.console_replacement_pending)
        client.close(process)
        self.assertGreaterEqual(process.wait.call_args.kwargs['timeout'], 55)
    def test_diagnostic_wire_rejects_corruption_and_preserves_structured_text(self):
        from plugin_core import decode_diagnostics
        data = bytearray(38448)
        struct.pack_into('<IIQIIQQII', data, 0, 38448, 1, 0, 1, 0, 1, 2, 0, 1)
        struct.pack_into('<QQI64s512sI', data, 48, 1, 500, 2, b'alpha',
                         'message: "quoted"\nстрока'.encode('utf-8'), 0)
        decoded = decode_diagnostics(data.hex())
        self.assertEqual(decoded['records'][0]['level'], 'warning')
        self.assertEqual(decoded['records'][0]['module'], 'alpha')
        self.assertEqual(decoded['records'][0]['message'], 'message: "quoted"\nстрока')
        self.assertEqual(decoded['cursor'], 1)
        for offset, format, value in ((0, '<I', 1), (4, '<I', 99), (16, '<I', 4),
                                      (20, '<I', 1), (44, '<I', 65), (48, '<Q', 2),
                                      (64, '<I', 0), (644, '<I', 1)):
            with self.subTest(offset=offset):
                corrupted = bytearray(data)
                struct.pack_into(format, corrupted, offset, value)
                with self.assertRaises(ValueError):
                    decode_diagnostics(corrupted.hex())
        for raw in (b'\xff\0', b'x' * 64, b'\0'):
            corrupted = bytearray(data)
            struct.pack_into('64s', corrupted, 68, raw)
            with self.assertRaises(ValueError):
                decode_diagnostics(corrupted.hex())
        with self.assertRaises(ValueError):
            decode_diagnostics(data.hex()[:-2])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.entry = self.source / 'plugin.cpp'
        self.entry.write_text('version one', encoding='utf-8')
        self.watcher = watch_module.PluginWatch(self.source, self.root / 'output',
                                                'cl.exe', {}, Mock())
        self.watcher.request = Mock()

    def test_build_error_keeps_previous_payload_and_never_requests_reload(self):
        previous = self.root / 'previous.dll'
        self.watcher.ready = previous
        with patch.object(self.watcher, 'build', return_value=(SimpleNamespace(
                returncode=1, stdout='syntax error', stderr=''), 0)):
            result = self.watcher.update(self.watcher.snapshot(), time.perf_counter())
        self.assertEqual(result['status'], 'build_failed')
        self.assertEqual(self.watcher.ready, previous)
        self.watcher.request.assert_not_called()

    def test_compiler_uses_short_output_names_and_resolvable_cached_objects(self):
        definition = self.source / 'plugin.def'
        definition.write_text('EXPORTS\nAddedFunction\n', encoding='utf-8')
        generation = self.watcher.output / ('generation-' + 'a' * 32)
        generation.mkdir()
        payload = generation / 'plugin.dll'

        def compile_output(arguments, **keywords):
            for argument in arguments:
                if argument.startswith('/Fo'):
                    output_name = argument[3:]
                    self.assertFalse(Path(output_name).is_absolute())
                    (keywords['cwd'] / output_name).write_bytes(b'object')
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        with patch.object(watch_module.subprocess, 'run', side_effect=compile_output) as run:
            result, compiled = self.watcher.build([self.entry], self.watcher.snapshot(),
                                                  generation, payload, False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(compiled, 1)
        link_arguments = run.call_args.args[0]
        self.assertIn('/Feplugin.dll', link_arguments)
        self.assertIn('/DEF:' + str(definition), link_arguments)
        cached_object = next(argument for argument in link_arguments if argument.endswith('.obj'))
        self.assertTrue(Path(cached_object).is_file())

    def test_edit_during_build_prevents_stale_payload_application(self):
        snapshot = self.watcher.snapshot()
        def compile_while_editing(*arguments, **keywords):
            self.entry.write_text('version two', encoding='utf-8')
            return SimpleNamespace(returncode=0, stdout='', stderr=''), 1
        with patch.object(self.watcher, 'build', side_effect=compile_while_editing):
            result = self.watcher.update(snapshot, time.perf_counter())
        self.assertEqual(result['status'], 'superseded')
        self.watcher.request.assert_not_called()
        self.assertNotEqual(self.watcher.snapshot(), self.watcher.last_attempt)

    def test_header_edit_changes_snapshot_but_binary_artifacts_do_not(self):
        before = self.watcher.snapshot()
        (self.source / 'scratch.obj').write_bytes(b'object')
        self.assertEqual(self.watcher.snapshot(), before)
        (self.source / 'plugin.hpp').write_text('header', encoding='utf-8')
        self.assertNotEqual(self.watcher.snapshot(), before)
        before_definition = self.watcher.snapshot()
        definition = self.source / 'plugin.def'
        definition.write_text('EXPORTS\nFirstFunction\n', encoding='utf-8')
        with_definition = self.watcher.snapshot()
        self.assertNotEqual(with_definition, before_definition)
        definition.write_text('EXPORTS\nFirstFunction\nNewFunction\n', encoding='utf-8')
        self.assertNotEqual(self.watcher.snapshot(), with_definition)

    def test_output_inside_sources_is_rejected(self):
        with self.assertRaises(ValueError):
            watch_module.PluginWatch(self.source, self.source / 'build', 'cl.exe', {}, Mock())

    def test_failed_new_probe_restores_and_observes_previous_payload(self):
        previous = self.root / 'previous.dll'
        self.watcher.ready = previous
        self.watcher.request.side_effect = [
            {'status': 0}, {'status': 6}, {'status': 0}, {'status': 0, 'result': 1}]
        with patch.object(self.watcher, 'build', return_value=(SimpleNamespace(
                returncode=0, stdout='', stderr=''), 1)):
            result = self.watcher.update(self.watcher.snapshot(), time.perf_counter())
        self.assertEqual(result['status'], 'observation_failed')
        self.assertEqual(result['rollback']['result'], 1)
        self.assertEqual(self.watcher.ready, previous)
        self.assertEqual(self.watcher.request.call_args_list[-2].args, ('reload ' + str(previous),))

    def test_inherited_environment_skips_toolchain_and_rejects_wrong_architecture(self):
        compiler = self.root / 'cl.exe'
        compiler.write_bytes(b'fixture')
        with patch.dict(watch_module.os.environ, {'VSCMD_ARG_TGT_ARCH': 'x86'}, clear=True), \
                patch.object(watch_module.subprocess, 'run') as run:
            selected, environment = watch_module.compiler_environment(self.root / 'absent.bat', compiler)
        self.assertEqual(selected, compiler)
        self.assertEqual(environment['VSCMD_ARG_TGT_ARCH'], 'x86')
        run.assert_not_called()
        with patch.dict(watch_module.os.environ, {'VSCMD_ARG_TGT_ARCH': 'x64'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'restart the supervisor'):
                watch_module.compiler_environment(self.root / 'absent.bat', compiler)

    def test_prepared_environment_has_no_case_duplicates_rejected_by_msbuild(self):
        compiler = self.root / 'cl.exe'
        compiler.write_bytes(b'fixture')
        toolchain = self.root / 'vcvarsall.bat'
        toolchain.write_bytes(b'fixture')
        output = SimpleNamespace(stdout=f'Path={self.root}\n__PSLockDownPolicy=0\n', returncode=0)
        with patch.dict(watch_module.os.environ, {'__PSLOCKDOWNPOLICY': '0'}, clear=True), \
                patch.object(watch_module.subprocess, 'run', return_value=output):
            selected, environment = watch_module.compiler_environment(toolchain)
        self.assertEqual(selected, compiler)
        self.assertEqual(len(environment), len({key.upper() for key in environment}))
        self.assertEqual(environment['PATH'], str(self.root))

    def test_supervisor_prepares_once_and_passes_environment_to_both_workers(self):
        plugins = self.root / 'plugins'
        for name in ('alpha', 'beta'):
            source = plugins / name
            source.mkdir(parents=True)
            (source / 'plugin.cpp').write_text('fixture', encoding='utf-8')
        bridge = self.root / 'bridge.dll'
        bridge.write_bytes(b'fixture')
        compiler = self.root / 'cl.exe'
        environment = {'Path': str(self.root), 'VSCMD_ARG_TGT_ARCH': 'x86'}
        options = SimpleNamespace(plugins=plugins, output=self.root / 'supervised',
                                  toolchain=self.root / 'vcvarsall.bat', client=self.root / 'client.exe',
                                  bridge=bridge, owned_game=False, main_thread=False, control_stdin=False,
                                  core_source=None, log_format='json', log_session=None, managed_projects=False,
                                  console_build=self.root / 'console-build', console_project='alpha')
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch.object(watch_module, 'compiler_environment', return_value=(compiler, environment)) as prepare, \
                patch.object(watch_module.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(watch_module.threading, 'Thread'), \
                patch.object(watch_module.time, 'sleep', side_effect=KeyboardInterrupt), \
                patch('sys.stdout', new=io.StringIO()) as output:
            watch_module.supervise_plugins(options)
        prepare.assert_called_once_with(options.toolchain)
        self.assertEqual(spawn.call_count, 2)
        for call in spawn.call_args_list:
            self.assertEqual(call.kwargs['env'], environment)
            arguments = call.args[0]
            self.assertEqual(arguments[arguments.index('--prepared-compiler') + 1], str(compiler))
            if arguments[arguments.index('--plugin-id') + 1] == 'alpha':
                self.assertEqual(arguments[arguments.index('--console-build') + 1], str(options.console_build))
            else:
                self.assertNotIn('--console-build', arguments)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[0]['status'], 'supervising')
        self.assertGreaterEqual(events[0]['compiler_setup_seconds'], 0)

    def test_uncertain_retirement_stops_supervisor_before_admitting_another_plugin(self):
        for scenario in ('failed_worker', 'stop_timeout'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plugins = root / 'plugins'
                source = plugins / 'alpha'
                source.mkdir(parents=True)
                source_file = source / 'plugin.cpp'
                source_file.write_text('fixture', encoding='utf-8')
                bridge = root / 'bridge.dll'
                bridge.write_bytes(b'fixture')
                options = SimpleNamespace(plugins=plugins, output=root / 'supervised',
                    toolchain=root / 'vcvarsall.bat', client=root / 'client.exe', bridge=bridge,
                    owned_game=False, main_thread=False, control_stdin=False, core_source=None,
                    log_format='json', log_session=None, managed_projects=False,
                    console_build=None, console_project=None)
                process = Mock()
                process.poll.return_value = 1 if scenario == 'failed_worker' else None
                process.wait.return_value = 1
                def spawn_worker(*arguments, **keywords):
                    source_file.unlink()  # Removal while its game teardown is unconfirmed.
                    other = plugins / 'beta'
                    other.mkdir()
                    (other / 'plugin.cpp').write_text('fixture', encoding='utf-8')
                    return process
                clock = iter(range(0, 1000, 60))
                with patch.object(watch_module, 'compiler_environment', return_value=(root/'cl.exe', {})), \
                        patch.object(watch_module.subprocess, 'Popen', side_effect=spawn_worker) as spawn, \
                        patch.object(watch_module.threading, 'Thread'), \
                        patch.object(watch_module.time, 'sleep'), \
                        patch.object(watch_module.time, 'monotonic', side_effect=lambda: next(clock)), \
                        patch.object(watch_module, 'EventLog') as event_log:
                    with self.assertRaisesRegex(RuntimeError, 'game cleanup is unconfirmed'):
                        watch_module.supervise_plugins(options)
                spawn.assert_called_once()  # No beta bridge or reused slot is admitted.
                statuses = [call.args[0]['status'] for call in event_log.return_value.emit.call_args_list]
                self.assertIn('runtime_failed', statuses)
                if scenario == 'stop_timeout':
                    self.assertIn('plugin_cleanup_unconfirmed', statuses)
                    process.terminate.assert_called_once()

    def test_slot_remains_reserved_when_worker_exits_between_scan_and_admission(self):
        plugins = self.root / 'late-exit-plugins'
        source = plugins / 'alpha'
        source.mkdir(parents=True)
        (source / 'plugin.cpp').write_text('fixture', encoding='utf-8')
        bridge = self.root / 'late-exit-bridge.dll'
        bridge.write_bytes(b'fixture')
        options = SimpleNamespace(plugins=plugins, output=self.root / 'late-exit-supervised',
            toolchain=self.root / 'vcvarsall.bat', client=self.root / 'client.exe', bridge=bridge,
            owned_game=False, main_thread=False, control_stdin=False, core_source=None,
            log_format='json', log_session=None, managed_projects=False,
            console_build=None, console_project=None)
        alpha = Mock()
        alpha.poll.side_effect = [None, 1, 1]
        alpha.wait.return_value = 1
        beta = Mock()
        beta.poll.return_value = None
        beta.wait.return_value = 0
        def spawn_worker(*arguments, **keywords):
            if not (plugins / 'beta').exists():
                other = plugins / 'beta'
                other.mkdir()
                (other / 'plugin.cpp').write_text('fixture', encoding='utf-8')
                return alpha
            return beta
        with patch.object(watch_module, 'compiler_environment', return_value=(self.root/'cl.exe', {})), \
                patch.object(watch_module.subprocess, 'Popen', side_effect=spawn_worker) as spawn, \
                patch.object(watch_module.threading, 'Thread'), \
                patch.object(watch_module.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
                patch.object(watch_module, 'EventLog'):
            watch_module.supervise_plugins(options)
        self.assertEqual(spawn.call_count, 2)
        beta_arguments = spawn.call_args_list[1].args[0]
        self.assertEqual(beta_arguments[beta_arguments.index('--slot') + 1], '1')

    def test_core_transaction_retains_membership_and_requires_live_matching_instances(self):
        for scenario in ('accepted', 'rejected', 'lost_queued', 'lost_applied', 'stale_reply'):
            with self.subTest(scenario=scenario):
                root = self.root / scenario
                plugins = root / 'plugins'
                for name in ('alpha', 'beta'):
                    source = plugins / name
                    source.mkdir(parents=True)
                    (source / 'plugin.cpp').write_text('fixture', encoding='utf-8')
                bridge = root / 'bridge.dll'
                bridge.write_bytes(b'fixture')
                options = SimpleNamespace(plugins=plugins, output=root / 'supervised',
                                          toolchain=root / 'vcvarsall.bat', client=root / 'client.exe',
                                          bridge=bridge, owned_game=False, main_thread=False, control_stdin=False,
                                          core_source=root / 'native', log_format='json', log_session=None,
                                          managed_projects=False, console_build=None, console_project=None)
                processes = []
                for _ in range(3):
                    process = Mock()
                    process.stdin = io.StringIO()
                    process.poll.return_value = None
                    process.wait.return_value = 0
                    processes.append(process)
                command_queue, reply_queue = watch_module.queue.Queue(), watch_module.queue.Queue()
                record = {'status': 'core_built', 'bridge': str(root / 'candidate.dll'),
                          'controller': str(root / 'candidate.exe')}
                Path(record['bridge']).write_bytes(b'candidate fixture')
                builder = Mock()
                builder.update.side_effect = [None, record] + [None] * 20
                output = io.StringIO()
                answered = set()
                ticks = 0

                def advance_workers(_seconds):
                    nonlocal ticks
                    ticks += 1
                    events = [json.loads(line) for line in output.getvalue().splitlines()]
                    instances = {event['plugin']: event['instance'] for event in events
                                 if event['status'] == 'plugin_discovered'}
                    if ticks == 1:
                        for name, instance in instances.items():
                            reply_queue.put({'status': 'applied', 'plugin': name, 'instance': instance})
                    if ticks == 3:
                        (plugins / 'beta' / 'plugin.cpp').unlink()
                        source = plugins / 'gamma'
                        source.mkdir()
                        (source / 'plugin.cpp').write_text('fixture', encoding='utf-8')
                        if scenario == 'lost_queued':
                            processes[1].poll.return_value = 1
                    if ticks == 4 and scenario == 'lost_applied':
                        processes[0].poll.return_value = 1
                    terminal = [event for event in events if event['status'] in
                                ('core_update_completed', 'core_update_rejected')]
                    if terminal:
                        raise KeyboardInterrupt
                    if ticks >= 4:
                        self.assertNotIn('stop', processes[1].stdin.getvalue(),
                                         'Removing beta must wait for the entire transaction')
                        self.assertNotIn('gamma', instances,
                                         'New plugins must wait for the entire transaction')
                    if scenario == 'stale_reply' and ticks == 5:
                        self.assertNotIn('core-reload', processes[1].stdin.getvalue(),
                                         'A previous instance cannot acknowledge the current core update')
                    for name, process in zip(('alpha', 'beta'), processes):
                        for line in process.stdin.getvalue().splitlines():
                            request = json.loads(line)
                            if request['command'] != 'core-reload' or request['id'] in answered:
                                continue
                            instance = instances[name]
                            if scenario == 'stale_reply' and ticks == 3:
                                instance = 'previous-instance'
                            else:
                                answered.add(request['id'])
                            status = ('core_rejected' if scenario == 'rejected' and name == 'beta'
                                      else 'core_applied')
                            reply_queue.put({'status': status, 'plugin': name,
                                             'instance': instance, 'id': request['id']})
                    if ticks > 12:
                        self.fail('Core transaction did not reach a confirmed result')

                with patch.object(watch_module, 'compiler_environment', return_value=(root / 'cl.exe', {})), \
                        patch.object(watch_module, 'CoreBuild', return_value=builder), \
                        patch.object(watch_module.subprocess, 'Popen', side_effect=processes) as spawn, \
                        patch.object(watch_module.threading, 'Thread'), \
                        patch.object(watch_module.queue, 'Queue', side_effect=[command_queue, reply_queue]), \
                        patch.object(watch_module.time, 'sleep', side_effect=advance_workers), \
                        patch.object(watch_module.time, 'perf_counter', side_effect=lambda: float(ticks)), \
                        patch('sys.stdout', new=output):
                    if scenario.startswith('lost_'):
                        with self.assertRaisesRegex(RuntimeError, 'lost a participant'):
                            watch_module.supervise_plugins(options)
                    else:
                        watch_module.supervise_plugins(options)
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                completed = [event for event in events if event['status'] == 'core_update_completed']
                if scenario.startswith('lost_'):
                    self.assertFalse(completed)
                    self.assertEqual(spawn.call_count, 2)
                    self.assertNotIn('core-reload', processes[1].stdin.getvalue())
                elif scenario == 'rejected':
                    self.assertFalse(completed)
                    rejected = next(event for event in events if event['status'] == 'core_update_rejected')
                    self.assertEqual(rejected['duration_seconds'], ticks - 1 - 2)
                    self.assertEqual(rejected['timing_scope'], 'source_scan_to_verified_rollback')
                    self.assertEqual(rejected['rolled_back_plugins'], ['alpha'])
                    requests = [json.loads(line) for line in processes[0].stdin.getvalue().splitlines()]
                    self.assertEqual([request['bridge'] for request in requests if request['command'] == 'core-reload'],
                                     [record['bridge'], str(bridge)])
                else:
                    self.assertEqual(len(completed), 1)
                    self.assertEqual(completed[0]['plugins'], ['alpha', 'beta'])
                    self.assertEqual(completed[0]['duration_seconds'], ticks - 1 - 2)
                    self.assertEqual(completed[0]['timing_scope'], 'source_scan_to_verified_plugin_observations')
                if not scenario.startswith('lost_'):
                    self.assertEqual(spawn.call_count, 3)
                    arguments = spawn.call_args_list[2].args[0]
                    copied_bridge = Path(arguments[arguments.index('--bridge') + 1])
                    expected_bridge = bridge if scenario == 'rejected' else Path(record['bridge'])
                    self.assertEqual(copied_bridge.read_bytes(), expected_bridge.read_bytes())

    def test_failed_core_build_preserves_previous_generation_and_waits_for_an_edit(self):
        with patch.object(watch_module.shutil, 'which', return_value=str(self.entry)):
            builder = watch_module.CoreBuild(self.source, self.root / 'core-build', {'Path': ''})
        previous = {'bridge': 'previous.dll'}
        builder.ready = previous
        failure = SimpleNamespace(returncode=1, stdout='compiler error', stderr='')
        with patch.object(watch_module.subprocess, 'run', return_value=failure) as compile_core:
            result = builder.update()
            self.assertIsNone(builder.update())
        self.assertEqual(result['status'], 'core_build_failed')
        self.assertIs(builder.ready, previous)
        compile_core.assert_called_once()
        scripts = self.source.parent / 'scripts'
        scripts.mkdir(exist_ok=True)
        (scripts / 'native-ui-resources.py').write_text('fixture', encoding='utf-8')
        with patch.object(watch_module.shutil, 'which', return_value=str(self.entry)):
            console = watch_module.CoreBuild(self.source, self.root / 'console-build', {'Path': ''}, console=True)
        # A failed configure may write its cache before generating the target project.
        console.cache.mkdir(parents=True)
        (console.cache / 'CMakeCache.txt').write_text('XALKIT_BUILD_CONSOLE:BOOL=ON\n', encoding='utf-8')
        console.ready = {'console': 'previous.dll'}
        with patch.object(watch_module.subprocess, 'run', return_value=failure) as configure:
            result = console.update()
            self.assertIsNone(console.update())
        self.assertIn('-S', configure.call_args.args[0])
        self.assertEqual(result['status'], 'console_build_failed')
        self.assertEqual(console.ready['console'], 'previous.dll')
        locale = self.source.parent / 'locale/en/LC_MESSAGES'
        locale.mkdir(parents=True)
        catalog = locale / 'xalkit.po'
        catalog.write_text('msgid "broken\n', encoding='utf-8')
        with patch.object(watch_module.subprocess, 'run') as compiler:
            result = console.update()
            self.assertIsNone(console.update())
            compiler.assert_not_called()
        self.assertEqual(result['reason'], 'translation_failed')
        self.assertEqual(console.ready['console'], 'previous.dll')
        catalog.write_text('msgid "ok"\nmsgstr "ok"\n', encoding='utf-8')
        with patch.object(watch_module.subprocess, 'run', return_value=failure) as compiler:
            self.assertEqual(console.update()['status'], 'console_build_failed')
            compiler.assert_called_once()

    def test_core_edit_during_build_never_publishes_stale_artifacts(self):
        with patch.object(watch_module.shutil, 'which', return_value=str(self.entry)):
            builder = watch_module.CoreBuild(self.source, self.root / 'core-build', {'Path': ''})
        def edit_during_build(*arguments, **keywords):
            self.entry.write_text('updated while building', encoding='utf-8')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        with patch.object(watch_module.subprocess, 'run', side_effect=edit_during_build):
            result = builder.update()
        self.assertEqual(result['status'], 'core_build_superseded')
        self.assertIsNone(builder.ready)
        self.assertIsNone(builder.last_attempt)

    def test_core_snapshot_tracks_shared_api_headers(self):
        with patch.object(watch_module.shutil, 'which', return_value=str(self.entry)):
            builder = watch_module.CoreBuild(self.source, self.root / 'core-build', {'PATH': ''})
        include = self.root / 'game-api/include/h5'
        include.mkdir(parents=True)
        header = include / 'fixture.hpp'
        header.write_text('version one', encoding='utf-8')
        before = builder.snapshot()
        header.write_text('version two', encoding='utf-8')
        self.assertIn('@game-api/h5/fixture.hpp', before)
        self.assertNotEqual(builder.snapshot(), before)

    def test_core_output_inside_sources_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside core sources'):
            watch_module.CoreBuild(self.source, self.source / 'build', {'PATH': ''})

    def test_changed_sdk_location_builds_without_overwriting_previous_cmake_cache(self):
        output = self.root / 'core-build'
        previous_cache = output / 'cmake/CMakeCache.txt'
        previous_cache.parent.mkdir(parents=True)
        previous = 'CMAKE_HOME_DIRECTORY:INTERNAL=' + str(self.root / 'previous-sdk') + '\n'
        previous_cache.write_text(previous, encoding='utf-8')
        with patch.object(watch_module.shutil, 'which', return_value=str(self.entry)):
            builder = watch_module.CoreBuild(self.source, output, {'PATH': ''})
            repeated = watch_module.CoreBuild(self.source, output, {'PATH': ''})
        failure = SimpleNamespace(returncode=1, stdout='compiler fixture', stderr='')
        with patch.object(watch_module.subprocess, 'run', return_value=failure) as configure:
            builder.update()
        arguments = configure.call_args.args[0]
        selected_cache = Path(arguments[arguments.index('-B') + 1])
        self.assertNotEqual(selected_cache, previous_cache.parent)
        self.assertEqual(repeated.cache, selected_cache)
        self.assertEqual(previous_cache.read_text(encoding='utf-8'), previous)

    def test_release_has_one_plugin_and_both_shared_dependencies_with_matching_hashes(self):
        from zipfile import ZipFile
        import hashlib
        loader = self.root / 'dinput8.dll'
        graphics = self.root / 'd3d9.dll'
        loader.write_bytes(b'input-bootstrap')
        graphics.write_bytes(b'graphics-facade')
        def release_fixture(sources, generation, payload, include):
            payload.write_bytes(b'one-plugin')
            return SimpleNamespace(returncode=0, stdout='', stderr=''), {'fixture.cpp': 'accepted'}
        with patch.object(watch_module.shutil, 'which', return_value=str(self.entry)), \
                patch.object(watch_module.CoreBuild, 'release', side_effect=release_fixture):
            result = self.watcher.update(self.watcher.snapshot(), time.perf_counter(), 'one-plugin', loader)
        self.assertEqual(result['status'], 'released')
        with ZipFile(result['archive']) as archive:
            self.assertEqual(set(archive.namelist()), {'bin/Heroes5Mods/Plugins/one-plugin.dll',
                'bin/dinput8.dll', 'bin/d3d9.dll', 'release.json', 'README.txt'})
            manifest = json.loads(archive.read('release.json'))
            self.assertEqual(manifest['graphics_facade_sha256'], hashlib.sha256(archive.read('bin/d3d9.dll')).hexdigest())
            self.assertNotIn('bin/d3d9.universe.dll', archive.namelist())
        self.watcher.request.assert_not_called()
