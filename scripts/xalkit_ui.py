"""GNU gettext catalogs; Babel owns extraction/compilation tooling."""
from functools import lru_cache
import gettext
import os
from workspace import DEVKIT
from xalkit_config import read_settings

ERROR_REASONS = {
    'SDK_STORAGE_LOW_SPACE': (507, 'RESOURCE_EXHAUSTED', 'storage_low_space'),
    'SDK_RUNTIME_NOT_READY': (409, 'FAILED_PRECONDITION', 'runtime_not_ready'),
    'SDK_CONSOLE_COMMAND_USAGE': (400, 'INVALID_ARGUMENT', 'console_command_usage'),
    'SDK_CONSOLE_RESULT_LARGE': (413, 'RESOURCE_EXHAUSTED', 'console_result_large'),
    'SDK_CONSOLE_INVALID_REQUEST': (400, 'INVALID_ARGUMENT', 'console_invalid_request'),
    'SDK_CONSOLE_UNAUTHORIZED': (401, 'UNAUTHENTICATED', 'console_unauthorized'),
    'SDK_CONSOLE_NOT_READY': (409, 'FAILED_PRECONDITION', 'console_not_ready'),
    'SDK_GAME_COMMAND_FAILED': (409, 'FAILED_PRECONDITION', 'game_command_failed'),
    'SDK_GAME_MAP_NOT_FOUND': (404, 'NOT_FOUND', 'game_map_not_found'),
    'SDK_GAME_CAPTURE_FAILED': (409, 'FAILED_PRECONDITION', 'game_capture_failed'),
    'SDK_SESSION_FAILED': (500, 'INTERNAL', 'session_failed'),
    'SDK_CHECK_FAILED': (500, 'INTERNAL', 'check_failed'),
    'SDK_CHECK_CANCELLED': (499, 'CANCELLED', 'check_cancelled'),
    'SDK_CHECK_CLEANUP_UNCONFIRMED': (409, 'FAILED_PRECONDITION', 'check_cleanup_unconfirmed'),
}


class CommandFailure(RuntimeError):
    """Keep the registered public error across CLI and console boundaries."""
    def __init__(self, payload):
        super().__init__(payload['message'])
        self.payload = payload


def error(reason, **metadata):
    code, status, message_key = ERROR_REASONS[reason]
    return {'code': code, 'status': status, 'message': text(message_key, **metadata),
            'details': [{'@type': 'ErrorInfo', 'reason': reason, 'domain': 'heroes5.xalkit', 'metadata': metadata}]}


@lru_cache(maxsize=4)
def translations(language):
    english = gettext.translation('xalkit', localedir=DEVKIT / 'locale', languages=['en'])
    if language == 'en':
        return english
    selected = gettext.translation('xalkit', localedir=DEVKIT / 'locale', languages=[language], fallback=True)
    selected.add_fallback(english)
    return selected


def text(key, **values):
    language = os.environ.get('XALKIT_LANG')
    if not language:
        try:
            language = read_settings().get('language', 'ru')
        except (OSError, ValueError):
            language = 'ru'
    return translations(language).gettext('xalkit.' + key).format(**values)
