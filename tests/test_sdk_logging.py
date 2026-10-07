"""Structured log integrity and readable diagnostics without a running game."""
from concurrent.futures import ThreadPoolExecutor
import io
import gzip
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sdk_logging import EventLog


class SdkLoggingTests(unittest.TestCase):
    def test_public_progress_headings_follow_selected_language_in_journal_and_terminal(self):
        for language, expected in (('en', 'Waiting for game initialization'), ('ru', 'Ожидание инициализации игры')):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {'XALKIT_LANG': language}):
                stream = io.StringIO()
                log = EventLog(Path(directory), console=True, stream=stream)
                log.emit({'status': 'game_initializing'})
                log.close()
                event = json.loads((Path(directory) / 'events.jsonl').read_text(encoding='utf-8'))
                self.assertEqual(event['event'], expected)
                self.assertIn(expected, stream.getvalue())

    def test_rotation_compresses_and_prunes_only_old_event_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            retained = {name: b'retained evidence' for name in ('report.json', 'crash.dmp', 'compiler.log')}
            for name, contents in retained.items():
                (root / name).write_bytes(contents)
            log = EventLog(root, stream=io.StringIO())
            handler = log.logger.handlers[0]
            self.assertEqual(handler.maxBytes, 10 * 1024 * 1024)
            self.assertEqual(handler.backupCount, 5)
            self.assertTrue(handler.use_gzip)
            handler.maxBytes = 1000
            handler.backupCount = 2
            for number in range(8):
                log.emit({'status': 'diagnostic', 'id': number, 'payload_text': 'x' * 1200})
            log.close()
            archives = sorted(root.glob('events.jsonl.*.gz'))
            self.assertEqual(len(archives), 2)
            remaining = []
            for archive in archives:
                with gzip.open(archive, 'rt', encoding='utf-8') as stream:
                    remaining.extend(json.loads(line) for line in stream)
            remaining.extend(json.loads(line) for line in (root / 'events.jsonl').read_text(encoding='utf-8').splitlines())
            self.assertEqual({event['id'] for event in remaining}, {5, 6, 7})
            for name, contents in retained.items():
                self.assertEqual((root / name).read_bytes(), contents)

    def test_separate_processes_preserve_complete_large_json_records(self):
        script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from sdk_logging import EventLog
with open(os.devnull, 'w', encoding='utf-8') as output:
    log = EventLog(Path(sys.argv[1]), session=sys.argv[2], stream=output)
    for number in range(25):
        log.emit({'status': 'diagnostic', 'id': number, 'payload_text': 'x' * 65536})
    log.close()
