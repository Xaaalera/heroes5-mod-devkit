"""Filesystem integration: shared immutable resources, snapshots and cleanup boundaries."""
import json
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import portalocker
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sdk_storage import GameAssets, unlink_readonly


class SharedGameStorage(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.installation = self.root / 'original-game'
        (self.installation / 'bin').mkdir(parents=True)
        (self.installation / 'bin/H5_Game.exe').write_bytes(b'fixture game')
        (self.installation / 'data').mkdir()
        self.resource = self.installation / 'data/resource.pak'
        self.resource.write_bytes(b'original resources')
        self.minimum = patch('sdk_storage.SHARED_MIN_BYTES', 1)
        self.minimum.start()
        self.closed = patch('sdk_storage.require_games_closed')
        self.closed.start()
        self.addCleanup(self.cleanup)
        self.assets = GameAssets(self.root, self.installation)

    def cleanup(self):
        self.closed.stop()
        self.minimum.stop()
        for directory, _, files in os.walk(self.root):
            for name in files:
                path = Path(directory) / name
                if os.name == 'nt' and path.stat().st_file_attributes & 1:
                    unlink_readonly(path)
        self.temporary.cleanup()

    def sandbox(self, name, modified=1, owned=True):
        workspace = self.root / '.local/test-state' / name / 'workspace'
        game = workspace / '.local/test-game'
        (game / 'bin').mkdir(parents=True)
        (game / 'bin/H5_Game.exe').write_bytes(b'fixture game')
        (game / 'data').mkdir()
        self.assets.copy(self.resource, game / 'data/resource.pak')
        (game / 'Maps').mkdir()
        (game / 'Maps/unique.h5m').write_bytes(name.encode())
        if owned:
            marker = workspace / '.local/test-state/prepared.json'
            marker.parent.mkdir()
            marker.write_text(json.dumps({'status': 'complete', 'game': str(game), 'profile': 'WorkshopDev'}))
            os.utime(marker, (modified, modified))
        return workspace, game

    def test_two_copies_share_private_cache_but_never_original(self):
        _, first = self.sandbox('first')
        _, second = self.sandbox('second')
        first_resource, second_resource = first / 'data/resource.pak', second / 'data/resource.pak'
        self.assertTrue(os.path.samefile(first_resource, second_resource))
        self.assertFalse(os.path.samefile(first_resource, self.resource))
        self.resource.write_bytes(b'changed original!')
        self.assertEqual(first_resource.read_bytes(), b'original resources')
        if os.name == 'nt':
            with self.assertRaises(PermissionError):
                first_resource.write_bytes(b'accidental shared write')

    def test_mutable_files_remain_independent(self):
        profile = self.installation / 'profiles/input.cfg'
        profile.parent.mkdir()
        profile.write_bytes(b'original input')
        destination = self.root / '.local/test-game/input.cfg'
        destination.parent.mkdir(parents=True)
        self.assets.copy(profile, destination)
        destination.write_bytes(b'test input')
        self.assertEqual(profile.read_bytes(), b'original input')

    def test_unmarked_installation_is_not_adopted_or_retired(self):
        _, game = self.sandbox('unmarked', owned=False)
        self.assertEqual(self.assets.games(), [])
        self.assets.clean(keep=0)
        self.assertTrue(game.exists())

    @unittest.skipUnless(os.name == 'nt', 'NTFS junction refusal is Windows-specific')
    def test_retirement_and_recursive_cleanup_refuse_real_directory_junctions(self):
        for operation in ('retire', 'remove_tree'):
            with self.subTest(operation=operation):
                workspace, game = self.sandbox('junction-' + operation)
                target = self.root / ('outside-game-' + operation)
                target.mkdir()
                sentinel = target / 'keep.txt'
                sentinel.write_bytes(b'outside contents must survive')
                junction = game / 'outside-link'
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)],
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                try:
                    self.assertTrue(junction.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
                    with self.assertRaisesRegex(ValueError, 'reparse point'):
                        if operation == 'retire':
                            self.assets.clean(keep=0)
                        else:
                            self.assets.remove_tree(game)
                    self.assertEqual(sentinel.read_bytes(), b'outside contents must survive')
                    self.assertTrue((game / 'bin/H5_Game.exe').exists())
                    self.assertTrue((workspace / '.local/test-state/prepared.json').exists())
                finally:
                    # Remove only the junction entry; never traverse its target.
                    junction.rmdir()

    def test_retirement_preserves_maps_and_shared_resource_manifest(self):
        for index in range(4):
            self.sandbox(str(index), modified=index+1)
        result = self.assets.clean(keep=3)
        self.assertEqual(result['kept'], 3)
        self.assertEqual(len(result['retired']), 1)
        retired = result['retired'][0]
        self.assertFalse(Path(retired['game']).exists())
        with ZipFile(retired['archive']) as package:
            self.assertEqual(package.read('Maps/unique.h5m'), b'0')
            metadata = json.loads(package.read('sandbox-storage.json'))
            reference = metadata['shared_assets']['data/resource.pak']
        self.assertEqual((self.assets.cache / reference['sha256']).read_bytes(), b'original resources')
        self.assertEqual(self.resource.read_bytes(), b'original resources')
        self.assertEqual(len(self.assets.games()), 3)
        self.assets.clean(keep=2)
        restored = self.assets.restore(Path(retired['archive']))
        restored_game = Path(restored['game'])
        self.assertEqual((restored_game / 'Maps/unique.h5m').read_bytes(), b'0')
        self.assertEqual((restored_game / 'data/resource.pak').read_bytes(), b'original resources')
        self.assertEqual(len(self.assets.games()), 3)
        self.assets.clean(keep=0)
        self.assertFalse(restored_game.exists())
        self.assertEqual(len(list(Path(retired['archive']).parent.glob('retired-game*.zip'))), 2)

    def test_busy_storage_never_deletes_installations(self):
        _, game = self.sandbox('locked')
        lock = self.assets.cache.parent / 'game-storage.lock'
        with portalocker.Lock(lock, mode='a', timeout=0):
            with self.assertRaises(FileExistsError):
                self.assets.clean(keep=0)
        self.assertTrue(game.exists())

    def test_changed_test_resource_is_preserved_in_snapshot(self):
        _, game = self.sandbox('custom')
        destination = game / 'data/resource.pak'
        unlink_readonly(destination)
        destination.write_bytes(b'custom resource')
        result = self.assets.clean(keep=0)
        with ZipFile(result['retired'][0]['archive']) as package:
            self.assertEqual(package.read('data/resource.pak'), b'custom resource')

    def test_marker_pointing_to_original_is_rejected(self):
        workspace, game = self.sandbox('wrong-owner')
        marker = workspace / '.local/test-state/prepared.json'
        metadata = json.loads(marker.read_text())
        metadata['game'] = str(self.installation)
        marker.write_text(json.dumps(metadata))
        self.assertEqual(self.assets.clean(keep=0)['retired'], [])
        self.assertTrue(game.exists())

    def test_configured_original_is_excluded_even_with_a_valid_marker(self):
        original = self.root / '.local/test-game'
        original.parent.mkdir(exist_ok=True)
        self.installation.rename(original)
        marker = self.root / '.local/test-state/prepared.json'
        marker.parent.mkdir()
        marker.write_text(json.dumps({'status': 'complete', 'game': str(original), 'profile': 'WorkshopDev'}))
        assets = GameAssets(self.root, original)
        self.assertEqual(assets.games(), [])
        self.assertTrue((original / 'bin/H5_Game.exe').exists())

    def test_archive_failure_keeps_the_original_test_installation(self):
        _, game = self.sandbox('archive-failure')
        with patch.object(ZipFile, 'testzip', return_value='Maps/unique.h5m'):
            with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                self.assets.clean(keep=0)
        self.assertEqual((game / 'Maps/unique.h5m').read_bytes(), b'archive-failure')

    def test_locked_retirement_prevents_creating_a_fourth_installation(self):
        oldest, _ = self.sandbox('oldest', modified=1)
        self.sandbox('middle', modified=2)
        self.sandbox('newest', modified=3)
        (oldest / '.local/test-state/mod-dev.lock').write_text('busy')
        specification = importlib.util.spec_from_file_location(
            'storage_workshop_fixture', Path(__file__).resolve().parents[1] / 'scripts/mod-dev.py')
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        with patch.dict(os.environ, H5_GAME_DIR=str(self.installation)):
            workshop = module.Workshop(self.root, 'sample', True)
            with self.assertRaisesRegex(RuntimeError, 'other storage operation'):
                workshop.prepare()
        self.assertFalse(workshop.game.exists())
        self.assertEqual(len(self.assets.games()), 3)

    def test_low_space_is_reported_before_any_partial_game_is_created(self):
        specification = importlib.util.spec_from_file_location(
            'storage_space_fixture', Path(__file__).resolve().parents[1] / 'scripts/mod-dev.py')
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        from xalkit_ui import CommandFailure
        with patch.dict(os.environ, H5_GAME_DIR=str(self.installation)), \
                patch.object(module.shutil, 'disk_usage', return_value=type('Space', (), {'free': 0})()):
            workshop = module.Workshop(self.root, 'sample', True)
            with self.assertRaises(CommandFailure) as caught:
                workshop.prepare()
        self.assertEqual(caught.exception.payload['details'][0]['reason'], 'SDK_STORAGE_LOW_SPACE')
        self.assertFalse(workshop.game.exists())

    def test_old_dumps_are_losslessly_archived_and_restorable(self):
        directory = self.root / '.local/test-state/dumps'
        directory.mkdir(parents=True)
        originals = {}
        for index in range(4):
            path = directory / f'capture-{index}.dmp'
            payload = b'MDMP' + bytes([index]) * 4096
            path.write_bytes(payload)
            os.utime(path, (index + 1, index + 1))
            originals[path.name] = payload
        result = self.assets.clean(keep=0)
        self.assertEqual(len(result['compressed_dumps']), 2)
        self.assertEqual(len(list(directory.glob('*.dmp'))), 2)
        archive = directory / 'capture-0.dmp.gz'
        restored = self.assets.restore_dump(archive)
        self.assertEqual(restored.read_bytes(), originals['capture-0.dmp'])
        os.utime(restored, (1, 1))
        self.assets.clean(keep=0)
        self.assertEqual(len(list(directory.glob('*.dmp'))), 2)

    def test_dump_restore_rejects_an_archive_outside_the_storage_root(self):
        outside = self.installation / 'outside.dmp.gz'
        outside.write_bytes(b'not an archive')
        with self.assertRaisesRegex(ValueError, 'belong to this storage root'):
            self.assets.restore_dump(outside)


if __name__ == '__main__':
    unittest.main()
