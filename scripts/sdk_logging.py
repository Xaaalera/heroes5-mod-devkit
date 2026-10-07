"""Structured SDK events with maintained process-safe file logging."""
import logging
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sys
import time
import uuid
import structlog
from concurrent_log_handler import ConcurrentRotatingFileHandler
from xalkit_ui import text


MESSAGES = {
    'game_initializing': 'log_game_initializing',
    'game_loop_ready': 'log_game_loop_ready',
    'sdk_runtime_applied': 'log_sdk_runtime_applied',
    'sdk_runtime_rejected': 'log_sdk_runtime_rejected',
    'background_budget_unavailable': 'log_background_budget_unavailable',
    'console_built': 'log_console_built',
    'console_applied': 'log_console_applied',
    'console_rejected': 'log_console_rejected',
    'console_build_failed': 'log_console_build_failed',
    'console_build_superseded': 'log_console_build_superseded',
    'supervising': 'log_supervising',
    'watching': 'log_watching',
    'plugin_discovered': 'log_plugin_discovered',
    'applied': 'log_applied',
    'core_built': 'log_core_built',
    'core_applied': 'log_core_applied',
    'core_update_completed': 'log_core_update_completed',
    'core_update_rejected': 'log_core_update_rejected',
    'core_rejected': 'log_core_rejected',
    'build_failed': 'log_build_failed',
    'core_build_failed': 'log_core_build_failed',
    'plugin_stopped': 'log_plugin_stopped',
    'plugin_failed': 'log_plugin_failed',
    'plugin_cleanup_unconfirmed': 'log_plugin_cleanup_unconfirmed',
    'reload_rejected': 'log_reload_rejected',
    'released': 'log_released',
    'command_rejected': 'log_command_rejected',
    'runtime_failed': 'log_runtime_failed',
    'game_started': 'log_game_started',
    'game_owned_before_resume': 'log_game_owned_before_resume',
    'game_launch_cancelled': 'log_game_launch_cancelled',
    'resource_deployed': 'log_resource_deployed',
    'session_report_saved': 'log_session_report_saved',
    'session_report_unavailable': 'log_session_report_unavailable',
    'windows_events_saved': 'log_windows_events_saved',
    'windows_events_unavailable': 'log_windows_events_unavailable',
    'session_stop_requested': 'log_session_stop_requested',
    'game_close_requested': 'log_game_close_requested',
    'game_closed': 'log_game_closed',
    'game_exited': 'log_game_exited',
    'sandbox_background_enabled': 'log_sandbox_background_enabled',
}
COMPILER_LOCATION = re.compile(
    r'^(.+?)\((\d+)(?:,(\d+))?\)\s*:\s*(fatal error|error|warning)\s+([A-Z]+\d+):\s*(.*)$')


def console_fields(logger, method, event):
    for diagnostic in event.get('diagnostics', []):
        location = f"{diagnostic['file']}:{diagnostic['line']}:{diagnostic['column'] or 1}"
        severity = 'error' if diagnostic['severity'] == 'fatal error' else diagnostic['severity']
        event['event'] += f"\n{location}: {severity}: {diagnostic['code']}: {diagnostic['message']}"
    visible = {'event', 'level', 'timestamp', 'plugin', 'stage', 'reason', 'diagnostic_log', 'archive',
               'duration_seconds'}
    result = {key: value for key, value in event.items() if key in visible}
    if isinstance(result.get('duration_seconds'), (float, int)):
        result['duration_seconds'] = round(result['duration_seconds'], 2)
    return result


