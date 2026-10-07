"""Share immutable game assets and retire owned, closed test installations."""
import ctypes
from contextlib import contextmanager
import hashlib
import gzip
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading
import uuid
import zlib
from zipfile import ZipFile, ZIP_DEFLATED
import portalocker


MAX_SANDBOXES = 3
SHARED_EXTENSIONS = {'.pak', '.ogg', '.mp3', '.wav', '.bik'}
SHARED_MIN_BYTES = 8 * 1024 * 1024


def require_games_closed():
    if os.name == 'nt':
        for name in ('H5_Game.exe', 'H5_MapEditor.exe'):
            result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + name, '/FO', 'CSV', '/NH'],
                                    capture_output=True, check=True, timeout=10)
            if name.lower().encode() in result.stdout.lower():
                raise RuntimeError('Close Heroes V and its editor before changing test storage.')


def file_digest(path, opener=open):
    digest = hashlib.sha256()
    with opener(path, 'rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def unlink_readonly(path):
    """Delete this link without changing attributes of its other hard links."""
    path = Path(path)
    if os.name != 'nt' or not path.stat().st_file_attributes & 1:
        path.unlink()
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x10000, 7, None, 3, 0x200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # FILE_DISPOSITION_INFO_EX: DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE.
        flags = wintypes.DWORD(0x13)
        if not kernel.SetFileInformationByHandle(handle, 21, ctypes.byref(flags), ctypes.sizeof(flags)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


class GameAssets:
    def __init__(self, workspace, installation):
        self.workspace = Path(workspace).resolve()
        self.installation = Path(installation).resolve()
        parent = self.installation.parent
        self.root = parent if self.workspace.is_relative_to(parent) else self.workspace
        self.cache = self.root / '.local/game-asset-cache'
        if self.cache.resolve() != self.cache or self.cache.is_relative_to(self.installation):
            raise ValueError('Unsafe shared game cache')
        self.source_hashes = {}
        self.validated_cache = set()
        self.shared_files = {}
        self.lock_thread = None

    @contextmanager
    def locked(self):
        thread = threading.get_ident()
        if self.lock_thread == thread:
            yield
            return
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        if self.cache.parent.resolve() != self.cache.parent:
            raise ValueError('Unsafe storage directory')
        lock = self.cache.parent / 'game-storage.lock'
        lease = portalocker.Lock(lock, mode='a', timeout=0)
        try:
            stream = lease.acquire()
        except portalocker.exceptions.LockException as failure:
            raise FileExistsError('Another game storage operation is running') from failure
        self.lock_thread = thread
        try:
            stream.seek(0)
            stream.truncate()
            stream.write(str(os.getpid()))
            stream.flush()
            yield
        finally:
            self.lock_thread = None
            lease.release()

    def required_space(self):
        total = 16 * 1024 * 1024
        for directory in ('bin', 'data', 'profiles', 'music', 'video', 'hwcursors'):
            for source in (self.installation / directory).rglob('*'):
                if source.is_file() and (not self.shared(source) or
                                         not (self.cache / self.source_digest(source)).exists()):
                    total += source.stat().st_size
        return total

    def record_owner(self, game):
        marker = self.workspace / '.local/test-state/sandbox-owner.json'
        if marker.parent.resolve() != marker.parent or game != self.workspace / '.local/test-game':
            raise ValueError('Unsafe sandbox ownership marker')
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'version': 1, 'kind': 'sdk-test-installation',
            'game': str(game), 'installation': str(self.installation),
            'game_sha256': file_digest(self.installation / 'bin/H5_Game.exe')}), encoding='utf-8')

    def shared(self, source):
        relative = source.relative_to(self.installation)
        return (relative.parts[0] in ('data', 'music', 'video', 'hwcursors') and
                source.suffix.lower() in SHARED_EXTENSIONS and source.stat().st_size >= SHARED_MIN_BYTES)

    def source_digest(self, source):
        identity = source.stat()
        key = (source, identity.st_size, identity.st_mtime_ns)
        if key not in self.source_hashes:
            self.source_hashes[key] = file_digest(source)
        return self.source_hashes[key]

    def copy(self, source, destination):
        source, destination = Path(source), Path(destination)
        if not self.shared(source):
            return shutil.copy2(source, destination)
        self.share(source, destination)
        return str(destination)

    def share(self, source, destination):
        if (source.is_symlink() or destination.is_symlink() or
                not source.resolve().is_relative_to(self.installation)):
            raise ValueError('Shared game assets must be regular files')
        digest = self.source_digest(source)
        cached = self.cache / digest
        if destination.exists():
            if os.path.samefile(source, destination):
                raise ValueError('Test assets must not link to the original installation')
            if (not cached.exists() or not os.path.samefile(cached, destination)) and file_digest(destination) != digest:
                return False  # Preserve a deliberately changed test resource.
        self.cache.mkdir(parents=True, exist_ok=True)
        if not cached.exists():
            if destination.exists():
                try:
                    os.link(destination, cached)
                except FileExistsError:
                    pass
            else:
                temporary = self.cache / ('copy-' + uuid.uuid4().hex)
                try:
                    shutil.copyfile(source, temporary)
                    if file_digest(temporary) != digest:
                        raise RuntimeError('Game asset changed during copying')
                    try:
                        os.link(temporary, cached)
                    except FileExistsError:
                        pass
                finally:
                    temporary.unlink(missing_ok=True)
        identity = cached.stat()
        cache_identity = (digest, identity.st_ino, identity.st_size, identity.st_mtime_ns)
        if cached.is_symlink() or os.path.samefile(source, cached) or identity.st_size != source.stat().st_size:
            raise RuntimeError('Shared game cache integrity check failed')
        if cache_identity not in self.validated_cache:
            if file_digest(cached) != digest:
                raise RuntimeError('Shared game cache integrity check failed')
            self.validated_cache.add(cache_identity)
        cached.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        self.shared_files[source.relative_to(self.installation).as_posix()] = cached
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and os.path.samefile(cached, destination):
            return True
        temporary = destination.with_name(destination.name + '.link-' + uuid.uuid4().hex)
        try:
            os.link(cached, temporary)
            if destination.exists() and os.name == 'nt' and destination.stat().st_file_attributes & 1:
                unlink_readonly(destination)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                unlink_readonly(temporary)
        return True

    def games(self):
        local = self.root / '.local'
        found = []
        expected = file_digest(self.installation / 'bin/H5_Game.exe')
        for directory, children, _ in os.walk(local, followlinks=False):
            parent = Path(directory)
            children[:] = [name for name in children if name not in
                           ('native-analysis', 'node_modules', '.venv', 'tools', 'xkit', 'game-asset-cache') and
                           not (getattr((parent / name).lstat(), 'st_file_attributes', 0) & 0x400)]
            if parent.name != '.local' or 'test-game' not in children:
                continue
            children.remove('test-game')
            game = parent / 'test-game'
            executable = game / 'bin/H5_Game.exe'
            workspace = parent.parent
            marker = parent / 'test-state/prepared.json'
            owner_marker = parent / 'test-state/sandbox-owner.json'
            if game == self.installation or marker.resolve() != marker or owner_marker.resolve() != owner_marker:
                continue
            prepared = False
            owned = False
            try:
                if marker.is_file():
                    metadata = json.loads(marker.read_text(encoding='utf-8'))
                    prepared = (isinstance(metadata, dict) and metadata.get('status') == 'complete' and
                                metadata.get('game') == str(game) and metadata.get('profile') == 'WorkshopDev')
                if owner_marker.is_file():
                    metadata = json.loads(owner_marker.read_text(encoding='utf-8'))
                    owned = (isinstance(metadata, dict) and metadata.get('version') == 1 and
                             metadata.get('kind') == 'sdk-test-installation' and metadata.get('game') == str(game) and
                             metadata.get('installation') == str(self.installation) and metadata.get('game_sha256') == expected)
            except (ValueError, OSError):
                continue
            if not prepared and not owned:
                continue
            if executable.is_file() and file_digest(executable) == expected:
                found.append({'game': game, 'workspace': workspace, 'marker': marker,
                              'ready': prepared, 'modified': marker.stat().st_mtime if prepared else game.stat().st_mtime})
        return sorted(found, key=lambda entry: (entry['ready'], entry['modified']), reverse=True)

    def adopt(self, progress=None):
        with self.locked():
            return self._adopt(progress)

    def _adopt(self, progress=None):
        require_games_closed()
        games = self.games()
        resources = [path for directory in ('data', 'music', 'video', 'hwcursors')
                     for path in (self.installation / directory).rglob('*')
                     if path.is_file() and self.shared(path)]
        linked = 0
        for index, entry in enumerate(games, 1):
            game = entry['game']
            for source in resources:
                destination = game / source.relative_to(self.installation)
                if destination.is_file() and destination.resolve().is_relative_to(game):
                    linked += self.share(source, destination)
            if progress is not None:
                progress(index, len(games))
        return {'sandboxes': len(games), 'shared_files': linked}

    def clean(self, keep=MAX_SANDBOXES, progress=None):
        with self.locked():
            result = self._clean(keep, progress)
            result['compressed_dumps'] = self._compress_dumps()
            return result

    def _compress_dumps(self, keep=2):
        require_games_closed()
        dumps = []
        for directory, children, files in os.walk(self.root / '.local', followlinks=False):
            parent = Path(directory)
            children[:] = [name for name in children if name not in ('native-analysis', 'node_modules', 'tools') and
                           not (getattr((parent / name).lstat(), 'st_file_attributes', 0) & 0x400)]
            for name in files:
                path = parent / name
                if path.suffix == '.dmp' and path.resolve() == path:
                    with path.open('rb') as stream:
                        if stream.read(4) == b'MDMP':
                            dumps.append(path)
        dumps.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        compressed = []
        for path in dumps[keep:]:
            archive = path.with_suffix('.dmp.gz')
            provenance = path.with_suffix('.dmp.archive.json')
            if (archive.resolve() != archive or provenance.resolve() != provenance or
                    archive.is_symlink() or provenance.is_symlink()):
                raise ValueError('Unsafe dump archive or provenance path')
            if archive.exists():
                try:
                    record = json.loads(provenance.read_text(encoding='utf-8'))
                    if not isinstance(record, dict):
                        raise ValueError('Dump provenance must be an object')
                    identity = path.stat()
                    digest = file_digest(path)
                    if (record['source'] != str(path) or record['archive'] != str(archive) or
                            record['original_bytes'] != identity.st_size or record['sha256'] != digest or
                            file_digest(archive, gzip.open) != digest):
                        raise ValueError('Existing dump archive does not match')
                    if path.stat().st_mtime_ns != identity.st_mtime_ns:
                        raise ValueError('Raw dump changed during duplicate verification')
                except (ValueError, OSError, KeyError, TypeError, EOFError, zlib.error):
                    compressed.append({'source': str(path), 'skipped': 'existing_archive_unverified'})
                    continue
                unlink_readonly(path)
                compressed.append({**record, 'restored_duplicate_removed': True})
                continue
            identity = path.stat()
            temporary = archive.with_name(archive.name + '.tmp-' + uuid.uuid4().hex)
            digest = hashlib.sha256()
            try:
                with path.open('rb') as source, gzip.open(temporary, 'xb', compresslevel=1) as destination:
                    for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                        digest.update(block)
                        destination.write(block)
                check = hashlib.sha256()
                with gzip.open(temporary, 'rb') as source:
                    for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                        check.update(block)
                current = path.stat()
                if (check.digest() != digest.digest() or current.st_size != identity.st_size or
                        current.st_mtime_ns != identity.st_mtime_ns):
                    raise RuntimeError('Dump changed during compression; original retained')
                os.replace(temporary, archive)
                record = {'source': str(path), 'archive': str(archive), 'sha256': digest.hexdigest(),
                          'original_bytes': identity.st_size, 'compressed_bytes': archive.stat().st_size}
                metadata_temporary = provenance.with_name(provenance.name + '.tmp-' + uuid.uuid4().hex)
                try:
                    with metadata_temporary.open('x', encoding='utf-8') as output:
                        output.write(json.dumps(record, indent=2))
                    os.replace(metadata_temporary, provenance)
                finally:
                    metadata_temporary.unlink(missing_ok=True)
                unlink_readonly(path)
                compressed.append(record)
            finally:
                temporary.unlink(missing_ok=True)
        return compressed

    def restore_dump(self, archive):
        with self.locked():
            archive = Path(archive).resolve(strict=True)
            if not archive.is_relative_to(self.root / '.local') or not archive.name.endswith('.dmp.gz'):
                raise ValueError('Dump archive must belong to this storage root')
            destination = archive.with_suffix('')
            if destination.exists():
                raise ValueError('Dump already exists; not overwriting it')
            provenance = destination.with_suffix('.dmp.archive.json')
            if provenance.is_symlink() or provenance.resolve() != provenance:
                raise ValueError('Unsafe dump provenance path')
            record = json.loads(provenance.read_text(encoding='utf-8'))
            if record['source'] != str(destination) or record['archive'] != str(archive):
                raise ValueError('Dump archive provenance mismatch')
            if shutil.disk_usage(destination.parent).free < record['original_bytes']:
                raise RuntimeError('Not enough space to restore the dump')
            temporary = destination.with_name(destination.name + '.restore-' + uuid.uuid4().hex)
            digest = hashlib.sha256()
            try:
                with gzip.open(archive, 'rb') as source, temporary.open('xb') as output:
                    for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                        digest.update(block)
                        output.write(block)
                        if output.tell() > record['original_bytes']:
                            raise ValueError('Dump exceeds its recorded size')
                if temporary.stat().st_size != record['original_bytes'] or digest.hexdigest() != record['sha256']:
                    raise ValueError('Restored dump checksum mismatch')
                os.rename(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            return destination

    def _clean(self, keep, progress=None):
        if not 0 <= keep <= MAX_SANDBOXES:
            raise ValueError('Keep between zero and three test installations')
        require_games_closed()
        self.adopt(progress)
        games = self.games()
        retired = []
        for entry in games[keep:]:
            game, workspace = entry['game'], entry['workspace']
            if (workspace / '.local/test-state/mod-dev.lock').exists():
                continue
            if game.resolve() != game or not game.is_relative_to(self.root / '.local'):
                raise ValueError('Unsafe test installation cleanup path')
            archive = workspace / '.local/test-state/retired-game.zip'
            if archive.parent.resolve() != archive.parent:
                raise ValueError('Unsafe test archive directory')
            archive.parent.mkdir(parents=True, exist_ok=True)
            if archive.is_symlink():
                raise ValueError('Unsafe existing test archive')
            temporary = archive.with_suffix('.zip.tmp')
            if temporary.is_symlink():
                raise ValueError('Unsafe temporary test archive')
            references = {}
            if (game / 'sandbox-storage.json').exists():
                raise ValueError('Test installation contains a reserved storage manifest name')
            with ZipFile(temporary, 'w', ZIP_DEFLATED) as package:
                for directory, children, files in os.walk(game, followlinks=False):
                    parent = Path(directory)
                    for name in children + files:
                        if getattr((parent / name).lstat(), 'st_file_attributes', 0) & 0x400:
                            raise ValueError('Refuse cleanup through a reparse point')
                    for name in files:
                        path = parent / name
                        relative = path.relative_to(game).as_posix()
                        cached = self.shared_files.get(relative)
                        if cached is not None and cached.exists():
                            if os.path.samefile(path, cached):
                                references[path.relative_to(game).as_posix()] = {
                                    'sha256': cached.name, 'size': path.stat().st_size}
                                continue
                        package.write(path, path.relative_to(game).as_posix())
                package.writestr('sandbox-storage.json', json.dumps({
                    'version': 1, 'shared_cache': str(self.cache), 'shared_assets': references,
                    'game': str(game), 'workspace': str(workspace)}))
            with ZipFile(temporary) as package:
                if package.testzip() is not None:
                    raise RuntimeError('Retired test archive verification failed')
                for reference in references.values():
                    cached = self.cache / reference['sha256']
                    identity = cached.stat()
                    signature = (cached.name, identity.st_ino, identity.st_size, identity.st_mtime_ns)
                    if identity.st_size != reference['size'] or signature not in self.validated_cache:
                        raise RuntimeError('Retired test archive resource reference failed')
            if archive.exists():
                history = archive.with_name('retired-game-' + uuid.uuid4().hex + '.zip')
                os.rename(archive, history)
            os.replace(temporary, archive)
            require_games_closed()
            self.remove_tree(game)
            if entry['marker'].exists():
                os.replace(entry['marker'], entry['marker'].with_name('prepared.retired.json'))
            retired.append({'game': str(game), 'archive': str(archive)})
        return {'kept': len(games) - len(retired), 'retired': retired}

    def remove_tree(self, game):
        if (game == self.installation or game.resolve() != game or
                not game.is_relative_to(self.root / '.local') or game.parent.name != '.local' or
                not game.name.startswith('test-game')):
            raise ValueError('Unsafe test directory cleanup')
        for directory, children, files in os.walk(game, followlinks=False):
            for name in children + files:
                if getattr((Path(directory) / name).lstat(), 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Refuse cleanup through a reparse point')
        def remove_readonly(function, path, exception):
            if Path(path).is_file():
                unlink_readonly(path)
            else:
                raise exception[1]
        shutil.rmtree(game, onerror=remove_readonly)

    def restore(self, archive):
        with self.locked():
            require_games_closed()
            archive = Path(archive).resolve(strict=True)
            if not archive.is_relative_to(self.root / '.local'):
                raise ValueError('Snapshot must belong to this storage root')
            with ZipFile(archive) as package:
                metadata = json.loads(package.read('sandbox-storage.json'))
                workspace = Path(metadata['workspace'])
                game = Path(metadata['game'])
                if (metadata.get('version') != 1 or metadata.get('shared_cache') != str(self.cache) or
                        game != workspace / '.local/test-game' or game == self.installation or
                        not game.is_relative_to(self.root / '.local') or game.resolve() != game or
                        archive.parent != workspace / '.local/test-state' or
                        not archive.name.startswith('retired-game') or archive.suffix != '.zip' or game.exists()):
                    raise ValueError('Unsafe test snapshot restore destination')
                if len(self.games()) >= MAX_SANDBOXES:
                    raise RuntimeError('Retire another test installation before restoring this snapshot')
                members = [name for name in package.namelist() if name != 'sandbox-storage.json']
                references = metadata['shared_assets']
                for name in [*members, *references]:
                    relative = Path(name)
                    if (relative.is_absolute() or '..' in relative.parts or ':' in name or
                            not (game / name).resolve().is_relative_to(game)):
                        raise ValueError('Snapshot resource path escapes test installation')
                for reference in references.values():
                    digest = reference['sha256']
                    if len(digest) != 64 or any(character not in '0123456789abcdef' for character in digest):
                        raise ValueError('Invalid shared resource digest')
                    cached = self.cache / digest
                    if cached.is_symlink() or cached.stat().st_size != reference['size'] or file_digest(cached) != digest:
                        raise RuntimeError('Snapshot shared resource is unavailable or changed')
                if package.testzip() is not None:
                    raise RuntimeError('Snapshot archive verification failed')
                required = sum(package.getinfo(name).file_size for name in members) + 16 * 1024 * 1024
                if shutil.disk_usage(workspace).free < required:
                    raise RuntimeError('Not enough space to restore the test snapshot')
                staged = game.with_name('test-game.restore-' + uuid.uuid4().hex)
                staged.mkdir()
                try:
                    for name in members:
                        target = staged / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(package.read(name))
                    for name, reference in references.items():
                        target = staged / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.link(self.cache / reference['sha256'], target)
                    os.rename(staged, game)
                except BaseException:
                    self.remove_tree(staged)
                    raise
            marker = workspace / '.local/test-state/prepared.retired.json'
            if marker.exists():
                os.replace(marker, marker.with_name('prepared.json'))
                os.utime(marker.with_name('prepared.json'), None)
            return {'game': str(game), 'archive': str(archive)}
