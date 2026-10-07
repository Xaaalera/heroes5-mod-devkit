"""Explicit live acceptance of owned mailbox and two native HMR observers."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid

from workspace import DEVKIT, workspace_root
from sdk_diagnostics import OwnedCrashMonitor


def cleanup_session(manager, close_game, read_subscriptions, report):
    """Attempt each owned cleanup boundary even if the preceding one fails."""
    cleanup_errors = report.setdefault('cleanup_errors', [])
    if manager is not None:
        try:
            if manager.poll() is None:
                manager.stdin.write('{"command":"stop"}\n')
                manager.stdin.flush()
            report['manager_exit_code'] = manager.wait(timeout=50)
        except Exception as error:
            cleanup_errors.append({'stage': 'manager_stop', 'reason': repr(error)})
        try:
            report['subscriptions_after_stop'] = read_subscriptions()
        except Exception as error:
            cleanup_errors.append({'stage': 'subscription_read', 'reason': repr(error)})
    try:
        report.update(close_game())
    except Exception as error:
        cleanup_errors.append({'stage': 'game_close', 'reason': repr(error)})
    # Only the returned child-process object is terminated, and only after the
    # immutable owned game handle has confirmed that its process is gone.
    if manager is not None and report.get('game_exit_code') is not None and \
            report['game_exit_code'] != 259:
        try:
            if manager.poll() is None:
                manager.terminate()
                report['manager_exit_code'] = manager.wait(timeout=5)
                cleanup_errors.append({'stage': 'manager_stop', 'reason': 'Manager required termination after game exit'})
        except Exception as error:
            cleanup_errors.append({'stage': 'manager_termination', 'reason': repr(error)})
    report['passed'] = (report.get('checks_passed', False) and not cleanup_errors and
                        report.get('manager_exit_code') == 0 and report.get('subscriptions_after_stop') == 0 and
                        report.get('game_exit_code') == 0)


def main():
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True, help='Explicitly launch the owned sandbox game')
    parser.add_argument('--toolchain', type=Path, required=True, help='MSVC vcvarsall.bat')
    parser.add_argument('--native-owner', action='store_true', help='Check native ownership without the developer mailbox')
    parser.add_argument('--crash-monitor', type=Path)
    parser.add_argument('--sdk-runtime', action='store_true', help='Use the current sealed graphics runtime and owned launch gate')
    parser.add_argument('--launch-gate', type=Path)
    parser.add_argument('--graphics-facade', type=Path)
    options = parser.parse_args()
    if bool(options.launch_gate) != bool(options.graphics_facade) or (options.sdk_runtime and options.launch_gate):
        parser.error('Choose a sealed runtime or a matching launch-gate/graphics-facade pair')
    root = workspace_root()
    sdk = DEVKIT
    os.environ['H5_WORKSPACE'] = str(root)
    sys.path.insert(0, str(sdk / 'scripts'))
    import game_control
    spec = importlib.util.spec_from_file_location('shared_probe', sdk / 'scripts/native-probe.py')
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    spec = importlib.util.spec_from_file_location('shared_watch', sdk / 'scripts/plugin-watch.py')
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    started = time.perf_counter()
    output = root / '.local/test-state' / ('sdk-shared-dispatch-' + time.strftime('%Y%m%dT%H%M%S'))
    output.mkdir()
    sources = output / 'plugins'
    sources.mkdir()
    core = output / 'core-source'
    core.mkdir()
    fixture = (sdk / 'native/plugin_runtime_fixture.cpp').read_text(encoding='utf-8')

    def write_plugin(name, version, added_value=None):
        source = sources / name
        source.mkdir(exist_ok=True)
        body = fixture
        if added_value is not None:
            (source / 'extra.hpp').write_text(f'#define ADDED_VALUE {added_value}\nunsigned AddedValue();\n', encoding='utf-8')
            (source / 'extra.cpp').write_text('#include "extra.hpp"\nunsigned AddedValue() { return ADDED_VALUE; }\n', encoding='utf-8')
            body = '#include "extra.hpp"\n' + fixture.replace('FIXTURE_VERSION == 2) { *result = 42;',
                                                               'FIXTURE_VERSION >= 2) { *result = AddedValue();')
        (source / 'plugin.cpp').write_text(f'#define FIXTURE_VERSION {version}\n#define FIXTURE_ENGINE_SITE 0xd112db\n' + body,
                                         encoding='utf-8')

    def write_core(version, reject=False):
        text = ('cmake_minimum_required(VERSION 3.21)\nproject(SharedDispatch LANGUAGES CXX)\n'
                'set(CMAKE_CXX_STANDARD 20)\nset(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)\n'
                f'add_subdirectory("{(sdk / "native").as_posix()}" canonical-sdk)\n'
                f'target_compile_definitions(heroes5_plugin_bridge PRIVATE H5_CORE_VERSION={version})\n'
                'set_target_properties(heroes5_plugin_bridge plugin_bridge_client PROPERTIES '
                'RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/Release")\n')
        if reject:
            text += 'target_compile_definitions(heroes5_plugin_bridge PRIVATE H5_CORE_REJECT_SLOT=1)\n'
        if version >= 2:
            (core / 'extra.hpp').write_text('unsigned SdkValue();\n', encoding='utf-8')
            (core / 'extra.cpp').write_text('#include "extra.hpp"\nunsigned SdkValue() { return 99; }\n', encoding='utf-8')
            text += ('target_sources(heroes5_plugin_bridge PRIVATE extra.cpp)\n'
                     'target_compile_definitions(heroes5_plugin_bridge PRIVATE "H5_CORE_FEATURE_RESULT=SdkValue()")\n'
                     f'target_compile_options(heroes5_plugin_bridge PRIVATE "/FI{(core / "extra.hpp").as_posix()}")\n')
        (core / 'CMakeLists.txt').write_text(text, encoding='utf-8')

    write_plugin('alpha', 3)
    write_plugin('beta', 3)
    write_core(1)
    toolchain = options.toolchain.resolve(strict=True)
    compiler, environment = watch.compiler_environment(toolchain)
    builder = watch.CoreBuild(core, output / 'watch/core', environment)
    initial = builder.update()
    assert initial['status'] == 'core_built', initial
    replies = queue.Queue()
    history = []
    pending = []
    errors = []
    manager = None
    monitor = None
    owned = None
    game_handle = None
    staged_graphics = None
    kernel = probe.api()
    report = {'passed': False, 'steps': [], 'scope': 'owned live game, two observers with native owner' if options.native_owner else
              'owned live game, shared mailbox and two native observers',
              'game_build': dict(probe.HASHES)}

    def record(step, **values):
        report['steps'].append({'step': step, **values})
        print(json.dumps(report['steps'][-1]), flush=True)

    def collect():
        for line in manager.stdout:
            replies.put(json.loads(line))
        replies.put(None)

    def collect_errors():
        errors.extend(manager.stderr)

    def receive(status, plugin=None, request_id=None):
        deadline = time.monotonic() + 45
        while True:
            for index, event in enumerate(pending):
                if event['status'] == status and (plugin is None or event.get('plugin') == plugin) and \
                        (request_id is None or event.get('id') == request_id):
                    return pending.pop(index)
            event = replies.get(timeout=max(0.01, deadline - time.monotonic()))
            if event is None:
                raise RuntimeError('Manager exited: ' + ''.join(errors))
            history.append(event)
            removal_in_progress = (status == 'plugin_stopped' and event.get('plugin') == plugin and
                                   event['status'] == 'build_failed' and event.get('reason') == 'no_cpp_sources')
            if not removal_in_progress and event['status'] in (
                    'runtime_failed', 'plugin_failed', 'plugin_cleanup_unconfirmed',
                    'reload_rejected', 'build_failed', 'core_build_failed'):
                raise RuntimeError(json.dumps(event))
            pending.append(event)

    def command(plugin, kind='main', function=1):
        request_id = uuid.uuid4().hex
        manager.stdin.write(json.dumps({'command': kind, 'plugin': plugin, 'function': function,
                                        'argument': 0, 'id': request_id}) + '\n')
        manager.stdin.flush()
        event = receive('command_result', plugin, request_id)
        assert event['response']['status'] == 0, event
        return event['response']['result']

    def subscriptions():
        assert probe.creation_time(kernel, game_handle) == owned['created']
        return game_control.read_script_observers(probe, kernel, game_handle)

    def control_read():
        before = time.perf_counter()
        if options.native_owner:
            time.sleep(0.1)
            return {'seconds': time.perf_counter() - before, 'method': 'passive_frames_no_mailbox'}
        result = game_control.main(['heroes'])
        assert result['status'] == 'completed' and 'Hero1' in result['result'], result
        return {'seconds': time.perf_counter() - before, 'result': result['result']}

    def close_game():
        if not owned:
            return {}
        if probe.creation_time(kernel, game_handle) != owned['created']:
            raise RuntimeError('Owned game identity changed; refusing to send exit')
        if options.native_owner:
            arguments = [initial['controller'], '--owned', str(owned['pid']), str(owned['created']),
                         str((root / '.local/test-game/bin/H5_Game.exe').resolve()), initial['bridge']]
            result = subprocess.run(arguments, input='exit\n', capture_output=True, text=True, encoding='utf-8', timeout=55)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            if result.returncode or len(replies) != 2 or not replies[1].get('game_exited'):
                raise RuntimeError('Native owned-game exit unconfirmed: ' + result.stderr)
            closed = {'pid': owned['pid'], 'status': 'game_closed', 'method': 'native_exit', 'response': replies[1]}
        else:
            controller = json.loads(game_control.STATE.read_text(encoding='utf-8'))
            if controller['pid'] != owned['pid'] or controller['created'] != owned['created']:
                raise RuntimeError('Owned mailbox identity changed; refusing to send exit')
            closed = game_control.main(['quit'])
        exit_code = wintypes.DWORD()
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        probe.checked(kernel.GetExitCodeProcess(game_handle, ctypes.byref(exit_code)))
        return {'game_close': closed, 'game_exit_code': exit_code.value}

    try:
        runtime = None
        if options.sdk_runtime:
            from plugin_core import load_sdk_runtime
            from xalkit_runtime import stage_owned_graphics
            runtime = load_sdk_runtime(sdk)
        elif options.launch_gate:
            runtime = {'launch_gate': str(options.launch_gate.resolve(strict=True)),
                       'graphics': str(options.graphics_facade.resolve(strict=True))}
        if runtime:
            from xalkit_runtime import stage_owned_graphics
            staged_graphics = {}
            stage_owned_graphics(root, runtime['graphics'], receipt=staged_graphics)
        probe.launch(kernel, map_name='WorkshopPolygon', control=not options.native_owner,
                     launch_gate=runtime['launch_gate'] if runtime else None,
                     graphics_facade_hash=staged_graphics['facade_sha256'] if staged_graphics else None)
        owned = json.loads(probe.STATE.read_text(encoding='utf-8'))
        report['owner'] = {key: owned[key] for key in ('pid', 'created')}
        game_handle = probe.checked(kernel.OpenProcess(0x100410, False, owned['pid']))
        assert probe.creation_time(kernel, game_handle) == owned['created']
        record('owned_launch', **report['owner'])
        if options.crash_monitor:
            monitor = OwnedCrashMonitor(probe, kernel, game_handle, owned,
                root / '.local/test-game/bin/H5_Game.exe', options.crash_monitor, output / 'dumps',
                graphics_facade_hash=staged_graphics['facade_sha256'] if staged_graphics else None)
            report['crash_monitor'] = monitor.snapshot()
        # Loading may precede the first dispatch. A timed-out request is never retried.
        if not options.native_owner:
            game_control.execute(probe, timeout=20)
            ready = game_control.main(['--timeout', '20', 'heroes'])
            assert ready['status'] == 'completed' and 'Hero1' in ready['result'], ready
            record('adventure_ready', result=ready['result'])
        arguments = [sys.executable, '-X', 'utf8', str(sdk / 'scripts/plugin-watch.py'),
                     '--plugins', str(sources), '--output', str(output / 'watch'), '--toolchain', str(toolchain),
                     '--client', initial['controller'], '--bridge', initial['bridge'], '--core-source', str(core),
                     '--owned-game', '--main-thread', '--control-stdin']
        manager = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8', env=environment,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        threading.Thread(target=collect, daemon=True).start()
        threading.Thread(target=collect_errors, daemon=True).start()
        receive('core_built')
        receive('core_update_completed')
        receive('applied', 'alpha')
        receive('applied', 'beta')
        assert len(subscriptions()) == 2
        values = {name: command(name) for name in ('alpha', 'beta')}
        reads = [control_read() for _ in range(3)]
        later = {name: command(name) for name in values}
        assert all(later[name] > values[name] and later[name] // 1000000 == 3 for name in values)
        record('two_observers_native_owner' if options.native_owner else 'two_observers_and_mailbox',
               before=values, after=later, reads=reads, subscriptions=2)
        write_plugin('alpha', 4)
        receive('applied', 'alpha')
        control_read()
        alpha = command('alpha')
        beta = command('beta')
        assert alpha // 1000000 == 4 and beta // 1000000 == 3
        assert alpha % 1000000 > later['alpha'] % 1000000 and beta > later['beta']
        assert len(subscriptions()) == 2
        record('payload_hmr', alpha=alpha, beta=beta)
        write_plugin('alpha', 4, added_value=77)
        receive('applied', 'alpha')
        assert command('alpha', function=2) == 77
        assert command('alpha') > alpha and command('beta') > beta
        (sources / 'alpha/extra.hpp').write_text('#define ADDED_VALUE 88\nunsigned AddedValue();\n', encoding='utf-8')
        receive('applied', 'alpha')
        assert command('alpha', function=2) == 88
        record('new_plugin_function_and_header', value=88)
        previous_callbacks = set(subscriptions())
        write_core(2)
        receive('core_update_completed')
        control_read()
        assert len(subscriptions()) == 2 and previous_callbacks.isdisjoint(subscriptions())
        assert command('alpha', kind='core') == 2 and command('beta', kind='core') == 2
        assert all(command(name, kind='feature') == 99 for name in ('alpha', 'beta'))
        assert command('alpha', function=2) == 88
        core_values = {name: command(name) for name in ('alpha', 'beta')}
        assert core_values['alpha'] > alpha and core_values['beta'] > beta
        record('core_hmr', values=core_values, subscriptions=2)
        record('extended_core_export', value=99)
        write_core(3, reject=True)
        receive('core_update_rejected')
        control_read()
        assert command('alpha', kind='core') == 2 and command('beta', kind='core') == 2
        assert len(subscriptions()) == 2
        rollback_values = {name: command(name) for name in ('alpha', 'beta')}
        assert all(command(name, kind='feature') == 99 for name in ('alpha', 'beta'))
        assert command('alpha', function=2) == 88
        assert all(rollback_values[name] > core_values[name] for name in core_values)
        record('coordinated_rollback', values=rollback_values, subscriptions=2)
        for filename in ('extra.cpp', 'extra.hpp', 'plugin.cpp'):
            (sources / 'alpha' / filename).unlink()
        receive('plugin_stopped', 'alpha')
        assert len(subscriptions()) == 1
        control_read()
        assert command('beta') > rollback_values['beta']
        record('independent_remove', subscriptions=1)
        write_plugin('alpha', 4, added_value=88)
        receive('applied', 'alpha')
        control_read()
        assert len(subscriptions()) == 2 and command('alpha') // 1000000 == 4
        assert probe.creation_time(kernel, game_handle) == owned['created']
        record('readd_same_owned_process', subscriptions=2)
        report['checks_passed'] = True
    except KeyboardInterrupt:
        report['cancelled'] = True
    except Exception as error:
        report['failure'] = repr(error)
        raise
    finally:
        try:
            cleanup_session(manager, close_game, lambda: len(subscriptions()), report)
        finally:
            if monitor:
                try:
                    report['crash_monitor'] = monitor.close()
                    if report['crash_monitor']['capture_status'] == 'dump_captured':
                        report.setdefault('cleanup_errors', []).append({'stage': 'game_crash', 'reason': 'Owned exception dump captured'})
                        report['passed'] = False
                except Exception as error:
                    report.setdefault('cleanup_errors', []).append({'stage': 'monitor_stop', 'reason': repr(error)})
                    try:
                        report['crash_monitor'] = monitor.snapshot()
                    except Exception as snapshot_error:
                        report['cleanup_errors'].append({'stage': 'monitor_snapshot', 'reason': repr(snapshot_error)})
                    report['passed'] = False
            if staged_graphics is not None:
                try:
                    from xalkit_runtime import restore_owned_graphics
                    restore_owned_graphics(staged_graphics)
                except Exception as restoration_error:
                    report.setdefault('cleanup_errors', []).append({'stage': 'graphics_restore', 'reason': repr(restoration_error)})
                    report['passed'] = False
            if game_handle:
                kernel.CloseHandle(game_handle)
            report['duration_seconds'] = time.perf_counter() - started
            inspected = {name: sdk / name for name in ('scripts/game_control.py', 'scripts/plugin-watch.py',
                'scripts/plugin_core.py', 'scripts/plugin-control-check.py', 'native/plugin_bridge.cpp', 'native/plugin_runtime.hpp')}
            inspected['game-api/script_observers.hpp'] = sdk / 'game-api/include/h5/script_observers.hpp'
            inspected['game-api/hooks.hpp'] = sdk / 'game-api/include/h5/hooks.hpp'
            inspected['native/plugin_bridge_client.cpp'] = sdk / 'native/plugin_bridge_client.cpp'
            report['source_hashes'] = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                      for name, path in inspected.items()}
            (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            (output / 'observed.json').write_text(json.dumps(history, indent=2), encoding='utf-8')
            (output / 'stderr.log').write_text(''.join(errors), encoding='utf-8')
            print(json.dumps({'report': str(output / 'report.json'), **report}), flush=True)
    require_complete = report.get('passed', False)
    if report.get('cancelled'):
        raise SystemExit(130 if not report.get('cleanup_errors') and report.get('game_exit_code') == 0 else 1)
    if not require_complete:
        raise RuntimeError('Live SDK checks or owned-session cleanup failed; inspect report.json')


if __name__ == '__main__':
    main()
