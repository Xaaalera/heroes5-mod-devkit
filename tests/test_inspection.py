"""Isolated archive fixtures: no installed game files are used."""
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

spec = importlib.util.spec_from_file_location('inspection', Path(__file__).resolve().parents[1] / 'inspect_universe.py')
inspection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspection)


class ArchiveInspectionTests(unittest.TestCase):
    def test_traversal_is_rejected_without_writing_outside_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / 'fixture.zip'
            with ZipFile(archive_path, 'w') as archive:
                archive.writestr('../outside.txt', 'must not escape')
            with ZipFile(archive_path) as archive:
                with self.assertRaises(ValueError):
                    inspection.extract_text(archive, archive.getinfo('../outside.txt'), root / 'output')
            self.assertFalse((root / 'outside.txt').exists())

    def test_utf16_game_text_is_readable_as_utf8_research_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / 'fixture.zip'
            original = '\u0413\u0435\u0440\u043e\u0438\r\r\nTest'
            with ZipFile(archive_path, 'w') as archive:
                archive.writestr('Text/name.txt', original.encode('utf-16'))
            with ZipFile(archive_path) as archive:
                inspection.extract_text(archive, archive.getinfo('Text/name.txt'), root / 'output')
            self.assertEqual((root / 'output/Text/name.txt').read_text(encoding='utf-8'), original.replace('\r\r\n', '\n'))

    def test_failed_rerun_cannot_leave_a_successful_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, output = root / 'game', root / 'output'
            (game / 'data').mkdir(parents=True)
            (game / 'bin').mkdir()
            for name in ['data.pak', 'a2p1-data.pak', 'texts.pak', 'a2p1-texts.pak', 'Universe_mod.pak', 'universe_mod_texts_ru.pak']:
                with ZipFile(game / 'data' / name, 'w') as archive:
                    if name == 'data.pak':
                        archive.writestr('GameMechanics/test.xdb', '<value>1</value>')
                    elif name == 'Universe_mod.pak':
                        archive.writestr('GameMechanics/test.xdb', '<value>2</value>')
            for name in ['H5_Game.exe', 'uni.dll', 'um.dll', 'd3d9.dll']:
                (game / 'bin' / name).write_bytes(b'fixture')
            with patch.object(inspection, 'GAME', game), patch.object(inspection, 'OUTPUT', output), redirect_stdout(io.StringIO()):
                inspection.main()
                initial = json.loads((output / 'inventory.json').read_text())
                self.assertEqual(initial['status'], 'complete')
                self.assertEqual(initial['diff']['Universe_mod.pak'], {'changed': 1})
                (game / 'bin/um.dll').unlink()
                with self.assertRaises(FileNotFoundError):
                    inspection.main()
                failed = json.loads((output / 'inventory.json').read_text())
                self.assertEqual(failed['status'], 'incomplete')
                self.assertNotIn('binaries', failed)


if __name__ == '__main__':
    unittest.main()
