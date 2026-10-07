"""Typer entry point; canonical backends stay in the SDK checkout."""
import importlib.util
import hashlib
from functools import wraps
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from zipfile import ZipFile, ZIP_DEFLATED
from typing import Annotated, Optional

import typer
import click
from rich.console import Console
from rich.table import Table
from workspace import DEVKIT, workspace_root
from sdk_logging import EventLog
from xalkit_ui import text, error, CommandFailure
from xalkit_config import config_path, settings, save_settings

app = typer.Typer(name='xkit', help=text('about'), no_args_is_help=True, pretty_exceptions_show_locals=False)
console = Console()


def logged_command(action):
    def decorate(command):
        @wraps(command)
        def run(*arguments, **options):
            context = click.get_current_context(silent=True)
            if context and isinstance(context.obj, dict) and context.obj.get('source') == 'completion':
                return command(*arguments, **options)
            started = time.perf_counter()
            events = EventLog(Path(settings()['workspace']) / '.local/xalkit/logs',
                              console=True, stream=sys.stderr)
            try:
                with events.operation(action, started=started):
                    return command(*arguments, **options)
            finally:
                events.close()
        return run
    return decorate


def load_backend(filename):
    # Only the canonical SDK scripts directory is importable, never workspace code.
    scripts = DEVKIT / 'scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    specification = importlib.util.spec_from_file_location('xalkit_' + filename.replace('-', '_'), scripts / filename)
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def select_project(saved, name):
    name = name or saved.get('project')
    if not name:
        raise ValueError(text('missing_project'))
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
        raise ValueError(text('bad_name'))
    root = Path(saved['workspace'])
    native = root / 'plugins' / name
    source = native if native.is_dir() else root / 'mods' / name
    if not source.is_dir():
        raise ValueError(text('missing_project'))
    saved['project'] = name
    save_settings(saved)
    return name, source, source == native


def complete_projects(incomplete: str):
    root = Path(settings()['workspace'])
    projects = set()
    for directory, marker in ((root / 'plugins', 'xalkit.json'), (root / 'mods', 'mod.json')):
        if not directory.is_dir():
            continue
        for source in directory.iterdir():
            if source.is_dir() and (source / marker).is_file() and \
                    re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', source.name) and \
                    source.name.lower().startswith(incomplete.lower()):
                projects.add(source.name)
    return sorted(projects)


