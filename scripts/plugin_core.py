"""Native core generations and portable state handoff for the developer SDK."""
import json
import hashlib
import io
from pathlib import Path
import queue
import shutil
import subprocess
import struct
import threading
import tempfile
import time
import uuid
import os
import re
from zipfile import ZipFile, ZIP_DEFLATED


def runtime_source(sdk, name):
    if name.startswith('@game-api/'):
        base, relative = sdk / 'game-api/include', name[len('@game-api/'):]
    elif name.startswith('@locale/'):
        base, relative = sdk / 'locale', name[len('@locale/'):]
    elif name == '@resources':
        base, relative = sdk / 'scripts', 'native-ui-resources.py'
    else:
        base, relative = sdk / 'native', name
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError('SDK runtime source path escapes its root')
    return target


def owned_core_endpoint(root, owner):
    """Resolve sealed resident-core binaries for one immutable SDK game owner."""
    root = Path(root).resolve()
    directory = (root / '.local/xalkit/watch').resolve()
    if not directory.is_relative_to(root):
        raise ValueError('Core endpoint directory escapes the workspace')
    endpoint_path = (directory / 'diagnostic-owner.json').resolve(strict=True)
    if not endpoint_path.is_relative_to(directory):
        raise ValueError('Core endpoint file escapes the workspace')
    with endpoint_path.open('rb') as source:
        content = source.read(65537)
    if len(content) > 65536:
        raise ValueError('Core endpoint exceeds its size limit')
    endpoint = json.loads(content)
    if not isinstance(endpoint, dict) or endpoint.get('version') != 1 or any(endpoint.get(key) != owner[key] for key in ('pid', 'created')):
        raise RuntimeError('Core endpoint does not belong to this SDK session')
    paths = {}
    for key in ('controller', 'bridge'):
        path = Path(endpoint[key]).resolve(strict=True)
        if not path.is_relative_to(directory) or hashlib.sha256(path.read_bytes()).hexdigest() != endpoint[key + '_sha256']:
            raise RuntimeError('Core endpoint binary changed')
        paths[key] = path
    return paths


def dispatch_owned_console(root, owner, command):
    """Borrow the connected core once; never stop its payload or replay a request."""
    if (not isinstance(command, str) or not command or any(value in command for value in ('\0', '\r', '\n')) or
            len(command.encode('utf-16-le')) > 8190):
        raise ValueError('Invalid bounded console command')
    cancelled = owner.get('_cancelled')
    if cancelled is not None and cancelled.is_set():
        raise RuntimeError('SDK command session was disconnected')
    paths = owned_core_endpoint(root, owner)
    if cancelled is not None and cancelled.is_set():
        raise RuntimeError('SDK command session was disconnected')
    result = subprocess.run([str(paths['controller']), '--owned-command', str(owner['pid']), str(owner['created']),
        str((Path(root) / '.local/test-game/bin/H5_Game.exe').resolve()), str(paths['bridge'])],
        input='console ' + command + '\nquit\n', capture_output=True, text=True, encoding='utf-8', timeout=55)
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    if (result.returncode or len(replies) != 2 or replies[0] != {'ready': True} or
            replies[1].get('status') != 0 or replies[1].get('dispatch_returned') is not True):
        raise RuntimeError('Native console dispatch failed or unconfirmed: ' + result.stderr)
    return {'pid': owner['pid'], 'status': 'dispatched', 'dispatch_returned': True,
            'effect_verified': False}


