"""Build and reversibly install a local Universe resource mod (Python 3.10+)."""
import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile, ZipInfo, ZIP_DEFLATED
from object_reference import compile_reference, compile_windows
from game_launch import map_arguments


from workspace import workspace_root, game_installation

ROOT = workspace_root()
GAME_NAME = 'Heroes of Might and Magic 5 Tribes of the East'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def confined(root, relative):
    parts = PurePosixPath(relative.replace('\\', '/'))
    if parts.is_absolute() or '..' in parts.parts or ':' in relative:
        raise ValueError('Unsafe resource path: ' + relative)
    target = root.joinpath(*parts.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path leaves its root: ' + relative)
    return target


@contextmanager
def exclusive(directory):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / 'mod-dev.lock'
    with lock.open('x', encoding='ascii') as handle:
        handle.write(str(os.getpid()))
    try:
        yield
    finally:
        lock.unlink()


def require_stopped():
    if os.name != 'nt':
        raise RuntimeError('Game operations require Windows.')
    for name in ('H5_Game.exe', 'H5_MapEditor.exe'):
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq ' + name, '/FO', 'CSV', '/NH'],
            capture_output=True, check=True,
        )
        if name.lower().encode('ascii') in result.stdout.lower():
            raise RuntimeError('Close ' + name + ' before installing, launching or rolling back.')


def patch_xml(original, source_path, patch):
    """Clone a game UI template while preserving its original reference locations."""
    document = ET.fromstring(original)
    document.attrib.pop('ObjectRecordID', None)
    for element in document.iter():
        reference = element.get('href')
        if reference and not reference.startswith('/'):
            element.set('href', '/' + posixpath.normpath(posixpath.join(posixpath.dirname(source_path), reference)))
    for path in patch.get('clear', []):
        element = document.find(path)
        if element is None:
            raise ValueError('Missing XML node: ' + path)
        element.clear()
    for path, value in patch.get('text', {}).items():
        element = document.find(path)
        if element is None:
            raise ValueError('Missing XML node: ' + path)
        element.text = value
    for path, attributes in patch.get('attributes', {}).items():
        element = document.find(path)
        if element is None:
            raise ValueError('Missing XML node: ' + path)
        element.attrib.update(attributes)
    for path, fragments in patch.get('append_xml', {}).items():
        element = document.find(path)
        if element is None:
            raise ValueError('Missing XML node: ' + path)
        for fragment in fragments:
            element.append(ET.fromstring(fragment))
    return ET.tostring(document, encoding='utf-8', xml_declaration=True)


