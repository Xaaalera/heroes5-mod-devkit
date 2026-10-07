"""Session-bound console transport; Click is the single command registry/parser."""
from concurrent.futures import ThreadPoolExecutor
import hmac
import json
import os
import shlex
import math
from pathlib import Path
import socketserver
import threading
import uuid
import time
import ctypes
from datetime import datetime
import struct
from ctypes import wintypes

import click
from click.shell_completion import CompletionItem, ShellComplete
import structlog
import typer
from xalkit_ui import error, CommandFailure, text
from sdk_console_commands import command_registry, creature_choices, display_result, command_help


def check_ui(workspace, probe, kernel, process, owner, events=None):
    """Run existing UI scenarios only on the retained SDK-owned process."""
    started = time.perf_counter()
    workspace = workspace.resolve()
    output = (workspace / '.local/test-state' / ('sdk-console-' + uuid.uuid4().hex)).resolve()
    report_file = (workspace / '.local/xalkit/console-input-validation.json').resolve()
    capture_file = (workspace / '.local/xalkit/console-input-capture.bmp').resolve()
    if any(not path.is_relative_to(workspace) for path in (output, report_file, capture_file)):
        raise ValueError('Console check paths must remain inside their workspace')
    output.mkdir(parents=True)
    report = {'passed': False, 'owner': {key: owner[key] for key in ('pid', 'created')},
              'scope': 'Public ImGui IO scenarios; not physical keyboard, mouse or camera-wheel acceptance',
              'scenarios': []}
    if events is not None:
        events.emit({'status': 'console_check_started', 'stage': 'console_validation',
                     'diagnostic_log': str(output / 'report.json')})
    try:
        # Caller retains this handle from its own validated launch through cleanup.
        if probe.creation_time(kernel, process) != owner['created'] or kernel.WaitForSingleObject(process, 0) != 258:
            raise RuntimeError('Console check owner is not alive')
        kernel.GetProcessId.argtypes = [wintypes.HANDLE]
        kernel.GetProcessId.restype = wintypes.DWORD
        if kernel.GetProcessId(process) != owner['pid']:
            raise RuntimeError('Console check process handle belongs to another owner')
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.IsWindowVisible.restype = wintypes.BOOL
        user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetWindow.restype = wintypes.HWND
        user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user.GetPropW.restype = wintypes.HANDLE
        user.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
        windows = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect(window, parameter):
            window_owner = wintypes.DWORD()
            user.GetWindowThreadProcessId(window, ctypes.byref(window_owner))
            name = ctypes.create_unicode_buffer(64)
            user.GetClassNameW(window, name, 64)
            if (window_owner.value == owner['pid'] and name.value in ('H5', 'H5UNI') and
                    user.IsWindowVisible(window) and not user.GetWindow(window, 4)):
                windows.append(window)
            return True

        user.EnumWindows(collect, 0)
        if len(windows) != 1:
            raise RuntimeError('Console check needs one owned game window')
        frame = user.GetPropW(windows[0], 'XalKit.Console.Frame.v1')
        if not frame:
            raise RuntimeError('Owned console panel is missing')
        deadline = time.monotonic() + 20
        while True:
            if probe.creation_time(kernel, process) != owner['created'] or kernel.WaitForSingleObject(process, 0) != 258:
                raise RuntimeError('Console check owner exited')
            if report_file.is_file() and report_file.stat().st_size <= 1048576:
                try:
                    with report_file.open('rb') as journal:
                        payload = journal.read(1048577)
                    if len(payload) > 1048576:
                        raise RuntimeError('Console report exceeds size limit')
                    initial = json.loads(payload)
                except (OSError, ValueError):
                    initial = {}
                if initial.get('returned_to_commands') and initial.get('owner') == report['owner']:
                    report['initial'] = initial
                    break
            if time.monotonic() >= deadline:
                raise RuntimeError('Console startup validation timed out')
            time.sleep(0.05)
        for scenario in range(1, 8):
            if probe.creation_time(kernel, process) != owner['created'] or kernel.WaitForSingleObject(process, 0) != 258:
                raise RuntimeError('Console check owner exited before scenario')
            frame_owner = wintypes.DWORD()
            user.GetWindowThreadProcessId(frame, ctypes.byref(frame_owner))
            if frame_owner.value != owner['pid']:
                raise RuntimeError('Console panel owner changed')
            previous_report = report_file.stat().st_mtime_ns
            previous_capture = capture_file.stat().st_mtime_ns if capture_file.exists() else None
            scenario_started = time.perf_counter()
            if not user.SetPropW(frame, 'XalKit.Validation.Scenario', ctypes.c_void_p(scenario)):
                raise OSError('Console scenario request failed')
            deadline = time.monotonic() + 10
            while True:
                if probe.creation_time(kernel, process) != owner['created'] or kernel.WaitForSingleObject(process, 0) != 258:
                    raise RuntimeError('Console check owner exited during scenario')
                try:
                    if report_file.stat().st_mtime_ns == previous_report or report_file.stat().st_size > 1048576:
                        raise ValueError('Awaiting fresh console report')
                    with report_file.open('rb') as journal:
                        payload = journal.read(1048577)
                    if len(payload) > 1048576:
                        raise RuntimeError('Console report exceeds size limit')
                    result = json.loads(payload)
                    if result.get('owner') != report['owner'] or result.get('scenario') != scenario or capture_file.stat().st_mtime_ns == previous_capture:
                        raise ValueError('Awaiting scenario capture')
                    if not capture_file.resolve().is_relative_to(workspace.resolve()) or capture_file.stat().st_size > 67108864:
                        raise RuntimeError('Console capture is outside its bounded workspace')
                    with capture_file.open('rb') as image_file:
                        capture = image_file.read(67108865)
                    if len(capture) > 67108864:
                        raise RuntimeError('Console bitmap exceeds capture limit')
                    if len(capture) < 54 or capture[:2] != b'BM' or struct.unpack_from('<I', capture, 2)[0] != len(capture):
                        raise ValueError('Awaiting complete console bitmap')
                    break
                except (OSError, ValueError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Console scenario timed out: ' + str(scenario))
                    time.sleep(0.05)
            checks = {1: 'Brem' in result.get('candidates', []),
                      2: 'CREATURE_ARCHER' in result.get('candidates', []),
                      3: any('army [OPTIONS]' in line for line in result.get('output', [])),
                      4: not result.get('pending') and isinstance(result.get('result', {}).get('records'), list),
                      5: result.get('editor', '').rstrip() == 'army Brem CREATURE_ARCHER',
                      6: result.get('editor', '').rstrip() == 'trace',
                      7: result.get('editor', '').rstrip() == 'draft'}
            destination = output / ('scenario-' + str(scenario) + '.bmp')
            destination.write_bytes(capture)
            report['scenarios'].append({'scenario': scenario, 'passed': checks[scenario], 'result': result,
                'capture': str(destination), 'duration_seconds': time.perf_counter() - scenario_started})
            if not checks[scenario]:
                raise RuntimeError('Console scenario failed: ' + str(scenario))
            if events is not None:
                events.emit({'status': 'console_scenario_completed', 'stage': 'console_validation',
                             'scenario': scenario, 'duration_seconds': time.perf_counter() - scenario_started,
                             'message': text('check_console_step', scenario=scenario),
                             'diagnostic_log': str(output / 'report.json')})
        report['passed'] = True
        return report
    except BaseException as failure:
        report['failure'] = type(failure).__name__ + ': ' + str(failure)
        raise
    finally:
        report['duration_seconds'] = time.perf_counter() - started
        report['report'] = str(output / 'report.json')
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def execute_registered(arguments, owner, session, source='console'):
    from xalkit import game_app
    group = typer.main.get_command(game_app)
    if '--help' in arguments:
        if arguments[0] in group.commands:
            return {'help_lines': command_help(arguments[0])}
    with structlog.contextvars.bound_contextvars(session_id=session, source=source):
        with group.make_context('game', arguments, obj={'source': source, 'owner': dict(owner)}) as context:
            return group.invoke(context)


class ConsoleBroker:
    def __init__(self, workspace, session):
        self.workspace = Path(workspace)
        self.session = session
        self.owner = None
        self.token = uuid.uuid4().hex
        self.jobs = {}
        self.unacknowledged = set()
        self.seen = {}
        self.cancelled = threading.Event()
        self.journal_offset = 0
        self.journal_identity = None
        self.skipping_line = False
        self.events = []
        self.modules = {}
        self.hero_names = set()
        self.hero_refresh = None
        self.hero_refresh_at = 0
        self.performance_until = 0
        self.creatures = None
        self.ui_checkpoint = None
        self.ui_checkpoint_version = 0
        self.ui_checkpoint_staging = None
        self.ui_checkpoint_cancelled = set()
        self.validation_enabled = os.environ.get('XALKIT_CONSOLE_VALIDATE_INPUT') == '1'
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='xalkit-console-command')
        broker = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                line = self.rfile.readline(65537)
                try:
                    if len(line) > 65536:
                        raise ValueError('Console request exceeds limit')
                    request = json.loads(line)
                    response = broker.dispatch(request)
                except (ValueError, TypeError, KeyError, OSError) as failure:
                    response = {'ok': False, 'error': error('SDK_CONSOLE_INVALID_REQUEST'),
                                'reason': type(failure).__name__}
                self.wfile.write((json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8'))

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False

        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='xalkit-console-server')
        self.thread.start()

    @property
    def port(self):
        return self.server.server_address[1]

    def game_operation_active(self):
        """Use the existing owned command journal to avoid throttling SDK work."""
        with self.lock:
            if self.owner is not None:
                try:
                    self.read_journal()
                except OSError:
                    pass  # Rotation can briefly remove the path; retry next poll.
            return time.monotonic() < self.performance_until or any(not job.done() for job in self.jobs.values())

    def dispatch(self, request):
        if not isinstance(request, dict) or not isinstance(request.get('token'), str) or not hmac.compare_digest(request['token'], self.token):
            return {'ok': False, 'error': error('SDK_CONSOLE_UNAUTHORIZED')}
        if self.owner is None:
            return {'ok': False, 'error': error('SDK_CONSOLE_NOT_READY')}
        kind = request.get('kind')
        if kind == 'ui_state_begin':
            with self.lock:
                checkpoint_id = request.get('id', uuid.uuid4().hex)
                if (not isinstance(checkpoint_id, str) or not 1 <= len(checkpoint_id) <= 96 or
                        any(not (character.isascii() and (character.isalnum() or character in '-_')) for character in checkpoint_id)):
                    raise ValueError('Invalid console checkpoint ID')
                if checkpoint_id in self.ui_checkpoint_cancelled:
                    raise ValueError('Console checkpoint was cancelled')
                if len(self.ui_checkpoint_cancelled) >= 4096:
                    raise ValueError('Console checkpoint cancellation limit reached')
                if self.ui_checkpoint_staging is not None:
                    if self.ui_checkpoint_staging['id'] == checkpoint_id:
                        return {'ok': True, 'id': checkpoint_id}
                    raise ValueError('Console checkpoint already in progress')
                self.ui_checkpoint_staging = {'id': checkpoint_id, 'parts': [], 'size': 0}
            return {'ok': True, 'id': checkpoint_id}
        if kind in ('ui_state_part', 'ui_state_commit', 'ui_state_abort'):
            with self.lock:
                staged = self.ui_checkpoint_staging
                if kind == 'ui_state_abort' and (staged is None or request.get('id') == staged['id']):
                    checkpoint_id = request.get('id')
                    if not isinstance(checkpoint_id, str) or not 1 <= len(checkpoint_id) <= 96:
                        raise ValueError('Invalid console checkpoint abort ID')
                    if checkpoint_id not in self.ui_checkpoint_cancelled and len(self.ui_checkpoint_cancelled) >= 4096:
                        raise ValueError('Console checkpoint cancellation limit reached')
                    self.ui_checkpoint_cancelled.add(checkpoint_id)
                    self.ui_checkpoint_staging = None
                    return {'ok': True}
                if staged is None or request.get('id') != staged['id']:
                    raise ValueError('Console checkpoint transaction changed')
                if kind == 'ui_state_abort':
                    self.ui_checkpoint_staging = None
                elif kind == 'ui_state_part':
                    part = request.get('text')
                    if (not isinstance(part, str) or not part.isascii() or len(part) > 16000 or
                            request.get('index') != len(staged['parts']) or staged['size'] + len(part) > 32 * 1024 * 1024):
                        raise ValueError('Invalid console checkpoint part')
                    staged['parts'].append(part)
                    staged['size'] += len(part)
                else:
                    encoded = ''.join(staged['parts'])
                    snapshot = json.loads(encoded)
                    if (not isinstance(snapshot, dict) or snapshot.get('version') != 1 or
                            snapshot.get('owner') != {key: self.owner[key] for key in ('pid', 'created')}):
                        raise ValueError('Console checkpoint owner or schema changed')
                    for field, limit in (('pending', 96), ('output', 512), ('history', 512), ('acknowledgements', 96)):
                        values = snapshot.get(field, [] if field == 'acknowledgements' else None)
                        if not isinstance(values, list) or len(values) > limit or any(not isinstance(value, str) for value in values):
                            raise ValueError('Invalid console checkpoint list')
                    rectangle = snapshot.get('window_rect')
                    if rectangle is not None and (not isinstance(rectangle, list) or len(rectangle) != 4 or
                            any(type(value) is not int or abs(value) > 100000 for value in rectangle) or
                            not 480 <= rectangle[2] - rectangle[0] <= 16384 or
                            not 300 <= rectangle[3] - rectangle[1] <= 16384):
                        raise ValueError('Invalid console checkpoint rectangle')
                    if 'scroll_positions' in snapshot:
                        positions = snapshot['scroll_positions']
                        following = snapshot.get('follow_scroll')
                        if (not isinstance(positions, list) or len(positions) != 2 or
                                any(not isinstance(position, list) or len(position) != 2 or
                                    any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10000000
                                        for value in position) for position in positions) or
                                not isinstance(following, list) or len(following) != 2 or
                                any(type(value) is not bool for value in following)):
                            raise ValueError('Invalid console checkpoint scroll')
                    if (any(value not in self.seen for value in snapshot['pending'] + snapshot.get('acknowledgements', [])) or
                            not isinstance(snapshot.get('editor'), str) or
                            not isinstance(snapshot.get('filter'), str) or len(snapshot['filter'].encode('utf-8')) >= 256 or
                            type(snapshot.get('minimum')) is not int or snapshot['minimum'] not in range(4) or
                            type(snapshot.get('selected_tab', 0)) is not int or snapshot.get('selected_tab', 0) not in range(3) or
                            type(snapshot.get('visible')) is not bool or
                            type(snapshot.get('event_cursor')) is not int or not 0 <= snapshot['event_cursor'] < 2 ** 64 or
                            not isinstance(snapshot.get('mirrored'), list) or len(snapshot['mirrored']) > 256 or
                            any(not isinstance(event, dict) for event in snapshot['mirrored']) or 'last_result' not in snapshot):
                        raise ValueError('Invalid console checkpoint state')
                    self.ui_checkpoint = encoded
                    self.ui_checkpoint_version += 1
                    self.ui_checkpoint_staging = None
            return {'ok': True}
        if kind == 'ui_state_get':
            offset = request.get('offset', 0)
            with self.lock:
                if (not isinstance(offset, int) or offset < 0 or
                        request.get('version', self.ui_checkpoint_version) != self.ui_checkpoint_version):
                    raise ValueError('Console checkpoint generation changed')
                encoded = self.ui_checkpoint or ''
                if offset > len(encoded):
                    raise ValueError('Console checkpoint offset exceeds size')
                return {'ok': True, 'version': self.ui_checkpoint_version, 'size': len(encoded),
                        'text': encoded[offset:offset + 16000]}
        if kind == 'validation_result':
            if not self.validation_enabled or not isinstance(request.get('result'), dict):
                raise ValueError('Console input validation is not enabled')
            target = self.workspace / '.local/xalkit/console-input-validation.json'
            if not target.resolve().is_relative_to(self.workspace.resolve()):
                raise ValueError('Console validation path escapes its workspace')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({**request['result'], 'owner': {
                key: self.owner.get(key) for key in ('pid', 'created')}}, ensure_ascii=False, indent=2), encoding='utf-8')
            return {'ok': True}
        if kind == 'catalog':
            return {'ok': True, 'version': 1, 'owner': {key: self.owner[key] for key in ('pid', 'created')},
                    'delivery_acknowledgements': True,
                    'validation_capture': str(self.workspace / '.local/xalkit/console-input-capture.bmp') if self.validation_enabled else '',
                    'commands': command_registry()}
        if kind == 'complete':
            arguments = request.get('arguments')
            if 'text' in request:
                text = request['text']
                if not isinstance(text, str) or len(text) > 4096:
                    raise ValueError('Invalid completion text')
                try:
                    arguments = shlex.split(text)
                except ValueError:
                    return {'ok': True, 'candidates': [], 'replacements': []}
                if not text or text[-1].isspace():
                    arguments.append('')
            if not isinstance(arguments, list) or not arguments or len(arguments) > 64 or any(
                    not isinstance(value, str) or len(value) > 4096 for value in arguments):
                raise ValueError('Invalid completion request')
            registry = command_registry()
            prefix = arguments[-1]
            description = ''
            if len(arguments) == 1:
                candidates = [command['name'] for command in registry]
            elif arguments[0] == 'help' and len(arguments) == 2:
                candidates = [command['name'] for command in registry]
            else:
                command = next((value for value in registry if value['name'] == arguments[0]), None)
                candidates = []
                if command:
                    from xalkit import game_app
                    group = typer.main.get_command(game_app)
                    selected = group.commands[arguments[0]]
                    # Click resolves option values, flags and positional order.
                    # Metadata callbacks keep roster/resource lookup bound to this session.
                    for index, parameter in enumerate(selected.params):
                        if isinstance(parameter, click.Argument) and parameter.name in ('hero', 'creature'):
                            selected.params[index] = click.Argument(parameter.opts, type=parameter.type,
                                required=parameter.required, nargs=parameter.nargs,
                                shell_complete=lambda context, current, incomplete, original=parameter:
                                    [CompletionItem(incomplete, parameter=original)])
                    completions = ShellComplete(group, {}, 'xkit game', '_XKIT_COMPLETE').get_completions(
                        arguments[:-1], prefix)
                    target = next((getattr(item, 'parameter', None) for item in completions
                                   if getattr(item, 'parameter', None) is not None), None)
                    candidates = [item.value for item in completions if getattr(item, 'parameter', None) is None]
                    if target is not None:
                        description = getattr(target, 'help', '') or ''
                        if target.name == 'creature':
                            if self.creatures is None:
                                self.creatures = creature_choices(self.workspace)
                            candidates = self.creatures
                        elif target.name == 'hero':
                            with self.lock:
                                self.read_journal()
                                if self.hero_refresh is not None and self.hero_refresh.done():
                                    try:
                                        result = self.hero_refresh.result()
                                        if isinstance(result, dict) and isinstance(result.get('result'), str):
                                            self.hero_names = set(result['result'].split())
                                    except Exception:
                                        pass
                                    self.hero_refresh = None
                                if self.hero_refresh is None and time.monotonic() - self.hero_refresh_at > 2:
                                    self.hero_refresh_at = time.monotonic()
                                    self.hero_refresh = self.executor.submit(execute_registered, ['heroes'],
                                        {**self.owner, '_cancelled': self.cancelled}, self.session, 'completion')
                                candidates = sorted(self.hero_names)
            if len(arguments) == 1:
                candidates += ['help', 'clear']
            matches = [value for value in candidates if value.casefold().startswith(prefix.casefold()) or
                       value.removeprefix('CREATURE_').casefold().startswith(prefix.casefold())][:256]
            response = {'ok': True, 'candidates': [], 'description': description,
                        'pending': self.hero_refresh is not None and not self.hero_refresh.done(),
                        'replacements': []}
            for value in matches:
                response['candidates'].append(value)
                response['replacements'].append(shlex.join(arguments[:-1] + [value]))
                if len(json.dumps(response, ensure_ascii=False).encode('utf-8')) > 60000:
                    response['candidates'].pop()
                    response['replacements'].pop()
                    break
            return response
        if kind == 'events':
            after = request.get('after', 0)
            if not isinstance(after, int) or after < 0:
                raise ValueError('Invalid console event cursor')
            with self.lock:
                self.read_journal()
                modules = sorted(self.modules.values(), key=lambda entry: entry['state'] not in ('active', 'loading'))[:64]
                response = {'ok': True, 'events': [], 'modules': modules}
                for event in (value for value in self.events if value['sequence'] > after):
                    response['events'].append(event)
                    if len(json.dumps(response, ensure_ascii=False).encode('utf-8')) > 60000:
                        response['events'].pop()
                        break
                    if len(response['events']) == 32:
                        break
                return response
        if kind == 'run':
            arguments = request.get('arguments')
            request_id = request.get('id')
            if (not isinstance(arguments, list) or not arguments or len(arguments) > 64 or
                    any(not isinstance(value, str) or len(value) > 4096 or '\0' in value for value in arguments) or
                    not isinstance(request_id, str) or not 1 <= len(request_id) <= 64):
                raise ValueError('Invalid console command')
            with self.lock:
                # A repeated ID observes the same job; commands are never replayed.
                if request_id not in self.jobs:
                    if request_id in self.seen:
                        raise ValueError('Console result expired; command will not be replayed')
                    if len(self.seen) >= 4096:
                        raise ValueError('Console session command limit reached')
                    completed = [key for key, job in self.jobs.items() if job.done() and key not in self.unacknowledged]
                    for key in completed[:-64]:
                        del self.jobs[key]
                    if len(self.jobs) >= 96:
                        evictable = next((key for key in completed if key in self.jobs), None)
                        if evictable is not None:
                            del self.jobs[evictable]
                    if len(self.jobs) >= 96:
                        raise ValueError('Console command queue is full')
                    self.seen[request_id] = list(arguments)
                    owner = {**self.owner, '_cancelled': self.cancelled}
                    self.jobs[request_id] = self.executor.submit(execute_registered, list(arguments), owner, self.session)
                    self.unacknowledged.add(request_id)
                elif self.seen[request_id] != arguments:
                    raise ValueError('Console request ID belongs to another command')
            return {'ok': True, 'id': request_id, 'status': 'accepted'}
        if kind == 'ack':
            request_id = request.get('id')
            with self.lock:
                if not isinstance(request_id, str) or request_id not in self.seen:
                    raise ValueError('Unknown console delivery acknowledgement')
                job = self.jobs.get(request_id)
                if request_id in self.unacknowledged and (job is None or not job.done()):
                    raise ValueError('Console result is not ready to acknowledge')
                self.unacknowledged.discard(request_id)
            return {'ok': True}
        if kind == 'poll':
            with self.lock:
                job = self.jobs.get(request.get('id'))
            if job is None:
                raise ValueError('Unknown console command')
            if not job.done():
                return {'ok': True, 'status': 'pending'}
            try:
                arguments = self.seen.get(request.get('id'), [])
                result = job.result()
                response = {'ok': True, 'status': 'completed', 'result': result,
                            'display_lines': display_result(arguments[0] if arguments else '', result)}
                if len(json.dumps(response, ensure_ascii=False).encode('utf-8')) > 60000:
                    response['result'] = {'display_truncated': True, 'message': error('SDK_CONSOLE_RESULT_LARGE')['message']}
                    response['display_lines'] = [error('SDK_CONSOLE_RESULT_LARGE')['message']]
                return response
            except CommandFailure as failure:
                return {'ok': False, 'status': 'failed', 'error': failure.payload}
            except click.ClickException as failure:
                arguments = self.seen.get(request.get('id'), [])
                command = next((item for item in command_registry() if arguments and item['name'] == arguments[0]), None)
                if command:
                    usage = command['name'] + ''.join(' <' + parameter['name'] + '>' for parameter in command['parameters']
                                                    if parameter['required'])
                    description = command['description']
                else:
                    usage = ', '.join(item['name'] for item in command_registry())
                    description = ''
                return {'ok': False, 'status': 'failed',
                        'error': error('SDK_CONSOLE_COMMAND_USAGE', usage=usage, description=description),
                        'hint': failure.format_message()}
            except (Exception, click.exceptions.Exit, SystemExit) as failure:
                return {'ok': False, 'status': 'failed', 'error': error('SDK_GAME_COMMAND_FAILED',
                        diagnostic=str(self.workspace / '.local/xalkit/logs')),
                        'reason': type(failure).__name__}
        raise ValueError('Unknown console request')

    def read_journal(self):
        path = self.workspace / '.local/xalkit/logs/events.jsonl'
        if not path.is_file():
            return
        identity = path.stat()
        key = (identity.st_dev, identity.st_ino)
        if key != self.journal_identity or identity.st_size < self.journal_offset:
            self.journal_identity = key
            self.journal_offset = 0
            self.skipping_line = False
        with path.open('rb') as journal:
            journal.seek(self.journal_offset)
            for _ in range(128):
                line = journal.readline(65537)
                if not line:
                    break
                if self.skipping_line or len(line) == 65537:
                    self.journal_offset = journal.tell()
                    self.skipping_line = not line.endswith(b'\n')
                    continue
                if not line.endswith(b'\n'):
                    break
                self.journal_offset = journal.tell()
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict) or not isinstance(event.get('status'), str):
                    continue
                lifecycle = event.get('session_id') == self.session and event.get('plugin') and event.get('status') in (
                    'plugin_discovered', 'applied', 'plugin_stopped', 'plugin_failed', 'plugin_cleanup_unconfirmed')
                command = event.get('status', '').startswith('game_request_') and all(
                    event.get('game_' + key) == self.owner[key] for key in ('pid', 'created'))
                if command and event.get('status') == 'game_request_started':
                    try:
                        value = event.get('timestamp')
                        if not isinstance(value, str):
                            raise ValueError('Command timestamp is not a string')
                        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        if parsed.tzinfo is None:
                            raise ValueError('Command timestamp has no timezone')
                        timestamp = parsed.timestamp()
                        now = time.time()
                        if timestamp > now + 5:
                            raise ValueError('Command timestamp is in the future')
                        duration = 120 if event.get('action') in ('map', 'restart', 'menu') else 15
                        remaining = min(duration, max(0, timestamp + duration - now))
                        self.performance_until = max(self.performance_until, time.monotonic() + remaining)
                    except (KeyError, ValueError, TypeError, OverflowError):
                        pass
                result = event.get('result')
                if command and event.get('action') == 'heroes' and isinstance(result, dict) and result.get('status') == 'completed':
                    names = result.get('result')
                    if isinstance(names, str):
                        self.hero_names = set(names.split())
                system = event.get('session_id') == self.session
                if lifecycle:
                    states = {'plugin_discovered': 'loading', 'applied': 'active', 'plugin_stopped': 'stopped',
                              'plugin_failed': 'failed', 'plugin_cleanup_unconfirmed': 'unconfirmed'}
                    self.modules[event['plugin']] = {'name': event['plugin'], 'state': states[event['status']],
                                                    'instance': event.get('instance')}
                if system or command:
                    sequence = self.events[-1]['sequence'] + 1 if self.events else 1
                    summary = event.get('event', '')
                    if event.get('plugin'):
                        summary = event['plugin'] + ': ' + summary
                    if event.get('arguments'):
                        summary += ': ' + ' '.join(event['arguments'])
                    result_lines = display_result(event.get('action', ''), result) if command and isinstance(result, dict) else []
                    if event.get('action') == 'trace' and result_lines:
                        result_lines = result_lines[-1:]
                    self.events.append({'sequence': sequence, 'id': event.get('event_id'),
                        'operation': event.get('operation_id'), 'source': event.get('source', 'SDK'),
                        'level': event.get('level', 'info'), 'message': summary[:2048],
                        'result_summary': '\n'.join(result_lines).encode('utf-8')[:1024].decode('utf-8', errors='ignore')})
                    if len(self.events) > 256:
                        del self.events[:-256]

    def close(self):
        self.cancelled.set()
        self.owner = None
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.executor.shutdown(wait=True, cancel_futures=True)
