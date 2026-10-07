"""Explicit live acceptance of native watch, hooks, UI, rollback and release."""
import argparse
import ctypes
from ctypes import wintypes as W
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from zipfile import ZipFile

from workspace import DEVKIT, workspace_root


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--multi', action='store_true', help='Check dynamic multi-plugin discovery and file growth')
    parser.add_argument('--core', action='store_true', help='Also check core replacement, new export and state rollback')
    parser.add_argument('--auto-core', action='store_true', help='Check automatic core source builds across two plugins')
    parser.add_argument('--toolchain', type=Path, required=True)
    options = parser.parse_args()
    if options.core and options.multi:
        parser.error('--core currently uses the standalone acceptance scenario')
    if options.auto_core and not options.multi:
        parser.error('--auto-core requires --multi')
    import keystone  # Fail before starting a game when the environment is wrong.
    import unicorn
    root = workspace_root()
    os.environ['H5_WORKSPACE'] = str(root)
    build = DEVKIT / '.local/native-check/Release'
    for name in ('plugin_bridge_client.exe', 'heroes5_plugin_bridge.dll', 'dinput8.dll', 'd3d9.dll'):
        require((build / name).is_file(), 'Build native Release targets first: ' + name)
    stamp = time.strftime('%Y%m%dT%H%M%S')
    output = root / '.local/test-state' / ('sdk-acceptance-' + stamp)
    output.mkdir(parents=True, exist_ok=False)
    source = output / 'source'
    source.mkdir()
    core_source = output / 'core-source'
    if options.auto_core:
        core_source.mkdir()
        def write_core_project(version, extra=False, reject_slot=None):
            text = ('cmake_minimum_required(VERSION 3.21)\nproject(SdkCoreAcceptance LANGUAGES CXX)\n'
                    'set(CMAKE_CXX_STANDARD 20)\nset(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)\n'
                    f'add_library(heroes5_plugin_bridge SHARED "{(DEVKIT / "native/plugin_bridge.cpp").as_posix()}")\n'
                    'target_link_libraries(heroes5_plugin_bridge PRIVATE user32 bcrypt)\n'
                    f'target_compile_definitions(heroes5_plugin_bridge PRIVATE H5_CORE_VERSION={version})\n'
                    f'add_executable(plugin_bridge_client "{(DEVKIT / "native/plugin_bridge_client.cpp").as_posix()}")\n'
                    'target_compile_definitions(plugin_bridge_client PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN)\n'
                    'target_link_libraries(plugin_bridge_client PRIVATE bcrypt comdlg32 dxguid)\n')
            if extra:
                text += ('target_sources(heroes5_plugin_bridge PRIVATE extra.cpp)\n'
                         'target_compile_definitions(heroes5_plugin_bridge PRIVATE "H5_CORE_FEATURE_RESULT=SdkValue()")\n'
                         f'target_compile_options(heroes5_plugin_bridge PRIVATE "/FI{(core_source / "extra.hpp").as_posix()}")\n')
            if reject_slot is not None:
                text += f'target_compile_definitions(heroes5_plugin_bridge PRIVATE H5_CORE_REJECT_SLOT={reject_slot})\n'
            (core_source / 'CMakeLists.txt').write_text(text, encoding='utf-8')
        write_core_project(1)
    entry = source / 'plugin.cpp'
    fixture = (DEVKIT / 'native/plugin_runtime_fixture.cpp').read_text(encoding='utf-8')
    fixture = fixture.replace('FIXTURE_VERSION == 2', 'FIXTURE_VERSION >= 2')
    fixture = fixture.replace('counter += argument; *result = FIXTURE_VERSION * 1000 + counter;',
                              '*result = argument ? FIXTURE_VERSION * 1000 + argument : counter;')
    version1 = fixture
    version2 = '#define FIXTURE_VERSION 2\n' + fixture
    version3 = '#define FIXTURE_VERSION 3\n#define FIXTURE_ENGINE_SITE 0xd112db\n' + fixture
    entry.write_text(version1, encoding='utf-8')
    specification = importlib.util.spec_from_file_location('acceptance_probe', DEVKIT / 'scripts/native-probe.py')
    probe = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(probe)
    kernel = probe.api()
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    user.SendMessageW.restype = W.LPARAM
    user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    user.EnumChildWindows.argtypes = [W.HWND, callback, W.LPARAM]
    report = {'scope': 'ABI3 dynamic multi-plugin discovery/files/state/hooks' if options.multi else
              'ABI3 owned-game watch/hooks/UI/error rollback/release', 'steps': [], 'passed': False}
    started = time.perf_counter()
    state = None
    watcher = None
    installed = False
    installed_plugins = []
    installed_digests = {}
    cleanup_spec = importlib.util.spec_from_file_location('legacy_player_cleanup', DEVKIT / 'scripts/plugin-player-check.py')
    cleanup_module = importlib.util.module_from_spec(cleanup_spec)
    cleanup_spec.loader.exec_module(cleanup_module)
    owned_loader_digest = None
    owned_preload_digest = None
    saved_loader = None
    loader_path = root / '.local/test-game/bin/dinput8.dll'
    preload_path = root / '.local/test-game/bin/wsock32.dll'
    saved_preload = preload_path.read_bytes() if preload_path.exists() else None
    staged_graphics = None
    release_plugin = root / '.local/test-game/bin/Heroes5Mods/Plugins/sdk-acceptance.dll'

    def record(name, **values):
        item = {'step': name, **values}
        report['steps'].append(item)
        print(json.dumps(item, ensure_ascii=True), flush=True)

    def launch():
        if staged_graphics is not None:
            pid, owned_handle = cleanup_module.launch_player_game(probe, kernel, root / '.local/test-game/bin/H5_Game.exe',
                staged_graphics['facade_sha256'])
            try:
                owned_state = {'pid': pid, 'created': probe.creation_time(kernel, owned_handle)}
                probe.STATE.write_text(json.dumps(owned_state), encoding='utf-8')
                return owned_state
            finally:
                kernel.CloseHandle(owned_handle)
        result = subprocess.run([sys.executable, '-X', 'utf8', str(DEVKIT / 'scripts/native-probe.py'),
                                 'launch', '--map', 'WorkshopPolygon'], capture_output=True,
                                text=True, encoding='utf-8', timeout=30)
        require(result.returncode == 0, result.stdout + result.stderr)
        return json.loads((root / '.local/test-state/native-probe.json').read_text(encoding='utf-8'))

    def windows_and_labels():
        windows, labels = [], []
        def child(window, unused):
            name = ctypes.create_unicode_buffer(128)
            user.GetClassNameW(window, name, 128)
            if name.value.lower() == 'static':
                text = ctypes.create_unicode_buffer(128)
                user.SendMessageW(window, 13, 128, ctypes.addressof(text))
                labels.append(text.value)
            return True
        def parent(window, unused):
            pid = W.DWORD()
            user.GetWindowThreadProcessId(window, ctypes.byref(pid))
            if pid.value == state['pid']:
                windows.append(window)
                user.EnumChildWindows(window, callback(child), 0)
            return True
        user.EnumWindows(callback(parent), 0)
        return windows, labels

    def wait_label(expected):
        deadline = time.perf_counter() + 5
        while True:
            labels = windows_and_labels()[1]
            if expected in labels:
                return labels
            require(time.perf_counter() < deadline, 'Missing UI value: ' + expected + '; got ' + repr(labels))
            time.sleep(0.02)

    def post(argument):
        for window in windows_and_labels()[0]:
            require(user.PostMessageW(window, 0x8000 + 0x531, argument, 0), 'Cannot post owned UI event')

    def read_call():
        process = probe.checked(kernel.OpenProcess(0x410, False, state['pid']))
        try:
            require(probe.creation_time(kernel, process) == state['created'], 'Owned PID creation changed')
            return probe.read(kernel, process, 0xd112db, 5).hex()
        finally:
            kernel.CloseHandle(process)

    def engine_subscription_count():
        import game_control
        process = probe.checked(kernel.OpenProcess(0x410, False, state['pid']))
        try:
            require(probe.creation_time(kernel, process) == state['created'], 'Owned PID creation changed')
            return len(game_control.read_script_observers(probe, kernel, process))
        finally:
            kernel.CloseHandle(process)

    def capture_ready(expected_controls=None):
        if expected_controls is None:
            expected_controls = 2 if options.multi else 1
        deadline = time.perf_counter() + 15
        while True:
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(DEVKIT / 'scripts/game-ui.ps1'),
                                     '-Action', 'capture', '-GameProcessId', str(state['pid']),
                                     '-ExpectedSdkControls', str(expected_controls)],
                                    capture_output=True, text=True, encoding='utf-8', timeout=20)
            if result.returncode == 0:
                captured = json.loads(result.stdout)
                if any('1510' in line['Text'] for line in captured['Lines']):
                    destination = output / ('map-' + str(state['pid']) + '.png')
                    destination.write_bytes(Path(captured['Image']).read_bytes())
                    record('map_ready', pid=state['pid'], image=str(destination))
                    return
            require(time.perf_counter() < deadline, 'Expected WorkshopPolygon stamina not observed before close')
            time.sleep(0.1)

    def close():
        result = subprocess.run(['powershell', '-NoProfile', '-File', str(DEVKIT / 'scripts/game-ui.ps1'),
                                 '-Action', 'close', '-GameProcessId', str(state['pid'])],
                                capture_output=True, text=True, encoding='utf-8', timeout=45)
        require(result.returncode == 0, 'Normal close failed: ' + result.stderr)
        record('normal_close', result=json.loads(result.stdout))

    try:
        if options.auto_core:
            # Finish the first full compiler/configure pass before game startup.
            # The supervisor reuses this cache; later edits still build live.
            specification = importlib.util.spec_from_file_location('acceptance_watch', DEVKIT / 'scripts/plugin-watch.py')
            watch_module = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(watch_module)
            compiler, environment = watch_module.compiler_environment(options.toolchain)
            initial_build = watch_module.CoreBuild(core_source, output / 'build/core', environment).update()
            require(initial_build['status'] == 'core_built', 'Initial core build failed: ' + repr(initial_build))
            record('core_initial_build', result=initial_build)
        state = launch()
        record('developer_launch', pid=state['pid'], created=state['created'])
        if options.auto_core:
            capture_ready(expected_controls=0)
        require(read_call() == 'e8a0f6ffff', 'Pinned engine CALL already modified')
        arguments = [sys.executable, '-X', 'utf8', str(DEVKIT / 'scripts/plugin-watch.py'),
                     '--owned-game', '--main-thread', '--control-stdin',
                     '--plugins' if options.multi else '--source', str(source),
                     '--output', str(output / 'build'), '--client', str(build / 'plugin_bridge_client.exe'),
                     '--bridge', str(build / 'heroes5_plugin_bridge.dll'), '--toolchain', str(options.toolchain)]
        if options.auto_core:
            arguments += ['--core-source', str(core_source)]
        watcher = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8')
        responses = queue.Queue()
        def collect():
            for line in watcher.stdout:
                responses.put(json.loads(line))
        threading.Thread(target=collect, daemon=True).start()
        if options.multi:
            pending_events = []
            def receive(plugin, status, request_id=None):
                deadline = time.perf_counter() + 35
                while True:
                    for index, response in enumerate(pending_events):
                        if response.get('plugin') == plugin and response['status'] == status and (
                                request_id is None or response.get('id') == request_id):
                            return pending_events.pop(index)
                    response = responses.get(timeout=max(0.01, deadline - time.perf_counter()))
                    record('supervisor_event', response=response)
                    require(response['status'] not in ('plugin_failed', 'plugin_cleanup_unconfirmed'),
                            'Plugin lifecycle failure: ' + repr(response))
                    pending_events.append(response)
                    require(time.perf_counter() < deadline, 'Supervisor response timeout')
            def plugin_command(plugin, function, argument=0, command='main'):
                request_id = len(report['steps'])
                watcher.stdin.write(json.dumps({'plugin': plugin, 'id': request_id, 'command': command,
                                                'function': function, 'argument': argument}) + '\n')
                watcher.stdin.flush()
                response = receive(plugin, 'command_result', request_id)['response']
                require(response['status'] == 0, 'Plugin command failed: ' + repr(response))
                return response['result']
            supervisor_ready = receive(None, 'supervising')
            record('compiler_setup_seconds', seconds=supervisor_ready['compiler_setup_seconds'])
            if options.auto_core:
                receive(None, 'core_update_completed')
            alpha = source / 'alpha'; beta = source / 'beta'
            added_at = time.perf_counter(); alpha.mkdir()
            (alpha / 'plugin.cpp').write_text(version2, encoding='utf-8')
            alpha_first = receive('alpha', 'applied')['instance']
            require(receive('alpha', 'compiler_ready')['reused'], 'Alpha repeated compiler setup')
            require(plugin_command('alpha', 1, 10) == 10, 'Alpha state missing')
            plugin_command('alpha', 1, command='event'); wait_label('2001')
            record('add_plugin_seconds', plugin='alpha', seconds=time.perf_counter() - added_at)
            added_at = time.perf_counter(); beta.mkdir()
            (beta / 'plugin.cpp').write_text(version2, encoding='utf-8')
            receive('beta', 'applied')
            require(receive('beta', 'compiler_ready')['reused'], 'Beta repeated compiler setup')
            require(plugin_command('beta', 1, 20) == 20, 'Beta state not independent')
            require(plugin_command('alpha', 1) == 10, 'Beta initialization altered alpha')
            plugin_command('beta', 2, command='event'); wait_label('2002')
            require('2001' in windows_and_labels()[1], 'Targeted beta event reached alpha')
            record('add_plugin_seconds', plugin='beta', seconds=time.perf_counter() - added_at)
            if options.auto_core:
                require(plugin_command('alpha', 0, command='core') == 1 and
                        plugin_command('beta', 0, command='core') == 1, 'Initial automatic core missing')
                (core_source / 'extra.hpp').write_text('unsigned SdkValue();\n', encoding='utf-8')
                (core_source / 'extra.cpp').write_text('unsigned SdkValue() { return 77; }\n', encoding='utf-8')
                write_core_project(2, extra=True)
                receive(None, 'core_update_completed')
                for name, value in (('alpha', 10), ('beta', 20)):
                    require(plugin_command(name, 0, command='core') == 2 and
                            plugin_command(name, 0, command='feature') == 77 and
                            plugin_command(name, 1) == value, 'Automatic core update lost functionality/state')
                # Alpha accepts this core; beta rejects Restore. The manager
                # must restore alpha too before announcing overall rejection.
                write_core_project(2, extra=True, reject_slot=1)
                receive(None, 'core_update_rejected')
                for name, value in (('alpha', 10), ('beta', 20)):
                    require(plugin_command(name, 0, command='feature') == 77 and
                            plugin_command(name, 1) == value, 'Coordinated rollback lost surviving state')
                (core_source / 'extra.cpp').write_text('invalid C++\n', encoding='utf-8')
                receive(None, 'core_build_failed')
                require(plugin_command('alpha', 1) == 10 and plugin_command('beta', 1) == 20,
                        'Core compiler failure damaged plugin state')
                (core_source / 'extra.cpp').write_text('unsigned SdkValue() { return 88; }\n', encoding='utf-8')
                write_core_project(2, extra=True)
                receive(None, 'core_update_completed')
                require(plugin_command('alpha', 0, command='feature') == 88 and
                        plugin_command('beta', 0, command='feature') == 88, 'Core helper edit not applied')
                write_core_project(1)
                (core_source / 'extra.cpp').unlink(); (core_source / 'extra.hpp').unlink()
                receive(None, 'core_update_completed')
                require(plugin_command('alpha', 0, command='core') == 1 and
                        plugin_command('beta', 0, command='core') == 1, 'Core source removal not applied')
                plugin_command('alpha', 1, command='event'); wait_label('2001')
                plugin_command('beta', 2, command='event'); wait_label('2002')
                require(plugin_command('alpha', 1) == 10 and plugin_command('beta', 1) == 20,
                        'Restored event subscriptions changed persistent state')
                record('automatic_core_acceptance', plugins=['alpha', 'beta'], states=[10, 20])
            # A new translation unit, then a new header, then deletion of both.
            helper = alpha / 'extra.cpp'; header = alpha / 'extra.hpp'
            helper.write_text('unsigned AddedValue() { return 77; }\n', encoding='utf-8')
            extended = 'extern unsigned AddedValue();\n' + version2.replace('*result = 42;', '*result = AddedValue();')
            (alpha / 'plugin.cpp').write_text(extended, encoding='utf-8')
            receive('alpha', 'applied')
            require(plugin_command('alpha', 2) == 77 and plugin_command('beta', 2) == 42, 'New cpp not isolated')
            header.write_text('#define ADDED_VALUE 88\n', encoding='utf-8')
            helper.write_text('#include "extra.hpp"\nunsigned AddedValue() { return ADDED_VALUE; }\n', encoding='utf-8')
            receive('alpha', 'applied')
            require(plugin_command('alpha', 2) == 88, 'New header not applied')
            (alpha / 'plugin.cpp').write_text(version2, encoding='utf-8')
            helper.unlink(); header.unlink()
            receive('alpha', 'applied')
            require(plugin_command('alpha', 2) == 42 and plugin_command('alpha', 1) == 10,
                    'File removal changed state or retained deleted function')
            (alpha / 'plugin.cpp').write_text('invalid C++', encoding='utf-8')
            receive('alpha', 'build_failed')
            require(plugin_command('beta', 1, 2) == 22 and plugin_command('alpha', 1) == 10,
                    'Compile error escaped its plugin')
            (alpha / 'plugin.cpp').write_text(version3, encoding='utf-8')
            receive('alpha', 'applied')
            patched = read_call(); require(patched != 'e8a0f6ffff', 'Alpha engine hook missing')
            invalid_beta = version3.replace('FIXTURE_ENGINE_SITE 0xd112db', 'FIXTURE_ENGINE_SITE 0xd112dc')
            (beta / 'plugin.cpp').write_text(invalid_beta, encoding='utf-8')
            receive('beta', 'reload_rejected')
            require(read_call() == patched and plugin_command('beta', 1) == 22,
                    'Unsupported beta hook damaged alpha or beta state')
            require(plugin_command('alpha', 1) // 1000000 == 3, 'Alpha hook stopped after beta conflict')
            if options.auto_core:
                (core_source / 'extra.hpp').write_text('unsigned SdkValue();\n', encoding='utf-8')
                (core_source / 'extra.cpp').write_text('unsigned SdkValue() { return 99; }\n', encoding='utf-8')
                write_core_project(2, extra=True)
                receive(None, 'core_update_completed')
                require(read_call() != 'e8a0f6ffff' and plugin_command('alpha', 1) // 1000000 == 3,
                        'Automatic core update lost the active engine observer')
                require(plugin_command('alpha', 0, command='feature') == 99 and
                        plugin_command('beta', 0, command='feature') == 99 and
                        plugin_command('beta', 1) == 22, 'Extended core lost shared function/independent state')
                plugin_command('beta', 2, command='event'); wait_label('2002')
                record('automatic_core_with_engine_hook', feature=99, surviving_beta_state=22)
            archived = output / 'removed-alpha'
            require(alpha.resolve().is_relative_to(output.resolve()) and archived.resolve().is_relative_to(output.resolve()),
                    'Test directory move escaped workspace')
            removed_at = time.perf_counter(); alpha.rename(archived)
            receive('alpha', 'plugin_stopped')
            require(engine_subscription_count() == 0, 'Removing alpha left an engine subscriber')
            require(windows_and_labels()[1] == ['2002'], 'Removing alpha disturbed beta UI: ' + repr(windows_and_labels()[1]))
            require(plugin_command('beta', 1) == 22, 'Removing alpha changed beta state')
            record('remove_plugin_seconds', plugin='alpha', seconds=time.perf_counter() - removed_at)
            (archived / 'plugin.cpp').write_text(version2, encoding='utf-8')
            archived.rename(alpha)
            alpha_second = receive('alpha', 'applied')['instance']
            require(receive('alpha', 'compiler_ready')['reused'], 'Re-added alpha repeated compiler setup')
            require(alpha_second != alpha_first and plugin_command('alpha', 1) == 0,
                    'Re-added plugin did not get a fresh runtime')
            require(plugin_command('beta', 1) == 22, 'Re-add altered surviving plugin')
            plugin_command('alpha', 3, command='event'); wait_label('2003')
            beta_release_source = version3.replace('#define FIXTURE_ENGINE_SITE 0xd112db\n', '')
            (beta / 'plugin.cpp').write_text(beta_release_source, encoding='utf-8')
            receive('beta', 'applied')
            require(plugin_command('beta', 1) == 22, 'Beta state lost while resolving hook conflict')
            plugin_command('beta', 2, command='event'); wait_label('3002')
            require('2003' in windows_and_labels()[1], 'Beta update altered alpha UI')
            capture_ready()
            watcher.stdin.write('{"command":"stop"}\n'); watcher.stdin.flush()
            require(watcher.wait(timeout=50) == 0, watcher.stderr.read())
            watcher = None
            require(windows_and_labels()[1] == [] and engine_subscription_count() == 0, 'Supervisor stop left callbacks/UI')
            close(); state = None
            saved_loader = loader_path.read_bytes() if loader_path.exists() else None
            for name, plugin_source in (('alpha', alpha), ('beta', beta)):
                release_name = 'sdk-acceptance-' + name
                arguments = [sys.executable, '-X', 'utf8', str(DEVKIT / 'scripts/plugin-watch.py'),
                                         '--source', str(plugin_source), '--output', str(output / ('release-' + name)),
                                         '--toolchain', str(options.toolchain), '--release', release_name,
                                         '--loader', str(build / 'dinput8.dll')]
                if options.auto_core:
                    arguments += ['--core-source', str(core_source)]
                result = subprocess.run(arguments,
                                        capture_output=True, text=True, encoding='utf-8', timeout=45)
                require(result.returncode == 0, result.stdout + result.stderr)
                package = json.loads(result.stdout)
                live_source = next(step['response']['source_hashes'] for step in reversed(report['steps'])
                                   if step['step'] == 'supervisor_event' and
                                   step['response'].get('plugin') == name and step['response']['status'] == 'applied')
                require(package['manifest']['source_hashes'] == live_source, 'Release source differs from live plugin')
                if options.auto_core:
                    live_core = next(step['response']['source_hashes'] for step in reversed(report['steps'])
                                     if step['step'] == 'supervisor_event' and step['response']['status'] == 'core_built')
                    require(package['manifest']['core_source_hashes'] == live_core,
                            'Release core differs from the live extended core')
                record('plugin_release', plugin=name, result=package)
                target = release_plugin.parent / (release_name + '.dll')
                require(not target.exists(), 'Refuse to overwrite existing release test plugin')
                target.parent.mkdir(parents=True, exist_ok=True)
                with ZipFile(package['archive']) as archive:
                    installed = True
                    installed_plugins.append(target)
                    cleanup_module.stage_player_file(target, archive.read('bin/Heroes5Mods/Plugins/' + release_name + '.dll'), installed_digests)
                    shared_staged = {}
                    cleanup_module.stage_player_file(loader_path, archive.read('bin/dinput8.dll'), shared_staged)
                    owned_loader_digest = shared_staged[loader_path]
                    if staged_graphics is None:
                        from xalkit_runtime import stage_owned_graphics
                        candidate = output / 'd3d9.dll'
                        candidate.write_bytes(archive.read('bin/d3d9.dll'))
                        staged_graphics = {}
                        stage_owned_graphics(root, candidate, receipt=staged_graphics)
            state = launch(); record('multi_release_launch', pid=state['pid'], created=state['created'])
            deadline = time.perf_counter() + 8
            while windows_and_labels()[1].count('0') != 2:
                require(time.perf_counter() < deadline, 'Two release plugins did not initialize')
                time.sleep(0.05)
            post(1); wait_label('2001'); wait_label('3001')
            record('multi_release_ui', labels=windows_and_labels()[1])
            capture_ready()
            if options.auto_core:
                for plugin in installed_plugins:
                    result = subprocess.run([str(build / 'plugin_bridge_client.exe'), '--owned', str(state['pid']),
                        str(state['created']), str((root / '.local/test-game/bin/H5_Game.exe').resolve()), str(plugin)],
                        input='feature\nquit\n', capture_output=True, text=True, encoding='utf-8', timeout=30)
                    require(result.returncode == 0, 'Player core probe failed: ' + result.stderr)
                    messages = [json.loads(line) for line in result.stdout.splitlines()]
                    require(messages[0] == {'ready': True} and messages[1]['status'] == 0 and
                            messages[1]['result'] == 99, 'Extra core unit not active in player release')
                    record('player_extended_core_export', plugin=plugin.name, feature=99)
            close(); state = None
            report['passed'] = True
            return
        compiler_ready = responses.get(timeout=35)
        require(compiler_ready['status'] == 'compiler_ready' and not compiler_ready['reused'],
                'Standalone watcher must prepare its own compiler environment')
        record('compiler_setup_seconds', seconds=compiler_ready['compiler_setup_seconds'])
        def update():
            while True:
                response = responses.get(timeout=30)
                if response['status'] != 'watching':
                    record('source_update', response=response)
                    return response
        def command(name, function, argument=0):
            request = {'id': len(report['steps']), 'command': name, 'function': function, 'argument': argument}
            watcher.stdin.write(json.dumps(request) + '\n'); watcher.stdin.flush()
            response = responses.get(timeout=15)
            require(response.get('id') == request['id'] and response['status'] == 'command_result',
                    'Unexpected control response: ' + repr(response))
            return response['response']
        require(update()['observation']['result'] == 1, 'Initial payload missing')
        baseline = windows_and_labels()[1]
        require(command('main', 1, 7)['result'] == 7, 'Initial state missing')
        entry.write_text(version2, encoding='utf-8')
        require(update()['status'] == 'applied', 'Event payload rejected')
        require(command('main', 2)['result'] == 42, 'New function missing')
        post(1); wait_label('2001')
        require(command('main', 1)['result'] == 7, 'State changed on UI reload')
        if options.core:
            def replace_core(filename, expected):
                request = {'id': len(report['steps']), 'command': 'core-reload',
                           'bridge': str(build / filename)}
                watcher.stdin.write(json.dumps(request) + '\n'); watcher.stdin.flush()
                response = responses.get(timeout=35)
                require(response.get('id') == request['id'] and response['status'] == expected,
                        'Unexpected core replacement response: ' + repr(response))
                record('core_replacement', response=response)
                return response
            require(command('core', 0)['result'] == 1, 'Initial core version differs')
            replace_core('heroes5_plugin_bridge_bad_restore.dll', 'core_rejected')
            require(command('core', 0)['result'] == 1 and command('main', 1)['result'] == 7,
                    'Rejected core damaged old version/state')
            post(2); wait_label('2002')
            replace_core('heroes5_plugin_bridge_v2.dll', 'core_applied')
            require(command('core', 0)['result'] == 2 and command('feature', 0)['result'] == 42,
                    'New core export unavailable')
            require(command('main', 1)['result'] == 7, 'Core replacement lost state')
            post(3); wait_label('2003')
        saved = time.perf_counter(); entry.write_text(version3, encoding='utf-8')
        require(update()['status'] == 'applied', 'Engine hook payload rejected')
        post(1); wait_label('3001')
        record('save_to_ui', seconds=time.perf_counter() - saved)
        require(read_call() != 'e8a0f6ffff', 'Engine CALL was not patched')
        engine_value = command('main', 1)['result']
        require(engine_value // 1000000 == 3 and engine_value % 1000000 >= 7, 'Engine callback/state not observed')
        record('engine_callback', value=engine_value, call_bytes=read_call())
        if options.core:
            replace_core('heroes5_plugin_bridge_bad_restore.dll', 'core_rejected')
            require(command('core', 0)['result'] == 2 and read_call() != 'e8a0f6ffff',
                    'Rollback failed to restore previous core/hook')
            require(command('main', 1)['result'] >= engine_value, 'Rollback lost engine state')
            replace_core('heroes5_plugin_bridge.dll', 'core_applied')
            require(command('core', 0)['result'] == 1 and read_call() != 'e8a0f6ffff',
                    'Core handoff failed to install replacement engine hook')
            require(command('main', 1)['result'] >= engine_value, 'Core handoff lost engine state')
            post(1); wait_label('3001')
        invalid = version3.replace('0xd112db', '0xd112dc')
        entry.write_text(invalid, encoding='utf-8'); require(update()['status'] == 'reload_rejected', 'Bad hook accepted')
        post(1); wait_label('3001')
        rejected = version3.replace('if (command == 0) { *result = FIXTURE_VERSION; return 1; }',
                                    'if (command == 0) { return 0; }')
        entry.write_text(rejected, encoding='utf-8'); require(update()['status'] == 'observation_failed', 'Bad probe accepted')
        require(command('main', 1)['result'] // 1000000 == 3, 'Previous engine handler not restored')
        entry.write_text('invalid C++', encoding='utf-8'); require(update()['status'] == 'build_failed', 'Bad compile accepted')
        post(1); wait_label('3001')
        capture_ready()
        entry.write_text(version1, encoding='utf-8'); require(update()['status'] == 'applied', 'Disable reload failed')
        require(engine_subscription_count() == 0 and windows_and_labels()[1] == baseline, 'Callbacks/UI not removed')
        counter = command('main', 1)['result']; time.sleep(0.1)
        require(command('main', 1)['result'] == counter, 'Removed callback still executes')
        watcher.stdin.write('{"command":"stop"}\n'); watcher.stdin.flush()
        require(watcher.wait(timeout=15) == 0, watcher.stderr.read())
        watcher = None
        require(engine_subscription_count() == 0, 'Stop left engine callbacks')
        close(); state = None
        entry.write_text(version3, encoding='utf-8')
        release = subprocess.run([sys.executable, '-X', 'utf8', str(DEVKIT / 'scripts/plugin-watch.py'),
                                  '--source', str(source), '--output', str(output / 'build'),
                                  '--toolchain', str(options.toolchain), '--release', 'sdk-acceptance',
                                  '--loader', str(build / 'dinput8.dll')],
                                 capture_output=True, text=True, encoding='utf-8', timeout=45)
        require(release.returncode == 0, release.stdout + release.stderr)
        package = json.loads(release.stdout); record('release', result=package)
        require(not release_plugin.exists(), 'Refuse to replace an existing acceptance plugin')
        saved_loader = loader_path.read_bytes() if loader_path.exists() else None
        release_plugin.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(package['archive']) as archive:
            require(set(archive.namelist()) == {'bin/Heroes5Mods/Plugins/sdk-acceptance.dll',
                                                'bin/dinput8.dll', 'bin/wsock32.dll', 'release.json'}, 'Unexpected release contents')
            installed = True
            installed_plugins.append(release_plugin)
            shared_staged = {}
            cleanup_module.stage_player_file(loader_path, archive.read('bin/dinput8.dll'), shared_staged)
            owned_loader_digest = shared_staged[loader_path]
            cleanup_module.stage_player_file(preload_path, archive.read('bin/wsock32.dll'), shared_staged)
            owned_preload_digest = shared_staged[preload_path]
            cleanup_module.stage_player_file(release_plugin, archive.read('bin/Heroes5Mods/Plugins/sdk-acceptance.dll'), installed_digests)
        state = launch(); record('release_launch', pid=state['pid'], created=state['created'])
        wait_label('0'); post(1); wait_label('3001')
        require(read_call() != 'e8a0f6ffff', 'Release engine hook missing')
        post(0)
        deadline = time.perf_counter() + 5
        while not any(value.isdigit() and int(value) // 1000000 == 3 for value in windows_and_labels()[1]):
            require(time.perf_counter() < deadline, 'Release engine callback not observed')
            time.sleep(0.02)
        record('release_engine_ui', labels=windows_and_labels()[1])
        capture_ready(); close(); state = None
        report['passed'] = True
    except Exception as error:
        report['failure'] = repr(error)
        raise
    finally:
        if watcher and watcher.poll() is None:
            try:
                watcher.stdin.write('{"command":"stop"}\n'); watcher.stdin.flush(); watcher.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                watcher.kill(); watcher.wait()
        if state:
            process = kernel.OpenProcess(0x100401, False, state['pid'])
            if process:
                if probe.creation_time(kernel, process) == state['created']:
                    kernel.TerminateProcess(process, 1); kernel.WaitForSingleObject(process, 5000)
                    report['failure_cleanup'] = 'terminated verified owned process'
                kernel.CloseHandle(process)
        if installed:
            cleanup_errors = []
            cleanup_module.restore_player_files(installed_digests, loader_path, owned_loader_digest,
                saved_loader, cleanup_errors, preload_path, owned_preload_digest, saved_preload)
            if staged_graphics is not None:
                try:
                    from xalkit_runtime import restore_owned_graphics
                    restore_owned_graphics(staged_graphics)
                except Exception as restoration_error:
                    cleanup_errors.append({'stage': 'graphics_restore', 'error': repr(restoration_error)})
            report['sandbox_restored'] = not cleanup_errors
            if cleanup_errors:
                report['cleanup_errors'] = cleanup_errors
                report['passed'] = False
        report['total_seconds'] = time.perf_counter() - started
        report['sdk_source_hashes'] = {str(path.relative_to(DEVKIT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                       for path in [DEVKIT / 'native/plugin_bridge.cpp', DEVKIT / 'native/plugin_runtime.hpp',
                                                    DEVKIT / 'scripts/plugin-watch.py', DEVKIT / 'scripts/plugin_core.py',
                                                    DEVKIT / 'native/player_plugin.cmake', DEVKIT / 'scripts/sdk_logging.py',
                                                    Path(__file__)]}
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        journal_path = root / '.local/test-state/development-timings.json'
        journal = json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {'operations': []}
        journal['operations'].append({'category': 'sdk_live_acceptance', 'duration_seconds': report['total_seconds'],
                                      'why': 'Complete automatic development and same-source player release control',
                                      'improvement': 'Reuse cached objects and persistent bridge'})
        journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed': report['passed'], 'report': str(output / 'report.json')}), flush=True)


if __name__ == '__main__':
    main()