class EventLog:
    def __init__(self, directory, console=False, session=None, plugin=None, instance=None, stream=None):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session = (session or structlog.contextvars.get_contextvars().get('session_id') or
                        os.environ.get('XALKIT_LOG_SESSION_ID') or uuid.uuid4().hex)
        self.context = {key: value for key, value in {'plugin': plugin, 'instance': instance}.items() if value}
        self.logger = logging.Logger('heroes5-sdk-' + uuid.uuid4().hex, logging.DEBUG)
        processors = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
        json_formatter = structlog.stdlib.ProcessorFormatter(
            processors=[*processors, structlog.processors.JSONRenderer(ensure_ascii=False)])
        journal = ConcurrentRotatingFileHandler(self.directory / 'events.jsonl',
                                               maxBytes=10 * 1024 * 1024, backupCount=5,
                                               use_gzip=True, encoding='utf-8')
        journal.setFormatter(json_formatter)
        self.logger.addHandler(journal)
        output = logging.StreamHandler(stream or sys.stdout)
        output.setFormatter(structlog.stdlib.ProcessorFormatter(processors=[
            *processors, console_fields, structlog.dev.ConsoleRenderer(colors=False)
        ]) if console else json_formatter)
        output.setLevel(logging.INFO if console else logging.DEBUG)
        self.logger.addHandler(output)
        self.bound = structlog.wrap_logger(self.logger, wrapper_class=structlog.stdlib.BoundLogger,
            processors=[structlog.contextvars.merge_contextvars, structlog.stdlib.add_log_level,
                        structlog.processors.MaybeTimeStamper(fmt='iso', utc=True),
                        structlog.processors.format_exc_info, structlog.stdlib.ProcessorFormatter.wrap_for_formatter])

    def emit(self, item):
        event = {**self.context, **item}
        status = event.get('status', 'diagnostic')
        level = ('error' if any(word in status for word in ('failed', 'unconfirmed')) else
                 'warning' if any(word in status for word in ('rejected', 'superseded')) else
                 'debug' if status in ('diagnostic', 'command_result', 'compiler_ready', 'check_timing') else 'info')
        event.setdefault('schema_version', 1)
        event.setdefault('session_id', self.session)
        event.setdefault('event_id', uuid.uuid4().hex)
        operation_context = structlog.contextvars.get_contextvars()
        event.setdefault('operation_id', operation_context.get('operation_id') or
                         str(event.get('id', event.get('payload', event['event_id']))))
        inherited_parent = None
        if event['operation_id'] == operation_context.get('operation_id'):
            inherited_parent = operation_context.get('parent_operation_id')
        elif 'operation_id' not in item:
            inherited_parent = os.environ.get('XALKIT_LOG_PARENT_OPERATION_ID')
        else:
            # A forwarded operation with an unknown parent must not inherit the receiver's context.
            event.setdefault('parent_operation_id', None)
        if inherited_parent and event['operation_id'] != inherited_parent:
            event.setdefault('parent_operation_id', inherited_parent)
        event.setdefault('process_id', os.getpid())
        event.setdefault('level', level)
        event.setdefault('stage', 'build' if 'build' in status or status == 'released' else
                         'core' if status.startswith('core_') else
                         'reload' if status in ('applied', 'reload_rejected', 'observation_failed') else 'runtime')
        if isinstance(event.get('diagnostic'), str):
            raw = event.pop('diagnostic')
            diagnostics = []
            for line in raw.splitlines():
                match = COMPILER_LOCATION.match(line.strip())
                if match:
                    file, number, column, severity, code, message = match.groups()
                    diagnostics.append({'file': file, 'line': int(number),
                                        'column': int(column) if column else None,
                                        'severity': severity, 'code': code, 'message': message})
            event['diagnostics'] = diagnostics
            path = self.directory / (event['event_id'] + '.log')
            path.write_text(raw, encoding='utf-8')
            event['diagnostic_log'] = str(path)
        heading = text(MESSAGES[status]) if status in MESSAGES else status
        message = event.pop('event', event.pop('message', heading))
        selected_level = event.pop('level')
        self.bound.log(getattr(logging, selected_level.upper()), message, **event)

    def close(self):
        active_exception = sys.exc_info()[0] is not None
        failures = []
        for handler in self.logger.handlers:
            try:
                handler.close()
            except Exception as failure:
                failures.append(failure)
        if failures and not active_exception:
            raise failures[0]
        if failures:
            from xalkit_ui import text
            try:
                sys.stderr.write(text('operation_log_failed', diagnostic=self.directory) + '\n')
            except Exception:
                pass

    def child_environment(self, environment=None):
        """Pass correlation to one child without changing process-wide environment."""
        context = structlog.contextvars.get_contextvars()
        inherited = dict(os.environ if environment is None else environment)
        inherited['XALKIT_LOG_SESSION_ID'] = self.session
        parent = context.get('operation_id') or os.environ.get('XALKIT_LOG_PARENT_OPERATION_ID')
        if parent:
            inherited['XALKIT_LOG_PARENT_OPERATION_ID'] = str(parent)
        else:
            inherited.pop('XALKIT_LOG_PARENT_OPERATION_ID', None)
        return inherited

    @contextmanager
    def operation(self, action, started=None):
        from xalkit_ui import text
        parent = (structlog.contextvars.get_contextvars().get('operation_id') or
                  os.environ.get('XALKIT_LOG_PARENT_OPERATION_ID'))
        operation_id = uuid.uuid4().hex
        started = time.perf_counter() if started is None else started
        with structlog.contextvars.bound_contextvars(operation_id=operation_id, action=action,
                                                    session_id=self.session, parent_operation_id=parent):
            self.emit({'status': 'operation_started', 'parent_operation_id': parent,
                       'message': text('operation_started', action=action)})
            try:
                yield operation_id
            except BaseException as failure:
                if isinstance(failure, SystemExit) and failure.code in (None, 0):
                    self.emit({'status': 'operation_completed', 'duration_seconds': time.perf_counter() - started,
                               'message': text('operation_completed', action=action)})
                    raise
                cancelled = isinstance(failure, KeyboardInterrupt)
                try:
                    self.emit({'status': 'operation_cancelled' if cancelled else 'operation_failed',
                               'duration_seconds': time.perf_counter() - started,
                               'reason': type(failure).__name__, 'exc_info': not cancelled,
                               'message': text('operation_cancelled' if cancelled else 'operation_failed',
                                               action=action, diagnostic=self.directory)})
                except Exception:
                    # A journal failure must preserve the command's original exception.
                    try:
                        sys.stderr.write(text('operation_log_failed', diagnostic=self.directory) + '\n')
                    except Exception:
                        pass
                raise
            else:
                self.emit({'status': 'operation_completed', 'duration_seconds': time.perf_counter() - started,
                           'message': text('operation_completed', action=action)})
