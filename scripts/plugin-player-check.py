"""Explicit player DLL acceptance from a completed canonical native HMR check."""
import argparse
import signal
import shutil
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile

from workspace import DEVKIT, workspace_root
from sdk_diagnostics import OwnedCrashMonitor
from game_launch import map_arguments


def launch_player_game(probe, kernel, game, graphics_facade_hash=None):
    """Create an owned ordinary player process without developer startup patches."""
    for name in ('H5_Game.exe', 'H5_MapEditor.exe'):
        running = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + name, '/FO', 'CSV', '/NH'],
                                 check=True, capture_output=True)
        if name.encode('ascii').lower() in running.stdout.lower():
            raise RuntimeError('Close Heroes V and its editor before player acceptance')
    for name, expected in probe.HASHES.items():
        actual = hashlib.sha256((game.parent / name).read_bytes()).hexdigest()
        if name == 'd3d9.dll' and graphics_facade_hash is not None and actual == graphics_facade_hash:
            if (len(graphics_facade_hash) != 64 or any(value not in '0123456789abcdef' for value in graphics_facade_hash) or
                    hashlib.sha256((game.parent / 'd3d9.universe.dll').read_bytes()).hexdigest() != expected):
                raise RuntimeError('Unsupported retained player graphics binary')
            continue
        if actual != expected:
            raise RuntimeError('Unsupported player binary: ' + name)
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline(
        [str(game), *map_arguments(game.parent.parent, 'WorkshopPolygon')]))
    process = probe.Process()
    startup = probe.Startup()
    startup.cb = ctypes.sizeof(startup)
    probe.checked(kernel.CreateProcessW(str(game), command, None, None, False, 0, None,
                                       str(game.parent), ctypes.byref(startup), ctypes.byref(process)))
    try:
        return process.pid, process.process
    finally:
        kernel.CloseHandle(process.thread)


