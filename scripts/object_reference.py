"""Compile public object-reference text from shipped catalogs, never map instances."""
from collections import Counter
import json
import hashlib
import posixpath
import re
import xml.etree.ElementTree as ET


def tier_armies(bank, tiers):
    """Keep tiers/variants separate and group identical slots, not creature marginals."""
    variants = bank.findall('Variants/Item')
    if len(variants) != len(tiers):
        raise ValueError('Bank variants changed; verify the tier mapping: ' + bank.tag)
    armies = []
    for variant, tier in zip(variants, tiers):
        if int(variant.findtext('ChanceOfVariant', '0')) <= 0:
            continue
        slots = Counter()
        for slot in variant.findall('Creatures/Item'):
            branches = []
            for number in (1, 2):
                if int(slot.findtext(f'ChanceOfCreature{number}', '0')) > 0:
                    creature = slot.findtext(f'Creature{number}')
                    low = int(slot.findtext(f'MinCount{number}'))
                    high = int(slot.findtext(f'MaxCount{number}'))
                    if low < 0 or high < low:
                        raise ValueError('Invalid creature range')
                    branches.append((creature, low, high, int(slot.findtext(f'ChanceOfCreature{number}'))))
            if not branches:
                raise ValueError('Creature slot has no possible branch')
            slots[tuple(branches)] += 1
        army = (tier, list(slots.items()))
        if army not in armies:
            armies.append(army)
    return armies


def render_armies(bank, config, labels, read, monsters):
    armies = tier_armies(bank, config['tiers'])
    rows = [labels['army']]
    multiplicity = Counter(tier for tier, groups in armies)
    seen = Counter()
    for tier, groups in armies:
        reference = read(f"UI/AGINFO/{config['info']}_{tier:02d}_SIZE.txt").decode('utf-16')
        guard_text = re.sub('<[^>]*>', '', reference).split(labels['reward_marker'])[0]
        blocks = re.split(r'\[\d+\]', guard_text)[1:]
        expected_slots = sum(max([int(value) for value in re.findall(
            r'(\d+)\s+' + labels['stack_pattern'], block)] or [1]) for block in blocks)
        if expected_slots != sum(count for branches, count in groups):
            raise ValueError(f"AG_INFO slot count disagrees with {bank.tag}, tier {tier}")
        seen[tier] += 1
        variant = chr(96 + seen[tier]) if multiplicity[tier] > 1 else ''
        rows.append(labels['tier'].format(tier=tier, variant=variant))
        for branches, count in groups:
            names = [read(monsters[name]).decode('utf-16').strip() for name, low, high, chance in branches]
            bounds = {(low, high) for name, low, high, chance in branches}
            if len(bounds) == 1:
                low, high = next(iter(bounds))
                rows.append(labels['group'].format(
                    names=' / '.join(dict.fromkeys(names)), count=count, low=low, high=high,
                    total_low=count * low, total_high=count * high))
            else:
                # Different-sized substitutions cannot share a total for either species.
                alternatives = [labels['alternative'].format(name=name, low=branch[1], high=branch[2])
                                for name, branch in zip(names, branches)]
                rows.append(labels['different_group'].format(count=count, alternatives=' / '.join(alternatives)))
    rows.append(labels['bank_note'])
    return '\n'.join(rows)


