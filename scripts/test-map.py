"""Build the owned Universe test polygon from local public game templates."""
import copy
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from workspace import workspace_root, game_installation

ROOT = workspace_root()
GAME = ROOT / '.local/test-game'
DATA = game_installation(ROOT) / 'data'
PREFIX = 'Maps/SingleMissions/WorkshopPolygon/'
SIZE = 96


def blank_terrain(size):
    """Encode the documented framed planes; no terrain copied from a map.

    Format reference: senyaak/homm5-editor, docs/TERRAIN_FORMAT.md.
    """
    vertex = size + 1
    count = vertex * vertex
    water = 2 * vertex - 1
    # A road is an overlay. Retain actual ground underneath for terrain typing.
    tiles = [b'/MapObjects/_(AdvMapTile)/Grass/Grass.xdb#xpointer(/AdvMapTile)',
             b'/MapObjects/_(AdvMapTile)/Grass/StoneRoad.xdb#xpointer(/AdvMapTile)']
    layers = bytearray()
    for tile in tiles:
        layers.extend(b'\x01' + struct.pack('<I', 2 * count + 2 * len(tile) + 53))
        layers.extend(b'\x02' + struct.pack('<I', 2 * count + 35) + b'\x01\x08')
        layers.extend(struct.pack('<I', vertex) + b'\x02\x08' + struct.pack('<I', vertex))
        layers.extend(b'\x03' + struct.pack('<I', 2 * count + 1) + b'\xff' * count)
        layers.extend(bytes([3, 2 * (len(tile) + 2), 3, 2 * len(tile)]) + tile)
    planes = [(5, vertex, struct.pack('<f', 2.0) * count),
              (7, vertex, b'\x10' * count),
              (8, vertex, bytes(count)), (10, water, bytes(water * water))]
    body = bytearray()
    for tag, dimension, payload in planes:
        body.extend(bytes([tag]) + struct.pack('<I', 2 * len(payload) + 35) + b'\x01')
        body.extend(b'\x08' + struct.pack('<I', dimension) + b'\x02\x08')
        body.extend(struct.pack('<I', dimension) + b'\x03' + struct.pack('<I', 2 * len(payload) + 1))
        body.extend(payload)
    for tag in [13, 14, 15, 16]:
        body.extend(bytes([tag]))
        body.extend(b'\x02\x00' if tag == 14 else b'\x18\x01\x08' + bytes(4) + b'\x02\x08' + bytes(4))
    body.extend(b'\x00\x00\x02\x00\x05\x00')
    header = b'\x04\x08' + struct.pack('<I', 4) + b'\x01' + bytes(4) + b'\x01' + bytes(4)
    header += b'\x02\x08' + struct.pack('<I', size) + b'\x03\x08' + struct.pack('<I', size)
    header += b'\x04' + struct.pack('<I', 2 * len(layers) + 13) + b'\x02\x08' + struct.pack('<I', len(tiles))
    result = bytearray(header + layers + body)
    struct.pack_into('<I', result, 7, 2 * len(result) - 33)
    struct.pack_into('<I', result, 12, 2 * len(result) - 43)
    assert len(result) == 240 + sum(map(len, tiles)) + 8 * count + water * water
    return bytes(result)


def set_value(parent, path, value):
    element = parent.find(path)
    if element is None:
        element = ET.SubElement(parent, path)
    element.text = str(value)
    return element


