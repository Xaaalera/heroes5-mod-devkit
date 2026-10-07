"""Compile existing gettext UI strings into standard Windows string resources."""
import argparse
import gettext
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
options = parser.parse_args()
keys = ('console_button', 'console_logs', 'console_commands', 'console_modules',
        'console_empty', 'console_help', 'console_pending_commands', 'console_pending_modules', 'console_clear', 'console_filter', 'console_connection_lost', 'console_run', 'console_input_too_long', 'console_complete', 'console_history', 'console_no_matches', 'console_close', 'console_input_hint', 'console_completion_pending', 'console_unknown_command',
        'module_loading', 'module_active', 'module_stopped', 'module_failed', 'module_unconfirmed', 'module_unknown')
lines = ['#pragma code_page(65001)', '#include <windows.h>']
for language, language_id in (('en', 'LANG_ENGLISH'), ('ru', 'LANG_RUSSIAN')):
    catalog = gettext.translation('xalkit', localedir=Path(__file__).resolve().parents[1] / 'locale',
                                  languages=[language])
    lines.extend(['LANGUAGE ' + language_id + ', SUBLANG_DEFAULT', 'STRINGTABLE', 'BEGIN'])
    for number, key in enumerate(keys, 100):
        value = catalog.gettext('xalkit.' + key)
        if value == 'xalkit.' + key:
            raise ValueError('Missing native UI translation: ' + key)
        lines.append(str(number) + ' "' + value.replace('"', '""').replace('\n', '\\n') + '"')
    lines.append('END')
options.output.parent.mkdir(parents=True, exist_ok=True)
options.output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
