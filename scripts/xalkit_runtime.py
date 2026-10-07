"""Human development session over the canonical owned-game supervisor."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import queue
import ctypes
import re
import signal
import time
import hashlib
import os
import shutil
import tempfile
from contextlib import ExitStack
from ctypes import wintypes

from workspace import DEVKIT
from sdk_logging import EventLog
from xalkit_ui import text, error as sdk_error
from sdk_diagnostics import OwnedCrashMonitor, OwnedBackgroundBudget, read_owned_windows_events


def stage_owned_graphics(root, facade, receipt=None):
    """Stage only a prepared private game; retain its original bytes for restore."""
    from sdk_storage import require_games_closed
    require_games_closed()
    root = Path(root).resolve()
    game = root / '.local/test-game'
    marker = root / '.local/test-state/prepared.json'
    metadata = json.loads(marker.read_text(encoding='utf-8'))
    if (marker.resolve() != marker or game.resolve() != game or
            metadata.get('status') != 'complete' or metadata.get('game') != str(game) or
            metadata.get('profile') != 'WorkshopDev'):
        raise ValueError('Graphics staging requires a prepared private test game')
    graphics = game / 'bin/d3d9.dll'
    retained = graphics.with_name('d3d9.universe.dll')
    if graphics.resolve() != graphics or retained.resolve() != retained or graphics.stat().st_nlink != 1:
        raise ValueError('Graphics staging refuses linked game files')
    original_hash = '5eb152357f99d53397b764384d5cf9a0f6aece733ced30a34186ac57fb15be25'
    if hashlib.sha256(graphics.read_bytes()).hexdigest() != original_hash or retained.exists():
        raise ValueError('Original graphics file changed or a retained original already exists')
    candidate = Path(facade).read_bytes()
    candidate_hash = hashlib.sha256(candidate).hexdigest()
    staged = {'graphics': graphics, 'retained': retained, 'original_sha256': original_hash,
              'facade_sha256': candidate_hash}
    if receipt is not None:
        # The session owns recovery BEFORE the first filesystem mutation.
        receipt.update(staged)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=graphics.parent, prefix='xkit-graphics-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(candidate)
            output.flush()
            os.fsync(output.fileno())
        os.rename(graphics, retained)
        try:
            os.replace(temporary, graphics)
        except BaseException:
            os.rename(retained, graphics)
            raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return staged


def restore_owned_graphics(staged):
    """Refuse changed files; restore only once all game processes are closed."""
    from sdk_storage import require_games_closed
    require_games_closed()
    if not staged:
        return
    graphics, retained = staged['graphics'], staged['retained']
    if not retained.exists():
        if (graphics.resolve() == graphics and graphics.stat().st_nlink == 1 and
                hashlib.sha256(graphics.read_bytes()).hexdigest() == staged['original_sha256']):
            return  # No mutation, or staging already rolled back.
        raise ValueError('Original graphics recovery file is missing')
    for role, digest in (('graphics', 'facade_sha256'), ('retained', 'original_sha256')):
        path = staged[role]
        if role == 'graphics' and not path.exists() and path.resolve() == path:
            continue  # Interrupted between original rename and facade replacement.
        if path.resolve() != path or path.stat().st_nlink != 1 or hashlib.sha256(path.read_bytes()).hexdigest() != staged[digest]:
            raise ValueError('Staged graphics file changed; original retained for recovery')
    os.replace(staged['retained'], staged['graphics'])


def stage_resource_runtime(root, ready):
    built = dict(ready)
    watch = (root / '.local/xalkit/watch').resolve()
    generation = watch / 'runtime' / Path(built['bridge']).parent.name
    if not generation.resolve().is_relative_to(watch):
        raise ValueError('SDK runtime stage escapes the owned watch root')
    generation.mkdir(parents=True, exist_ok=True)
    roles = ('bridge', 'controller', 'launch_gate', 'console', 'licenses')
    if 'graphics' in built:
        roles += ('graphics',)
    for role in roles:
        original = Path(built[role])
        destination = generation
        if role in ('console', 'licenses'):
            destination = watch / 'console' / ('generation-' + built['manifest']['files']['XalKitConsole.dll'][:16])
            if not destination.resolve().is_relative_to(watch):
                raise ValueError('SDK console stage escapes the owned watch root')
            destination.mkdir(parents=True, exist_ok=True)
        target = (destination / original.name).resolve()
        if not target.is_relative_to(destination.resolve()):
            raise ValueError('SDK runtime stage file escapes its generation')
        if not target.exists():
            shutil.copyfile(original, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != built['manifest']['files'][original.name]:
            raise ValueError('SDK runtime staged file changed: ' + original.name)
        built[role] = str(target)
    return built


def publish_resource_endpoint(root, owned, built):
    endpoint = {'version': 1, 'pid': owned['pid'], 'created': owned['created'],
                'controller': built['controller'], 'bridge': built['bridge'],
                'controller_sha256': hashlib.sha256(Path(built['controller']).read_bytes()).hexdigest(),
                'bridge_sha256': hashlib.sha256(Path(built['bridge']).read_bytes()).hexdigest()}
    watch = root / '.local/xalkit/watch'
    watch.mkdir(parents=True, exist_ok=True)
    temporary = watch / 'diagnostic-owner.tmp'
    temporary.write_text(json.dumps(endpoint), encoding='utf-8')
    temporary.replace(watch / 'diagnostic-owner.json')


def resource_runtime_candidate(root, previous_pointer, active):
    from plugin_core import load_sdk_runtime
    pointer_bytes = (DEVKIT / 'runtime/current.json').read_bytes()
    if pointer_bytes == previous_pointer:
        return previous_pointer, None
    pointer_data = json.loads(pointer_bytes)
    if not isinstance(pointer_data, dict) or pointer_data.get('version') != 1:
        raise ValueError('Invalid SDK runtime pointer')
    pointer = pointer_data['generation']
    candidate = None
    if pointer != Path(active['bridge']).parent.name:
        candidate = stage_resource_runtime(root, load_sdk_runtime(DEVKIT, pointer))
    return pointer_bytes, candidate


def replace_resource_runtime(client, previous, candidate):
    core = client.reload_core(candidate['bridge'], None, 0, 'main', controller=candidate['controller'])
    if core['status'] != 'core_applied':
        return {'status': 'sdk_runtime_rejected', 'stage': 'core', 'result': core}
    console = client.request('console-replace ' + candidate['console'])
    if console['status'] == 0:
        return {'status': 'sdk_runtime_applied', 'core': core, 'console': console}
    if console['status'] in (1279, 1460):  # Recovery failure/timeout: UI lifetime is unconfirmed.
        raise RuntimeError('resource_console_recovery_unconfirmed')
    rollback = client.reload_core(previous['bridge'], None, 0, 'main', controller=previous['controller'])
    if rollback['status'] != 'core_applied':
        raise RuntimeError('resource_core_rollback_unconfirmed')
    return {'status': 'sdk_runtime_rejected', 'stage': 'console', 'result': console, 'rollback': rollback}


def start_session(saved, name, source, map_name=None, resources=False, background=False, console_check=False):
    from xalkit import load_backend, native_tools, resource_operation
    root = Path(saved['workspace'])
    events = EventLog(root / '.local/xalkit/logs', console=True, plugin=name)
    workshop_module = load_backend('mod-dev.py')
    workshop_module.require_stopped()
    process = None
    owned = None
    owned_handle = None
    kernel = None
    monitor = None
    stop_reason = 'supervisor_finished'
    session_failures = []
    game_started = False
    started = time.perf_counter()
    final_game_exit = None
    monitoring = None
    staged_graphics = None
    console_broker = None
    resource_client = None
    runtime_pointer = None
    next_runtime_check = 0
    last_runtime_problem = None
    background_budget = None
    plugin_ready = False
    console_validation = None
    try:
        if resources:
            from plugin_core import load_sdk_runtime
            try:
                built = stage_resource_runtime(root, load_sdk_runtime(DEVKIT))
            except (OSError, ValueError) as runtime_error:
                failure = sdk_error('SDK_RUNTIME_NOT_READY')
                events.emit({'status': 'sdk_runtime_unavailable', 'error': failure, 'reason': str(runtime_error)})
                raise RuntimeError(failure['message']) from runtime_error
            if not console_check:
                deployment = resource_operation(saved, name, source, 'deploy')
                events.emit({'status': 'resource_deployed', 'result': deployment})
        else:
            toolchain, compiler, environment, built, loader = native_tools(saved, events, with_console=True)
        if built.get('console'):
            from sdk_console import ConsoleBroker
            console_broker = ConsoleBroker(root, str(events.session))
            if console_check:
                console_broker.validation_enabled = True
        if not (root / '.local/test-state/prepared.json').is_file():
            events.emit({'status': 'prepare', 'message': text('prepare')})
            workshop = workshop_module.Workshop(root, name, True)
            with workshop_module.exclusive(workshop.local):
                workshop.prepare()
        profiles = root / '.local/test-game/UniverseTeam/Universe Mod/Profiles'
        global_settings = profiles / 'global_a2.cfg'
        if global_settings.is_file():
            match = re.search(r'(?m)^setvar profile_name\s*=\s*(\S+)\s*$', global_settings.read_text(encoding='utf-8'))
            if match:
                profile = (profiles / match.group(1)).resolve()
                if not profile.is_relative_to(profiles.resolve()):
                    raise ValueError('Sandbox profile path escapes its root')
                user_settings = profile / 'user_a2.cfg'
                content = user_settings.read_text(encoding='utf-8')
                line = 'setvar app_always_active = 1'
                if re.search(r'(?m)^setvar app_always_active\s*=', content):
                    content = re.sub(r'(?m)^setvar app_always_active\s*=.*$', line, content)
                else:
                    content += '\n' + line + '\n'
                user_settings.write_text(content, encoding='utf-8')
                events.emit({'status': 'sandbox_background_enabled', 'profile': profile.name})
        if built.get('graphics'):
            staged_graphics = {}
            with workshop_module.exclusive(root / '.local'):
                stage_owned_graphics(root, built['graphics'], receipt=staged_graphics)
        probe = load_backend('native-probe.py')
        events.emit({'status': 'launching', 'message': text('launching')})
        def retain_owner(state, process_kernel):
            nonlocal owned, owned_handle, kernel
            kernel = process_kernel
            owned = dict(state)
            if console_broker is not None:
                console_broker.owner = dict(state)
            owned_handle = probe.checked(kernel.OpenProcess(0x100400, False, owned['pid']))
            if probe.creation_time(kernel, owned_handle) != owned['created']:
                raise RuntimeError('Owned game creation time changed before resume')
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetExitCodeProcess.restype = wintypes.BOOL
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            events.emit({'status': 'game_owned_before_resume', 'game_pid': owned['pid'],
                         'game_created': owned['created']})
        with ExitStack() as child_environment:
            if built.get('console'):
                process_kernel = probe.api()
                process_kernel.GetCurrentProcess.restype = wintypes.HANDLE
                console_environment = {'XALKIT_CONSOLE_PATH': str(Path(built['console']).resolve()),
                    'XALKIT_CONSOLE_HOST_PID': str(os.getpid()),
                    'XALKIT_CONSOLE_HOST_CREATED': str(probe.creation_time(process_kernel, process_kernel.GetCurrentProcess())),
                    'XALKIT_LANG': saved.get('language', 'ru')}
                if console_broker is not None:
                    console_environment.update(XALKIT_CONSOLE_PORT=str(console_broker.port),
                                               XALKIT_CONSOLE_TOKEN=console_broker.token)
                if console_check:
                    console_environment['XALKIT_CONSOLE_VALIDATE_INPUT'] = '1'
                for key, value in console_environment.items():
                    previous = os.environ.get(key)
                    os.environ[key] = value
                    child_environment.callback(os.environ.pop, key, None) if previous is None else \
                        child_environment.callback(os.environ.__setitem__, key, previous)
            launched = probe.launch(probe.api(), map_name=map_name, control=True, before_resume=retain_owner,
                                    background=background,
                                    launch_gate=built['launch_gate'],
                                    graphics_facade_hash=staged_graphics['facade_sha256'] if staged_graphics else None) or {}
        owned['image_placement_verified'] = launched.get('image_placement_verified', False)
        game_started = True
        events.emit({'status': 'game_started', 'game_pid': owned['pid'], 'game_created': owned['created']})
        monitor_path = root / '.local/tools/procdump/procdump.exe'
        if monitor_path.is_file():
            monitor = OwnedCrashMonitor(probe, kernel, owned_handle, owned,
                root / '.local/test-game/bin/H5_Game.exe', monitor_path, root / '.local/xalkit/crashes' / str(owned['created']),
                graphics_facade_hash=staged_graphics['facade_sha256'] if staged_graphics else None)
            events.emit({'status': 'crash_monitor_started', 'message': text('crash_monitor_started'), **monitor.snapshot()})
        events.emit({'status': 'game_initializing', 'stage': 'startup'})
        readiness_started = time.perf_counter()
        load_backend('game_control.py').wait_for_game_loop(probe, owned)
        events.emit({'status': 'game_loop_ready', 'stage': 'startup',
                     'duration_seconds': time.perf_counter() - readiness_started})
        incoming = queue.Queue()
        selected_instance = None
        if resources:
            from plugin_core import CoreClient
            resource_client = CoreClient([built['controller'], '--owned', str(owned['pid']), str(owned['created']),
                str(root / '.local/test-game/bin/H5_Game.exe'), built['bridge']],
                root / '.local/xalkit/resource-host/controllers')
            response = resource_client.request('connect')
            if response['status'] != 0:
                events.emit({'status': 'sdk_connect_failed', 'stage': 'sdk_connect', 'response': response,
                             'game_pid': owned['pid'], 'game_created': owned['created']})
                # Read before cleanup so console startup reasons survive a failed
                # connection. Diagnostic failure must preserve the primary error.
                try:
                    from plugin_core import decode_diagnostics
                    diagnostic_response = resource_client.request('trace 0 2')
                    if diagnostic_response.get('status') != 0:
                        raise RuntimeError('SDK startup diagnostic read rejected')
                    snapshot = decode_diagnostics(diagnostic_response['diagnostics'])
                    for record in snapshot['records']:
                        events.emit({'status': 'sdk_startup_diagnostic', 'stage': 'sdk_connect',
                                     'level': record['level'], 'message': record['message'],
                                     'module': record['module'], 'sequence': record['sequence'],
                                     'game_pid': owned['pid'], 'game_created': owned['created']})
                except Exception as diagnostic_error:
                    events.emit({'status': 'sdk_startup_diagnostics_unavailable', 'stage': 'sdk_connect',
                                 'level': 'warning', 'reason': str(diagnostic_error)})
                raise RuntimeError(text('resource_sdk_failed'))
            publish_resource_endpoint(root, owned, built)
            try:
                background_budget = OwnedBackgroundBudget(probe, kernel, owned)
            except (OSError, RuntimeError) as budget_error:
                events.emit({'status': 'background_budget_unavailable', 'reason': str(budget_error)})
            events.emit({'status': 'resource_session_running', 'message': text('resource_session_running')})
        else:
            arguments = [sys.executable, '-X', 'utf8', str(DEVKIT / 'scripts/plugin-watch.py'),
                '--plugins', str(root / 'plugins'), '--output', str(root / '.local/xalkit/watch'),
                '--toolchain', str(toolchain), '--client', built['controller'], '--bridge', built['bridge'],
                '--core-source', str(DEVKIT / 'native'), '--owned-game', '--main-thread', '--control-stdin',
                '--managed-projects', '--log-session', events.session]
            if built.get('console'):
                arguments += ['--console-build', str(root / '.local/xalkit/console-build'), '--console-project', name]
            process = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       text=True, encoding='utf-8', env=events.child_environment(environment),
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            events.emit({'status': 'plugins_starting', 'message': text('plugins_starting')})
            def collect():
                for line in process.stdout:
                    incoming.put(line)
                incoming.put(None)
            threading.Thread(target=collect, daemon=True).start()
        while True:
            if console_check and console_validation is not None:
                break
            if kernel.WaitForSingleObject(owned_handle, 0) == 0:
                exit_code = wintypes.DWORD()
                probe.checked(kernel.GetExitCodeProcess(owned_handle, ctypes.byref(exit_code)))
                stop_reason = 'game_exited_before_stop_request'
                events.emit({'status': 'game_exited', 'reason': stop_reason,
                             'game_pid': owned['pid'], 'exit_code': exit_code.value})
                if exit_code.value != 0:
                    session_failures.append('game_exit:' + str(exit_code.value))
                break
            if background_budget is not None:
                user = ctypes.WinDLL('user32')
                user.GetForegroundWindow.restype = wintypes.HWND
                user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                foreground_pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(foreground_pid))
                background_budget.update(foreground_pid.value,
                    active=console_broker.game_operation_active() if console_broker is not None else False)
            if console_check:
                from sdk_console import check_ui
                console_validation = check_ui(root, probe, kernel, owned_handle, owned, events)
                stop_reason = 'console_check_completed'
                break
            if resource_client is not None and resource_client.poll() is not None:
                raise RuntimeError(text('resource_sdk_failed'))
            if resource_client is not None and time.monotonic() >= next_runtime_check:
                next_runtime_check = time.monotonic() + 1
                candidate = None
                try:
                    runtime_pointer, candidate = resource_runtime_candidate(root, runtime_pointer, built)
                    last_runtime_problem = None
                except (OSError, ValueError, KeyError) as candidate_error:
                    problem = str(candidate_error)
                    if problem != last_runtime_problem:
                        events.emit({'status': 'sdk_runtime_rejected', 'stage': 'validation', 'reason': problem})
                    last_runtime_problem = problem
                if candidate is not None:
                    update_started = time.perf_counter()
                    result = replace_resource_runtime(resource_client, built, candidate)
                    events.emit({**result, 'duration_seconds': time.perf_counter() - update_started})
                    if result['status'] == 'sdk_runtime_applied':
                        built = candidate
                        publish_resource_endpoint(root, owned, built)
            try:
                line = incoming.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            event = json.loads(line)
            events.emit(event)
            if event['status'] == 'plugin_discovered' and event.get('plugin') == name:
                selected_instance = event['instance']
            # The new starter has a passive display event: render after a save.
            # Existing arbitrary projects are not sent invented game messages.
            if ((event.get('plugin') == name and event['status'] == 'applied') or
                    (event['status'] == 'core_update_completed' and name in event.get('plugins', [])) or
                    (event['status'] == 'core_update_rejected' and name in event.get('rolled_back_plugins', []))):
                if not plugin_ready and event['status'] == 'applied':
                    plugin_ready = True
                    try:
                        background_budget = OwnedBackgroundBudget(probe, kernel, owned)
                    except (OSError, RuntimeError) as budget_error:
                        events.emit({'status': 'background_budget_unavailable', 'reason': str(budget_error)})
                    events.emit({'status': 'watching', 'message': text('watching')})
                    if selected_instance:
                        watch = root / '.local/xalkit/watch'
                        bridge = watch / (name + '-' + selected_instance) / ('bridge-' + name + '-' + selected_instance + '.dll')
                        controller = Path(built['controller'])
                        endpoint = {'version': 1, 'pid': owned['pid'], 'created': owned['created'],
                                    'controller': str(controller.resolve()), 'bridge': str(bridge.resolve()),
                                    'controller_sha256': hashlib.sha256(controller.read_bytes()).hexdigest(),
                                    'bridge_sha256': hashlib.sha256(bridge.read_bytes()).hexdigest()}
                        temporary = watch / 'diagnostic-owner.tmp'
                        temporary.write_text(json.dumps(endpoint), encoding='utf-8')
                        temporary.replace(watch / 'diagnostic-owner.json')
                metadata = source / 'xalkit.json'
                if metadata.is_file() and json.loads(metadata.read_text(encoding='utf-8')).get('display_event'):
                    process.stdin.write(json.dumps({'plugin': name, 'id': 'display-' + event.get('event_id', ''),
                        'command': 'event', 'function': 0, 'argument': 0}) + '\n')
                    process.stdin.flush()
            if event['status'] in ('runtime_failed', 'plugin_failed', 'plugin_cleanup_unconfirmed'):
                raise RuntimeError(event.get('reason') or event['status'])
        # A live supervisor needs its stop request before waiting when the game exits.
        # The finally block sends that request and waits for callback cleanup.
        if process and stop_reason != 'game_exited_before_stop_request' and process.wait() != 0:
            raise RuntimeError('supervisor_failed')
    except KeyboardInterrupt:
        stop_reason = 'keyboard_interrupt'
    except Exception as error:
        stop_reason = 'session_error'
        session_failures.append('session_error:' + type(error).__name__)
        events.emit({'status': 'runtime_failed', 'reason': str(error), 'exc_info': True})
        raise
    finally:
        with ExitStack() as cleanup_signals:
            interrupts = [signal.SIGINT]
            if hasattr(signal, 'SIGBREAK'):
                interrupts.append(signal.SIGBREAK)
            for interrupt in interrupts:
                previous = signal.getsignal(interrupt)
                signal.signal(interrupt, signal.SIG_IGN)
                cleanup_signals.callback(signal.signal, interrupt, previous)
            try:
                if background_budget is not None:
                    try:
                        background_budget.close()
                    except (OSError, RuntimeError) as budget_error:
                        session_failures.append('background_budget_cleanup:' + str(budget_error))
                if console_broker is not None:
                    try:
                        console_broker.close()
                    except Exception as broker_error:
                        session_failures.append('console_broker_stop:' + type(broker_error).__name__)
                        events.emit({'status': 'console_broker_stop_failed', 'reason': str(broker_error)})
                if resource_client is not None:
                    try:
                        resource_client.close()
                    except (OSError, RuntimeError) as client_error:
                        session_failures.append('sdk_host_cleanup:' + str(client_error))
                        events.emit({'status': 'plugin_cleanup_unconfirmed', 'reason': str(client_error)})
                if process and process.poll() is None:
                    events.emit({'status': 'session_stop_requested', 'reason': stop_reason})
                    try:
                        process.stdin.write('{"command":"stop"}\n'); process.stdin.flush()
                        if process.wait(timeout=55) != 0:
                            session_failures.append('supervisor_exit_nonzero')
                    except (OSError, subprocess.SubprocessError) as stop_error:
                        session_failures.append('supervisor_stop:' + str(stop_error))
                        events.emit({'status': 'plugin_cleanup_unconfirmed', 'reason': str(stop_error),
                                     'supervisor_pid': process.pid})
                elif process and process.poll() != 0:
                    session_failures.append('supervisor_exit_nonzero')
                if owned and owned_handle and kernel.WaitForSingleObject(owned_handle, 0) != 0:
                    probe = load_backend('native-probe.py')
                    handle = kernel.OpenProcess(0x1000, False, owned['pid'])
                    if handle:
                        try:
                            same_process = probe.creation_time(kernel, handle) == owned['created']
                        finally:
                            kernel.CloseHandle(handle)
                        if same_process:
                            events.emit({'status': 'game_close_requested', 'reason': stop_reason,
                                         'game_pid': owned['pid'], 'game_created': owned['created']})
                            control_state = root / '.local/test-state/game-control.json'
                            control_owner = json.loads(control_state.read_text(encoding='utf-8')) if control_state.is_file() else {}
                            if (control_owner.get('pid'), control_owner.get('created')) == (owned['pid'], owned['created']):
                                controller = load_backend('game_control.py')
                                try:
                                    closed = controller.main(['quit'])
                                except (OSError, RuntimeError, TimeoutError) as error:
                                    session_failures.append('game_close:' + str(error))
                                    events.emit({'status': 'close_unconfirmed', 'reason': stop_reason,
                                                 'game_pid': owned['pid'], 'detail': str(error)})
                                else:
                                    exit_code = wintypes.DWORD()
                                    probe.checked(kernel.GetExitCodeProcess(owned_handle, ctypes.byref(exit_code)))
                                    events.emit({'status': 'game_closed', 'reason': stop_reason,
                                                 'game_pid': owned['pid'], 'exit_code': exit_code.value,
                                                 'result': closed, 'transport': 'native_exit'})
                            else:
                                result = subprocess.run(['powershell', '-NoProfile', '-File', str(DEVKIT / 'scripts/game-ui.ps1'),
                                    '-Action', 'close', '-GameProcessId', str(owned['pid'])], capture_output=True,
                                    text=True, encoding='utf-8', timeout=45)
                                if result.returncode:
                                    session_failures.append('game_close_command_failed')
                                    events.emit({'status': 'close_failed', 'diagnostic': result.stdout + result.stderr})
                                else:
                                    exit_code = wintypes.DWORD()
                                    exited = kernel.WaitForSingleObject(owned_handle, 5000) == 0
                                    if exited:
                                        probe.checked(kernel.GetExitCodeProcess(owned_handle, ctypes.byref(exit_code)))
                                        if exit_code.value != 0:
                                            session_failures.append('game_exit:' + str(exit_code.value))
                                    else:
                                        session_failures.append('game_close_unconfirmed')
                                    events.emit({'status': 'game_closed' if exited else 'close_unconfirmed',
                                                 'reason': stop_reason, 'game_pid': owned['pid'],
                                                 'exit_code': exit_code.value if exited else None,
                                                 'result': json.loads(result.stdout)})
            except Exception as cleanup_error:
                session_failures.append('game_cleanup:' + str(cleanup_error))
                events.emit({'status': 'close_unconfirmed', 'reason': str(cleanup_error)})
            finally:
                if monitor:
                    try:
                        monitoring = monitor.close()
                        captured = monitoring['capture_status'] == 'dump_captured'
                        if captured:
                            session_failures.append('game_crash_dump_captured')
                        events.emit({'status': 'game_crash_captured' if captured else 'crash_monitor_stopped',
                                     'message': text('game_crash_captured') if captured else text('crash_monitor_stopped'), **monitoring})
                    except Exception as error:
                        session_failures.append('crash_monitor:' + str(error))
                        events.emit({'status': 'crash_monitor_unconfirmed', 'message': text('crash_monitor_unconfirmed'), 'reason': str(error)})
                if owned_handle:
                    if kernel.WaitForSingleObject(owned_handle, 0) != 0:
                        session_failures.append('game_cleanup_unconfirmed')
                    else:
                        final_exit_code = wintypes.DWORD()
                        exit_code_read = kernel.GetExitCodeProcess(owned_handle, ctypes.byref(final_exit_code))
                        if not exit_code_read:
                            session_failures.append('game_exit_code_unconfirmed')
                        elif final_exit_code.value != 0 and not (stop_reason == 'keyboard_interrupt' and not game_started):
                            failure = 'game_exit:' + str(final_exit_code.value)
                            if failure not in session_failures:
                                session_failures.append(failure)
                        if exit_code_read:
                            final_game_exit = final_exit_code.value
                    kernel.CloseHandle(owned_handle)
                if staged_graphics is not None:
                    try:
                        if owned and final_game_exit is None:
                            raise RuntimeError('Owned game exit unconfirmed; graphics files retained')
                        with workshop_module.exclusive(root / '.local'):
                            restore_owned_graphics(staged_graphics)
                        events.emit({'status': 'graphics_restored'})
                    except Exception as graphics_error:
                        session_failures.append('graphics_restore:' + str(graphics_error))
                        events.emit({'status': 'graphics_restore_unconfirmed', 'reason': str(graphics_error)})
                if stop_reason == 'keyboard_interrupt' and not game_started and not session_failures:
                    events.emit({'status': 'game_launch_cancelled', 'game_pid': owned['pid'] if owned else None})
                if session_failures:
                    if owned:
                        try:
                            windows_events = read_owned_windows_events(owned, root / '.local/test-game/bin/H5_Game.exe')
                            windows_path = root / '.local/xalkit/crashes' / str(owned['created']) / 'windows-events.json'
                            windows_path.parent.mkdir(parents=True, exist_ok=True)
                            windows_path.write_text(json.dumps({'owner': owned, 'events': windows_events}, indent=2), encoding='utf-8')
                            events.emit({'status': 'windows_events_saved', 'diagnostic_log': str(windows_path),
                                         'matched_events': len(windows_events)})
                        except Exception as diagnostic_error:
                            events.emit({'status': 'windows_events_unavailable', 'reason': str(diagnostic_error)})
                    events.emit({'status': 'session_failed', 'failures': session_failures,
                                 'message': text('session_failed', diagnostic=events.directory),
                                 'error': sdk_error('SDK_SESSION_FAILED', diagnostic=str(events.directory))})
                try:
                    report_path = root / '.local/xalkit/logs/session-latest.json'
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    report = {'schema_version': 1, 'session_id': str(events.session), 'project': name,
                              'kind': 'resources' if resources else 'native',
                              'scope': 'owned development lifecycle; no scene-rendering acceptance',
                              'game_pid': owned['pid'] if owned else None,
                              'game_created': str(owned['created']) if owned else None,
                              'game_started': game_started, 'game_exit_code': final_game_exit,
                              'image_placement_verified': owned.get('image_placement_verified', False) if owned else False,
                              'monitor_exit_code': monitoring.get('monitor_exit_code') if monitoring else None,
                              'stop_reason': stop_reason, 'failures': session_failures,
                              'passed': not session_failures and stop_reason != 'session_error',
                              'duration_seconds': time.perf_counter() - started}
                    if console_check:
                        report['console_validation'] = console_validation
                    temporary = report_path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
                    temporary.replace(report_path)
                    events.emit({'status': 'session_report_saved', 'diagnostic_log': str(report_path)})
                except Exception as report_error:
                    session_failures.append('session_report:' + str(report_error))
                    events.emit({'status': 'session_report_unavailable', 'reason': str(report_error),
                                 'error': sdk_error('SDK_SESSION_FAILED', diagnostic=str(events.directory))})
                finally:
                    events.close()
    if session_failures:
        raise RuntimeError(text('session_failed', diagnostic=events.directory))
    if console_check:
        return report


start_native = start_session