def compile_reference(read, paths, config):
    """read resolves/catalog-hashes dependencies; return only modified text resources."""
    labels = config['labels']
    stats = read('GameMechanics/RPGStats/DefaultStats.xdb')
    if config.get('stats_sha256') and hashlib.sha256(stats).hexdigest() != config['stats_sha256']:
        raise ValueError('Bank catalog changed; verify the tier mapping before building.')
    banks = ET.fromstring(stats).find('Banks')
    monsters = {}
    buildings = []
    treasures = []
    for path in paths:
        if not path.lower().startswith('mapobjects/') or not path.lower().endswith('.xdb'):
            continue
        # Catalog definitions only: no Maps, saves or running-process access.
        if '/_(advmapobjectlink)/' in path.lower():
            continue
        root = ET.fromstring(read(path))
        if root.tag == 'AdvMapMonsterShared':
            monster = root.findtext('Creature')
            messages = root.findall('messagesFileRef/Item')
            if monster and messages:
                monsters[monster] = posixpath.normpath(posixpath.join(
                    posixpath.dirname(path), messages[0].get('href', ''))).lstrip('/')
        elif root.tag == 'AdvMapBuildingShared':
            buildings.append((path, root))
        elif root.tag == 'AdvMapTreasureShared' and root.findtext('Type') in config['resources']:
            treasures.append((path, root))
    output = {}

    def append_description(path, suffix):
        original = read(path).decode('utf-16')
        value = b'\xff\xfe' + (original.rstrip() + '\n\n' + suffix).encode('utf-16-le')
        if path in output and output[path] != value:
            raise ValueError('Conflicting reference text: ' + path)
        output[path] = value

    for path, root in treasures:
        resource = config['resources'][root.findtext('Type')]
        scale = resource['scale']
        low = int(root.findtext('MinResource')) * scale
        high = int(root.findtext('MaxResource')) * scale
        messages = root.findall('messagesFileRef/Item')
        description = posixpath.normpath(posixpath.join(
            posixpath.dirname(path), messages[1].get('href'))).lstrip('/')
        append_description(description, labels['resource'].format(low=low, high=high))

    descriptions = {}
    for path, root in buildings:
        bank_config = config['banks'].get(root.findtext('Type'))
        if not bank_config:
            continue
        messages = root.findall('messagesFileRef/Item')
        if len(messages) < 2 or not messages[1].get('href'):
            continue
        description = posixpath.normpath(posixpath.join(
            posixpath.dirname(path), messages[1].get('href'))).lstrip('/')
        descriptions[description] = bank_config
    for bank_config in config['banks'].values():
        for description in bank_config.get('descriptions', []):
            descriptions[description] = bank_config

    used_banks = set()
    for description, bank_config in descriptions.items():
        bank_name = bank_config['stats']
        append_description(description, render_armies(banks.find(bank_name), bank_config, labels, read, monsters))
        used_banks.add(bank_name)
    missing = {entry['stats'] for entry in config['banks'].values()} - used_banks
    if missing:
        raise ValueError('No matching bank definitions: ' + ', '.join(sorted(missing)))
    return output


def reference_catalog(read, paths, config):
    """Collect public names, icons, tier groups and title routes; no map instances."""
    stats = read('GameMechanics/RPGStats/DefaultStats.xdb')
    if hashlib.sha256(stats).hexdigest() != config['stats_sha256']:
        raise ValueError('Bank catalog changed; verify the tier mapping.')
    banks = ET.fromstring(stats).find('Banks')
    monsters, visuals, titles = {}, {}, {}
    for path in paths:
        if not path.startswith('mapobjects/') or not path.endswith('.xdb') or '/_(advmapobjectlink)/' in path:
            continue
        root = ET.fromstring(read(path))
        messages = root.findall('messagesFileRef/Item')
        if not messages or not messages[0].get('href'):
            continue
        name_path = posixpath.normpath(posixpath.join(posixpath.dirname(path), messages[0].get('href', ''))).lstrip('/')
        if root.tag == 'AdvMapMonsterShared':
            creature = root.findtext('Creature')
            monsters[creature] = name_path
        elif root.tag == 'AdvMapBuildingShared' and root.findtext('Type') in config['banks']:
            title = read(name_path).decode('utf-16').strip()
            family = config['banks'][root.findtext('Type')]['info']
            if title in titles and titles[title] != family:
                raise ValueError('Ambiguous public building title: ' + title)
            titles[title] = family
    # Creature mechanics link canonical creature IDs to their public visual assets.
    for path in paths:
        if path.startswith('gamemechanics/creature/') and path.endswith('.xdb'):
            root = ET.fromstring(read(path))
            monster = root.find('MonsterShared')
            if monster is None or not monster.get('href'):
                continue
            monster_path = posixpath.normpath(posixpath.join(posixpath.dirname(path), monster.get('href').split('#')[0])).lstrip('/')
            creature = ET.fromstring(read(monster_path)).findtext('Creature')
            visual = root.find('Visual')
            if creature and visual is not None and visual.get('href'):
                visual_path = posixpath.normpath(posixpath.join(posixpath.dirname(path), visual.get('href').split('#')[0])).lstrip('/')
                visual_root = ET.fromstring(read(visual_path))
                for tag in ('Icon32', 'Icon64', 'Icon128', 'Icon'):
                    icon = visual_root.find(tag)
                    if icon is not None and icon.get('href'):
                        texture_path = posixpath.normpath(posixpath.join(posixpath.dirname(visual_path), icon.get('href').split('#')[0])).lstrip('/')
                        texture = ET.fromstring(read(texture_path))
                        visuals[creature] = ('/' + texture_path + '#xpointer(/Texture)',
                                            int(texture.findtext('Width')), int(texture.findtext('Height')))
                        break
    families = {}
    for entry in config['banks'].values():
        bank = banks.find(entry['stats'])
        # Existing AG_INFO cross-check validates group multiplicity for every tier.
        render_armies(bank, entry, config['labels'], read, monsters)
        families[entry['info']] = tier_armies(bank, entry['tiers'])
    return families, titles, monsters, visuals


