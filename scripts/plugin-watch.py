"""Developer-only native plugin watch/build/apply; no player Python dependency."""
import argparse
import hashlib
import json
import os
import re
import queue
import shutil
import sys
from pathlib import Path
import subprocess
import time
import threading
import tempfile
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from workspace import DEVKIT, workspace_root
from plugin_core import CoreBuild, CoreClient
from sdk_logging import EventLog


def supervise_plugins(options):
    """One source checkout; each plugin gets a distinct runtime image and client."""
    directory = options.plugins.resolve()
    output = options.output.resolve()
    if output.is_relative_to(directory):
        raise ValueError('Supervisor output must be outside the plugins directory')
    directory.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    compiler_started = time.perf_counter()
    compiler, environment = compiler_environment(options.toolchain)
    compiler_seconds = time.perf_counter() - compiler_started
    core_builder = CoreBuild(options.core_source, output / 'core', environment) if options.core_source else None
    active_bridge = options.bridge
    active_controller = options.client
    core_transaction = None
    events = EventLog(output / 'logs', console=options.log_format == 'console', session=options.log_session)
    children = {}
    owner_snapshot = None
    if options.owned_game:
        owner = options.session_state or workspace_root() / '.local/test-state/native-probe.json'
        owner_snapshot = output / ('owner-' + uuid.uuid4().hex + '.json')
        owner_snapshot.write_bytes(owner.read_bytes())
    incoming = queue.Queue()
    outgoing = queue.Queue()
    stopping = False

    def emit(item):
        events.emit(item)

    def read_commands():
        for line in sys.stdin:
            incoming.put(line)
        incoming.put('{"command":"stop"}')

    def read_child(stream, plugin, instance, diagnostic=False):
        for line in stream:
            if diagnostic:
                log = output / (plugin + '-' + instance) / 'diagnostic.log'
                with log.open('a', encoding='utf-8') as journal:
                    journal.write(line)
                event = {'status': 'diagnostic', 'message': line.rstrip()}
            else:
                try:
                    event = json.loads(line)
                except ValueError:
                    event = {'status': 'diagnostic', 'message': line.rstrip()}
            # Attribute a reply to the stream that produced it, not its payload.
            outgoing.put({**event, 'plugin': plugin, 'instance': instance})

    def stop_child(child):
        if child['process'].poll() is None and not child['stopping']:
            try:
                child['process'].stdin.write('{"command":"stop"}\n')
                child['process'].stdin.flush()
            except OSError:
                pass
            child['stopping'] = True
            child['stop_requested_at'] = time.monotonic()

    def send_core_request(transaction, name, bridge, controller):
        request_id = 'core-' + uuid.uuid4().hex
        transaction['waiting'] = (name, request_id)
        child = children[name]
        child['process'].stdin.write(json.dumps({'command': 'core-reload', 'bridge': str(bridge),
                                               'controller': str(controller), 'id': request_id}) + '\n')
        child['process'].stdin.flush()

    if options.control_stdin:
        threading.Thread(target=read_commands, daemon=True).start()
    emit({'status': 'supervising', 'directory': str(directory),
          'compiler_setup_seconds': compiler_seconds})
    try:
        while not stopping:
            if core_transaction:
                for name, instance in core_transaction['participants'].items():
                    child = children.get(name)
                    if child is None or child['instance'] != instance or child['stopping'] or \
                            child['process'].poll() is not None:
                        raise RuntimeError('Core update lost a participant; game cleanup is unconfirmed. Close the owned game before restarting.')
            if core_builder and core_transaction is None and all(child['ready'] for child in children.values()):
                update_started = time.perf_counter()
                record = core_builder.update()
                if record:
                    record.setdefault('duration_seconds', time.perf_counter() - update_started)
                    record.setdefault('timing_scope', 'source_scan_to_build_result')
                    emit(record)
                    if record['status'] == 'core_built':
                        targets = [name for name, child in children.items()
                                   if child['process'].poll() is None and not child['stopping']]
                        if targets:
                            core_transaction = {'record': record, 'started_at': update_started,
                                                'remaining': targets, 'applied': [],
                                                'waiting': None, 'rollback': False,
                                                'participants': {name: children[name]['instance'] for name in targets},
                                                'old_bridge': active_bridge, 'old_controller': active_controller}
                        else:
                            active_bridge = Path(record['bridge'])
                            active_controller = Path(record['controller'])
                            emit({'status': 'core_update_completed', 'plugins': [],
                                  'duration_seconds': time.perf_counter() - update_started,
                                  'timing_scope': 'source_scan_to_verified_plugin_observations'})
            if core_transaction and core_transaction['waiting'] is None:
                transaction = core_transaction
                if transaction['remaining']:
                    name = transaction['remaining'].pop(0)
                    bridge = transaction['old_bridge'] if transaction['rollback'] else transaction['record']['bridge']
                    controller = transaction['old_controller'] if transaction['rollback'] else transaction['record']['controller']
                    send_core_request(transaction, name, bridge, controller)
                else:
                    if transaction['rollback']:
                        emit({'status': 'core_update_rejected', 'rolled_back_plugins': transaction['applied'],
                              'duration_seconds': time.perf_counter() - transaction['started_at'],
                              'timing_scope': 'source_scan_to_verified_rollback'})
                    else:
                        active_bridge = Path(transaction['record']['bridge'])
                        active_controller = Path(transaction['record']['controller'])
                        emit({'status': 'core_update_completed', 'plugins': transaction['applied'],
                              'duration_seconds': time.perf_counter() - transaction['started_at'],
                              'timing_scope': 'source_scan_to_verified_plugin_observations'})
                    core_transaction = None
            discovered = {path.name: path for path in directory.iterdir()
                          if path.is_dir() and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', path.name)
                          and (not options.managed_projects or (path / 'xalkit.json').is_file())
                          and any(file.suffix.lower() == '.cpp' for file in path.rglob('*') if file.is_file())}
            for name, child in list(children.items()):
                # Removal waits until the whole core handoff or rollback ends.
                if name not in discovered and core_transaction is None:
                    stop_child(child)
                returncode = child['process'].poll()
                if returncode is not None and not child['reported_exit']:
                    emit({'status': 'plugin_stopped' if returncode == 0 else 'plugin_failed',
                          'plugin': name, 'instance': child['instance'], 'exit_code': returncode})
                    child['reported_exit'] = True
                    if core_transaction and core_transaction['waiting'] and core_transaction['waiting'][0] == name:
                        raise RuntimeError('Core update lost a worker; game cleanup is unconfirmed. Close the owned game before restarting.')
                if returncode is not None and name not in discovered and core_transaction is None:
                    del children[name]
                elif child['stopping'] and returncode is None and time.monotonic() - child['stop_requested_at'] > 50:
                    # Preserve remote code on uncertain cleanup; never silently
                    # claim unload or restart a possibly still-owned hook.
                    child['process'].terminate()
                    emit({'status': 'plugin_cleanup_unconfirmed', 'plugin': name})
            for name, source in sorted(discovered.items()):
                if core_transaction:
                    break
                if name in children:
                    continue
                used_slots = {child['slot'] for child in children.values() if child['process'].poll() is None}
                slot = next((number for number in range(64) if number not in used_slots), None)
                if slot is None:
                    continue
                instance = uuid.uuid4().hex
                instance_output = output / (name + '-' + instance)
                instance_output.mkdir()
                bridge = instance_output / ('bridge-' + name + '-' + instance + '.dll')
                shutil.copyfile(active_bridge.resolve(strict=True), bridge)
                arguments = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()),
                             '--source', str(source), '--output', str(instance_output / 'build'),
                             '--toolchain', str(options.toolchain), '--client', str(active_controller),
                             '--bridge', str(bridge), '--control-stdin', '--slot', str(slot),
                             '--prepared-compiler', str(compiler), '--log-session', events.session,
                             '--plugin-id', name, '--instance', instance]
                if options.owned_game:
                    arguments += ['--owned-game', '--session-state', str(owner_snapshot)]
                if options.main_thread:
                    arguments.append('--main-thread')
                if options.console_build and name == options.console_project:
                    arguments += ['--console-build', str(options.console_build)]
                process = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                           env=environment)
                children[name] = {'process': process, 'instance': instance, 'slot': slot,
                                  'stopping': False, 'reported_exit': False, 'ready': False}
                for stream, diagnostic in ((process.stdout, False), (process.stderr, True)):
                    threading.Thread(target=read_child, args=(stream, name, instance, diagnostic), daemon=True).start()
                emit({'status': 'plugin_discovered', 'plugin': name, 'instance': instance, 'slot': slot})
            while not outgoing.empty():
                event = outgoing.get_nowait()
                child = children.get(event['plugin'])
                if child is None or child['instance'] != event['instance']:
                    emit(event)
                    continue
                if event['status'] == 'applied':
                    children[event['plugin']]['ready'] = True
                if core_transaction and core_transaction['waiting'] == (event['plugin'], event.get('id')):
                    transaction = core_transaction
                    transaction['waiting'] = None
                    if event['status'] == 'core_applied':
                        if not transaction['rollback']:
                            transaction['applied'].append(event['plugin'])
                    elif event['status'] in ('core_rejected', 'command_rejected') and not transaction['rollback']:
                        transaction['rollback'] = True
                        transaction['remaining'] = list(reversed(transaction['applied']))
                    else:
                        raise RuntimeError('Core rollback unconfirmed. Close the owned game before restarting.')
                emit(event)
            for _ in range(32):
                try:
                    line = incoming.get_nowait()
                except queue.Empty:
                    break
                request_id = None
                try:
                    request = json.loads(line)
                    request_id = request.get('id')
                    plugin = request.pop('plugin', None)
                    if request.get('command') == 'stop' and plugin is None:
                        stopping = True
                        break
                    if core_transaction:
                        raise ValueError('SDK core update is in progress; wait for completion before sending plugin commands')
                    if plugin not in children or children[plugin]['process'].poll() is not None:
                        raise ValueError('plugin_not_running')
                    child = children[plugin]
                    if request.get('command') == 'stop':
                        stop_child(child)
                    elif not child['stopping']:
                        child['process'].stdin.write(json.dumps(request) + '\n')
                        child['process'].stdin.flush()
                    else:
                        raise ValueError('plugin_stopping')
                except (ValueError, AttributeError, TypeError, OSError) as error:
                    emit({'status': 'command_rejected', 'id': request_id, 'reason': str(error)})
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        emit({'status': 'runtime_failed', 'reason': str(error), 'exc_info': True})
        raise
    finally:
        for child in children.values():
            stop_child(child)
        for name, child in children.items():
            try:
                result = child['process'].wait(timeout=50)
                emit({'status': 'plugin_stopped' if result == 0 else 'plugin_failed',
                      'plugin': name, 'instance': child['instance'], 'exit_code': result})
            except subprocess.TimeoutExpired:
                child['process'].terminate()
                child['process'].wait(timeout=5)
                emit({'status': 'plugin_cleanup_unconfirmed', 'plugin': name})
        events.close()