def build(arena_smoke=None, confirm_placement=False, solo_smoke=None, stage=False):
    if solo_smoke is not None and (arena_smoke is not None or confirm_placement):
        raise ValueError('Solo object combat cannot be combined with scripted arena combat')
    if confirm_placement and arena_smoke is None:
        raise ValueError('--confirm-placement requires --arena-smoke')
    running = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq H5_Game.exe', '/FO', 'CSV', '/NH'],
                             check=True, capture_output=True)
    if not stage and b'h5_game.exe' in running.stdout.lower():
        raise RuntimeError('Close the game before rebuilding the test map.')
    target = ROOT / '.local/staged-maps/WorkshopPolygon.h5m' if stage else GAME / 'Maps/WorkshopPolygon.h5m'
    journal = ROOT / ('.local/test-state/test-map-staged.json' if stage else '.local/test-state/test-map.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    journal.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        previous = json.loads(journal.read_text(encoding='utf-8')) if journal.exists() else {}
        if hashlib.sha256(target.read_bytes()).hexdigest() != previous.get('sha256'):
            raise RuntimeError('Refusing to replace an unowned or externally edited test map.')
    catalog = {}
    sources = {}
    with ZipFile(DATA / 'data.pak') as archive:
        template_bytes = archive.read('Maps/Multiplayer/A1M2/map.xdb')
    root = ET.fromstring(template_bytes)
    templates = {}
    for item in root.find('objects'):
        templates.setdefault(item[0].tag, copy.deepcopy(item))
    for filename in ['data.pak', 'a2p1-data.pak', 'Universe_mod.pak']:
        with ZipFile(DATA / filename) as archive:
            for name in archive.namelist():
                if name.startswith(('MapObjects/', 'GameMechanics/Creature/Creatures/')) and name.endswith('.xdb'):
                    data = archive.read(name)
                    catalog[name] = ET.fromstring(data)
                    sources[name] = {'archive': filename, 'sha256': hashlib.sha256(data).hexdigest()}
    objects = root.find('objects')
    objects.clear()
    placed = []
    consumed = {}
    hero_names = []
    armies = []

    def place(kind, shared, x, y, name, owner=None):
        definition = catalog[shared]
        item = copy.deepcopy(templates[kind])
        item.set('id', 'workshop_' + name)
        obj = item[0]
        for path, value in [('Pos/x', x), ('Pos/y', y), ('Pos/z', 0), ('Floor', 0), ('Rot', 0), ('Name', name)]:
            set_value(obj, path, value)
        obj.find('Shared').set('href', '/' + shared + '#xpointer(/' + definition.tag + ')')
        if owner:
            set_value(obj, 'PlayerID', owner)
        for element in obj.iter():
            if element.tag == 'FunctionName':
                element.text = ''
            href = element.get('href', '')
            if href and not href.startswith(('/', '#')):
                element.clear()
        objects.append(item)
        consumed[shared] = sources[shared]
        placed.append({'name': name, 'kind': kind, 'shared': shared, 'x': x, 'y': y})
        return item, obj

    towns = ['Heaven', 'Inferno', 'Necromancy', 'Preserve', 'Academy', 'Dungeon', 'Fortress', 'Orc_Stronghold']
    for index, faction in enumerate(towns):
        x, y = 14 + 22 * (index % 4), 17 + 24 * (index // 4)
        shared = 'MapObjects/' + faction + '.(AdvMapTownShared).xdb'
        item, town = place('AdvMapTown', shared, x, y, faction, 'PLAYER_1')
        town.find('Specialization').clear()
        town.find('GarrisonHero').clear()
        town.find('armySlots').clear()
        town.find('buildings').clear()
        for building in ['TB_TOWN_HALL', 'TB_TAVERN', 'TB_FORT', 'TB_DWELLING_1', 'TB_DWELLING_2']:
            row = ET.SubElement(town.find('buildings'), 'Item')
            for tag, value in [('Type', building), ('InitialUpgrade', 'BLD_UPG_1'), ('MaxUpgrade', 'BLD_UPG_5')]:
                set_value(row, tag, value)
        race = catalog[shared].findtext('Type')
        heroes = sorted(n for n, e in catalog.items() if e.tag == 'AdvMapHeroShared'
                        and e.findtext('TownType') == race and e.findtext('ScenarioHero') == 'false'
                        and e.findtext('HideInEditor') != 'true')
        if not heroes:
            raise RuntimeError('No regular hero for ' + race)
        hero_item, hero = place('AdvMapHero', heroes[0], x + 1, y - 7, 'hero_' + faction, 'PLAYER_1')
        set_value(hero, 'Experience', 0)
        hero.find('armySlots').clear()
        candidates = [(n, e) for n, e in catalog.items() if e.tag == 'Creature' and e.findtext('CreatureTown') == race]
        tier = max(int(e.findtext('CreatureTier', '0')) for _, e in candidates)
        highest = [(n, e) for n, e in candidates if int(e.findtext('CreatureTier', '0')) == tier
                   and e.findtext('BaseCreature') == 'CREATURE_UNKNOWN' and e.findtext('Upgrade') == 'false']
        if len(highest) != 1:
            raise RuntimeError('Expected one base creature at the highest tier for ' + race)
        creature_path, creature = highest[0]
        monster_path = creature.find('MonsterShared').get('href').split('#')[0].lstrip('/')
        creature_id = catalog[monster_path].findtext('Creature')
        if not creature_id or creature_id == 'CREATURE_UNKNOWN':
            raise RuntimeError('Missing creature identifier for ' + race)
        slot = ET.SubElement(hero.find('armySlots'), 'Item')
        set_value(slot, 'Creature', creature_id)
        set_value(slot, 'Count', 10)
        hero_names.append(catalog[heroes[0]].findtext('InternalName'))
        armies.append({'faction': faction, 'hero': hero_names[-1], 'creature': creature_id, 'tier': tier, 'count': 10})
        consumed[creature_path] = sources[creature_path]
        consumed[monster_path] = sources[monster_path]
        if index == 0:
            main_town = item.get('id')
            main_hero = hero_item.get('id')

    # Universe reuses old engine Type values for different public objects.
    # Pin the actual Universe/editor models; never choose alphabetically by Type.
    bank_paths = {
        'BUILDING_SUNKEN_TEMPLE': 'MapObjects/Universe_mod/Demonbank.(AdvMapBuildingShared).xdb',
        'BUILDING_DWARVEN_TREASURE': 'MapObjects/DwarvenTreasury.(AdvMapBuildingShared).xdb',
        'BUILDING_CYCLOPS_STOCKPILE': 'MapObjects/Elemantal_Stockpile.(AdvMapBuildingShared).xdb',
        'BUILDING_NAGA_BANK': 'MapObjects/MagiVault.xdb',
        'BUILDING_GARGOYLE_STONEVAULT': 'MapObjects/GargoyleStonevault.(AdvMapBuildingShared).xdb',
        'BUILDING_DRAGON_UTOPIA': 'MapObjects/Dragon_Utopia.(AdvMapBuildingShared).xdb',
        'BUILDING_CRYPT': 'MapObjects/Crypt.(AdvMapBuildingShared).xdb',
        'BUILDING_PYRAMID': 'MapObjects/Pyramid.(AdvMapBuildingShared).xdb',
        'BUILDING_UNKEMPT': 'MapObjects/Universe_mod/Monasterybank.(AdvMapBuildingShared).xdb',
        'BUILDING_DEMOLISH': 'MapObjects/Universe_mod/NecroEstate.(AdvMapBuildingShared).xdb',
        'BUILDING_TREANT_THICKET': 'MapObjects/TreantThicket.(AdvMapBuildingShared).xdb',
        'BUILDING_BLOOD_TEMPLE': 'MapObjects/WitchBank.(AdvMapBuildingShared).xdb',
    }
    for index, (kind, shared) in enumerate(bank_paths.items()):
        if catalog[shared].findtext('Type') != kind:
            raise RuntimeError('Universe bank model binding changed: ' + shared)
        place('AdvMapBuilding', shared, 12 + 14 * (index % 6), 60 + 12 * (index // 6), kind)
    for index, shared in enumerate(['Learning_Stone', 'Marletto_Tower']):
        place('AdvMapBuilding', 'MapObjects/' + shared + '.(AdvMapBuildingShared).xdb', 12 + index * 14, 85, shared)
    place('AdvMapBuilding', 'MapObjects/Universe_mod/EnchantedTreasure.(AdvMapBuildingShared).xdb',
          84, 85, 'EnchantedTreasure')
    treasure_paths = sorted(n for n, e in catalog.items() if e.tag == 'AdvMapTreasureShared'
                            and e.findtext('Type') in ['TREASURE_' + t for t in ['WOOD', 'ORE', 'MERCURY', 'CRYSTAL', 'SULFUR', 'GEMS', 'GOLD']])
    for index, shared in enumerate(treasure_paths):
        _, obj = place('AdvMapTreasure', shared, 40 + index * 6, 85, 'resource_' + str(index))
        set_value(obj, 'IsCustom', 'false')
        set_value(obj, 'Amount', 0)
    creature_models = {e.findtext('Creature'): n for n, e in sorted(catalog.items(), reverse=True)
                       if e.tag == 'AdvMapMonsterShared' and e.findtext('Creature') not in (None, 'CREATURE_UNKNOWN')}
    packs = [[('CREATURE_PEASANT', 30)], [('CREATURE_ARCHER', 40)],
             [('CREATURE_IMP', 60), ('CREATURE_CERBERI', 12)],
             [('CREATURE_SKELETON_ARCHER', 80), ('CREATURE_ZOMBIE', 30)]]
    for faction in towns:
        race = catalog['MapObjects/' + faction + '.(AdvMapTownShared).xdb'].findtext('Type')
        army = []
        for tier, amount in [(1, 80), (3, 35), (5, 12)]:
            candidates = [e for e in catalog.values() if e.tag == 'Creature' and e.findtext('CreatureTown') == race
                          and e.findtext('CreatureTier') == str(tier) and e.findtext('BaseCreature') == 'CREATURE_UNKNOWN'
                          and e.findtext('Upgrade') == 'false' and e.find('MonsterShared') is not None
                          and e.find('MonsterShared').get('href')]
            if len(candidates) != 1:
                raise RuntimeError('Ambiguous faction pack creature: ' + race + '/' + str(tier))
            shared = candidates[0].find('MonsterShared').get('href').split('#')[0].lstrip('/')
            army.append((catalog[shared].findtext('Creature'), amount))
        packs.append(army)
    packs.extend([
        [('CREATURE_FIRE_ELEMENTAL', 15), ('CREATURE_WATER_ELEMENTAL', 15), ('CREATURE_AIR_ELEMENTAL', 15), ('CREATURE_EARTH_ELEMENTAL', 15)],
        [('CREATURE_MARKSMAN', 35), ('CREATURE_GRAND_ELF', 25), ('CREATURE_SKELETON_ARCHER', 50)],
        [('CREATURE_BLACK_DRAGON', 3), ('CREATURE_ARCHANGEL', 3), ('CREATURE_SHADOW_DRAGON', 3)],
        [('CREATURE_FAMILIAR', 50), ('CREATURE_CERBERI', 30), ('CREATURE_INFERNAL_SUCCUBUS', 20), ('CREATURE_PIT_FIEND', 8),
         ('CREATURE_PEASANT', 40), ('CREATURE_ARCHER', 25), ('CREATURE_GRAND_ELF', 20)],
    ])
    for index, army in enumerate(packs):
        creature, count = army[0]
        _, obj = place('AdvMapMonster', creature_models[creature], 14 + index % 4 * 22,
                       [28, 48, 51, 54][index // 4], 'pack_' + str(index))
        for tag, value in [('Custom', 'true'), ('Amount', count), ('Amount2', 0), ('DoesNotGrow', 'true'),
                           ('DoesNotDependOnDifficulty', 'true'), ('MoveType', 'MOVE_STAND'),
                           ('Courage', 'MONSTER_COURAGE_ALWAYS_FIGHT')]:
            set_value(obj, tag, value)
        obj.find('AdditionalStacks').clear()
        obj.find('CombatScript').set('href', '/' + PREFIX + 'CombatScript.xdb#xpointer(/Script)')
        for creature, amount in army[1:]:
            if creature not in creature_models:
                raise RuntimeError('Missing mixed-pack creature: ' + creature)
            slot = ET.SubElement(obj.find('AdditionalStacks'), 'Item')
            for tag, value in [('Creature', creature), ('CustomAmount', 'true'), ('Amount', amount), ('Amount2', 0)]:
                set_value(slot, tag, value)
    # Same authored mixed army across six arenas isolates arena effects.
    arena_scenes = ['Grass_Big_01', 'Dirt_Small_01', 'Sand_Big_01',
                    'Snow_01', 'Lava_Small_01', 'River_Grass_Big_01']
    arena_cases = []
    for index, scene in enumerate(arena_scenes, 1):
        scene_path = 'Scenes/CombatArenas/' + scene + '.xdb'
        with ZipFile(DATA / 'data.pak') as archive:
            scene_bytes = archive.read(scene_path)
            scene_root = ET.fromstring(scene_bytes)
            scenery = scene_root.find('Scenery').get('href').split('#')[0].lstrip('/')
            archive.getinfo(scenery)
        consumed[scene_path] = {'archive': 'data.pak', 'sha256': hashlib.sha256(scene_bytes).hexdigest()}
        case_name = 'arena_' + str(index)
        place('AdvMapBuilding', 'MapObjects/Arena.(AdvMapBuildingShared).xdb',
              10 + 15 * (index - 1), 7, case_name)
        arena_cases.append({'name': case_name, 'scene': '/' + scene_path + '#xpointer(/AdventureFlybyScene)',
                            'army': packs[4], 'x': 10 + 15 * (index - 1), 'y': 7})
    blocked = {}
    entrances = []
    for obj in placed:
        definition = catalog[obj['shared']]
        for tile in definition.findall('blockedTiles/Item'):
            point = (obj['x'] + int(tile.findtext('x')), obj['y'] + int(tile.findtext('y')))
            if not (0 < point[0] < SIZE - 1 and 0 < point[1] < SIZE - 1) or (point in blocked and blocked[point] != obj['name']):
                raise RuntimeError('Overlapping or out-of-bounds footprint: ' + obj['name'] + ' at ' + str(point) + ' with ' + str(blocked.get(point)))
            blocked[point] = obj['name']
        for tile in definition.findall('activeTiles/Item'):
            entrances.append((obj['name'], (obj['x'] + int(tile.findtext('x')), obj['y'] + int(tile.findtext('y')))))
    reachable = {(15, 10)}
    queue = deque(reachable)
    while queue:
        x, y = queue.popleft()
        for point in [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]:
            if 0 < point[0] < SIZE - 1 and 0 < point[1] < SIZE - 1 and point not in blocked and point not in reachable:
                reachable.add(point)
                queue.append(point)
    for name, point in entrances:
        if point not in reachable:
            raise RuntimeError('Unreachable object entrance: ' + name)
    for tag in ['tiles', 'regions', 'MapScript', 'ScenarioInformation', 'Objectives', 'AvailableHeroes',
                'StartScene', 'dialogs', 'sRMGProps', 'ImportantArtifacts', 'MoonCalendarModifications', 'thumbnailImages']:
        root.find(tag).clear()
    for tag, value in [('TileX', SIZE), ('TileY', SIZE), ('HasUnderground', 'false'), ('InitialFloor', 0),
                       ('CustomGameMap', 'true'), ('CustomMapGoal', 'true'), ('RandomMoons', 'false')]:
        set_value(root, tag, value)
    root.find('GroundTerrainFileName').set('href', 'GroundTerrain.bin')
    root.find('UndergroundTerrainFileName').clear()
    root.find('NameFileRef').set('href', 'name.txt')
    root.find('DescriptionFileRef').set('href', 'description.txt')
    root.find('CustomGoal').set('href', 'description.txt')
    root.find('MapScript').set('href', 'MapScript.xdb#xpointer(/Script)')
    for index, player in enumerate(root.find('players')):
        for tag, value in [('ActivePlayer', str(index == 0).lower()), ('CanBeHumanPlayer', 'true'),
                           ('CanBeComputerPlayer', 'false'), ('HeroInTown', 'false'), ('CanBeDisabled', 'false'),
                           ('Race', 'TOWN_HEAVEN'), ('Colour', 'PCOLOR_RED' if index == 0 else 'PCOLOR_NEUTRAL')]:
            set_value(player, tag, value)
        for tag in ['MainTown', 'MainHero', 'StartHero', 'ReserveHeroes']:
            player.find(tag).clear()
        if index == 0:
            player.find('MainTown').set('href', '#xpointer(id(' + main_town + ')/AdvMapTown)')
            player.find('MainHero').set('href', '#xpointer(id(' + main_hero + ')/AdvMapHero)')
    for element in root.iter('FunctionName'):
        element.text = ''
    # A manual objective prevents a one-player test map completing immediately.
    original = ET.fromstring(template_bytes)
    objective = copy.deepcopy(original.find('Objectives/Primary/Common/Objectives/Item'))
    for tag, value in [('Name', 'workshop_testing'), ('Kind', 'OBJECTIVE_KIND_MANUAL'), ('InstantVictory', 'false')]:
        set_value(objective, tag, value)
    for tag in ['CaptionFileRef', 'DescriptionFileRef', 'ObscureCaptionFileRef']:
        objective.find(tag).set('href', 'description.txt')
    primary = ET.SubElement(root.find('Objectives'), 'Primary')
    common = ET.SubElement(primary, 'Common')
    ET.SubElement(common, 'Objectives').append(objective)
    description = 'Полигон / Test polygon: 8 фракций / factions, 12 хранилищ / banks, 16 паков / packs (14 смешанных / mixed), 6 тестовых арен / test arenas (y=7). Объекты: ряды y=60,72,85. Перезапуск сбрасывает тест / Relaunch resets the test.'
    script = 'OpenCircleFog(48, 48, 0, 150, PLAYER_1);\n'
    script += 'for resource = 0, 5 do SetPlayerResource(PLAYER_1, resource, 1000); end;\nSetPlayerResource(PLAYER_1, GOLD, 100000);\n'
    script += 'function workshop_movement()\n local heroes = {' + ','.join('"' + name + '"' for name in hero_names) + '}\n'
    script += ' while 1 do\n  for i = 1, 8 do\n   if IsHeroAlive(heroes[i]) then ChangeHeroStat(heroes[i], STAT_MOVE_POINTS, 9999999); end;\n  end;\n  sleep(5);\n end;\nend;\nstartThread(workshop_movement);\n'
    for case in arena_cases:
        arguments = ', '.join(creature + ', ' + str(amount) for creature, amount in case['army'])
        script += 'function workshop_' + case['name'] + '(hero)\n'
        script += ' StartCombat(hero, nil, ' + str(len(case['army'])) + ', ' + arguments
        script += ', "/' + PREFIX + 'CombatScript.xdb#xpointer(/Script)", nil, "' + case['scene'] + '", nil);\nend;\n'
        script += 'SetObjectEnabled("' + case['name'] + '", false);\n'
        script += 'Trigger(OBJECT_TOUCH_TRIGGER, "' + case['name'] + '", "workshop_' + case['name'] + '");\n'
    if arena_smoke is not None:
        script += 'function workshop_arena_smoke()\n sleep(20);\n workshop_arena_' + str(arena_smoke) + '("' + hero_names[0] + '");\nend;\nstartThread(workshop_arena_smoke);\n'
    if solo_smoke is not None:
        creature, amount = packs[solo_smoke][0]
        script += 'function workshop_solo_smoke()\n sleep(20);\n'
        script += ' StartCombat("' + hero_names[0] + '", nil, 1, ' + creature + ', ' + str(amount)
        script += ', "/' + PREFIX + 'CombatScript.xdb#xpointer(/Script)");\nend;\nstartThread(workshop_solo_smoke);\n'
    # Observation happens inside the actual fight, never as preview input.
    combat_script = ('function Start()\n'
                     ' local units = GetDefenderCreatures();\n'
                     ' local expected = {CREATURE_PEASANT, CREATURE_FOOTMAN, CREATURE_PRIEST};\n'
                     ' for _, creature in expected do\n'
                     ' for index, unit in units do\n'
                     ' if GetCreatureType(unit) == creature then\n'
                     '  local x, y = GetUnitPosition(unit);\n'
                     '  print("WORKSHOP_DEPLOYMENT", unit, GetCreatureType(unit), x, y);\n'
                     ' end;\n end;\n end;\nend;\n')
    if solo_smoke is not None or arena_smoke is None:
        combat_script = ('function Start()\n'
                         ' for index, unit in GetDefenderCreatures() do\n'
                         '  local x, y = GetUnitPosition(unit);\n'
                         '  print("WORKSHOP_SOLO", unit, GetCreatureType(unit), x, y);\n'
                         ' end;\nend;\n')
    if confirm_placement:
        combat_script += ('function workshop_confirm_placement()\n'
                          ' sleep(40);\n postEvent("confirm_placement");\nend;\n'
                          'startThread(workshop_confirm_placement);\n')
    files = {'map.xdb': ET.tostring(root, encoding='utf-8', xml_declaration=True),
             'GroundTerrain.bin': blank_terrain(SIZE),
             'name.txt': 'Полигон Universe / Workshop'.encode('utf-16'),
             'description.txt': description.encode('utf-16'),
             'MapScript.xdb': b'<Script><FileName href="MapScript.lua"/></Script>',
             'MapScript.lua': script.encode('ascii'),
             'CombatScript.xdb': b'<Script><FileName href="CombatScript.lua"/></Script>',
             'CombatScript.lua': combat_script.encode('ascii')}
    tag = ET.Element('AdvMapDescTag')
    for name, href in [('AdvMapDesc', 'map.xdb#xpointer(/AdvMapDesc)'), ('NameFileRef', 'name.txt'),
                       ('DescriptionFileRef', 'description.txt'), ('MapGoal', 'description.txt')]:
        ET.SubElement(tag, name, href=href)
    for name, value in [('TileX', SIZE), ('TileY', SIZE), ('HasUnderground', 'false'),
                        ('CustomGameMap', 'true'), ('RandomMap', 'false'), ('CustomMapGoal', 'true'), ('Version', 1)]:
        set_value(tag, name, value)
    ET.SubElement(ET.SubElement(tag, 'teams'), 'Item').text = '1'
    files['map-tag.xdb'] = ET.tostring(tag, encoding='utf-8', xml_declaration=True)
    for element in root.iter():
        href = element.get('href', '')
        if href and not href.startswith(('/', '#')) and href.split('#')[0] not in files:
            raise RuntimeError('Unresolved local reference: ' + href)
    temporary = target.with_suffix('.tmp')
    with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
        for name, data in files.items():
            entry = ZipInfo(PREFIX + name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, data)
    temporary.replace(target)
    report = {'map': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
              'size': SIZE, 'objects': placed, 'sources': consumed, 'armies': armies, 'packs': packs,
              'arena_cases': arena_cases, 'arena_smoke': arena_smoke,
              'solo_smoke': solo_smoke,
              'test_movement_refill': 9999999,
              'confirm_placement': confirm_placement,
              'template_sha256': hashlib.sha256(template_bytes).hexdigest(),
              'unmapped_banks': ['BUILDING_ORC_DEPOSIT'], 'in_game': 'not_verified'}
    journal.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'map': str(target), 'objects': len(placed), 'unmapped_banks': report['unmapped_banks']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arena-smoke', type=int, choices=range(1, 7),
                        help='Automatically enter one authored arena case after map startup.')
    parser.add_argument('--confirm-placement', action='store_true',
                        help='Probe the native confirm_placement event in an automatic arena case.')
    parser.add_argument('--solo-smoke', type=int, choices=(0, 1),
                        help='Automatically start a scripted single-type peasant or archer control battle.')
    parser.add_argument('--stage', action='store_true',
                        help='Build outside the sandbox without replacing a map used by a running game.')
    arguments = parser.parse_args()
    build(arguments.arena_smoke, arguments.confirm_placement, arguments.solo_smoke, arguments.stage)