def compile_windows(read, paths, config, options, clone):
    """Build isolated bank windows with each complete army variant on its own row."""
    labels = options['labels']
    count_style = read(options['count_style_source']).decode('utf-16')
    colors = re.findall(r'<color=[^>]+>', count_style)
    if len(colors) != 1:
        raise ValueError('Expected one native neutral-range color in ' + options['count_style_source'])
    range_color = colors[0]
    families, titles, monsters, visuals = reference_catalog(read, paths, config)
    output, registrations = {}, []
    base = 'UI/Tooltips/CommonAdvObjTooltip/'

    def resource(source, target, **patch):
        output[target] = clone(read(source), source, patch)

    def child(path, kind):
        return f'<Item href="/{path}#xpointer(/{kind})"/>'

    def text(path, content, x, y, width, height, priority=0):
        output[path + '.txt'] = b'\xff\xfe' + content.encode('utf-16-le')
        resource(base + 'CreaturesNumber.(WindowTextView).xdb', path + '.xdb', text={
            'Name': path.rsplit('/', 1)[-1], 'ResizeOnTextSet': 'false', 'Priority': str(priority),
            'Placement/Position/First/x': str(x), 'Placement/Position/First/y': str(y),
            'Placement/Position/Second': 'true', 'Placement/Size/First/x': str(width),
            'Placement/Size/First/y': str(height), 'Placement/Size/Second': 'true',
            'Placement/VerAllign/First': 'EPA_LOW_END', 'Placement/VerAllign/Second': 'true',
            'Placement/HorAllign/First': 'EPA_LOW_END', 'Placement/HorAllign/Second': 'true',
        }, attributes={'TextFileRef': {'href': '/' + path + '.txt'}})
        return child(path + '.xdb', 'WindowTextView')

    for family, armies in families.items():
        prefix = f'UI/WorkshopArmy/{family}/'
        tiers = sorted({tier for tier, groups in armies})
        variant_counts = Counter(tier for tier, groups in armies)
        width = max(197, max(len(groups) * 36 + (16 if variant_counts[tier] > 1 else 0)
                             for tier, groups in armies) + 28)
        children = []
        y = 0
        for tier in tiers:
            children.append(text(prefix + f'Tier{tier}', labels['tier'].format(tier=tier), 0, y + 8, 26, 18))
            variants = [groups for number, groups in armies if number == tier]
            for variant, groups in enumerate(variants):
                x = 28
                if len(variants) > 1:
                    children.append(text(prefix + f'Variant{tier}_{variant}', chr(65 + variant), x, y + 8, 16, 18))
                    x += 16
                for group, (branches, count) in enumerate(groups):
                    cell = prefix + f'T{tier}V{variant}G{group}'
                    faces = []
                    percentages = []
                    for side, (creature, low, high, chance) in enumerate(branches):
                        if creature not in visuals:
                            raise ValueError('No public portrait for ' + creature)
                        icon = cell + f'S{side}'
                        split = len(branches) == 2
                        # Nine-slice backgrounds expose source rectangles in pixels.
                        # Draw the fill patch with no borders: a cropped source half.
                        background = ET.fromstring(read(base + 'Background.(BackgroundTiledTexture).xdb'))
                        background.attrib.pop('ObjectRecordID', None)
                        texture_href, texture_width, texture_height = visuals[creature]
                        background.find('Texture').set('href', texture_href)
                        for tile in background:
                            if tile.tag.startswith('r'):
                                for node in tile.iter():
                                    if len(node) == 0:
                                        node.text = '0'
                        tile = background.find('rF')
                        tile.find('Size/x').text = '12' if split else '24'
                        tile.find('Size/y').text = '24'
                        tile.find('Maps/x1').text = str(side * texture_width // 2 if split else 0)
                        tile.find('Maps/x2').text = str((side + 1) * texture_width // 2 if split else texture_width)
                        tile.find('Maps/y2').text = str(texture_height)
                        if split:
                            output[icon + 'Texture.xdb'] = ET.tostring(background, encoding='utf-8', xml_declaration=True)
                            background_kind = 'BackgroundTiledTexture'
                        else:
                            resource(base + 'NoCreatureTexture.xdb', icon + 'Texture.xdb',
                                     attributes={'Texture': {'href': texture_href}})
                            background_kind = 'BackgroundSimpleScallingTexture'
                        resource(base + 'CreatureIconPlace.(WindowSimpleShared).xdb', icon + 'Shared.xdb',
                                 attributes={'Background': {'href': '/' + icon + f'Texture.xdb#xpointer(/{background_kind})'}})
                        resource(base + 'CreateIconPlace.(WindowSimple).xdb', icon + '.xdb', text={
                            'Name': f'Portrait{side}', 'Placement/Position/First/x': str(4 + (side * 12 if split else 0)),
                            'Placement/Position/First/y': '4', 'Placement/Size/First/x': '12' if split else '24',
                            'Placement/Size/First/y': '24',
                        }, attributes={'Shared': {'href': '/' + icon + 'Shared.xdb#xpointer(/WindowSimpleShared)'}})
                        faces.append(child(icon + '.xdb', 'WindowSimple'))
                        if split:
                            percentages.append(text(icon + 'Chance', labels['chance'].format(chance=chance),
                                                    side * 16, 3, 16, 12, priority=10))
                    # The count is the total group envelope across alternatives,
                    # never a simultaneous count promised for each species.
                    low = min(branch[1] for branch in branches) * count
                    high = max(branch[2] for branch in branches) * count
                    amount = str(low) if low == high else f'{low}-{high}'
                    count_label = text(cell + 'Count', labels['count'].format(
                        amount=amount, range_color=range_color, size=10 if len(amount) <= 6 else 9),
                        0, 19, 32, 13, priority=11)
                    resource(base + 'CreatureFace.(WindowSimpleShared).xdb', cell + 'Shared.xdb',
                             clear=['Children'], append_xml={'Children': faces + percentages + [count_label]})
                    resource(base + 'CreatureFaces/1.(WindowSimple).xdb', cell + '.xdb', text={
                        'Name': cell.rsplit('/', 1)[-1], 'Placement/Position/First/x': str(x + 2),
                        'Placement/Position/First/y': str(y),
                        'Placement/Size/First/x': '32', 'Placement/Size/First/y': '32',
                        'Placement/Size/Second': 'true',
                    }, attributes={'Shared': {'href': '/' + cell + 'Shared.xdb#xpointer(/WindowSimpleShared)'}})
                    children.append(child(cell + '.xdb', 'WindowSimple'))
                    x += 36
                y += 36
        notes = []
        if any(len(branches) > 1 for tier, groups in armies for branches, count in groups):
            notes.append(labels['chance_note'])
        if any(count > 1 for count in variant_counts.values()):
            notes.append(labels['variant_note'])
        height = len(armies) * 36 + len(notes) * 16
        if notes:
            children.append(text(prefix + 'Note', '\n'.join(notes), 0, height - len(notes) * 16, width, len(notes) * 16))
        resource(base + 'ArmyWnd.(WindowSimpleShared).xdb', prefix + 'ArmyShared.xdb',
                 clear=['Children'], append_xml={'Children': children})
        resource(base + 'ArmyWnd.(WindowSimple).xdb', prefix + 'Army.xdb', text={
            'Name': 'WorkshopReferenceArmy', 'Visible': 'true', 'Placement/Size/First/x': str(width),
            'Placement/Size/First/y': str(height), 'Placement/Position/First/x': '0',
        }, attributes={'Shared': {'href': '/' + prefix + 'ArmyShared.xdb#xpointer(/WindowSimpleShared)'}})
        resource(base + 'MainWnd.(WindowGatheringShared).xdb', prefix + 'MainShared.xdb',
                 clear=['Children'], append_xml={'Children': [child(base + 'Info.(WindowGathering).xdb', 'WindowGathering'),
                                                            child(prefix + 'Army.xdb', 'WindowSimple')]})
        resource(base + 'MainWnd.(WindowGathering).xdb', prefix + 'Main.xdb', text={'Name': 'WorkshopBank' + family},
                 attributes={'Shared': {'href': '/' + prefix + 'MainShared.xdb#xpointer(/WindowGatheringShared)'}})
        registrations.append(f'<Item><ID>WORKSHOP_BANK_{family}</ID><Window href="/{prefix}Main.xdb#xpointer(/WindowGathering)"/></Item>')
    root = 'UI/UIGameRoot.(UIGameRoot).xdb'
    resource(root, root, append_xml={'typedWindows': registrations})
    routes = [{'title': title, 'family': family, 'window_id': 'WORKSHOP_BANK_' + family}
              for title, family in sorted(titles.items())]
    output['UI/WorkshopArmy/routes.json'] = json.dumps(routes, ensure_ascii=False).encode('utf-8')
    return output