def verify_development_sources(development, sdk):
    for name, expected in development['source_hashes'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Development source path is outside the canonical SDK')
        if name.startswith('game-api/'):
            source = sdk / 'game-api/include/h5' / name.split('/', 1)[1]
        else:
            source = sdk / name
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise RuntimeError('SDK source changed after development acceptance: ' + name)


def restore_accepted_core_project(project):
    return project.replace(b'H5_CORE_VERSION=3', b'H5_CORE_VERSION=2').replace(
        b'target_compile_definitions(heroes5_plugin_bridge PRIVATE H5_CORE_REJECT_SLOT=1)\r\n', b'').replace(
        b'target_compile_definitions(heroes5_plugin_bridge PRIVATE H5_CORE_REJECT_SLOT=1)\n', b'')


def verify_accepted_core(history, snapshot):
    candidate = None
    accepted = None
    for event in history:
        if event['status'] == 'core_built':
            candidate = event['source_hashes']
        elif event['status'] == 'core_update_completed':
            accepted = candidate
    if accepted is None or snapshot != accepted:
        raise RuntimeError('Core source changed after the last accepted core update')


def validate_player_workspace(root):
    root = Path(root).resolve()
    game = root / '.local/test-game'
    marker = root / '.local/test-state/prepared.json'
    metadata = json.loads(marker.read_text(encoding='utf-8'))
    if (game.resolve() != game or marker.resolve() != marker or metadata.get('status') != 'complete'
            or metadata.get('profile') != 'WorkshopDev' or metadata.get('game') != str(game)):
        raise ValueError('Player staging requires the unredirected prepared private game')
    return game


def validate_player_path(path):
    path = Path(path).absolute()
    if path.resolve() != path or (path.exists() and path.stat().st_nlink != 1):
        raise ValueError('Player file mutation refuses redirected or linked paths')


def stage_player_file(path, contents, installed):
    validate_player_path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(contents)
        os.replace(temporary, path)
        installed[path] = hashlib.sha256(contents).hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


def restore_player_files(installed, loader_path, owned_loader_digest, saved_loader, errors,
                         preload_path=None, owned_preload_digest=None, saved_preload=None):
    for path, digest in installed.items():
        try:
            validate_player_path(path)
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                path.unlink()
            else:
                raise RuntimeError('Owned package changed; not deleting it')
        except Exception as error:
            errors.append({'stage': 'restore_plugin', 'file': str(path), 'error': repr(error)})
    for stage, path, digest, saved in (
            ('restore_bootstrap', loader_path, owned_loader_digest, saved_loader),
            ('restore_preload', preload_path, owned_preload_digest, saved_preload)):
        if not digest:
            continue
        try:
            validate_player_path(path)
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError('Bootstrap changed; not overwriting it')
            if saved is None:
                path.unlink()
            else:
                path.write_bytes(saved)
        except Exception as error:
            errors.append({'stage': stage, 'error': repr(error)})


def main(argv=None):
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--development-report', type=Path, required=True)
    parser.add_argument('--toolchain', type=Path, required=True)
    parser.add_argument('--loader', type=Path, required=True)
    parser.add_argument('--client', type=Path, required=True)
    parser.add_argument('--bridge', type=Path, required=True)
    parser.add_argument('--crash-monitor', type=Path)
    options = parser.parse_args(argv)
    root = workspace_root()
    validate_player_workspace(root)
    sdk = DEVKIT
    os.environ['H5_WORKSPACE'] = str(root)
    sys.path.insert(0, str(sdk / 'scripts'))
    import game_control
    from plugin_core import CoreClient
    for name, filename in (('player_probe', 'native-probe.py'), ('player_watch', 'plugin-watch.py')):
        spec = importlib.util.spec_from_file_location(name, sdk / 'scripts' / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    probe = sys.modules['player_probe']
    watch = sys.modules['player_watch']
    started = time.perf_counter()
    previous = options.development_report.resolve(strict=True).parent
    development = json.loads(options.development_report.read_text(encoding='utf-8'))
    assert development['passed'] and not development.get('cancelled'), 'Development acceptance must pass before player delivery'
    verify_development_sources(development, sdk)
    history = json.loads((previous / 'observed.json').read_text(encoding='utf-8'))
    output = root / '.local/test-state' / ('sdk-native-owner-release-' + time.strftime('%Y%m%dT%H%M%S'))
    output.mkdir()
    core = output / 'core-source'
    shutil.copytree(previous / 'core-source', core)
    project = restore_accepted_core_project((previous / 'core-source/CMakeLists.txt').read_bytes())
    toolchain = options.toolchain.resolve(strict=True)
    compiler, environment = watch.compiler_environment(toolchain)
    accepted_snapshot = watch.CoreBuild(previous / 'core-source', output / 'core-source-guard', environment).snapshot()
    accepted_snapshot['CMakeLists.txt'] = hashlib.sha256(project).hexdigest()
    verify_accepted_core(history, accepted_snapshot)
    copied_snapshot = watch.CoreBuild(core, output / 'copied-core-source-guard', environment).snapshot()
    copied_snapshot['CMakeLists.txt'] = hashlib.sha256(project).hexdigest()
    verify_accepted_core(history, copied_snapshot)
    project = project.replace((previous / 'core-source').as_posix().encode('utf-8'), core.as_posix().encode('utf-8'))
    (core / 'CMakeLists.txt').write_bytes(project)
    client_binary = options.client.resolve(strict=True)
    bridge_binary = options.bridge.resolve(strict=True)
    loader_binary = options.loader.resolve(strict=True)
    game = root / '.local/test-game/bin/H5_Game.exe'
    loader_path = game.parent / 'dinput8.dll'
    saved_loader = loader_path.read_bytes() if loader_path.exists() else None
    preload_path = game.parent / 'wsock32.dll'
    saved_preload = preload_path.read_bytes() if preload_path.exists() else None
    packages = []
    owned_loader_digest = None
    owned_preload_digest = None
    staged_graphics = None
    exit_requested = False
    installed = {}
    clients = {}
    monitor = None
    owner = None
    handle = None
    game_exit_confirmed = False
    kernel = probe.api()
    report = {'passed': False, 'scope': 'ordinary DLL bootstrap, two released observers, no mailbox or development manager',
              'steps': [], 'game_build': dict(probe.HASHES)}
    report['source_hashes'] = {name: hashlib.sha256((sdk / name).read_bytes()).hexdigest()
        for name in ('native/plugin_bridge.cpp', 'native/plugin_bridge_client.cpp', 'native/plugin_runtime.hpp',
                     'native/mod_loader.cpp', 'native/player_plugin.cmake', 'game-api/include/h5/hooks.hpp',
                     'game-api/include/h5/script_observers.hpp', 'scripts/plugin-player-check.py')}

    def record(step, **values):
        report['steps'].append({'step': step, **values})
        print(json.dumps(report['steps'][-1]), flush=True)

    def subscriptions():
        assert probe.creation_time(kernel, handle) == owner['created']
        return game_control.read_script_observers(probe, kernel, handle)

    try:
        destination = game.parent / 'Heroes5Mods/Plugins'
        assert not list(destination.glob('*.dll')), 'Refuse a mixed player acceptance installation'
        for name in ('alpha', 'beta'):
            source = previous / 'plugins' / name
            watcher = watch.PluginWatch(source, output / ('release-' + name), compiler, environment, None)
            snapshot = watcher.snapshot()
            accepted = next(item['source_hashes'] for item in reversed(history)
                            if item.get('plugin') == name and item.get('status') == 'applied')
            assert snapshot == accepted, 'Plugin/dependency sources changed after development acceptance'
            record_before = time.perf_counter()
            package = watcher.update(snapshot, record_before, 'sdk-native-owner-' + name, loader_binary, core)
            assert package['status'] == 'released', package
            with ZipFile(package['archive']) as archive:
                from plugin_core import validate_player_archive
                manifest = validate_player_archive(archive, 'sdk-native-owner-' + name)
                assert manifest['source_hashes'] == snapshot
                expected = 'bin/Heroes5Mods/Plugins/sdk-native-owner-' + name + '.dll'
                assert name + '.dll' in archive.read('README.txt').decode('utf-8')
                target = destination / ('sdk-native-owner-' + name + '.dll')
                assert not target.exists()
                destination.mkdir(parents=True, exist_ok=True)
                payload = archive.read(expected)
                bootstrap = archive.read('bin/dinput8.dll')
                graphics = archive.read('bin/d3d9.dll')
                if packages:
                    assert manifest['graphics_facade_sha256'] == packages[0]['manifest']['graphics_facade_sha256']
                    assert manifest['loader_sha256'] == packages[0]['manifest']['loader_sha256']
                owned_loader_digest = hashlib.sha256(bootstrap).hexdigest()
                assert owned_loader_digest == manifest['loader_sha256']
                assert hashlib.sha256(graphics).hexdigest() == manifest['graphics_facade_sha256']
                assert manifest['graphics_original_sha256'] == probe.HASHES['d3d9.dll']
                assert hashlib.sha256(payload).hexdigest() == manifest['plugin_sha256']
                stage_player_file(target, payload, installed)
                shared_staged = {}
                stage_player_file(loader_path, bootstrap, shared_staged)
                if staged_graphics is None:
                    from xalkit_runtime import stage_owned_graphics
                    candidate = output / 'd3d9.dll'
                    candidate.write_bytes(graphics)
                    staged_graphics = {}
                    stage_owned_graphics(root, candidate, receipt=staged_graphics)
            packages.append({'name': name, 'dll': target, 'package': package, 'manifest': manifest})
            record('same_source_package', plugin=name, archive=package['archive'], seconds=time.perf_counter() - record_before)
        assert packages[0]['manifest']['core_source_hashes'] == packages[1]['manifest']['core_source_hashes']
        pid, handle = launch_player_game(probe, kernel, game, staged_graphics['facade_sha256'])
        report['created_game_pid'] = pid
        owner = {'pid': pid, 'created': probe.creation_time(kernel, handle)}
        assert probe.creation_time(kernel, handle) == owner['created']
        report['owner'] = {key: owner[key] for key in ('pid', 'created')}
        report['launch'] = {'method': 'CreateProcessW', 'flags': 0,
                            'requested_map': 'WorkshopPolygon', 'startup_memory_patches': False}
        if options.crash_monitor:
            monitor = OwnedCrashMonitor(probe, kernel, handle, owner, game, options.crash_monitor, output / 'dumps',
                graphics_facade_hash=staged_graphics['facade_sha256'])
            report['crash_monitor'] = monitor.snapshot()
        deadline = time.monotonic() + 15
        while len(subscriptions()) != 2:
            assert time.monotonic() < deadline, 'Two player observers did not initialize automatically'
            time.sleep(0.1)
        record('automatic_player_startup_before_any_controller', pid=owner['pid'], created=owner['created'], subscriptions=2)
        for package in packages:
            name = package['name']
            clients[name] = CoreClient([str(client_binary), '--owned', str(owner['pid']),
                                       str(owner['created']), str(game.resolve()), str(package['dll'].resolve())],
                                      output / ('controllers-' + name))
            assert clients[name].request('core')['result'] == 2
            assert clients[name].request('feature')['result'] == 99
        assert clients['alpha'].request('main 2 0')['result'] == 88
        record('released_extended_functions', plugin_value=88, core_value=99)
        before = {name: client.request('main 1 0')['result'] for name, client in clients.items()}
        time.sleep(0.1)
        after = {name: client.request('main 1 0')['result'] for name, client in clients.items()}
        assert before['alpha'] // 1000000 == 4 and before['beta'] // 1000000 == 3
        assert all(after[name] > before[name] for name in before)
        record('both_released_callbacks_execute', before=before, after=after)
        clients['alpha'].close()
        del clients['alpha']
        assert len(subscriptions()) == 1
        assert clients['beta'].request('main 1 0')['result'] > after['beta']
        record('independent_player_stop', surviving='beta', subscriptions=1)
        clients['beta'].close()
        del clients['beta']
        assert len(subscriptions()) == 0
        record('all_player_callbacks_removed', subscriptions=0)
        exit_requested = True
        result = subprocess.run([str(client_binary), '--owned', str(owner['pid']),
            str(owner['created']), str(game.resolve()), str(bridge_binary)],
            input='exit\n', capture_output=True, text=True, encoding='utf-8', timeout=55)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert result.returncode == 0 and replies[1]['game_exited'] and replies[1]['exit_code'] == 0, result.stderr
        report['game_exit'] = replies[1]
        record('normal_native_exit', exit_code=0)
        report['checks_passed'] = True
    except KeyboardInterrupt:
        report['cancelled'] = True
    except Exception as error:
        report['failure'] = repr(error)
        raise
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, signal.SIG_IGN)
        cleanup_errors = []
        for name, client in list(clients.items()):
            try:
                client.close()
                del clients[name]
            except Exception as error:
                cleanup_errors.append({'stage': 'controller_close', 'plugin': name, 'error': repr(error)})
        try:
            if handle:
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.WaitForSingleObject.restype = wintypes.DWORD
                if owner is not None and kernel.WaitForSingleObject(handle, 0) != 0 and not exit_requested:
                    exit_requested = True
                    try:
                        result = subprocess.run([str(client_binary), '--owned', str(owner['pid']),
                            str(owner['created']), str(game.resolve()), str(bridge_binary)], input='exit\n',
                            capture_output=True, text=True, encoding='utf-8', timeout=55)
                        replies = [json.loads(line) for line in result.stdout.splitlines()]
                        if result.returncode or len(replies) != 2 or not replies[1].get('game_exited'):
                            raise RuntimeError('Owned native exit unconfirmed')
                    except Exception as error:
                        cleanup_errors.append({'stage': 'game_close', 'error': repr(error)})
                if kernel.WaitForSingleObject(handle, 0) != 0:
                    if owner is None or probe.creation_time(kernel, handle) == owner['created']:
                        probe.checked(kernel.TerminateProcess(handle, 1))
                        assert kernel.WaitForSingleObject(handle, 5000) == 0
                        cleanup_errors.append({'stage': 'game_close', 'error': 'Terminated immutable owned failure process'})
                exit_code = wintypes.DWORD()
                game_exit_confirmed = kernel.WaitForSingleObject(handle, 0) == 0
                if not game_exit_confirmed:
                    raise RuntimeError('Owned player game is still running')
                kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                kernel.GetExitCodeProcess.restype = wintypes.BOOL
                probe.checked(kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
                report['game_exit_code'] = exit_code.value
        except Exception as error:
            cleanup_errors.append({'stage': 'game_teardown', 'error': repr(error)})
        finally:
            if handle:
                kernel.CloseHandle(handle)
        try:
            if monitor is not None:
                report['crash_monitor'] = monitor.close()
                if report['crash_monitor']['capture_status'] == 'dump_captured':
                    cleanup_errors.append({'stage': 'game_crash', 'error': 'Owned exception dump captured'})
        except Exception as error:
            cleanup_errors.append({'stage': 'monitor_stop', 'error': repr(error)})
            if monitor:
                try:
                    report['crash_monitor'] = monitor.snapshot()
                except Exception as snapshot_error:
                    cleanup_errors.append({'stage': 'monitor_snapshot', 'error': repr(snapshot_error)})
        if handle is None or game_exit_confirmed:
            restore_player_files(installed, loader_path, owned_loader_digest, saved_loader, cleanup_errors,
                                 preload_path, owned_preload_digest, saved_preload)
            if staged_graphics is not None:
                try:
                    from xalkit_runtime import restore_owned_graphics
                    restore_owned_graphics(staged_graphics)
                except Exception as restoration_error:
                    cleanup_errors.append({'stage': 'graphics_restore', 'error': repr(restoration_error)})
        else:
            cleanup_errors.append({'stage': 'restore_files',
                                   'error': 'Package files retained because owned game exit is unconfirmed'})
        report['game_exit_confirmed'] = game_exit_confirmed
        report['manager_exit_code'] = 0 if not clients else None
        report['subscriptions_after_stop'] = 0 if report.get('game_exit_code') == 0 and not clients else None
        report['cleanup_errors'] = cleanup_errors
        report['passed'] = report.get('checks_passed', False) and not cleanup_errors
        report['duration_seconds'] = time.perf_counter() - started
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'report': str(output / 'report.json'), **report}), flush=True)

    if not report['passed']:
        raise SystemExit(130 if report.get('cancelled') and not cleanup_errors else 1)


if __name__ == '__main__':
    main()