def compiler_setup(saved):
    installation = None
    program_files = os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')
    finder = Path(program_files) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    if finder.is_file():
        result = subprocess.run([str(finder), '-latest', '-products', '*', '-requires',
            'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
            capture_output=True, text=True, check=True)
        installation = result.stdout.strip()
    toolchain = Path(installation or '') / 'VC/Auxiliary/Build/vcvarsall.bat'
    if not installation or not toolchain.is_file():
        raise RuntimeError(text('missing_compiler'))
    module = load_backend('plugin-watch.py')
    compiler, environment = module.compiler_environment(toolchain)
    return toolchain, compiler, environment


def native_tools(saved, events, with_console=False):
    events.emit({'status': 'native_build', 'message': text('native_build')})
    toolchain, compiler, environment = compiler_setup(saved)
    from plugin_core import CoreBuild
    output = Path(saved['workspace']) / '.local/xalkit/watch/core'
    builder = CoreBuild(DEVKIT / 'native', output, environment)
    built = builder.update()
    events.emit(built)
    if built['status'] != 'core_built':
        raise RuntimeError(text('native_build_failed', diagnostic=events.directory))
    result = subprocess.run([str(builder.cmake), '--build', str(builder.cache), '--config', 'Release',
                            '--target', 'heroes5_mod_loader', 'sdk_launch_gate'], env=environment, capture_output=True, text=True, encoding='utf-8', timeout=60)
    if result.returncode:
        events.emit({'status': 'build_failed', 'diagnostic': result.stdout + result.stderr})
        raise RuntimeError(text('native_build_failed', diagnostic=events.directory))
    built['launch_gate'] = str(builder.cache / 'Release/sdk_launch_gate.exe')
    built['graphics'] = str(builder.cache / 'Release/d3d9.dll')
    if with_console:
        console_builder = CoreBuild(DEVKIT / 'native', Path(saved['workspace']) / '.local/xalkit/console-build',
                                    environment, console=True)
        console_build = console_builder.update()
        events.emit(console_build)
        if console_build['status'] != 'console_built':
            raise RuntimeError(text('native_build_failed', diagnostic=events.directory))
        built['console'] = console_build['console']
        built['console_source_hashes'] = console_build['source_hashes']
    return toolchain, compiler, environment, built, builder.cache / 'Release/dinput8.dll'


def resource_operation(saved, name, source, action):
    module = load_backend('mod-dev.py')
    workshop = module.Workshop(Path(saved['workspace']), name, True, source)
    with module.exclusive(workshop.local):
        if not (workshop.local / 'prepared.json').is_file():
            module.require_stopped()
            console.print(text('prepare'))
            workshop.prepare()
        if action in ('start', 'deploy'):
            module.require_stopped()
            workshop.build()
            deployment = workshop.deploy()
            return workshop.launch() if action == 'start' else deployment
        return workshop.build()


def build_project(saved, name, source, native, events):
    events.emit({'status': 'building', 'message': text('building', name=name), 'plugin': name})
    if not native:
        adapter = json.loads((source / 'mod.json').read_text(encoding='utf-8')).get('native_adapter')
        if adapter and adapter != 'bank-selector':
            raise ValueError(text('native_adapter_unknown', adapter=str(adapter)))
        result = resource_operation(saved, name, source, 'build')
        artifact = Path(result.get('artifact') or Path(saved['workspace']) / '.local/test-state' / (name + '.h5u'))
        output = Path(saved['workspace']) / '.local/xalkit/releases' / name
        output.mkdir(parents=True, exist_ok=True)
        if adapter:
            toolchain, compiler, environment, built, loader = native_tools(saved, events)
            module = load_backend('plugin-watch.py')
            watcher = module.PluginWatch(source / 'src', output / 'build', compiler, environment, None)
            from plugin_core import CoreBuild
            cmake = CoreBuild(DEVKIT / 'native', output / 'tools', environment).cmake
            candidate = watcher.build_bank_payload(source, artifact, Path(built['graphics']), cmake,
                                                    time.perf_counter(), managed=False)
            events.emit(candidate)
            if not candidate or candidate['status'] != 'bank_built':
                raise RuntimeError(text('native_build_failed', diagnostic=events.directory))
            archive = output / (name + '.zip')
            temporary = output / (name + '.zip.tmp')
            try:
                with ZipFile(temporary, 'w', ZIP_DEFLATED) as package:
                    member_hashes = {}
                    for filename, member in ((Path(candidate['payload']), 'bin/Heroes5Mods/WorkshopBankReference.dll'),
                            (artifact, 'UserMODs/workshop-army-reference.h5u'), (loader, 'bin/dinput8.dll'),
                            (Path(built['graphics']), 'bin/d3d9.dll'), (DEVKIT / 'NOTICE.md', 'NOTICE.txt'),
                            (source / 'README.md', 'README.md')):
                        content = filename.read_bytes()
                        digest = hashlib.sha256(content).hexdigest()
                        expected = {'bin/Heroes5Mods/WorkshopBankReference.dll': candidate['payload_sha256'],
                                    'UserMODs/workshop-army-reference.h5u': candidate['package_sha256'],
                                    'bin/d3d9.dll': candidate['graphics_sha256']}.get(member)
                        if expected is not None and digest != expected:
                            raise ValueError(text('native_build_failed', diagnostic=events.directory))
                        package.writestr(member, content)
                        member_hashes[member] = digest
                    package.writestr('build.json', json.dumps({'native_adapter': adapter, 'mode': 'player',
                        'source_hashes': candidate['source_hashes'], 'files': member_hashes}, indent=2) + '\n')
                os.replace(temporary, archive)
            finally:
                temporary.unlink(missing_ok=True)
            events.emit({'status': 'released', 'archive': str(archive), 'native_adapter': adapter})
            return str(archive)
        released = output / (name + '.h5u')
        shutil.copyfile(artifact, released)
        shutil.copyfile(artifact.with_suffix('.build.json'), released.with_suffix('.build.json'))
        events.emit({'status': 'released', 'message': text('resource_released', path=released), 'artifact': str(released)})
        return str(released)
    toolchain, compiler, environment, built, loader = native_tools(saved, events)
    module = load_backend('plugin-watch.py')
    output = Path(saved['workspace']) / '.local/xalkit/releases' / name
    watcher = module.PluginWatch(source, output, compiler, environment, None)
    result = watcher.update(watcher.snapshot(), time.perf_counter(), name, loader, DEVKIT / 'native')
    events.emit(result)
    if result['status'] != 'released':
        raise RuntimeError(text('failure', reason=result['status']))
    return result['archive']


sdk_app = typer.Typer(help=text('sdk_tools'))
app.add_typer(sdk_app, name='sdk')


@sdk_app.command(name='recover-bank', help=text('recover_bank_help'))
@logged_command('sdk recover-bank')
def recover_bank(manifest: Path = typer.Argument(..., exists=True, dir_okay=False)):
    from xalkit_runtime import recover_bank_runtime
    root = Path(settings()['workspace'])
    try:
        if (root / '.local').resolve() != root / '.local':
            raise ValueError('Workspace mutation directory is redirected')
        module = load_backend('mod-dev.py')
        with module.exclusive(root / '.local', process_lease=True):
            result = recover_bank_runtime(root, manifest)
    except (OSError, ValueError, KeyError, TypeError) as recovery_error:
        events = EventLog(root / '.local/xalkit/logs', console=False)
        try:
            events.emit({'status': 'bank_recovery_refused', 'reason': str(recovery_error),
                         'recovery_manifest': str(manifest)})
        finally:
            events.close()
        raise CommandFailure(error('SDK_BANK_RECOVERY_REFUSED', manifest=str(manifest))) from recovery_error
    console.print(text('recover_bank_done'), markup=False)
    return result

storage_app = typer.Typer(help=text('storage_help'))
app.add_typer(storage_app, name='storage')


@storage_app.command(name='status', help=text('storage_status_help'))
@logged_command('storage status')
def storage_status():
    from sdk_storage import GameAssets
    from workspace import game_installation
    saved = settings()
    root = Path(saved['workspace'])
    assets = GameAssets(root, game_installation(root))
    games = assets.games()
    console.print(text('storage_status', count=len(games), cache=str(assets.cache)), markup=False)


@storage_app.command(name='clean', help=text('storage_clean_help'))
@logged_command('storage clean')
def storage_clean(keep: int = typer.Option(3, '--keep', min=0, max=3, help=text('storage_keep_help'))):
    import shutil
    from sdk_storage import GameAssets
    from workspace import game_installation
    saved = settings()
    root = Path(saved['workspace'])
    assets = GameAssets(root, game_installation(root))
    before = shutil.disk_usage(root).free
    events = EventLog(root / '.local/xalkit/logs', console=True)
    try:
        events.emit({'status': 'storage_cleanup_started', 'message': text('storage_working')})
        result = assets.clean(keep=keep, progress=lambda index, total: events.emit({
            'status': 'storage_shared', 'message': text('storage_share_progress', index=index, total=total)}))
        result['freed_bytes'] = shutil.disk_usage(root).free - before
        events.emit({'status': 'storage_cleaned', 'result': result})
        console.print(text('storage_cleaned', count=len(result['retired']), kept=result['kept'],
                           size=f"{result['freed_bytes'] / 2**30:.2f}"), markup=False)
        return result
    finally:
        events.close()


@storage_app.command(name='restore', help=text('storage_restore_help'))
@logged_command('storage restore')
def storage_restore(archive: Path = typer.Argument(..., exists=True, dir_okay=False)):
    from sdk_storage import GameAssets
    from workspace import game_installation
    root = Path(settings()['workspace'])
    result = GameAssets(root, game_installation(root)).restore(archive)
    console.print(text('storage_restored', path=result['game']), markup=False)


@storage_app.command(name='restore-dump', help=text('storage_restore_dump_help'))
@logged_command('storage restore-dump')
def storage_restore_dump(archive: Path = typer.Argument(..., exists=True, dir_okay=False)):
    from sdk_storage import GameAssets
    from workspace import game_installation
    root = Path(settings()['workspace'])
    destination = GameAssets(root, game_installation(root)).restore_dump(archive)
    console.print(text('result', path=destination), markup=False)


@sdk_app.command(name='build', help=text('sdk_build'))
@logged_command('sdk build')
def build_sdk():
    saved = settings()
    events = EventLog(Path(saved['workspace']) / '.local/xalkit/logs', console=True)
    try:
        _, _, _, built, _ = native_tools(saved, events, with_console=True)
        from plugin_core import package_sdk_runtime
        artifact = package_sdk_runtime(DEVKIT, built, Path(saved['workspace']) / '.local/xalkit/releases/xkit-runtime.zip')
        console.print(text('result', path=artifact))
    finally:
        events.close()


@sdk_app.command(name='release', help=text('sdk_release'))
@logged_command('sdk release')
def release_sdk():
    build_sdk()
    from plugin_core import package_sdk_release
    output = Path(settings()['workspace']) / '.local/xalkit/releases'
    artifact = package_sdk_release(DEVKIT, output / 'xkit-runtime.zip', output / 'xkit-sdk.zip')
    console.print(text('result', path=artifact))


@app.command(help=text('setup'))
def setup(workspace: Optional[Path] = None, game: Optional[Path] = None):
    started = time.perf_counter()
    saved = settings()
    root = workspace or Path(typer.prompt(text('workspace'), default=saved['workspace']))
    installation = game or Path(typer.prompt(text('game'), default=saved.get('game', '')))
    events = EventLog(root / '.local/xalkit/logs', console=True, stream=sys.stderr)
    try:
        with events.operation('setup', started=started):
            if not (installation / 'bin/H5_Game.exe').is_file():
                raise ValueError(text('missing_game'))
            root.mkdir(parents=True, exist_ok=True)
            saved.update(workspace=str(root.resolve()), game=str(installation.resolve()))
            save_settings(saved)
            console.print(text('saved'))
    finally:
        events.close()


@app.command(help=text('doctor'))
@logged_command('doctor')
def doctor():
    saved = settings()
    table = Table(title='xkit', show_header=True)
    table.add_column(text('check')); table.add_column(text('status'))
    table.add_row(text('projects'), saved['workspace'])
    installation = Path(saved.get('game') or Path(saved['workspace']) / 'Heroes of Might and Magic 5 Tribes of the East')
    table.add_row(text('installation'), text('ok') if (installation / 'bin/H5_Game.exe').is_file() else text('missing_game'))
    try:
        toolchain, compiler, environment = compiler_setup(saved)
        table.add_row(text('compiler'), text('ok'))
    except RuntimeError:
        table.add_row(text('compiler'), text('missing_compiler'))
    table.add_row(text('settings'), str(config_path()))
    from sdk_diagnostics import input_profile_files
    for profile in input_profile_files(saved['workspace']):
        table.add_row(text('input_profile', profile=profile['profile']),
                      text('ok') if profile['present'] else text('input_profile_missing', path=profile['path']))
    console.print(table)


@app.command(help=text('language_help'))
@logged_command('language')
def language(code: str):
    if code not in ('ru', 'en'):
        raise ValueError(text('language_choice'))
    saved = settings(); saved['language'] = code; save_settings(saved)
    os.environ['XALKIT_LANG'] = code
    console.print(text('language_saved'))


@app.command(help=text('diagnostics_help'))
def diagnostics(json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    root = Path(settings()['workspace'])
    local = root / '.local/xalkit'
    artifacts = []
    windows_summary = []
    for kind, path in (('session_log', local / 'logs/events.jsonl'),
                       ('session_report', local / 'logs/session-latest.json'),
                       ('check_report', local / 'check/report.json'),
                       ('check_log', local / 'check/logs/backend.log')):
        if path.is_file() and path.resolve().is_relative_to(local.resolve()):
            artifacts.append({'kind': kind, 'path': str(path)})
    for directory in (local / 'logs', local / 'check/logs'):
        for archive in sorted(directory.glob('events.jsonl.*.gz')):
            if archive.is_file() and archive.resolve().is_relative_to(local.resolve()):
                artifacts.append({'kind': 'log_archive', 'path': str(archive)})
    monitor_logs = [path for path in (local / 'crashes').glob('*/procdump.log')
                    if path.is_file() and path.resolve().is_relative_to(local.resolve())]
    windows_reports = [path for path in (local / 'crashes').glob('*/windows-events.json')
                       if path.is_file() and path.resolve().is_relative_to(local.resolve())]
    if windows_reports:
        windows_path = max(windows_reports, key=lambda path: path.stat().st_mtime_ns)
        artifacts.append({'kind': 'windows_events', 'path': str(windows_path)})
        try:
            windows_report = json.loads(windows_path.read_text(encoding='utf-8'))
            windows_summary = [{'module': item['module'], 'timestamp': item['timestamp']}
                               for item in windows_report['events']]
        except (OSError, ValueError, TypeError, KeyError):
            windows_summary = []
    if monitor_logs:
        latest = max(monitor_logs, key=lambda path: path.stat().st_mtime_ns)
        artifacts.append({'kind': 'monitor_log', 'path': str(latest)})
        for dump in sorted(latest.parent.rglob('*.dmp')):
            if dump.is_file() and dump.resolve().is_relative_to(local.resolve()):
                artifacts.append({'kind': 'exception_dump', 'path': str(dump)})
    monitor = root / '.local/tools/procdump/procdump.exe'
    result = {'schema_version': 1, 'workspace': str(root),
              'monitor_file_present': monitor.is_file(), 'monitor_path': str(monitor),
              'monitor_signature_checked': False, 'artifacts': artifacts,
              'windows_event_summary': windows_summary,
              'scope': 'existing SDK artifacts and latest monitor directory; no process attachment'}
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        return
    typer.echo(text('diagnostics_title'))
    typer.echo(text('diagnostics_monitor_present' if result['monitor_file_present'] else
                    'diagnostics_monitor_missing', path=monitor))
    for artifact in artifacts:
        typer.echo(text('diagnostics_' + artifact['kind']) + ': ' + artifact['path'])
    for summary in windows_summary:
        typer.echo(text('diagnostics_windows_failure', **summary))
    if not artifacts:
        typer.echo(text('diagnostics_empty'))
    typer.echo(text('diagnostics_scope'))


game_app = typer.Typer(help=text('game_help'), no_args_is_help=True)
app.add_typer(game_app, name='game')


@logged_command('game')
def game_action(arguments, json_output=False):
    saved = settings()
    events = EventLog(Path(saved['workspace']) / '.local/xalkit/logs', console=True, stream=sys.stderr)
    started = time.perf_counter()
    backend = None
    owner_token = None
    context = click.get_current_context(silent=True)
    request_context = context.obj if context and isinstance(context.obj, dict) else {}
    operation_owner = request_context.get('owner')
    if operation_owner is None:
        owner_path = Path(saved['workspace']) / '.local/test-state/native-probe.json'
        try:
            operation_owner = json.loads(owner_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            operation_owner = {}
    telemetry = {'game_pid': operation_owner.get('pid'), 'game_created': operation_owner.get('created'),
                 'source': request_context.get('source', 'terminal')}
    try:
        events.emit({'status': 'game_request_started', 'level': 'debug' if request_context.get('source') == 'completion' else 'info',
                     'stage': 'game', 'action': arguments[0], 'arguments': arguments,
                     **telemetry, 'message': text('game_request_started')})
        module = load_backend('game_control.py')
        if request_context.get('source') in ('console', 'completion'):
            backend = module
            owner_token = module.expected_session.set(request_context['owner'])
        result = module.main(arguments)
        events.emit({'status': 'game_request_completed', 'level': 'debug' if request_context.get('source') == 'completion' else 'info',
                     'stage': 'game', 'action': arguments[0], 'result': result,
                     **telemetry,
                     'duration_seconds': time.perf_counter() - started,
                     'message': text('game_request_completed')})
        if request_context.get('source') in ('console', 'completion'):
            return result
        if json_output:
            console.print_json(json.dumps(result, ensure_ascii=False))
        elif arguments[0] == 'map':
            console.print(text('game_map_dispatched', name=result['map']))
        elif arguments[0] == 'menu':
            console.print(text('game_menu_dispatched'))
        elif arguments[0] == 'trace':
            styles = {'debug': 'dim', 'info': None, 'warning': 'yellow', 'error': 'red'}
            for record in result['records']:
                console.print('[' + record['level'].upper() + '] ' + record['module'] + ': ' + record['message'],
                              style=styles[record['level']], markup=False, highlight=False)
            console.print(text('game_trace_summary', count=len(result['records']), cursor=result['cursor'],
                               dropped=result['dropped']), markup=False)
        else:
            from sdk_console_commands import display_result
            for line in display_result(arguments[0], result):
                console.print(line, markup=False, highlight=False, soft_wrap=True)
        return result
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as command_error:
        reason = 'SDK_GAME_CAPTURE_FAILED' if arguments[0] == 'screenshot' else 'SDK_GAME_COMMAND_FAILED'
        failure = error(reason, diagnostic=str(events.directory))
        if arguments[0] in ('map', 'restart') and isinstance(command_error, FileNotFoundError) and command_error.filename:
            missing = Path(command_error.filename)
            maps = Path(saved['workspace']) / '.local/test-game/Maps'
            if missing.parent.resolve() == maps.resolve():
                reason = 'SDK_GAME_MAP_NOT_FOUND'
                failure = error(reason, name=missing.name, folder=str(maps))
        events.emit({'status': 'game_request_failed', 'stage': 'game',
                     **telemetry,
                     'action': arguments[0], 'error': failure, 'reason': reason,
                     'failure_detail': str(command_error),
                     'duration_seconds': time.perf_counter() - started, 'exc_info': True,
                     'message': failure['message']})
        raise CommandFailure(failure) from command_error
    finally:
        if backend is not None and owner_token is not None:
            backend.expected_session.reset(owner_token)
        events.close()


def complete_maps(incomplete: str):
    root = Path(settings()['workspace']) / '.local/test-game/Maps'
    return [path.stem for path in sorted(root.glob('*.h5m')) if path.stem.lower().startswith(incomplete.lower())]


@game_app.command('map', help=text('game_map'))
def load_map(name: Annotated[str, typer.Argument(help=text('map_argument'), autocompletion=complete_maps)],
             json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    return game_action(['map', name], json_output=json_output)


@game_app.command(help=text('game_restart'))
def restart(name: Annotated[str, typer.Argument(help=text('map_argument'), autocompletion=complete_maps)],
            json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    return game_action(['map', name], json_output=json_output)


@game_app.command(help=text('game_menu'))
def menu(json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    return game_action(['menu'], json_output=json_output)


@game_app.command(help=text('game_status'))
def status(json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    return game_action(['status'], json_output=json_output)


@game_app.command(help=text('game_heroes'))
def heroes(json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    return game_action(['heroes'], json_output=json_output)


@game_app.command(help=text('game_screenshot'))
def screenshot(json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    return game_action(['screenshot'], json_output=json_output)


@game_app.command(help=text('game_trace'))
def trace(after: int = 0, level: str = 'info', limit: int = 16,
          console_output: bool = typer.Option(False, '--console', help=text('game_trace_console')),
          json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    arguments = ['trace', '--after', str(after), '--level', level, '--limit', str(limit)]
    if console_output:
        arguments.append('--console')
    return game_action(arguments, json_output=json_output)


def complete_creatures(incomplete: str):
    from sdk_console_commands import creature_choices
    return creature_choices(Path(settings()['workspace']), incomplete)


def complete_heroes(incomplete: str):
    from sdk_console_commands import hero_choices
    try:
        root = Path(settings()['workspace'])
        owner = json.loads((root / '.local/test-state/native-probe.json').read_text(encoding='utf-8'))
        probe = load_backend('native-probe.py')
        kernel = probe.api()
        process = kernel.OpenProcess(0x100000 | 0x1000, False, owner['pid'])
        if not process:
            return []
        try:
            if probe.creation_time(kernel, process) != owner['created'] or kernel.WaitForSingleObject(process, 0) != 258:
                return []
            return hero_choices(root, owner, incomplete)
        finally:
            kernel.CloseHandle(process)
    except (OSError, ValueError, KeyError, TypeError):
        return []


@game_app.command(help=text('game_army'))
def army(hero: Annotated[str, typer.Argument(help=text('argument_hero'), autocompletion=complete_heroes)],
         creature: Annotated[str, typer.Argument(help=text('argument_creature'), autocompletion=complete_creatures)],
         count: Annotated[Optional[int], typer.Option(min=0, max=1000000, help=text('argument_count'))] = None,
         json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    arguments = ['creature', hero, creature]
    if count is not None:
        arguments += ['--count', str(count)]
    return game_action(arguments, json_output=json_output)


@game_app.command(help=text('game_resource'))
def resource(player: Annotated[int, typer.Argument(min=1, max=8, help=text('argument_player'))],
             kind: Annotated[int, typer.Argument(min=0, max=6, help=text('argument_resource'))],
             amount: Annotated[Optional[int], typer.Option(min=0, max=100000000, help=text('argument_amount'))] = None,
             json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    arguments = ['resource', str(player), str(kind)]
    if amount is not None:
        arguments += ['--amount', str(amount)]
    return game_action(arguments, json_output=json_output)


@game_app.command(help=text('game_teleport'))
def teleport(hero: Annotated[str, typer.Argument(help=text('argument_hero'), autocompletion=complete_heroes)],
             x: Annotated[int, typer.Argument(help=text('argument_x'))],
             y: Annotated[int, typer.Argument(help=text('argument_y'))],
             floor: Annotated[int, typer.Option(help=text('argument_floor'))] = 0,
             json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    return game_action(['teleport', hero, str(x), str(y), '--floor', str(floor)], json_output=json_output)


@game_app.command(help=text('game_level'))
def level(hero: Annotated[str, typer.Argument(help=text('argument_hero'), autocompletion=complete_heroes)],
          target: Annotated[int, typer.Argument(min=1, max=40, help=text('argument_level'))],
          json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    return game_action(['level', hero, str(target)], json_output=json_output)


@game_app.command(help=text('game_interact'))
def interact(hero: Annotated[str, typer.Argument(help=text('argument_hero'), autocompletion=complete_heroes)],
             target: Annotated[str, typer.Argument(help=text('argument_object'))],
             json_output: bool = typer.Option(False, '--json', help=text('diagnostics_json'))):
    return game_action(['interact', hero, target], json_output=json_output)


@app.command(name='new', help=text('new'))
@logged_command('new')
def new_project(name: str, resources: bool = False):
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
        raise ValueError(text('bad_name'))
    saved = settings(); root = Path(saved['workspace'])
    source = root / ('mods' if resources else 'plugins') / name
    if source.exists():
        raise ValueError(text('unsafe_source'))
    template = DEVKIT / 'examples' / ('menu-marker' if resources else 'native-counter')
    shutil.copytree(template, source)
    if not resources:
        entry = source / 'plugin.cpp'
        entry.write_text(entry.read_text(encoding='utf-8').replace('"native-counter"', json.dumps(name)),
                         encoding='utf-8')
        (source / 'xalkit.json').write_text(json.dumps({'kind': 'native', 'display_event': True}), encoding='utf-8')
    if resources:
        recipe = json.loads((source / 'mod.json').read_text(encoding='utf-8'))
        recipe.update(id=name, append=' [xkit: ' + name + ']')
        (source / 'mod.json').write_text(json.dumps(recipe, indent=2), encoding='utf-8')
    (source / 'README.md').write_text(text('readme_resources' if resources else 'readme', name=name) +
                                    text('ide_readme'), encoding='utf-8')
    (source / 'AGENTS.md').write_text('[SDK instructions](https://github.com/Xaaalera/heroes5-mod-devkit/blob/main/AGENTS.md)\n', encoding='utf-8')
    editor = source / '.vscode'
    editor.mkdir(exist_ok=True)
    tasks = []
    for action in ('start', 'build', 'release', 'diagnostics'):
        task = {'label': text('ide_task_' + action), 'type': 'process', 'command': 'xkit',
                'args': [action] + ([name] if action != 'diagnostics' else []),
                'options': {'cwd': '${workspaceFolder}', 'env': {'H5_WORKSPACE': '${workspaceFolder}/../..'}},
                'problemMatcher': {'owner': 'xalkit', 'source': 'xkit', 'fileLocation': 'absolute',
                                   'applyTo': 'allDocuments', 'pattern': {
                                       'regexp': r'^(.*):(\d+):(\d+):\s+(warning|error):\s+(.*)$',
                                       'file': 1, 'line': 2, 'column': 3, 'severity': 4, 'message': 5}}
                                  if not resources else [],
                'presentation': {'reveal': 'always', 'panel': 'dedicated'},
                'runOptions': {'instanceLimit': 1}}
        if action == 'build':
            task['group'] = {'kind': 'build', 'isDefault': True}
        tasks.append(task)
    (editor / 'tasks.json').write_text(json.dumps({'version': '2.0.0', 'tasks': tasks},
                                                 ensure_ascii=False, indent=2), encoding='utf-8')
    saved['project'] = name; save_settings(saved)
    console.print(text('created', path=source, name=name))


@app.command(help=text('build'))
@logged_command('build')
def build(name: Annotated[Optional[str], typer.Argument(help=text('project_argument'), autocompletion=complete_projects)] = None):
    saved = settings(); name, source, native = select_project(saved, name)
    events = EventLog(Path(saved['workspace']) / '.local/xalkit/logs', console=True, plugin=name)
    try:
        artifact = build_project(saved, name, source, native, events)
        console.print(text('result', path=artifact))
    finally:
        events.close()


@app.command(help=text('release'))
@logged_command('release')
def release(name: Annotated[Optional[str], typer.Argument(help=text('project_argument'), autocompletion=complete_projects)] = None):
    build(name)


@app.command(name='check', help=text('check_help'))
@logged_command('check')
def check_sdk(player: bool = typer.Option(False, help=text('check_player_option')),
              console_ui: bool = typer.Option(False, '--console', help=text('check_console_option'))):
    saved = settings()
    if console_ui:
        if player:
            raise typer.BadParameter(text('check_console_conflict'))
        from xalkit_runtime import start_session
        root = Path(saved['workspace'])
        polygon = root / '.local/test-game/Maps/WorkshopPolygon.h5m'
        workshop_module = load_backend('mod-dev.py')
        workshop_module.require_stopped()
        if not (root / '.local/test-state/prepared.json').is_file():
            workshop = workshop_module.Workshop(root, 'sdk-console-check', True)
            with workshop_module.exclusive(workshop.local):
                workshop.prepare()
        if not polygon.is_file():
            load_backend('test-map.py').build()
        console.print(text('check_console_scope'), markup=False)
        try:
            result = start_session(saved, 'sdk-console-check', None, 'WorkshopPolygon', resources=True, console_check=True)
        except (OSError, RuntimeError, ValueError) as failure:
            raise CommandFailure(error('SDK_CHECK_FAILED', diagnostic=str(root / '.local/xalkit/logs'))) from failure
        validation = result.get('console_validation')
        if result.get('stop_reason') == 'keyboard_interrupt':
            raise typer.Exit(130)
        if not result.get('passed') or not validation or not validation.get('passed'):
            raise CommandFailure(error('SDK_CHECK_FAILED', diagnostic=str(root / '.local/xalkit/logs')))
        console.print(text('check_console_passed', seconds=f"{result['duration_seconds']:.1f}", report=validation['report']), markup=False, soft_wrap=True)
        return
    root = Path(saved['workspace'])
    workshop_module = load_backend('mod-dev.py')
    events = EventLog(root / '.local/xalkit/check/logs', console=True)
    process = None
    started = time.perf_counter()
    report = None
    diagnostic = events.directory / 'backend.log'
    diagnostic.touch()
    try:
        workshop_module.require_stopped()
        console.print(text('check_player_scope') if player else text('check_scope'), markup=False)
        toolchain, compiler, environment, built, loader = native_tools(saved, events)
        if not (root / '.local/test-state/prepared.json').is_file():
            events.emit({'status': 'prepare', 'message': text('prepare')})
            workshop = workshop_module.Workshop(root, 'sdk-check', True)
            with workshop_module.exclusive(workshop.local):
                workshop.prepare()
        polygon = root / '.local/test-game/Maps/WorkshopPolygon.h5m'
        if not polygon.is_file():
            events.emit({'status': 'prepare', 'message': text('prepare')})
            load_backend('test-map.py').build()
        arguments = [sys.executable, '-X', 'utf8', str(DEVKIT / 'scripts/plugin-control-check.py'),
                     '--live', '--native-owner', '--toolchain', str(toolchain),
                     '--launch-gate', built['launch_gate'], '--graphics-facade', built['graphics']]
        monitor = root / '.local/tools/procdump/procdump.exe'
        if monitor.is_file():
            arguments += ['--crash-monitor', str(monitor)]
        diagnostic.write_text('', encoding='utf-8')
        development_report = None
        while True:
            with diagnostic.open('a', encoding='utf-8') as raw:
                process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding='utf-8', env=events.child_environment(environment),
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
                for line in process.stdout:
                    raw.write(line); raw.flush()
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if 'report' in item:
                        report = item
                    elif item.get('step'):
                        step = item['step']
                        events.emit({'status': 'check_step', 'message': text('check_phase_' + step), 'stage': step})
                returncode = process.wait()
            if returncode or not report or not report.get('passed'):
                failure = error('SDK_CHECK_FAILED', diagnostic=str(diagnostic))
                events.emit({'status': 'check_failed', 'error': failure, 'message': failure['message'],
                             'diagnostic_log': str(diagnostic)})
                raise typer.Exit(1)
            if not player or development_report is not None:
                break
            development_report = report['report']
            arguments = [sys.executable, '-X', 'utf8', str(DEVKIT / 'scripts/plugin-player-check.py'),
                '--live', '--development-report', development_report, '--toolchain', str(toolchain),
                '--loader', str(loader), '--client', built['controller'], '--bridge', built['bridge']]
            if monitor.is_file():
                arguments += ['--crash-monitor', str(monitor)]
            report = None
        if player:
            combined_path = events.directory.parent / 'report.json'
            combined = {'passed': True, 'scope': 'native HMR and same-source player DLL delivery',
                        'development_report': development_report, 'player_report': report['report'],
                        'duration_seconds': time.perf_counter() - started}
            combined_path.write_text(json.dumps(combined, indent=2) + '\n', encoding='utf-8')
            report = {**report, 'report': str(combined_path)}
        events.emit({'status': 'check_completed', 'message': text('check_passed',
                    seconds=f"{time.perf_counter() - started:.1f}", report=report['report']),
                    'report': report['report'], 'duration_seconds': time.perf_counter() - started})
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
        try:
            remaining, _ = process.communicate(timeout=50) if process is not None else ('', None)
        except subprocess.TimeoutExpired:
            failure = error('SDK_CHECK_CLEANUP_UNCONFIRMED', diagnostic=str(diagnostic))
            events.emit({'status': 'check_cleanup_unconfirmed', 'error': failure, 'message': failure['message'],
                         'checker_pid': process.pid, 'diagnostic_log': str(diagnostic)})
            raise typer.Exit(1)
        with diagnostic.open('a', encoding='utf-8') as raw:
            raw.write(remaining or '')
        for line in (remaining or '').splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if 'report' in item:
                report = item
        cleanup_confirmed = process is None or (report is not None and not report.get('cleanup_errors') and
            report.get('game_exit_code') == 0 and report.get('manager_exit_code') == 0 and
            report.get('subscriptions_after_stop') == 0)
        reason = 'SDK_CHECK_CANCELLED' if cleanup_confirmed else 'SDK_CHECK_CLEANUP_UNCONFIRMED'
        failure = error(reason, diagnostic=str(diagnostic))
        events.emit({'status': 'check_rejected' if cleanup_confirmed else 'check_cleanup_unconfirmed',
                     'error': failure, 'message': failure['message'], 'report': report.get('report') if report else None,
                     'cleanup_confirmed': cleanup_confirmed, 'duration_seconds': time.perf_counter() - started})
        if cleanup_confirmed and report:
            console.print(text('check_cancel_report', report=report['report']), markup=False)
        if not cleanup_confirmed:
            raise typer.Exit(1)
        raise typer.Exit(130)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exception:
        with diagnostic.open('a', encoding='utf-8') as raw:
            raw.write(repr(exception) + '\n')
        failure = error('SDK_CHECK_FAILED', diagnostic=str(diagnostic))
        events.emit({'status': 'check_failed', 'error': failure, 'message': failure['message'],
                     'diagnostic_log': str(diagnostic), 'exc_info': True})
        raise typer.Exit(1)
    finally:
        events.emit({'status': 'check_timing', 'duration_seconds': time.perf_counter() - started,
                     'diagnostic_log': str(diagnostic)})
        events.close()


@app.command(help=text('start'))
@logged_command('start')
def start(name: Annotated[Optional[str], typer.Argument(help=text('project_argument'), autocompletion=complete_projects)] = None, background: bool = typer.Option(False, help=text('start_background')), map: Optional[str] = typer.Option(None, help=text('map_option'),
                                                                     autocompletion=complete_maps)):
    saved = settings(); name, source, native = select_project(saved, name)
    from xalkit_runtime import start_session
    if not native:
        adapter = json.loads((source / 'mod.json').read_text(encoding='utf-8')).get('native_adapter')
        if adapter:
            if adapter != 'bank-selector':
                raise ValueError(text('native_adapter_unknown', adapter=str(adapter)))
            return start_session(saved, name, source, map, background=background, native_adapter=adapter)
    start_session(saved, name, source, map, resources=not native, background=background)


def main():
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        app(prog_name='xkit')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        console.print(text('failure', reason=str(error)), style='red', markup=False)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