class Workshop:
    def __init__(self, root, mod, sandbox=False):
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', mod):
            raise ValueError('Mod ID must contain lowercase letters, digits and single hyphens.')
        self.root = root.resolve()
        self.baseline = game_installation(self.root)
        self.game = confined(self.root, '.local/test-game') if sandbox else self.baseline
        self.source = confined(self.root, 'mods/' + mod)
        self.local = confined(self.root, '.local/test-state' if sandbox else '.local')
        self.local.mkdir(parents=True, exist_ok=True)
        self.artifact = confined(self.local, mod + '.h5u')
        self.state_path = confined(self.local, mod + '.deployment.json')
        self.target = confined(self.game, 'UserMODs/workshop-' + mod + '.h5u')

    def prepare(self):
        if self.game == self.baseline:
            raise ValueError('prepare requires --sandbox.')
        if self.game.exists():
            raise ValueError('Test installation already exists; it will not be overwritten.')
        # Separate copies, not hardlinks: writes in the test game cannot alter baseline bytes.
        self.game.mkdir()
        for directory in ('bin', 'data', 'profiles', 'music', 'video', 'hwcursors'):
            source = confined(self.baseline, directory)
            if source.exists():
                shutil.copytree(source, self.game / directory)
        for directory in ('Maps', 'UserMODs', 'UserCampaigns', 'DuelPresets'):
            (self.game / directory).mkdir()
        splash = self.baseline / 'splasha2.bmp'
        if splash.exists():
            shutil.copy2(splash, self.game / splash.name)
        profiles = self.game / 'UniverseTeam/Universe Mod/Profiles'
        profile = profiles / 'WorkshopDev'
        shutil.copytree(self.game / 'profiles/default_profile', profile)
        (profiles / 'global_a2.cfg').write_text('setvar profile_name = WorkshopDev\n', encoding='ascii')
        settings = profile / 'user_a2.cfg'
        text = settings.read_text(encoding='utf-8')
        text = re.sub(r'(?m)^setvar gfx_fullscreen = .*$', 'setvar gfx_fullscreen = 0', text)
        settings.write_text(text, encoding='utf-8')
        write_json(self.local / 'prepared.json', {
            'status': 'complete', 'game': str(self.game), 'profile': 'WorkshopDev',
            'runtime_profile_isolation': 'not_yet_verified',
        })
        return {'game': str(self.game), 'profile': str(profile), 'status': 'prepared'}

    def build(self):
        recipe = json.loads((self.source / 'mod.json').read_text(encoding='utf-8'))
        if recipe['id'] != self.source.name:
            raise ValueError('Recipe ID must match its folder.')
        archive_path = confined(self.game / 'data', recipe['source_archive'])
        resource = recipe['source_path']
        confined(self.source, resource)
        with ZipFile(archive_path) as archive:
            member = archive.getinfo(resource)
            original = archive.read(member)
        changed = original
        if 'append' in recipe:
            if original.startswith(b'\xff\xfe'):
                changed = b'\xff\xfe' + (original[2:].decode('utf-16-le') + recipe['append']).encode('utf-16-le')
            else:
                changed = (original.decode('utf-8') + recipe['append']).encode('utf-8')
        stamp = datetime(*member.date_time) + timedelta(seconds=2)
        resources = {} if 'object_reference' in recipe or 'reference_windows' in recipe else {resource: changed}
        templates = []
        if 'object_reference' in recipe or 'reference_windows' in recipe:
            if 'reference_windows' in recipe:
                catalog_path = confined(self.root / 'mods', recipe['reference_windows']['catalog'] + '/mod.json')
                catalog_config = json.loads(catalog_path.read_text(encoding='utf-8'))['object_reference']
            else:
                catalog_config = recipe['object_reference']
            with ExitStack() as stack:
                catalogs = {}
                for name in catalog_config['archives']:
                    archive = stack.enter_context(ZipFile(confined(self.game / 'data', name)))
                    for member in archive.infolist():
                        if not member.is_dir():
                            catalogs[member.filename.casefold()] = (name, archive, member)
                consumed = set()

                def read_catalog(path):
                    nonlocal stamp
                    confined(self.source, path)
                    name, archive, member = catalogs[path.casefold()]
                    content = archive.read(member)
                    if path.casefold() not in consumed:
                        templates.append({'archive': name, 'path': member.filename,
                                          'sha256': digest(content)})
                        consumed.add(path.casefold())
                        stamp = max(stamp, datetime(*member.date_time) + timedelta(seconds=2))
                    return content

                if 'reference_windows' in recipe:
                    generated = compile_windows(read_catalog, sorted(catalogs), catalog_config,
                                                recipe['reference_windows'], patch_xml)
                else:
                    generated = compile_reference(read_catalog, sorted(catalogs), catalog_config)
                for path, content in generated.items():
                    if path.casefold() in {name.casefold() for name in resources}:
                        raise ValueError('Duplicate reference resource: ' + path)
                    resources[path] = content
        if stamp.year > 2107:
            raise ValueError('No later representable ZIP timestamp for this source.')
        targets = set()
        with ExitStack() as stack:
            archives = {recipe['source_archive']: stack.enter_context(ZipFile(archive_path))}
            for patch in recipe.get('xml_patches', []):
                archive_name = patch.get('archive', recipe['source_archive'])
                if archive_name not in archives:
                    archives[archive_name] = stack.enter_context(ZipFile(confined(self.game / 'data', archive_name)))
                archive = archives[archive_name]
                source = patch['source']
                target = patch.get('target', source)
                confined(self.source, source)
                confined(self.source, target)
                if target.casefold() in targets:
                    raise ValueError('Duplicate XML patch target: ' + target)
                targets.add(target.casefold())
                content = archive.read(source)
                templates.append({'archive': archive_name, 'path': source, 'sha256': digest(content)})
                resources[target] = patch_xml(content, source, patch)
                stamp = max(stamp, datetime(*archive.getinfo(source).date_time) + timedelta(seconds=2))
        if stamp.year > 2107:
            raise ValueError('No later representable ZIP timestamp for a template.')
        files = self.source / 'files'
        if files.exists():
            for path in sorted(files.rglob('*')):
                if path.is_symlink() or not path.resolve().is_relative_to(files.resolve()):
                    raise ValueError('Linked mod resources are unsupported.')
                if path.is_file():
                    name = path.relative_to(files).as_posix()
                    confined(files, name)
                    if name.casefold() in {item.casefold() for item in resources}:
                        raise ValueError('Duplicate resource: ' + name)
                    data = path.read_bytes()
                    if path.suffix.lower() in {'.xml', '.xdb'}:
                        ET.fromstring(data)
                    if path.suffix.lower() == '.txt' and recipe.get('encode_texts') == 'utf-16-le':
                        data = b'\xff\xfe' + data.decode('utf-8-sig').encode('utf-16-le')
                    resources[name] = data
        temporary = self.artifact.with_suffix('.tmp')
        with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as archive:
            for name, data in sorted(resources.items()):
                info = ZipInfo(name, stamp.timetuple()[:6])
                info.compress_type = ZIP_DEFLATED
                archive.writestr(info, data)
        with ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('Archive CRC verification failed.')
        os.replace(temporary, self.artifact)
        report = {
            'artifact_sha256': digest(self.artifact.read_bytes()),
            'source_archive': recipe['source_archive'], 'source_path': resource,
            'source_sha256': digest(original), 'member_timestamp': stamp.isoformat(),
            'templates': templates,
            'resources': list(sorted(resources)),
            'binaries': {name: digest((self.game / 'bin' / name).read_bytes())
                         for name in ('H5_Game.exe', 'uni.dll', 'um.dll', 'd3d9.dll')},
        }
        write_json(self.artifact.with_suffix('.build.json'), report)
        return report

    def deploy(self):
        data = self.artifact.read_bytes()
        report = json.loads(self.artifact.with_suffix('.build.json').read_text(encoding='utf-8'))
        if digest(data) != report['artifact_sha256']:
            raise ValueError('Artifact changed since build; rebuild it.')
        with ZipFile(confined(self.game / 'data', report['source_archive'])) as archive:
            if digest(archive.read(report['source_path'])) != report['source_sha256']:
                raise ValueError('Source resource changed; rebuild the mod.')
            for source, expected in report.get('source_members', {}).items():
                if digest(archive.read(source)) != expected:
                    raise ValueError('Source template changed; rebuild the mod.')
        with ExitStack() as stack:
            archives = {}
            for template in report.get('templates', []):
                name = template['archive']
                if name not in archives:
                    archives[name] = stack.enter_context(ZipFile(confined(self.game / 'data', name)))
                if digest(archives[name].read(template['path'])) != template['sha256']:
                    raise ValueError('Source template changed; rebuild the mod.')
        for name, expected in report['binaries'].items():
            if digest((self.game / 'bin' / name).read_bytes()) != expected:
                raise ValueError('Game build changed; rebuild the mod.')
        existing = json.loads(self.state_path.read_text(encoding='utf-8')) if self.state_path.exists() else None
        old_hash = digest(self.target.read_bytes()) if self.target.exists() else None
        if old_hash and (not existing or old_hash not in existing['owned_hashes']):
            raise ValueError('Destination belongs to another file or was edited; refusing overwrite.')
        owned = sorted(set(([old_hash] if old_hash else []) + [digest(data)]))
        # Journal before mutation allows rollback after an interrupted installation.
        state = {'status': 'pending', 'owned_hashes': owned, 'target': str(self.target)}
        write_json(self.state_path, state)
        self.target.parent.mkdir(exist_ok=True)
        temporary = self.target.with_suffix('.workshop-tmp')
        with temporary.open('xb') as handle:
            handle.write(data)
        os.replace(temporary, self.target)
        state['status'] = 'installed'
        state['owned_hashes'] = [digest(data)]
        write_json(self.state_path, state)
        return {'target': str(self.target), 'sha256': digest(data)}

    def rollback(self):
        if not self.state_path.exists():
            if self.target.exists():
                raise ValueError('No ownership record; refusing removal.')
            return {'status': 'already_absent'}
        state = json.loads(self.state_path.read_text(encoding='utf-8'))
        if self.target.exists():
            if digest(self.target.read_bytes()) not in state['owned_hashes']:
                raise ValueError('Installed file changed externally; refusing removal.')
            self.target.unlink()
        temporary = self.target.with_suffix('.workshop-tmp')
        if temporary.exists():
            if digest(temporary.read_bytes()) not in state['owned_hashes']:
                raise ValueError('Unknown temporary file; manual inspection required.')
            temporary.unlink()
        self.state_path.unlink()
        return {'status': 'removed'}

    def status(self):
        return {
            'artifact': str(self.artifact), 'built': self.artifact.exists(),
            'installed': self.target.exists(),
            'deployment': json.loads(self.state_path.read_text(encoding='utf-8'))
                          if self.state_path.exists() else None,
            'in_game_verification': 'manual; archive loading is not yet confirmed',
        }

    def launch(self, map_name=None):
        if self.game != self.baseline and not (self.local / 'prepared.json').exists():
            raise ValueError('Test installation is incomplete; preparation must finish before launch.')
        executable = self.game / 'bin' / 'H5_Game.exe'
        options = map_arguments(self.game, map_name) if map_name else []
        process = subprocess.Popen([str(executable), *options], cwd=executable.parent)
        return {'pid': process.pid, 'status': 'started', 'map_arguments': options, 'in_game_verification': 'not_performed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'build', 'deploy', 'launch', 'rollback', 'status', 'cycle'])
    parser.add_argument('--mod', default='menu-marker')
    parser.add_argument('--sandbox', action='store_true', help='Use the separate local test installation.')
    parser.add_argument('--launch', action='store_true', help='Launch the game after cycle deployment.')
    parser.add_argument('--menu', action='store_true', help='Skip automatic map startup in the sandbox.')
    parser.add_argument('--map', help='Experimental -advmap launch; sandbox map filename.')
    args = parser.parse_args()
    started = time.perf_counter()
    workshop = Workshop(ROOT, args.mod, args.sandbox)
    with exclusive(confined(ROOT, '.local')):
        try:
            if args.command in {'prepare', 'deploy', 'rollback', 'launch', 'cycle'}:
                require_stopped()
            if args.command == 'cycle':
                result = {'build': workshop.build(), 'deploy': workshop.deploy()}
                if args.launch:
                    result['launch'] = workshop.launch(args.map if args.sandbox and not args.menu else None)
            elif args.command == 'launch':
                result = workshop.launch(args.map if args.sandbox and not args.menu else None)
            else:
                result = getattr(workshop, args.command)()
            event = {'command': args.command, 'mod': args.mod, 'ok': True, 'result': result}
        except (OSError, ValueError, KeyError, RuntimeError, BadZipFile, ET.ParseError, subprocess.SubprocessError) as error:
            event = {'command': args.command, 'mod': args.mod, 'ok': False, 'error': str(error)}
        event['duration_seconds'] = round(time.perf_counter() - started, 3)
        event['at'] = datetime.now().isoformat()
        with (workshop.local / 'runs.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + '\n')
        print(json.dumps(event, ensure_ascii=True, indent=2))
        return 0 if event['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
