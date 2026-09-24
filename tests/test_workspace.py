"""Workspace portability: no game, GUI or network required."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

DEVKIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEVKIT / 'scripts'))
from workspace import game_installation, workspace_root
from game_launch import map_arguments


class WorkspaceTests(unittest.TestCase):
    def test_default_locations_are_derived_from_checkout(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(workspace_root(), DEVKIT)
            self.assertEqual(game_installation(DEVKIT), DEVKIT / 'Heroes of Might and Magic 5 Tribes of the East')

    def test_external_workspace_and_game_do_not_follow_tool_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'mod workspace'
            baseline = root / 'separate game'
            with patch.dict(os.environ, {'H5_WORKSPACE': str(workspace), 'H5_GAME_DIR': str(baseline)}):
                self.assertEqual(workspace_root(), workspace.resolve())
                self.assertEqual(game_installation(workspace), baseline.resolve())
                environment = os.environ.copy()
                result = subprocess.run([sys.executable, str(DEVKIT / 'scripts/mod-dev.py'), 'status', '--sandbox'],
                                        cwd=root, env=environment, text=True, capture_output=True, check=True)
            report = json.loads(result.stdout)
            self.assertTrue(report['ok'])
            self.assertEqual(Path(report['result']['artifact']), workspace / '.local/test-state/menu-marker.h5u')
            self.assertFalse(baseline.exists())
            self.assertFalse((root / '.local').exists())

    def test_control_help_loads_its_probe_from_devkit_with_external_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, H5_WORKSPACE=directory)
            result = subprocess.run([sys.executable, str(DEVKIT / 'scripts/game_control.py'), '--help'],
                                    cwd=directory, env=environment, text=True, capture_output=True, check=True)
            self.assertIn('interact', result.stdout)
            self.assertIn('quit', result.stdout)
            self.assertFalse((Path(directory) / '.local').exists())


    def test_control_never_imports_modules_from_workspace_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / '.local/native-analysis'
            cache.mkdir(parents=True)
            for name in ['game_launch.py', 'game_control.py', 'workspace.py']:
                (cache / name).write_text("raise RuntimeError('workspace module must not execute')", encoding='utf-8')
            environment = dict(os.environ, H5_WORKSPACE=directory)
            result = subprocess.run([sys.executable, str(DEVKIT / 'scripts/game_control.py'), '--help'],
                                    cwd=directory, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('interact', result.stdout)

    def test_map_startup_requires_one_safe_descriptor(self):
        cases = [
            ('valid', ['Maps/SingleMissions/Demo/map.xdb'], True),
            ('missing', ['Maps/SingleMissions/Demo/name.txt'], False),
            ('ambiguous', ['Maps/A/map.xdb', 'Maps/B/map.xdb'], False),
            ('traversal', ['Maps/../outside/map.xdb'], False),
            ('console-separator', ['Maps/Demo;quit/map.xdb'], False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            (game / 'Maps').mkdir()
            for label, entries, accepted in cases:
                with self.subTest(label=label):
                    with ZipFile(game / 'Maps/Demo.h5m', 'w') as archive:
                        for entry in entries:
                            archive.writestr(entry, '<map/>')
                    if accepted:
                        self.assertEqual(map_arguments(game, 'Demo'), ['-advmap', entries[0]])
                    else:
                        with self.assertRaises(ValueError):
                            map_arguments(game, 'Demo')

    def test_map_filename_cannot_escape_maps_directory(self):
        for name in ['../Demo', 'nested/Demo', 'nested\\Demo', 'C:Demo', '..']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                map_arguments(Path('unused-game'), name)


if __name__ == '__main__':
    unittest.main()
