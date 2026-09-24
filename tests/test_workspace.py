"""Workspace portability: no game, GUI or network required."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

DEVKIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEVKIT / 'scripts'))
from workspace import game_installation, workspace_root


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


if __name__ == '__main__':
    unittest.main()
