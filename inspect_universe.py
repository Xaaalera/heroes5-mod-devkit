"""Index local archives and extract readable Universe resources without editing the game."""
import csv
import hashlib
import json
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from zipfile import ZipFile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'scripts'))
from workspace import workspace_root, game_installation

ROOT = workspace_root()
GAME = game_installation(ROOT)
OUTPUT = ROOT / 'research'
TEXT_TYPES = {'.xdb', '.xml', '.lua', '.txt'}


def key(name):
    return name.replace('\\', '/').casefold()


def extract_text(archive, member, destination):
    target = (destination / member.filename.replace('\\', '/')).resolve()
    if not target.is_relative_to(destination.resolve()):
        raise ValueError('Archive path leaves output directory: ' + member.filename)
    data = archive.read(member)
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        text = data.decode('cp1251')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.replace('\r\r\n', '\n').replace('\r\n', '\n'), encoding='utf-8')


def main():
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / 'inventory.json').write_text(json.dumps({
        'status': 'incomplete',
        'game': str(GAME),
        'message': 'Inspection in progress or failed. Do not use the CSV or extraction as a completed snapshot.',
    }, indent=2), encoding='utf-8')
    with ExitStack() as stack:
        archives = {p.name: stack.enter_context(ZipFile(p)) for p in sorted((GAME / 'data').glob('*.pak'))}
        inventory = {}
        for name, archive in archives.items():
            members = [i for i in archive.infolist() if not i.is_dir()]
            inventory[name] = {
                'file_size': (GAME / 'data' / name).stat().st_size,
                'members': len(members),
                'extensions': dict(Counter(Path(i.filename).suffix.casefold() for i in members)),
                'roots': dict(Counter(i.filename.split('/')[0] for i in members)),
            }
        baseline = {}
        for name in ['data.pak', 'a2p1-data.pak', 'texts.pak', 'a2p1-texts.pak']:
            for member in archives[name].infolist():
                if member.is_dir():
                    continue
                previous = baseline.get(key(member.filename))
                if previous is None or member.date_time > previous[1].date_time:
                    baseline[key(member.filename)] = (name, member)
        rows = []
        for name in ['Universe_mod.pak', 'universe_mod_texts_ru.pak']:
            archive = archives[name]
            for member in archive.infolist():
                if member.is_dir():
                    continue
                previous = baseline.get(key(member.filename))
                status = 'added'
                if previous:
                    original = archives[previous[0]].read(previous[1])
                    status = 'same' if original == archive.read(member) else 'changed'
                rows.append([name, member.filename, status, previous[0] if previous else '', member.file_size, '-'.join(map(str, member.date_time))])
                if Path(member.filename).suffix.casefold() in TEXT_TYPES:
                    extract_text(archive, member, OUTPUT / 'unpacked' / Path(name).stem)
                    if previous:
                        extract_text(archives[previous[0]], previous[1], OUTPUT / 'unpacked' / 'baseline')
        with (OUTPUT / 'archive-diff.csv').open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(['archive', 'path', 'status', 'baseline_archive', 'bytes', 'member_date'])
            writer.writerows(rows)
        binaries = {}
        for name in ['H5_Game.exe', 'uni.dll', 'um.dll', 'd3d9.dll']:
            data = (GAME / 'bin' / name).read_bytes()
            binaries[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        result = {
            'status': 'complete',
            'game': str(GAME),
            'comparison_rule': 'Baseline archives only; newest member timestamp; equal timestamps keep first. Not a verified Universe runtime mount order.',
            'archives': inventory,
            'diff': {name: dict(Counter(row[2] for row in rows if row[0] == name)) for name in ['Universe_mod.pak', 'universe_mod_texts_ru.pak']},
            'binaries': binaries,
            'extraction': 'UTF-8 research copies; do not deploy these normalized files directly.',
        }
        (OUTPUT / 'inventory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result['diff'], indent=2))


if __name__ == '__main__':
    main()
