"""Console descriptions and display use the canonical Click game commands."""
import json
import re
from zipfile import ZipFile, BadZipFile

import typer
from xalkit_ui import text


def command_registry():
    from xalkit import game_app
    group = typer.main.get_command(game_app)
    commands = []
    for name, command in sorted(group.commands.items()):
        context = command.make_context(name, [], resilient_parsing=True)
        with context:
            usage = command.get_usage(context).removeprefix('Usage: ')
        commands.append({'name': name, 'description': command.help or '', 'usage': usage,
                         'parameters': [
                             {'name': parameter.name, 'type': parameter.type.name, 'required': parameter.required,
                              'options': list(getattr(parameter, 'opts', [])), 'default': parameter.default,
                              'help': getattr(parameter, 'help', '') or ''}
                             for parameter in command.params]})
    return commands


def creature_choices(workspace, incomplete=''):
    """Read public constant declarations as data, never import game scripts."""
    creatures = set()
    for archive in sorted((workspace / '.local/test-game/data').glob('*.pak')):
        try:
            with ZipFile(archive) as package:
                for entry in package.infolist():
                    if entry.filename.lower() != 'scripts/common.lua' or entry.file_size > 2000000:
                        continue
                    source = package.read(entry).decode('utf-8-sig', errors='replace')
                    for name, identifier in re.findall(r'(?m)^\s*(CREATURE_[A-Z0-9_]+)\s*=\s*(\d+)', source):
                        if int(identifier) > 0 and not name.endswith(('_COUNT', '_FIRST', '_LAST')):
                            creatures.add(name)
        except (OSError, BadZipFile):
            continue
    prefix = incomplete.casefold()
    return sorted(name for name in creatures if name.casefold().startswith(prefix) or
                  name.removeprefix('CREATURE_').casefold().startswith(prefix))


def command_help(name):
    lines = []
    for command in command_registry():
        if name and command['name'] != name:
            continue
        lines.extend([command['name'] + ' — ' + command['description'], '  ' + command['usage']])
        for parameter in command['parameters']:
            if parameter['help']:
                lines.append('  ' + parameter['name'] + ': ' + parameter['help'])
    return lines


def hero_choices(workspace, owner, incomplete=''):
    """Read the latest successful roster for this exact owner from a bounded journal tail."""
    path = workspace / '.local/xalkit/logs/events.jsonl'
    try:
        with path.open('rb') as journal:
            journal.seek(0, 2)
            offset = max(0, journal.tell() - 1024 * 1024)
            journal.seek(offset)
            if offset:
                journal.readline(65537)
            lines = journal.read(1024 * 1024).splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        if len(line) > 65536:
            continue
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict) or event.get('status') != 'game_request_completed' or event.get('action') != 'heroes':
            continue
        if any(event.get('game_' + key) != owner.get(key) for key in ('pid', 'created')):
            continue
        result = event.get('result')
        if isinstance(result, dict) and result.get('status') == 'completed' and isinstance(result.get('result'), str):
            return sorted({name for name in result['result'].split() if name.startswith(incomplete)})
    return []


def display_result(command, result):
    if not isinstance(result, dict):
        return [str(result)]
    if 'help_lines' in result:
        return result['help_lines']
    if command == 'trace' and isinstance(result.get('records'), list):
        lines = ['[' + record['level'].upper() + '] ' + record['module'] + ': ' + record['message']
                 for record in result['records']]
        lines.append(text('game_trace_summary', count=len(result['records']), cursor=result.get('cursor', 0),
                          dropped=result.get('dropped', 0)))
        return lines
    if command == 'heroes' and isinstance(result.get('result'), str):
        return [text('console_hero_list')] + result['result'].split()
    if command in ('map', 'restart') and isinstance(result.get('map'), str):
        return [text('game_map_dispatched', name=result['map'])]
    if command == 'menu' and result.get('menu_requested'):
        return [text('game_menu_dispatched')]
    if command in ('army', 'creature') and all(key in result for key in ('hero', 'creature', 'before', 'count')):
        return [text('console_army_result', **result)]
    if command == 'level' and all(key in result for key in ('hero', 'level')):
        return [text('console_level_result', **result)]
    if command == 'resource' and all(key in result for key in ('player', 'resource', 'before', 'amount')):
        return [text('console_resource_result', **result)]
    if command == 'teleport' and all(key in result for key in ('hero', 'x', 'y', 'floor')):
        return [text('console_position_result', **result)]
    if command == 'status':
        return [text('console_game_connected')]
    if command == 'screenshot' and isinstance(result.get('image_file'), str):
        return [text('console_screenshot_result', path=result['image_file'])]
    # Keep useful domain values; process identity and transport fields stay in the journal.
    visible = {key: value for key, value in result.items() if key not in
               ('pid', 'created', 'heartbeat', 'mailbox_status', 'command', 'note', 'status')}
    return [json.dumps(visible, ensure_ascii=False, indent=2)] if visible else [text('console_command_done')]