'''
        with tempfile.TemporaryDirectory() as directory:
            processes = []
            try:
                for worker in range(4):
                    processes.append(subprocess.Popen([sys.executable, '-X', 'utf8', '-c', script,
                        directory, 'worker-' + str(worker), str(Path(__file__).resolve().parents[1] / 'scripts')],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding='utf-8'))
                for process in processes:
                    _, errors = process.communicate(timeout=20)
                    self.assertEqual(process.returncode, 0, errors)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=5)
            events = [json.loads(line) for line in (Path(directory) / 'events.jsonl')
                      .read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(events), 100)
            self.assertEqual({(event['session_id'], event['id']) for event in events},
                             {('worker-' + str(worker), number) for worker in range(4) for number in range(25)})
            self.assertTrue(all(event['payload_text'] == 'x' * 65536 for event in events))

    def test_close_attempts_every_handler_and_preserves_active_exception(self):
        for failure in (RuntimeError('command failed'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                log = EventLog(Path(directory), stream=io.StringIO())
                first, second = log.logger.handlers
                with patch.object(first, 'close', side_effect=OSError('flush failed')), \
                        patch.object(second, 'close', wraps=second.close) as close_second, \
                        patch('sdk_logging.sys.stderr', new=io.StringIO()):
                    with self.assertRaises(type(failure)) as caught:
                        try:
                            raise failure
                        finally:
                            log.close()
                    self.assertIs(caught.exception, failure)
                    close_second.assert_called_once()
                first.close()

    def test_failed_journal_cannot_replace_command_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(Path(directory), stream=io.StringIO())
            failure = RuntimeError('original command failure')
            with patch.object(log, 'emit', side_effect=[None, OSError('journal unavailable')]), \
                    patch('sdk_logging.sys.stderr', new=io.StringIO()) as warning:
                with self.assertRaises(RuntimeError) as caught:
                    with log.operation('build'):
                        raise failure
                self.assertIs(caught.exception, failure)
                self.assertIn(str(log.directory), warning.getvalue())
            log.close()

    def test_parallel_operations_have_stable_ids_and_restore_context(self):
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(Path(directory), stream=io.StringIO())
            before = structlog.contextvars.get_contextvars()
            def perform(number):
                with log.operation('build-' + str(number)) as operation_id:
                    log.emit({'status': 'building', 'plugin': str(number)})
                    return operation_id
            with ThreadPoolExecutor(max_workers=4) as workers:
                operation_ids = list(workers.map(perform, range(12)))
            self.assertEqual(structlog.contextvars.get_contextvars(), before)
            log.close()
            events = [json.loads(line) for line in (Path(directory) / 'events.jsonl')
                      .read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(set(operation_ids)), 12)
            for operation_id in operation_ids:
                records = [event for event in events if event['operation_id'] == operation_id]
                self.assertEqual([event['status'] for event in records],
                                 ['operation_started', 'building', 'operation_completed'])
                self.assertEqual(len({event['action'] for event in records}), 1)
                self.assertGreaterEqual(records[-1]['duration_seconds'], 0)

    def test_nested_operations_link_parent_and_inherit_session(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XALKIT_LOG_PARENT_OPERATION_ID': 'upstream'}):
            log = EventLog(Path(directory), stream=io.StringIO())
            with log.operation('release') as parent:
                child = EventLog(Path(directory), stream=io.StringIO())
                with child.operation('build') as nested:
                    child.emit({'status': 'building'})
                log.emit({'status': 'released'})
                child.close()
            log.close()
            events = [json.loads(line) for line in (Path(directory) / 'events.jsonl')
                      .read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len({event['session_id'] for event in events}), 1)
            child_start = next(event for event in events if event['operation_id'] == nested)
            self.assertEqual(child_start['parent_operation_id'], parent)
            self.assertEqual({event['parent_operation_id'] for event in events if event['operation_id'] == nested}, {parent})
            self.assertEqual(next(event for event in events if event['status'] == 'released')['operation_id'], parent)

    def test_failure_and_interruption_keep_original_exception_and_total_time(self):
        for failure, status in ((RuntimeError('private failure'), 'operation_failed'),
                                (KeyboardInterrupt(), 'operation_cancelled')):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                log = EventLog(Path(directory), stream=io.StringIO())
                with patch('sdk_logging.time.perf_counter', side_effect=[10, 13.5]):
                    with self.assertRaises(type(failure)) as caught:
                        with log.operation('start'):
                            raise failure
                self.assertIs(caught.exception, failure)
                log.close()
                events = [json.loads(line) for line in (Path(directory) / 'events.jsonl')
                          .read_text(encoding='utf-8').splitlines()]
                self.assertEqual([event['status'] for event in events], ['operation_started', status])
                self.assertEqual(events[-1]['duration_seconds'], 3.5)

    def test_child_process_inherits_session_and_parent_without_global_environment_mutation(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/game_control.py'
        original = dict(os.environ)
        with tempfile.TemporaryDirectory() as directory:
            log_directory = Path(directory) / '.local/xalkit/logs'
            log = EventLog(log_directory, stream=io.StringIO())
            with log.operation('parent') as parent:
                baseline = {**os.environ, 'H5_WORKSPACE': directory, 'PRESERVED_TEST_SETTING': 'value'}
                inherited = log.child_environment(baseline)
                self.assertNotIn('XALKIT_LOG_PARENT_OPERATION_ID', baseline)
                self.assertEqual(inherited['PRESERVED_TEST_SETTING'], 'value')
                result = subprocess.run([sys.executable, '-X', 'utf8', str(script), 'runtime-state'],
                                        env=inherited, capture_output=True, text=True, encoding='utf-8', timeout=10)
                self.assertNotEqual(result.returncode, 0)
                worker_script = ('import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
                                 'from sdk_logging import EventLog; '
                                 'log = EventLog(Path(sys.argv[2])); log.emit({"status": "worker_event"}); log.close()')
                worker = subprocess.run([sys.executable, '-X', 'utf8', '-c', worker_script,
                                         str(script.parent), str(log_directory)], env=inherited,
                                        capture_output=True, text=True, encoding='utf-8', timeout=10)
                self.assertEqual(worker.returncode, 0, worker.stderr)
            log.close()
            events = [json.loads(line) for line in (log_directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(events), 5)
            self.assertEqual(len({event['session_id'] for event in events}), 1)
            child = next(event for event in events if event['action'] == 'game-control' and event['status'] == 'operation_started')
            self.assertEqual(child['parent_operation_id'], parent)
            self.assertNotEqual(child['operation_id'], parent)
            self.assertEqual([event['status'] for event in events if event['operation_id'] == child['operation_id']],
                             ['operation_started', 'operation_failed'])
            self.assertEqual(next(event for event in events if event['status'] == 'worker_event')['parent_operation_id'], parent)
            self.assertEqual(dict(os.environ), original)

    def test_system_exit_logs_success_or_failure_without_changing_exit(self):
        for code, expected in ((None, 'operation_completed'), (0, 'operation_completed'), (2, 'operation_failed')):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                log = EventLog(Path(directory), stream=io.StringIO())
                with self.assertRaises(SystemExit) as raised:
                    with log.operation('backend'):
                        raise SystemExit(code)
                self.assertEqual(raised.exception.code, code)
                log.close()
                events = [json.loads(line) for line in (Path(directory) / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual([event['status'] for event in events], ['operation_started', expected])
                self.assertGreaterEqual(events[-1]['duration_seconds'], 0)

    def test_direct_backend_failures_and_help_write_correlated_operation_logs(self):
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        for script, arguments, successful in (
                ('game_control.py', ['--help'], True), ('mod-dev.py', ['--help'], True),
                ('game_control.py', ['runtime-state'], False), ('native-probe.py', ['status'], False)):
            with self.subTest(script=script, arguments=arguments), tempfile.TemporaryDirectory() as directory:
                environment = {**os.environ, 'H5_WORKSPACE': directory}
                result = subprocess.run([sys.executable, '-X', 'utf8', str(scripts / script), *arguments],
                                        env=environment, capture_output=True, text=True, encoding='utf-8', timeout=10)
                self.assertEqual(result.returncode == 0, successful, result.stderr)
                if successful:
                    self.assertFalse((Path(directory) / '.local').exists())
                    continue
                events = [json.loads(line) for line in (Path(directory) / '.local/xalkit/logs/events.jsonl')
                          .read_text(encoding='utf-8').splitlines()]
                self.assertEqual([event['status'] for event in events],
                                 ['operation_started', 'operation_completed' if successful else 'operation_failed'])
                self.assertEqual(events[0]['operation_id'], events[1]['operation_id'])
                self.assertEqual(events[0]['session_id'], events[1]['session_id'])
                self.assertGreaterEqual(events[1]['duration_seconds'], 0)
                self.assertNotIn('operation_started', result.stdout)

    def test_parallel_records_remain_parseable_and_keep_plugin_context(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            log = EventLog(Path(directory), session='test-session', stream=output)
            with ThreadPoolExecutor(max_workers=4) as workers:
                list(workers.map(lambda number: log.emit({'status': 'applied',
                    'plugin': 'alpha' if number % 2 else 'beta', 'id': number}), range(100)))
            log.close()
            journal = [json.loads(line) for line in (Path(directory) / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            console = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len(journal), 100)
            self.assertEqual({event['id'] for event in journal}, set(range(100)))
            self.assertEqual({event['event_id'] for event in journal}, {event['event_id'] for event in console})
            for event in journal:
                self.assertEqual(event['session_id'], 'test-session')
                self.assertEqual(event['plugin'], 'alpha' if event['id'] % 2 else 'beta')
                self.assertIn('timestamp', event)

    def test_console_hides_protocol_noise_and_exports_compiler_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            log = EventLog(Path(directory), console=True, plugin='alpha', stream=output)
            log.emit({'status': 'command_result', 'id': 2, 'response': {'status': 0}})
            log.emit({'status': 'build_failed', 'diagnostic':
                      'compiler banner\nC:\\source\\plugin.cpp(12,3): error C2065: unknown name\n'
                      'C:\\source\\missing.hpp(3): fatal error C1083: missing include\n'})
            log.close()
            events = [json.loads(line) for line in (Path(directory) / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            error = events[-1]
            self.assertNotIn('diagnostic', error)
            self.assertEqual(error['diagnostics'][0]['line'], 12)
            self.assertEqual(error['diagnostics'][0]['column'], 3)
            self.assertEqual(error['diagnostics'][0]['code'], 'C2065')
            self.assertIn('compiler banner', Path(error['diagnostic_log']).read_text(encoding='utf-8'))
            self.assertIn('C:\\source\\plugin.cpp:12:3:', output.getvalue())
            self.assertIn('C:\\source\\missing.hpp:3:1: error: C1083', output.getvalue())
            self.assertNotIn('compiler banner', output.getvalue())
            self.assertNotIn('command_result', output.getvalue())

    def test_forwarded_worker_event_preserves_origin_and_correlation(self):
        with tempfile.TemporaryDirectory() as directory:
            log = EventLog(Path(directory), session='parent-session', stream=io.StringIO())
            with structlog.contextvars.bound_contextvars(operation_id='receiver', parent_operation_id='reload-7'):
                log.emit({'status': 'applied', 'session_id': 'worker-session', 'event_id': 'worker-event',
                          'operation_id': 'reload-7', 'timestamp': '2026-10-04T20:00:00Z', 'process_id': 123,
                          'plugin': 'alpha', 'instance': 'instance-one'})
            log.close()
            event = json.loads((Path(directory) / 'events.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(event['event_id'], 'worker-event')
            self.assertEqual(event['timestamp'], '2026-10-04T20:00:00Z')
            self.assertEqual(event['process_id'], 123)
            self.assertEqual(event['operation_id'], 'reload-7')
            self.assertIsNone(event['parent_operation_id'])