def load_sdk_runtime(sdk, generation=None):
    """Read a shipped immutable runtime; never discover or invoke a compiler."""
    sdk = sdk.resolve()
    root = sdk / 'runtime'
    if not root.resolve().is_relative_to(sdk):
        raise ValueError('SDK runtime root escapes the SDK')
    if generation is None:
        pointer = json.loads((root / 'current.json').read_text(encoding='utf-8'))
        if pointer.get('version') != 1:
            raise ValueError('Invalid SDK runtime pointer')
        generation = pointer.get('generation', '')
    if not isinstance(generation, str) or not re.fullmatch(r'generation-[a-f0-9]{64}', generation):
        raise ValueError('Invalid SDK runtime generation')
    directory = (root / generation).resolve()
    if not directory.is_relative_to(root.resolve()):
        raise ValueError('SDK runtime generation escapes its root')
    manifest = json.loads((directory / 'runtime.json').read_text(encoding='utf-8'))
    files = {'bridge': 'heroes5_plugin_bridge.dll', 'controller': 'plugin_bridge_client.exe',
             'launch_gate': 'sdk_launch_gate.exe', 'console': 'XalKitConsole.dll',
             'licenses': 'XalKitConsole.LICENSES.txt'}
    if manifest.get('version') == 2:
        files['graphics'] = 'd3d9.dll'
    if manifest.get('version') not in (1, 2) or not isinstance(manifest.get('files'), dict) or set(manifest['files']) != set(files.values()):
        raise ValueError('Invalid SDK runtime manifest')
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if generation != 'generation-' + identity:
        raise ValueError('SDK runtime manifest identity mismatch')
    result = {}
    for role, name in files.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or hashlib.sha256(path.read_bytes()).hexdigest() != manifest['files'][name]:
            raise ValueError('SDK runtime file changed: ' + name)
        result[role] = str(path)
    if not isinstance(manifest.get('sources'), dict) or not manifest['sources']:
        raise ValueError('SDK runtime source manifest is empty')
    for name, digest in manifest['sources'].items():
        current = runtime_source(sdk, name).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(current).hexdigest() != digest:
            raise ValueError('SDK runtime is outdated: ' + name)
    result['manifest'] = manifest
    return result


