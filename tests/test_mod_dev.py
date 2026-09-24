"""Filesystem integration fixtures; no installed game, network, clock or GUI required.

Axes: boundary/encoding, absence, repeat calls, external edits, dependency failure,
concurrent ownership and input preservation. Scale/permissions/locale are not game
acceptance tests; generated invariants and GUI automation are not configured here.
"""
import importlib.util
import json
from unittest.mock import patch
import struct
from pathlib import Path
import tempfile
import unittest
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZipInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
SPEC = importlib.util.spec_from_file_location('mod_dev', Path(__file__).resolve().parents[1] / 'scripts/mod-dev.py')
mod_dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod_dev)
from object_reference import tier_armies, render_armies
RESOURCE = 'UI/MainMenu2/Version.txt'
SOURCE_DATE = (2107, 12, 31, 23, 59, 56)  # Matches the installed Universe timestamp boundary.
EXPECTED_DATE = (2107, 12, 31, 23, 59, 58)  # Last ZIP two-second slot must beat the source.


class ModDevelopmentTests(unittest.TestCase):
    def test_polygon_road_keeps_ground_and_valid_nested_layer_lengths(self):
        specification = importlib.util.spec_from_file_location(
            'test_map_generator', Path(__file__).resolve().parents[1] / 'scripts/test-map.py')
        generator = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(generator)
        for size in (8, 96):
            data = generator.blank_terrain(size)
            self.assertEqual(struct.unpack_from('<I', data, 7)[0], 2 * len(data) - 33)
            self.assertEqual(struct.unpack_from('<I', data, 12)[0], 2 * len(data) - 43)
            self.assertEqual(data[28], 4)
            container_end = 33 + (struct.unpack_from('<I', data, 29)[0] - 1) // 2
            self.assertEqual(struct.unpack_from('<I', data, 35)[0], 2)
            offset = 39
            for name in (b'Grass.xdb', b'StoneRoad.xdb'):
                self.assertEqual(data[offset], 1)
                layer_end = offset + 5 + (struct.unpack_from('<I', data, offset + 1)[0] - 1) // 2
                self.assertIn(name, data[offset:layer_end])
                count = (size + 1) ** 2
                self.assertEqual(data[offset + 27:offset + 27 + count], b'\xff' * count)
                offset = layer_end
            self.assertEqual(offset, container_end)
            # The layer container must end exactly before the independent height plane.
            self.assertEqual(data[offset], 5)
            self.assertEqual(struct.unpack_from('<f', data, offset + 22)[0], 2.0)

    def test_reference_sums_slots_without_adding_mutually_exclusive_branches(self):
        bank = ET.fromstring('''<Bank><Variants><Item><ChanceOfVariant>100</ChanceOfVariant>
          <Creatures>
            <Item><Creature1>A</Creature1><Creature2>A</Creature2>
              <ChanceOfCreature1>50</ChanceOfCreature1><ChanceOfCreature2>50</ChanceOfCreature2>
              <MinCount1>10</MinCount1><MaxCount1>15</MaxCount1>
              <MinCount2>20</MinCount2><MaxCount2>25</MaxCount2></Item>
            <Item><Creature1>A</Creature1><Creature2>B</Creature2>
              <ChanceOfCreature1>50</ChanceOfCreature1><ChanceOfCreature2>50</ChanceOfCreature2>
              <MinCount1>3</MinCount1><MaxCount1>5</MaxCount1>
              <MinCount2>7</MinCount2><MaxCount2>9</MaxCount2></Item>
          </Creatures></Item></Variants></Bank>''')
        self.assertEqual(tier_armies(bank, [2]), [(2, [
            ((('A', 10, 15, 50), ('A', 20, 25, 50)), 1),
            ((('A', 3, 5, 50), ('B', 7, 9, 50)), 1),
        ])])
        with self.assertRaisesRegex(ValueError, 'tier mapping'):
            tier_armies(bank, [1, 2])

    def test_reference_excludes_impossible_creatures_and_keeps_tiers_separate(self):
        bank = ET.fromstring('''<Bank><Variants>
          <Item><ChanceOfVariant>50</ChanceOfVariant><Creatures><Item>
            <Creature1>A</Creature1><Creature2>B</Creature2>
            <ChanceOfCreature1>100</ChanceOfCreature1><ChanceOfCreature2>0</ChanceOfCreature2>
            <MinCount1>10</MinCount1><MaxCount1>20</MaxCount1>
          </Item></Creatures></Item>
          <Item><ChanceOfVariant>50</ChanceOfVariant><Creatures><Item>
            <Creature1>A</Creature1><Creature2>C</Creature2>
            <ChanceOfCreature1>50</ChanceOfCreature1><ChanceOfCreature2>50</ChanceOfCreature2>
            <MinCount1>3</MinCount1><MaxCount1>5</MaxCount1>
            <MinCount2>8</MinCount2><MaxCount2>9</MaxCount2>
          </Item></Creatures></Item>
        </Variants></Bank>''')
        self.assertEqual(tier_armies(bank, [1, 2]), [
            (1, [((('A', 10, 20, 100),), 1)]),
            (2, [((('A', 3, 5, 50), ('C', 8, 9, 50)), 1)]),
        ])

    def test_ag_info_five_groups_of_three_are_fifteen_slots_and_ninety_minimum(self):
        slot = '''<Item><Creature1>A</Creature1><ChanceOfCreature1>100</ChanceOfCreature1>
          <MinCount1>6</MinCount1><MaxCount1>9</MaxCount1></Item>'''
        bank = ET.fromstring('<Bank><Variants><Item><ChanceOfVariant>100</ChanceOfVariant>'
                             '<Creatures>' + slot * 15 + '</Creatures></Item></Variants></Bank>')
        catalog = {
            'UI/AGINFO/05_01_SIZE.txt': ''.join(f'[{i}] A (3 stacks 6-9)\n' for i in range(1, 6)) + 'Reward',
            'Name.txt': 'A',
        }
        labels = {'army': 'Army', 'tier': 'T{tier}{variant}', 'bank_note': 'Unknown tier',
                  'reward_marker': 'Reward', 'stack_pattern': 'stacks',
                  'group': '{names}: {total_low}-{total_high} ({count} x {low}-{high})'}
        read = lambda path: b'\xff\xfe' + catalog[path].encode('utf-16-le')
        output = render_armies(bank, {'tiers': [1], 'info': '05'}, labels, read, {'A': 'Name.txt'})
        self.assertIn('A: 90-135 (15 x 6-9)', output)
        catalog['UI/AGINFO/05_01_SIZE.txt'] = '[1] A (3 stacks 6-9)\nReward'
        with self.assertRaisesRegex(ValueError, 'AG_INFO slot count'):
            render_armies(bank, {'tiers': [1], 'info': '05'}, labels, read, {'A': 'Name.txt'})

    def test_reference_build_preserves_text_and_tracks_catalog_changes(self):
        catalog = self.workshop.game / 'data/catalog.pak'
        description = 'Text/Gold.txt'
        def write_catalog(maximum):
            with ZipFile(catalog, 'w') as archive:
                archive.writestr('GameMechanics/RPGStats/DefaultStats.xdb', '<Stats><Banks/></Stats>')
                archive.writestr('MapObjects/Gold.xdb', f'''<AdvMapTreasureShared>
                  <Type>GOLD</Type><MinResource>5</MinResource><MaxResource>{maximum}</MaxResource>
                  <messagesFileRef><Item href="/Text/Name.txt"/><Item href="/{description}"/></messagesFileRef>
                </AdvMapTreasureShared>''')
                archive.writestr(description, b'\xff\xfe' + 'Original'.encode('utf-16-le'))
        write_catalog(10)
        recipe = json.loads(self.recipe.read_text())
        recipe.pop('append')
        recipe['object_reference'] = {
            'archives': ['catalog.pak'], 'labels': {'resource': '{low}-{high}'},
            'resources': {'GOLD': {'scale': 100}}, 'banks': {},
        }
        self.recipe.write_text(json.dumps(recipe), encoding='utf-8')
        self.workshop.build()
        with ZipFile(self.workshop.artifact) as archive:
            self.assertEqual(archive.namelist(), [description])
            self.assertEqual(archive.read(description).decode('utf-16'), 'Original\n\n500-1000')
        write_catalog(11)
        with self.assertRaisesRegex(ValueError, 'Source template changed'):
            self.workshop.deploy()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workshop = mod_dev.Workshop(self.root, 'menu-marker')
        game = self.workshop.game
        (game / 'data').mkdir(parents=True)
        (game / 'bin').mkdir()
        (game / 'UserMODs').mkdir()
        for name in ('H5_Game.exe', 'uni.dll', 'um.dll', 'd3d9.dll'):
            (game / 'bin' / name).write_bytes(name.encode('ascii'))
        self.original = b'\xff\xfe' + '<h4>Fixture'.encode('utf-16-le')
        with ZipFile(game / 'data/Universe_mod.pak', 'w') as archive:
            archive.writestr(ZipInfo(RESOURCE, SOURCE_DATE), self.original)
        self.workshop.source.mkdir(parents=True)
        self.recipe = self.workshop.source / 'mod.json'
        self.recipe.write_text(json.dumps({
            'id': 'menu-marker', 'source_archive': 'Universe_mod.pak',
            'source_path': RESOURCE, 'append': ' [DEV]',
        }), encoding='utf-8')

    def test_external_source_checkout_builds_without_workspace_recipe_copy(self):
        source = self.root / 'separate-checkout'
        source.mkdir()
        (source / 'mod.json').write_text(self.recipe.read_text(encoding='utf-8'), encoding='utf-8')
        self.recipe.unlink()
        workshop = mod_dev.Workshop(self.root, 'menu-marker', source=source)
        workshop.build()
        with ZipFile(workshop.artifact) as archive:
            self.assertEqual(archive.read(RESOURCE), self.original + ' [DEV]'.encode('utf-16-le'))
        mismatched = json.loads((source / 'mod.json').read_text(encoding='utf-8'))
        mismatched['id'] = 'different-mod'
        (source / 'mod.json').write_text(json.dumps(mismatched), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'selected mod ID'):
            workshop.build()

    def test_reference_window_recipe_can_own_its_catalog(self):
        recipe = json.loads(self.recipe.read_text(encoding='utf-8'))
        recipe['reference_windows'] = {'labels': {}}
        recipe['object_reference'] = {'archives': [], 'banks': {}}
        self.recipe.write_text(json.dumps(recipe), encoding='utf-8')
        with patch.object(mod_dev, 'compile_windows', return_value={'UI/Owned/reference.txt': b'owned'}) as compile_windows:
            self.workshop.build()
        self.assertEqual(compile_windows.call_args.args[2], recipe['object_reference'])
        with ZipFile(self.workshop.artifact) as archive:
            self.assertEqual(archive.namelist(), ['UI/Owned/reference.txt'])
            self.assertEqual(archive.read('UI/Owned/reference.txt'), b'owned')

    def test_build_preserves_utf16_and_uses_a_later_zip_timestamp(self):
        # Exercise
        self.workshop.build()
        # Verify: text is the resource being tested, not incidental UI copy.
        with ZipFile(self.workshop.artifact) as archive:
            self.assertEqual(archive.read(RESOURCE), self.original + ' [DEV]'.encode('utf-16-le'))
            self.assertEqual(archive.getinfo(RESOURCE).date_time, EXPECTED_DATE)

    def test_identical_inputs_produce_identical_archives(self):
        # Setup
        self.workshop.build()
        first = self.workshop.artifact.read_bytes()
        # Exercise
        self.workshop.build()
        # Verify
        self.assertEqual(self.workshop.artifact.read_bytes(), first)

    def test_deploy_then_rollback_preserves_unrelated_mods_and_source(self):
        # Setup
        unrelated = self.workshop.target.parent / 'other.h5u'
        unrelated.write_bytes(b'other-mod')
        original_archive = (self.workshop.game / 'data/Universe_mod.pak').read_bytes()
        self.workshop.build()
        # Exercise
        self.workshop.deploy()
        self.assertEqual(self.workshop.target.read_bytes(), self.workshop.artifact.read_bytes())
        self.workshop.deploy()
        self.workshop.rollback()
        self.workshop.rollback()
        # Verify
        self.assertFalse(self.workshop.target.exists())
        self.assertEqual(unrelated.read_bytes(), b'other-mod')
        self.assertEqual((self.workshop.game / 'data/Universe_mod.pak').read_bytes(), original_archive)

    def test_foreign_destination_is_neither_overwritten_nor_deleted(self):
        # Setup
        self.workshop.build()
        self.workshop.target.write_bytes(b'foreign')
        # Exercise + Verify
        with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
            self.workshop.deploy()
        with self.assertRaisesRegex(ValueError, 'refusing removal'):
            self.workshop.rollback()
        self.assertEqual(self.workshop.target.read_bytes(), b'foreign')

    def test_external_edit_after_deployment_blocks_rollback(self):
        # Setup
        self.workshop.build()
        self.workshop.deploy()
        self.workshop.target.write_bytes(b'edited')
        # Exercise + Verify
        with self.assertRaisesRegex(ValueError, 'changed externally'):
            self.workshop.rollback()
        self.assertEqual(self.workshop.target.read_bytes(), b'edited')

    def test_modified_artifact_is_rejected_before_installation(self):
        # Setup
        self.workshop.build()
        self.workshop.artifact.write_bytes(b'tampered')
        # Exercise + Verify
        with self.assertRaisesRegex(ValueError, 'Artifact changed'):
            self.workshop.deploy()
        self.assertFalse(self.workshop.target.exists())

    def test_changed_game_binary_is_rejected_before_installation(self):
        # Setup
        self.workshop.build()
        (self.workshop.game / 'bin/uni.dll').write_bytes(b'updated')
        # Exercise + Verify
        with self.assertRaisesRegex(ValueError, 'Game build changed'):
            self.workshop.deploy()
        self.assertFalse(self.workshop.target.exists())

    def test_recipe_cannot_read_an_archive_outside_game_data(self):
        # Setup
        recipe = json.loads(self.recipe.read_text(encoding='utf-8'))
        recipe['source_archive'] = '../outside.pak'
        self.recipe.write_text(json.dumps(recipe), encoding='utf-8')
        # Exercise + Verify
        with self.assertRaisesRegex(ValueError, 'Unsafe resource path'):
            self.workshop.build()
        self.assertFalse(self.workshop.artifact.exists())

    def test_changed_source_resource_is_rejected_with_unchanged_binaries(self):
        # Setup
        self.workshop.build()
        with ZipFile(self.workshop.game / 'data/Universe_mod.pak', 'w') as archive:
            archive.writestr(ZipInfo(RESOURCE, SOURCE_DATE), b'new-resource')
        # Exercise + Verify
        with self.assertRaisesRegex(ValueError, 'Source resource changed'):
            self.workshop.deploy()
        self.assertFalse(self.workshop.target.exists())

    def test_valid_overlay_is_packaged_without_changing_its_bytes(self):
        # Setup: non-UTF-8 text must remain in the encoding supplied by the author.
        files = self.workshop.source / 'files'
        files.mkdir()
        payload = b'\xff\xfe' + '<fixture/>'.encode('utf-16-le')
        (files / 'extra.xdb').write_bytes(payload)
        # Exercise
        self.workshop.build()
        # Verify
        with ZipFile(self.workshop.artifact) as archive:
            self.assertEqual(archive.read('extra.xdb'), payload)

    def test_overlay_cannot_duplicate_the_marker_with_different_case(self):
        # Setup
        files = self.workshop.source / 'files/ui/mainmenu2'
        files.mkdir(parents=True)
        (files / 'version.txt').write_bytes(b'duplicate')
        # Exercise + Verify
        with self.assertRaisesRegex(ValueError, 'Duplicate resource'):
            self.workshop.build()
        self.assertFalse(self.workshop.artifact.exists())

    def test_invalid_xml_overlay_does_not_replace_the_previous_build(self):
        # Setup
        self.workshop.build()
        original = self.workshop.artifact.read_bytes()
        files = self.workshop.source / 'files'
        files.mkdir()
        (files / 'broken.xdb').write_bytes(b'<unclosed>')
        # Exercise + Verify
        with self.assertRaises(mod_dev.ET.ParseError):
            self.workshop.build()
        self.assertEqual(self.workshop.artifact.read_bytes(), original)

    def test_pending_installation_can_be_rolled_back(self):
        # Setup: interrupted installer wrote the artifact but not its final journal.
        self.workshop.build()
        artifact = self.workshop.artifact.read_bytes()
        self.workshop.target.write_bytes(artifact)
        mod_dev.write_json(self.workshop.state_path, {
            'status': 'pending', 'owned_hashes': [mod_dev.digest(artifact)],
        })
        # Exercise
        self.workshop.rollback()
        # Verify
        self.assertFalse(self.workshop.target.exists())
        self.assertFalse(self.workshop.state_path.exists())

    def test_second_writer_cannot_acquire_the_workspace_lock(self):
        # Setup
        with mod_dev.exclusive(self.workshop.local):
            # Exercise + Verify
            with self.assertRaises(FileExistsError):
                with mod_dev.exclusive(self.workshop.local):
                    self.fail('Second writer entered the protected section.')

    def test_cloned_xml_keeps_references_to_its_original_directory(self):
        # Setup: relative dependencies must survive relocating the cloned UI template.
        original = b'<Window ObjectRecordID="42"><Name>Old</Name><Shared href="Style.xdb#xpointer(/Style)"/></Window>'
        # Exercise
        output = mod_dev.patch_xml(original, 'UI/Original/Source.xdb', {'text': {'Name': 'New'}})
        # Verify
        document = mod_dev.ET.fromstring(output)
        self.assertEqual(document.find('Shared').get('href'), '/UI/Original/Style.xdb#xpointer(/Style)')
        self.assertEqual(document.findtext('Name'), 'New')
        self.assertNotIn('ObjectRecordID', document.attrib)

    def test_xml_patch_fails_if_a_template_field_disappears(self):
        # Setup
        original = b'<Window><Name>Old</Name></Window>'
        # Exercise + Verify
        with self.assertRaisesRegex(ValueError, 'Missing XML node'):
            mod_dev.patch_xml(original, 'UI/Source.xdb', {'text': {'Missing': 'New'}})

    def test_changed_template_in_another_archive_blocks_deployment(self):
        # Setup: the main source and cloned UI template belong to different archives.
        other_archive = self.workshop.game / 'data/templates.pak'
        with ZipFile(other_archive, 'w') as archive:
            archive.writestr('UI/Template.xdb', '<Window><Name>Old</Name></Window>')
        recipe = json.loads(self.recipe.read_text(encoding='utf-8'))
        recipe['xml_patches'] = [{
            'archive': 'templates.pak', 'source': 'UI/Template.xdb',
            'target': 'UI/Clone.xdb', 'text': {'Name': 'New'},
        }]
        self.recipe.write_text(json.dumps(recipe), encoding='utf-8')
        self.workshop.build()
        with ZipFile(other_archive, 'w') as archive:
            archive.writestr('UI/Template.xdb', '<Window><Name>Updated</Name></Window>')
        # Exercise + Verify
        with self.assertRaisesRegex(ValueError, 'Source template changed'):
            self.workshop.deploy()
        self.assertFalse(self.workshop.target.exists())


if __name__ == '__main__':
    unittest.main()