def compiler_environment(toolchain, prepared_compiler=None):
    if prepared_compiler is not None:
        compiler = prepared_compiler.resolve(strict=True)
        environment = os.environ.copy()
        if compiler.name.lower() != 'cl.exe' or environment.get('VSCMD_ARG_TGT_ARCH') != 'x86':
            raise RuntimeError('Inherited MSVC x86 environment is invalid; restart the supervisor')
        return compiler, environment
    toolchain = toolchain.resolve(strict=True)
    if any(character in str(toolchain) for character in ('"', '%', '\n', '\r')):
        raise ValueError('Unsupported toolchain path')
    command = f'cmd.exe /d /s /c ""{toolchain}" x86 >nul && set"'
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
    environment = os.environ.copy()
    for line in result.stdout.splitlines():
        if '=' in line and not line.startswith('='):
            key, value = line.split('=', 1)
            environment[key.upper()] = value
    compiler = next((Path(directory) / 'cl.exe' for directory in environment['PATH'].split(';')
                     if (Path(directory) / 'cl.exe').is_file()), None)
    if compiler is None:
        raise RuntimeError('MSVC x86 compiler not found')
    return compiler, environment


class PluginWatch:
    def __init__(self, source, output, compiler, environment, client, main_thread=False):
        self.source = source.resolve(strict=True)
        self.output = output.resolve()
        if self.output.is_relative_to(self.source):
            raise ValueError('Build output must be outside source directory')
        self.output.mkdir(parents=True, exist_ok=True)
        self.intermediates = self.output
        if len(str(self.output)) > 128:
            cache_key = hashlib.sha256((str(self.source) + '\0' + str(self.output)).encode('utf-8')).hexdigest()[:24]
            self.intermediates = Path(tempfile.gettempdir()).resolve() / 'xkit-plugin' / cache_key
            self.intermediates.mkdir(parents=True, exist_ok=True)
        self.compiler = compiler
        self.environment = environment
        self.client = client
        self.invoke_command = 'main' if main_thread else 'invoke'
        self.last_attempt = None
        self.ready = None

    def snapshot(self):
        paths = sorted(path for path in self.source.rglob('*')
                       if path.is_file() and path.suffix.lower() in ('.cpp', '.h', '.hpp', '.inl', '.def'))
        snapshot = {str(path.relative_to(self.source)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in paths}
        for path in (DEVKIT / 'native').glob('*.hpp'):
            name = '@sdk/' + path.name
            if name in snapshot:
                raise ValueError('Source path conflicts with reserved SDK dependency namespace')
            snapshot[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        api_include = DEVKIT / 'game-api/include'
        for path in api_include.rglob('*.hpp'):
            name = '@game-api/' + path.relative_to(api_include).as_posix()
            if name in snapshot:
                raise ValueError('Source path conflicts with reserved game-api dependency namespace')
            snapshot[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return snapshot

    def request(self, command):
        return self.client.request(command)

    def build(self, sources, snapshot, generation, payload, release):
        """Cache translation units; conservatively invalidate all on header edits."""
        cache = self.intermediates / 'objects'
        cache.mkdir(exist_ok=True)
        headers = {name: digest for name, digest in snapshot.items() if Path(name).suffix.lower() not in ('.cpp', '.def')}
        options = ['/nologo', '/O2', '/MT', '/EHsc', '/std:c++20', '/I' + str(DEVKIT / 'native'),
                   '/I' + str(DEVKIT / 'game-api/include')]
        toolchain = {'compiler': str(self.compiler),
                     'version': self.environment.get('VCTOOLSVERSION'),
                     'sdk': self.environment.get('WINDOWSSDKVERSION')}
        objects = []
        compiled = 0
        units = [(source, []) for source in sources]
        if release:
            units.append((DEVKIT / 'native/plugin_bridge.cpp', ['/DHEROES5_PLUGIN_RELEASE']))
        for source, extra in units:
            identity = {'source': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'source_path': str(source), 'headers': headers,
                        'options': options + extra, 'toolchain': toolchain}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            target = cache / (key + '.obj')
            if not target.is_file():
                temporary = generation / 'unit.obj'
                result = subprocess.run([str(self.compiler), *options, *extra, '/c',
                                         '/Fo' + temporary.name, str(source)],
                                        cwd=generation, env=self.environment,
                                        capture_output=True, text=True, timeout=30)
                if result.returncode:
                    return result, compiled
                os.replace(temporary, target)
                compiled += 1
            objects.append(str(target))
        definitions = [self.source / name for name in snapshot if Path(name).suffix.lower() == '.def']
        if len(definitions) > 1:
            raise ValueError('Use one module-definition (.def) file per plugin DLL')
        definition_options = ['/DEF:' + str(definitions[0])] if definitions else []
        result = subprocess.run([str(self.compiler), '/nologo', '/LD', '/Fe' + payload.name,
                                 *objects, '/link', *definition_options, 'user32.lib', 'bcrypt.lib'], cwd=generation,
                                env=self.environment, capture_output=True, text=True, timeout=30)
        return result, compiled

    def update(self, snapshot, observed_at, release_name=None, loader=None, core_source=None):
        started = time.perf_counter()
        self.last_attempt = snapshot
        sources = [self.source / name for name in snapshot if Path(name).suffix.lower() == '.cpp']
        if not sources:
            return {'status': 'build_failed', 'reason': 'no_cpp_sources',
                    'duration_seconds': time.perf_counter() - started,
                    'elapsed': time.perf_counter() - observed_at}
        generation = self.intermediates / ('generation-' + uuid.uuid4().hex)
        generation.mkdir()
        payload = generation / 'plugin.dll'
        if release_name is not None:
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', release_name):
                raise ValueError('Release name must use lowercase letters, digits and single hyphens')
        core_hashes = None
        if release_name is not None:
            builder = CoreBuild(core_source or DEVKIT / 'native', self.output / 'core-release', self.environment)
            definitions = [self.source / name for name in snapshot if Path(name).suffix.lower() == '.def']
            if len(definitions) > 1:
                raise ValueError('Use one module-definition (.def) file per plugin DLL')
            result, core_hashes = builder.release(sources + definitions, generation, payload, DEVKIT / 'native')
            compiled = None
        else:
            result, compiled = self.build(sources, snapshot, generation, payload, False)
        build_seconds = time.perf_counter() - started
        if result.returncode:
            return {'status': 'build_failed', 'exit_code': result.returncode,
                    'build_seconds': build_seconds, 'diagnostic': result.stdout + result.stderr}
        if self.snapshot() != snapshot:
            return {'status': 'superseded', 'build_seconds': build_seconds}
        if release_name is not None:
            loader = loader.resolve(strict=True)
            graphics = loader.with_name('d3d9.dll').resolve(strict=True)
            archive_path = self.output / (release_name + '.zip')
            temporary = archive_path.with_suffix('.zip.tmp')
            manifest = {'abi': 3, 'source_hashes': snapshot,
                        'core_source_hashes': core_hashes,
                        'release_template_sha256': hashlib.sha256((DEVKIT / 'native/player_plugin.cmake').read_bytes()).hexdigest(),
                        'bridge_source_sha256': hashlib.sha256((DEVKIT / 'native/plugin_bridge.cpp').read_bytes()).hexdigest(),
                        'plugin_sha256': hashlib.sha256(payload.read_bytes()).hexdigest(),
                        'loader_sha256': hashlib.sha256(loader.read_bytes()).hexdigest(),
                        'graphics_facade_sha256': hashlib.sha256(graphics.read_bytes()).hexdigest(),
                        'graphics_original_sha256': '5eb152357f99d53397b764384d5cf9a0f6aece733ced30a34186ac57fb15be25'}
            with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
                archive.write(payload, 'bin/Heroes5Mods/Plugins/' + release_name + '.dll')
                archive.write(loader, 'bin/dinput8.dll')
                archive.write(graphics, 'bin/d3d9.dll')
                archive.writestr('release.json', json.dumps(manifest, indent=2))
                archive.writestr('README.txt', '\n'.join([
                    release_name + ' — Heroes V Universe',
                    '',
                    'Установка',
                    '1. Закрой игру.',
                    '2. Распакуй архив в отдельную папку.',
                    '3. При первой установке xkit переименуй исходный bin/d3d9.dll игры в d3d9.universe.dll. Если d3d9.universe.dll уже есть, сохрани его без изменений.',
                    '4. Скопируй папку bin из архива в папку игры: общие DLL должны оказаться рядом с H5_Game.exe.',
                    '5. Запусти игру через Heroes/Lobby обычным способом. SDK и Python игроку не нужны.',
                    '',
                    'Сохрани резервную копию существующего dinput8.dll перед заменой. Исходный d3d9.universe.dll не заменяй и не удаляй.',
                    'Совместимость с другими модами проверяй отдельно. Для нескольких модов xkit используй одну версию общих DLL.',
                    'Удаление: закрой игру и удали bin/Heroes5Mods/Plugins/' + release_name + '.dll.',
                    'Общие dinput8.dll и d3d9.dll могут использовать другие моды; не удаляй их вместе с одним плагином.',
                    'После удаления всех модов xkit можно удалить его d3d9.dll и вернуть имя d3d9.dll исходному d3d9.universe.dll; восстанови резервную копию dinput8.dll.',
                    '',
                    'Installation',
                    '1. Close the game.',
                    '2. Extract the archive into a separate folder.',
                    '3. On first xkit installation, rename the game bin/d3d9.dll to d3d9.universe.dll. Keep an existing d3d9.universe.dll unchanged.',
                    '4. Copy the archive bin folder into the game folder; shared DLLs belong beside H5_Game.exe.',
                    '5. Start Heroes/Lobby normally. Players need neither the SDK nor Python.',
                    'Back up existing dinput8.dll before replacement. Never overwrite or remove the retained original d3d9.universe.dll.',
                    'Use the same shared DLL version for multiple xkit plugins.',
                    'Uninstall: close the game and remove bin/Heroes5Mods/Plugins/' + release_name + '.dll.',
                    'Keep shared DLLs used by other mods.',
                    'After removing every xkit mod, remove its d3d9.dll and rename the retained original back to d3d9.dll; restore your dinput8.dll backup.',
                    '',
                    'Built with xkit / Xaaalera Toolkit SDK: https://github.com/Xaaalera/heroes5-mod-devkit',
                    'Game API: https://github.com/Xaaalera/heroes5-game-api',
                    'Heroes V Universe: https://h5lobby.com/',
                    'Author / Автор xkit: https://github.com/Xaaalera',
                    'Email: dampirsimpl@gmail.com | Telegram: https://t.me/Victima',
                    '',
                ]))
            os.replace(temporary, archive_path)
            return {'status': 'released', 'archive': str(archive_path), 'manifest': manifest,
                    'build_seconds': build_seconds, 'compiled_units': compiled}
        applied = self.request('reload ' + str(payload))
        if applied['status'] != 0:
            return {'status': 'reload_rejected', 'bridge_status': applied['status'],
                    'build_seconds': build_seconds}
        observed = self.request(self.invoke_command + ' 0 0')
        if observed['status'] != 0:
            rollback = None
            try:
                if self.ready is not None:
                    rollback = self.request('reload ' + str(self.ready))
                    if rollback['status'] == 0:
                        rollback = self.request(self.invoke_command + ' 0 0')
                    if rollback['status'] != 0:
                        raise RuntimeError('Previous payload could not be restored after observation failure')
                else:
                    rollback = self.request('stop')
                    if rollback['status'] != 0:
                        raise RuntimeError('Rejected first payload teardown is unconfirmed')
            except (OSError, RuntimeError, ValueError) as recovery_error:
                raise RuntimeError('Payload observation failed: ' + json.dumps(observed, sort_keys=True) +
                                   '; recovery unconfirmed: ' + str(recovery_error)) from recovery_error
            return {'status': 'observation_failed', 'observation': observed,
                    'rollback': rollback, 'build_seconds': build_seconds}
        self.ready = payload
        record = {'status': 'applied', 'payload': str(payload), 'source_hashes': snapshot,
                  'payload_sha256': hashlib.sha256(payload.read_bytes()).hexdigest(),
                  'build_seconds': build_seconds,
                  'compiled_units': compiled,
                  'observed_to_call_seconds': time.perf_counter() - observed_at,
                  'observation': observed}
        (generation / 'build.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--source', type=Path)
    sources.add_argument('--plugins', type=Path, help='Discover and manage child plugin source directories')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--client', type=Path)
    parser.add_argument('--bridge', type=Path)
    parser.add_argument('--release', metavar='NAME', help='Build a player ZIP from the same sources and exit')
    parser.add_argument('--loader', type=Path, help='SDK dinput8.dll required for a release archive')
    parser.add_argument('--toolchain', required=True, type=Path)
    parser.add_argument('--owned-game', action='store_true')
    parser.add_argument('--main-thread', action='store_true',
                        help='Observe payload command0 through the owned window-thread hook')
    parser.add_argument('--control-stdin', action='store_true',
                        help='Accept JSONL invoke/main/event/stop commands on stdin while watching')
    parser.add_argument('--max-updates', type=int, default=0,
                        help='Stop after this many attempted updates; 0 watches until interrupted')
    parser.add_argument('--slot', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--session-state', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--prepared-compiler', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--core-source', type=Path,
                        help='Watch and rebuild the SDK native CMake source directory without restarting the game')
    parser.add_argument('--console-build', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--console-project', help=argparse.SUPPRESS)
    parser.add_argument('--log-format', choices=('json', 'console'), default='json',
                        help='console: concise readable progress; json: structured events for tools (default)')
    parser.add_argument('--log-session', help=argparse.SUPPRESS)
    parser.add_argument('--plugin-id', help=argparse.SUPPRESS)
    parser.add_argument('--instance', help=argparse.SUPPRESS)
    parser.add_argument('--managed-projects', action='store_true', help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.max_updates < 0:
        parser.error('--max-updates must be nonnegative')
    if not 0 <= options.slot < 64:
        parser.error('--slot must be between 0 and 63')
    if options.source and options.output.resolve().is_relative_to(options.source.resolve()):
        parser.error('Build output must be outside source directory')
    if options.plugins:
        if options.release or not options.client or not options.bridge:
            parser.error('--plugins requires --client and --bridge, and does not take --release')
        supervise_plugins(options)
        return
    compiler_started = time.perf_counter()
    compiler, environment = compiler_environment(options.toolchain, options.prepared_compiler)
    compiler_seconds = time.perf_counter() - compiler_started
    events = EventLog(options.output / 'logs', console=options.log_format == 'console',
                      session=options.log_session, plugin=options.plugin_id or options.source.name,
                      instance=options.instance)
    if options.release:
        if not options.loader:
            parser.error('--release requires --loader')
        watcher = PluginWatch(options.source, options.output, compiler, environment, None)
        record = watcher.update(watcher.snapshot(), time.perf_counter(), options.release, options.loader,
                                options.core_source)
        events.emit(record)
        events.close()
        if record['status'] != 'released':
            raise SystemExit(1)
        return
    events.emit({'status': 'compiler_ready',
                      'reused': options.prepared_compiler is not None,
                      'compiler_setup_seconds': compiler_seconds})
    if not options.client or not options.bridge:
        parser.error('Watch mode requires --client and --bridge')
    arguments = [str(options.client.resolve(strict=True)), '--local', str(options.bridge.resolve(strict=True))]
    if options.owned_game:
        root = workspace_root()
        owner = options.session_state or root / '.local/test-state/native-probe.json'
        state = json.loads(owner.read_text(encoding='utf-8'))
        executable = root / '.local/test-game/bin/H5_Game.exe'
        arguments = [str(options.client.resolve(strict=True)), '--owned', str(state['pid']),
                     str(state['created']), str(executable.resolve(strict=True)),
                     str(options.bridge.resolve(strict=True))]
    client = CoreClient(arguments, options.output / 'controllers')
    try:
        core_builder = CoreBuild(options.core_source, options.output / 'core', environment) if options.core_source else None
        console_builder = CoreBuild(DEVKIT / 'native', options.console_build, environment, console=True) if options.console_build else None
        watcher = PluginWatch(options.source, options.output, compiler, environment, client,
                              options.main_thread)
        configured = watcher.request(f'slot {options.slot} 0')
        if configured['status'] != 0:
            raise RuntimeError('Plugin UI slot configuration failed')
        updates = 0
        commands = queue.Queue()
        if options.control_stdin:
            def read_commands():
                import sys
                for line in sys.stdin:
                    commands.put(line)
                commands.put('{"command":"stop"}')
            threading.Thread(target=read_commands, daemon=True).start()
        events.emit({'status': 'watching', 'source': str(watcher.source)})
        stopping = False
        while True:
            for _ in range(16):
                try:
                    line = commands.get_nowait()
                except queue.Empty:
                    break
                request_id = None
                try:
                    request = json.loads(line)
                    request_id = request.get('id')
                    command = request.get('command')
                    if command == 'stop':
                        stopping = True
                        break
                    if command == 'core-reload':
                        if watcher.ready is None:
                            raise ValueError('Wait for the first successful plugin build before replacing the core')
                        bridge_source = Path(request['bridge']).resolve(strict=True)
                        bridge = watcher.output / ('core-' + uuid.uuid4().hex + '.dll')
                        shutil.copyfile(bridge_source, bridge)
                        started = time.perf_counter()
                        result = client.reload_core(bridge, watcher.ready, options.slot, watcher.invoke_command,
                                                    request.get('controller'))
                        events.emit({'id': request_id, 'core_reload_seconds': time.perf_counter() - started,
                                     **result})
                        continue
                    if command in ('core', 'feature'):
                        response = watcher.request(command)
                        events.emit({'status': 'command_result', 'id': request_id, 'response': response})
                        continue
                    if command not in ('invoke', 'main', 'event'):
                        raise ValueError('unsupported_command')
                    function = request.get('function', 0)
                    argument = request.get('argument', 0)
                    if any(type(value) is not int or not 0 <= value <= 0xffffffff for value in (function, argument)):
                        raise ValueError('invalid_command_arguments')
                    response = watcher.request(f'{command} {function} {argument}')
                    events.emit({'status': 'command_result', 'id': request_id, 'response': response})
                except (ValueError, AttributeError, KeyError, OSError) as error:
                    events.emit({'status': 'command_rejected', 'id': request_id, 'reason': str(error)})
            if stopping:
                break
            if core_builder and watcher.ready is not None:
                record = core_builder.update()
                if record:
                    events.emit(record)
                    if record['status'] == 'core_built':
                        bridge = watcher.output / ('core-' + uuid.uuid4().hex + '.dll')
                        shutil.copyfile(record['bridge'], bridge)
                        started = time.perf_counter()
                        result = client.reload_core(bridge, watcher.ready, options.slot, watcher.invoke_command,
                                                    record['controller'])
                        events.emit({'core_reload_seconds': time.perf_counter() - started, **result})
            if console_builder and watcher.ready is not None:
                record = console_builder.update()
                if record:
                    events.emit(record)
                    if record['status'] == 'console_built':
                        started = time.perf_counter()
                        response = client.request('console-replace ' + record['console'])
                        events.emit({'status': 'console_applied' if response['status'] == 0 else 'console_rejected',
                                     'console': record['console'], 'response': response,
                                     'console_reload_seconds': time.perf_counter() - started})
            snapshot = watcher.snapshot()
            if snapshot != watcher.last_attempt:
                observed_at = time.perf_counter()
                time.sleep(0.1)
                if watcher.snapshot() == snapshot:
                    try:
                        record = watcher.update(snapshot, observed_at)
                    except subprocess.TimeoutExpired:
                        record = {'status': 'build_failed', 'reason': 'compiler_timeout'}
                    events.emit(record)
                    updates += 1
                    if options.max_updates and updates >= options.max_updates:
                        break
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        events.emit({'status': 'runtime_failed', 'reason': str(error), 'exc_info': True})
        raise
    finally:
        try:
            client.close()
        finally:
            events.close()


if __name__ == '__main__':
    main()
