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


MESSAGES = {
    'game_initializing': 'Ожидание инициализации игры',
    'game_loop_ready': 'Игра готова к подключению SDK',
    'sdk_runtime_applied': 'SDK обновлён',
    'sdk_runtime_rejected': 'Обновление SDK отклонено; предыдущая версия сохранена',
    'background_budget_unavailable': 'Фоновый лимит нагрузки недоступен; причина записана в журнал',
    'console_built': 'Новая консоль собрана',
    'console_applied': 'Консоль обновлена',
    'console_rejected': 'Новая консоль отклонена; проверь журнал операции',
    'console_build_failed': 'Консоль не собралась; рабочая версия сохранена',
    'console_build_superseded': 'Исходники консоли изменились во время сборки; готовлю новую версию',
    'supervising': 'Слежение за плагинами запущено',
    'watching': 'Слежение за исходниками запущено',
    'plugin_discovered': 'Найден новый плагин',
    'applied': 'Плагин обновлён',
    'core_built': 'Ядро собрано',
    'core_applied': 'Ядро плагина обновлено',
    'core_update_completed': 'Обновление ядра завершено',
    'core_update_rejected': 'Обновление ядра отклонено; предыдущие версии восстановлены',
    'core_rejected': 'Новое ядро отклонено',
    'build_failed': 'Сборка плагина не удалась',
    'core_build_failed': 'Сборка ядра не удалась; рабочая версия сохранена',
    'plugin_stopped': 'Плагин остановлен',
    'plugin_failed': 'Плагин завершился с ошибкой',
    'plugin_cleanup_unconfirmed': 'Остановка плагина не подтверждена',
    'reload_rejected': 'Обновление плагина отклонено',
    'released': 'Выпуск собран',
    'command_rejected': 'Команда отклонена',
    'runtime_failed': 'Сбой SDK; проверь причину и журнал операции',
    'game_started': 'Тестовая игра запущена',
    'game_owned_before_resume': 'Владелец тестовой игры сохранён до продолжения запуска',
    'game_launch_cancelled': 'Запуск отменён; созданный тестовый процесс закрыт',
    'resource_deployed': 'H5U установлен в тестовую копию',
    'session_report_saved': 'Итог сеанса сохранён',
    'session_report_unavailable': 'Итоговый отчёт не сохранён; причина записана в журнал',
    'windows_events_saved': 'Системные события собственной игры сохранены',
    'windows_events_unavailable': 'Системные события недоступны; причина записана в журнал',
    'session_stop_requested': 'Запрошена остановка сеанса SDK',
    'game_close_requested': 'SDK запросил закрытие тестовой игры',
    'game_closed': 'Тестовая игра закрыта; код завершения записан в журнал',
    'game_exited': 'Игра завершилась до запроса SDK; проверь код завершения и события Windows',
    'sandbox_background_enabled': 'Фоновое обновление включено только в тестовом профиле',
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
        message = event.pop('event', event.pop('message', MESSAGES.get(status, status)))
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