def package_sdk_runtime(sdk, built, archive):
    """Freeze the existing core/console artifacts for compiler-free resource sessions."""
    sdk = sdk.resolve()
    paths = {name: Path(built[role]) for role, name in (
        ('bridge', 'heroes5_plugin_bridge.dll'), ('controller', 'plugin_bridge_client.exe'),
        ('launch_gate', 'sdk_launch_gate.exe'), ('console', 'XalKitConsole.dll'),
        ('graphics', 'd3d9.dll'))}
    paths['XalKitConsole.LICENSES.txt'] = Path(built['console']).with_name('XalKitConsole.LICENSES.txt')
    sources = {}
    for snapshot in (built['source_hashes'], built['console_source_hashes']):
        for name, digest in snapshot.items():
            content = runtime_source(sdk, name).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError('SDK sources changed before packaging: ' + name)
            sources[name] = hashlib.sha256(content.replace(b'\r\n', b'\n')).hexdigest()
    manifest = {'version': 2, 'sources': sources,
                'files': {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}}
    generation = 'generation-' + hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    root = sdk / 'runtime'
    if not root.resolve().is_relative_to(sdk):
        raise ValueError('SDK runtime root escapes the SDK')
    root.mkdir(exist_ok=True)
    directory = (root / generation).resolve()
    if not directory.is_relative_to(root.resolve()):
        raise ValueError('SDK runtime output escapes its root')
    directory.mkdir(exist_ok=True)
    for name, path in paths.items():
        target = directory / name
        if not target.resolve().is_relative_to(directory):
            raise ValueError('SDK runtime file escapes its generation')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != manifest['files'][name]:
                raise ValueError('Immutable SDK runtime changed: ' + name)
        else:
            shutil.copyfile(path, target)
    manifest_path = directory / 'runtime.json'
    manifest_text = json.dumps(manifest, sort_keys=True, indent=2) + '\n'
    if not manifest_path.resolve().is_relative_to(directory):
        raise ValueError('SDK runtime manifest escapes its generation')
    if manifest_path.exists():
        if manifest_path.read_text(encoding='utf-8') != manifest_text:
            raise ValueError('Immutable SDK manifest changed')
    else:
        manifest_path.write_text(manifest_text, encoding='utf-8')
    load_sdk_runtime(sdk, generation)
    pointer = {'version': 1, 'generation': generation}
    temporary = root / ('current-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(pointer) + '\n', encoding='utf-8')
    os.replace(temporary, root / 'current.json')
    archive = archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name(archive.name + '.' + uuid.uuid4().hex + '.tmp')
    with ZipFile(temporary, 'w', ZIP_DEFLATED) as package:
        package.writestr('README.txt', '\n'.join([
            'xkit runtime — Xaaalera Toolkit SDK', '',
            'RU: Это готовые служебные файлы SDK, дополнение к исходникам xkit.',
            'Распакуй папку runtime рядом с README.md девкита. В папку игры этот архив не устанавливается.',
            'Далее используй обычные xkit setup, xkit new NAME --resources и xkit start NAME.',
            'Для H5U C++-компилятор не нужен. Версия исходников SDK должна соответствовать этому комплекту.', '',
            'EN: These ready SDK files accompany the matching xkit source checkout.',
            'Extract runtime beside the devkit README.md, not into the game folder.',
            'Use xkit setup, xkit new NAME --resources and xkit start NAME normally.',
            'H5U authors need no C++ compiler. Keep SDK sources and this runtime version together.', '',
            'SDK: https://github.com/Xaaalera/heroes5-mod-devkit',
            'Game API: https://github.com/Xaaalera/heroes5-game-api',
            'Universe: https://h5lobby.com/',
            'Author: https://github.com/Xaaalera | dampirsimpl@gmail.com | https://t.me/Victima', '',
        ]))
        package.writestr('runtime/current.json', json.dumps(pointer) + '\n')
        for name in [*paths, 'runtime.json']:
            package.write(directory / name, 'runtime/' + generation + '/' + name)
    os.replace(temporary, archive)
    return archive


def package_sdk_release(sdk, runtime_archive, archive):
    """Ship the current canonical sources, Game API and ready runtime together."""
    sdk = sdk.resolve()
    sources = {}
    for repository, prefix in ((sdk, ''), (sdk / 'game-api', 'game-api/')):
        repository = repository.resolve()
        listing = subprocess.run(['git', '-C', str(repository), 'ls-files', '--cached', '--others',
                                  '--exclude-standard', '-z'], capture_output=True, check=True)
        for raw in listing.stdout.split(b'\0'):
            if not raw:
                continue
            name = raw.decode('utf-8')
            if (repository / name).is_dir():
                continue
            path = (repository / name).resolve()
            if not path.is_relative_to(repository):
                raise ValueError('SDK source entry escapes its repository')
            if path.is_file():
                sources[prefix + name] = path.read_bytes()
    if not all(name in sources for name in ('README.md', 'pyproject.toml', 'scripts/xalkit.py', 'game-api/include/h5/hooks.hpp')):
        raise ValueError('SDK source distribution is incomplete')
    archive = archive.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name(archive.name + '.' + uuid.uuid4().hex + '.tmp')
    with ZipFile(runtime_archive) as runtime, ZipFile(temporary, 'w', ZIP_DEFLATED) as package:
        pointer = json.loads(runtime.read('runtime/current.json'))
        if not isinstance(pointer, dict) or pointer.get('version') != 1 or not isinstance(pointer.get('generation'), str):
            raise ValueError('Invalid SDK runtime archive pointer')
        ready = load_sdk_runtime(sdk, pointer['generation'])
        for name, digest in ready['manifest']['sources'].items():
            if name.startswith('@game-api/'):
                source_name = 'game-api/include/' + name[len('@game-api/'):]
            elif name.startswith('@locale/'):
                source_name = 'locale/' + name[len('@locale/'):]
            elif name == '@resources':
                source_name = 'scripts/native-ui-resources.py'
            else:
                source_name = 'native/' + name
            if source_name not in sources or hashlib.sha256(sources[source_name].replace(b'\r\n', b'\n')).hexdigest() != digest:
                raise ValueError('Packaged SDK sources do not match runtime: ' + source_name)
        allowed_runtime = {'README.txt', 'runtime/current.json', 'runtime/' + pointer['generation'] + '/runtime.json'}
        allowed_runtime.update('runtime/' + pointer['generation'] + '/' + name for name in ready['manifest']['files'])
        if set(runtime.namelist()) != allowed_runtime:
            raise ValueError('SDK runtime archive contents differ from its manifest')
        archived_manifest = json.loads(runtime.read('runtime/' + pointer['generation'] + '/runtime.json'))
        if archived_manifest != ready['manifest']:
            raise ValueError('SDK runtime archive manifest changed')
        for name, digest in ready['manifest']['files'].items():
            content = runtime.read('runtime/' + pointer['generation'] + '/' + name)
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError('SDK runtime archive file changed: ' + name)
        for name, content in sorted(sources.items()):
            package.writestr('xkit/' + name, content)
        for name in runtime.namelist():
            target = 'runtime-README.txt' if name == 'README.txt' else name
            package.writestr('xkit/' + target, runtime.read(name))
        package.writestr('xkit/sdk-release.json', json.dumps({
            'version': 1, 'runtime_generation': pointer['generation'],
            'source_files': {name: hashlib.sha256(content).hexdigest() for name, content in sorted(sources.items())},
            'runtime_files': ready['manifest']['files'],
        }, indent=2) + '\n')
        load_sdk_runtime(sdk, pointer['generation'])
    os.replace(temporary, archive)
    return archive


def decode_diagnostics(encoded):
    """Decode the bounded POD wire record; never expose binary transport to users."""
    size = 38448
    if not isinstance(encoded, str) or len(encoded) != size * 2:
        raise ValueError('Invalid diagnostic response size')
    data = bytes.fromhex(encoded)
    header_size, version, after, minimum, reserved, oldest, following, dropped, count = struct.unpack_from(
        '<IIQIIQQII', data)
    if (header_size != size or version != 1 or minimum > 3 or reserved or
            oldest < 1 or following < oldest or count > 64 or count > following - oldest):
        raise ValueError('Invalid diagnostic response header')
    records = []
    previous = after
    levels = ('debug', 'info', 'warning', 'error')
    for index in range(count):
        sequence, milliseconds, level, module, message, padding = struct.unpack_from(
            '<QQI64s512sI', data, 48 + index * 600)
        if not oldest <= sequence < following or sequence <= previous or not minimum <= level <= 3 or padding:
            raise ValueError('Invalid diagnostic record')
        strings = []
        for raw in (module, message):
            end = raw.find(b'\0')
            if end <= 0:
                raise ValueError('Unterminated or empty diagnostic text')
            strings.append(raw[:end].decode('utf-8'))
        records.append({'sequence': sequence, 'milliseconds': milliseconds, 'level': levels[level],
                        'module': strings[0], 'message': strings[1]})
        previous = sequence
    return {'oldest_sequence': oldest, 'next_sequence': following, 'dropped': dropped,
            'records': records, 'cursor': previous}


class CoreBuild:
    """Build the canonical CMake core targets once for a source generation."""
    def __init__(self, source, output, environment, console=False):
        self.source = source.resolve(strict=True)
        self.output = output.resolve()
        if self.output.is_relative_to(self.source):
            raise ValueError('Core build output must be outside core sources')
        self.environment = {key.upper(): value for key, value in environment.items()}
        self.last_attempt = None
        self.ready = None
        self.console = console
        self.cache = self.output if console else self.output / 'cmake'
        # MSBuild compiler-identification tracking files still exceed MAX_PATH
        # in nested workspaces. Keep only build intermediates in a short cache.
        if len(str(self.cache)) > 128:
            cache_key = hashlib.sha256((str(self.source) + '\0' + str(self.output)).encode('utf-8')).hexdigest()[:24]
            self.cache = Path(tempfile.gettempdir()).resolve() / 'xkit-cmake' / cache_key
        cache_file = self.cache / 'CMakeCache.txt'
        if cache_file.is_file():
            prefix = 'CMAKE_HOME_DIRECTORY:INTERNAL='
            previous_source = next((line[len(prefix):] for line in cache_file.read_text(encoding='utf-8').splitlines()
                                    if line.startswith(prefix)), None)
            if previous_source is None or Path(previous_source).resolve() != self.source:
                source_key = hashlib.sha256(str(self.source).encode('utf-8')).hexdigest()[:16]
                self.cache = self.output / ('cmake-' + source_key)
        cmake = shutil.which('cmake', path=self.environment.get('PATH'))
        if not cmake:
            cmake = str(Path(self.environment['VSINSTALLDIR']) /
                        'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe')
        self.cmake = Path(cmake).resolve(strict=True)

    def snapshot(self):
        snapshot = {path.relative_to(self.source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(self.source.rglob('*'))
                if path.is_file() and (path.suffix.lower() in ('.cpp', '.h', '.hpp', '.inl', '.cmake', '.def')
                                       or path.name == 'CMakeLists.txt')}
        include = self.source.parent / 'game-api/include'
        for path in include.rglob('*.hpp'):
            snapshot['@game-api/' + path.relative_to(include).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if self.console:
            for path in (self.source.parent / 'locale').rglob('*.po'):
                snapshot['@locale/' + path.relative_to(self.source.parent / 'locale').as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            resources = self.source.parent / 'scripts/native-ui-resources.py'
            snapshot['@resources'] = hashlib.sha256(resources.read_bytes()).hexdigest()
        return snapshot

    def release(self, sources, generation, payload, sdk_include):
        """Derive player compilation from the actual core target, including its extensions."""
        snapshot = self.snapshot()
        project = generation / 'cmake-project'
        project.mkdir()
        quote = lambda path: json.dumps(str(path).replace('\\', '/'), ensure_ascii=False)
        text = ('cmake_minimum_required(VERSION 3.21)\nproject(Heroes5PlayerPlugin LANGUAGES CXX)\n'
                'set(CMAKE_CXX_STANDARD 20)\nset(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)\n'
                f'add_subdirectory({quote(self.source)} core)\n'
                f'set(SDK_INCLUDE_DIRECTORY {quote(sdk_include)})\n'
                'set(SDK_PLUGIN_SOURCES\n'
                + ''.join('  ' + quote(source) + '\n' for source in sources) + ')\n'
                f'include({quote(sdk_include / "player_plugin.cmake")})\n')
        (project / 'CMakeLists.txt').write_text(text, encoding='utf-8')
        # MSBuild's tracking files can exceed MAX_PATH under generation folders.
        # Keep compiler intermediates in a standard short-lived temporary folder.
        with tempfile.TemporaryDirectory(prefix='h5r-') as temporary:
            cache = Path(temporary) / 'b'
            for arguments in ([str(self.cmake), '-S', str(project), '-B', str(cache), '-A', 'Win32'],
                              [str(self.cmake), '--build', str(cache), '--config', 'Release',
                               '--target', 'heroes5_plugin_release']):
                result = subprocess.run(arguments, env=self.environment, capture_output=True,
                                        text=True, encoding='utf-8', timeout=60)
                if result.returncode:
                    return result, snapshot
            if snapshot != self.snapshot():
                raise RuntimeError('Core sources changed during release; rerun release from a stable generation')
            shutil.copyfile(cache / 'Release/heroes5_plugin_release.dll', payload)
        return result, snapshot

    def update(self):
        snapshot = self.snapshot()
        if snapshot == self.last_attempt:
            return None
        self.last_attempt = snapshot
        started = time.perf_counter()
        cache = self.cache
        if self.console:
            try:
                from babel.messages.pofile import read_po
                from babel.messages.mofile import write_mo
                for path in (self.source.parent / 'locale').rglob('*.po'):
                    with path.open('rb') as stream:
                        catalog = read_po(stream, abort_invalid=True)
                    stream = io.BytesIO()
                    write_mo(stream, catalog)
                    compiled = stream.getvalue()
                    target = path.with_suffix('.mo')
                    if not target.is_file() or target.read_bytes() != compiled:
                        target.write_bytes(compiled)
            except Exception as failure:
                return {'status': 'console_build_failed', 'reason': 'translation_failed',
                        'diagnostic': str(failure), 'build_seconds': time.perf_counter() - started}
        configure = [str(self.cmake), '-S', str(self.source), '-B', str(cache), '-A', 'Win32']
        if self.console:
            configure.append('-DXALKIT_BUILD_CONSOLE=ON')
        targets = ['xalkit_console'] if self.console else ['heroes5_plugin_bridge', 'plugin_bridge_client']
        build = [str(self.cmake), '--build', str(cache), '--config', 'Release', '--target', *targets]
        cached_console = self.console and (cache / 'xalkit_console.vcxproj').is_file() and \
            (cache / 'CMakeCache.txt').is_file() and \
            'XALKIT_BUILD_CONSOLE:BOOL=ON' in (cache / 'CMakeCache.txt').read_text(encoding='utf-8')
        for arguments in ([build] if cached_console else [configure, build]):
            try:
                result = subprocess.run(arguments, env=self.environment, capture_output=True,
                                        text=True, encoding='utf-8', timeout=60)
            except subprocess.TimeoutExpired as failure:
                diagnostic = failure.stdout or b''
                if isinstance(diagnostic, bytes):
                    diagnostic = diagnostic.decode('utf-8', errors='replace')
                return {'status': 'console_build_failed' if self.console else 'core_build_failed', 'reason': 'compiler_timeout',
                        'diagnostic': diagnostic, 'command': arguments,
                        'build_seconds': time.perf_counter() - started}
            if result.returncode:
                return {'status': 'console_build_failed' if self.console else 'core_build_failed', 'diagnostic': result.stdout + result.stderr,
                        'build_seconds': time.perf_counter() - started}
        if snapshot != self.snapshot():
            self.last_attempt = None
            return {'status': 'console_build_superseded' if self.console else 'core_build_superseded', 'build_seconds': time.perf_counter() - started}
        if self.console:
            binary = cache / 'Release/XalKitConsole.dll'
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            generation = self.output / ('generation-' + digest[:16])
            generation.mkdir(parents=True, exist_ok=True)
            frozen = generation / binary.name
            if not frozen.exists():
                shutil.copyfile(binary, frozen)
            elif hashlib.sha256(frozen.read_bytes()).hexdigest() != digest:
                raise RuntimeError('Console generation binary changed')
            notices = cache / 'Release/XalKitConsole.LICENSES.txt'
            if not notices.is_file():
                return {'status': 'console_build_failed', 'reason': 'dependency_notices_missing',
                        'build_seconds': time.perf_counter() - started}
            shutil.copyfile(notices, generation / notices.name)
            self.ready = {'status': 'console_built', 'console': str(frozen), 'console_sha256': digest,
                          'source_hashes': snapshot, 'build_seconds': time.perf_counter() - started}
            return self.ready
        generation = self.output / ('generation-' + uuid.uuid4().hex)
        generation.mkdir(parents=True)
        bridge = generation / 'heroes5_plugin_bridge.dll'
        controller = generation / 'plugin_bridge_client.exe'
        shutil.copyfile(cache / 'Release' / bridge.name, bridge)
        shutil.copyfile(cache / 'Release' / controller.name, controller)
        self.ready = {'status': 'core_built', 'bridge': str(bridge), 'controller': str(controller),
                      'source_hashes': snapshot, 'build_seconds': time.perf_counter() - started}
        return self.ready


ERROR_MESSAGES = {
    'CORE_PREPARE_FAILED': 'The new core could not be prepared. The current core is still running.',
    'CORE_TRANSFER_FAILED': 'The new core rejected activation. The previous core was restored.',
}


def core_error(reason, stage):
    return {'status': 'core_rejected', 'error': {
        'code': 409, 'status': 'FAILED_PRECONDITION', 'message': ERROR_MESSAGES[reason],
        'details': [{'@type': 'ErrorInfo', 'reason': reason, 'domain': 'heroes5.devkit',
                     'metadata': {'stage': stage}}],
    }}


class CoreClient:
    def __init__(self, arguments, directory):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.arguments = list(arguments)
        self.process = self.start(Path(arguments[-1]))

    def start(self, bridge):
        bridge = Path(bridge)
        # Running helper EXEs must not lock the canonical build output: a core
        # update may rebuild both the DLL and its controller in the same session.
        executable = self.directory / ('controller-' + uuid.uuid4().hex + '.exe')
        shutil.copyfile(self.arguments[0], executable)
        process = subprocess.Popen([str(executable), *self.arguments[1:-1], str(bridge.resolve())],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True, encoding='utf-8',
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        process.responses = queue.Queue()

        def collect():
            for line in process.stdout:
                process.responses.put(line)
            process.responses.put(None)

        threading.Thread(target=collect, daemon=True).start()
        # The native loader has a 30-second budget; leave time for helper startup.
        ready = self.response(process, timeout=35)
        if ready != {'ready': True}:
            self.close(process)
            raise RuntimeError('core_client_not_ready')
        return process

    @staticmethod
    def response(process, timeout=15):
        try:
            line = process.responses.get(timeout=timeout)
        except queue.Empty as error:
            raise RuntimeError('core_call_unconfirmed_keep_remote_code') from error
        if line is None:
            raise RuntimeError('core_client_disconnected')
        return json.loads(line)

    def send(self, process, command):
        console_replacement = command.startswith('console-replace ')
        if console_replacement:
            process.console_replacement_pending = True
        process.stdin.write(command + '\n')
        process.stdin.flush()
        response = self.response(process, timeout=50) if console_replacement else self.response(process)
        if console_replacement:
            process.console_replacement_pending = False
        return response

    def request(self, command):
        return self.send(self.process, command)

    def poll(self):
        return self.process.poll()

    def close(self, process=None):
        process = process or self.process
        if process.poll() is None:
            process.stdin.write('quit\n')
            process.stdin.flush()
        try:
            result = process.wait(timeout=55 if getattr(process, 'console_replacement_pending', False) else 15)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError('core_cleanup_unconfirmed_keep_remote_code') from error
        if result != 0:
            raise RuntimeError('core_cleanup_failed_keep_remote_code')

    def reload_core(self, bridge, payload, slot, invoke_command, controller=None):
        prepare_command = 'connect-prepare' if payload is None else 'reload ' + str(payload)
        activate_command = 'connect' if payload is None else invoke_command + ' 0 0'
        old = self.process
        if payload is None and self.send(old, 'connect-check')['status'] != 0:
            return core_error('CORE_PREPARE_FAILED', 'host_mode')
        old_version = self.send(old, 'core')['result']
        previous_controller = self.arguments[0]
        if controller is not None:
            self.arguments[0] = str(controller)
        candidate = None
        stage = 'prepare'
        try:
            candidate = self.start(bridge)
            if self.send(candidate, f'slot {slot} 0')['status'] != 0:
                self.close(candidate)
                self.arguments[0] = previous_controller
                return core_error('CORE_PREPARE_FAILED', 'slot')
            if self.send(candidate, prepare_command)['status'] != 0:
                self.close(candidate)
                self.arguments[0] = previous_controller
                return core_error('CORE_PREPARE_FAILED', 'payload')
        except (RuntimeError, OSError, ValueError):
            # No old-core calls have happened. The existing session stays active.
            if candidate and candidate.poll() is None:
                self.close(candidate)
            self.arguments[0] = previous_controller
            return core_error('CORE_PREPARE_FAILED', stage)

        state = self.send(old, 'suspend')
        if state['status'] != 0 or 'snapshot' not in state:
            self.close(candidate)
            raise RuntimeError('old_core_quiescence_unconfirmed')
        stage = 'restore'
        restored = self.send(candidate, 'restore ' + state['snapshot'])
        if restored['status'] == 0:
            stage = 'activate'
            observed = self.send(candidate, activate_command)
        else:
            observed = restored
        if observed['status'] != 0:
            # Only confirmed native status failures reach this path. Timeout or
            # disconnection is uncertain and must never trigger unsafe unloading.
            self.close(candidate)
            if self.send(old, prepare_command)['status'] != 0 or \
               self.send(old, 'restore ' + state['snapshot'])['status'] != 0 or \
               self.send(old, activate_command)['status'] != 0:
                raise RuntimeError('core_rollback_failed')
            self.arguments[0] = previous_controller
            return core_error('CORE_TRANSFER_FAILED', stage)
        self.process = candidate
        self.arguments[-1] = str(bridge)
        self.close(old)
        return {'status': 'core_applied', 'from_version': old_version,
                'core_version': self.request('core')['result'],
                'observation': observed, 'state_preserved': True}
