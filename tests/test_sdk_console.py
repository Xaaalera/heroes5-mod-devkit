"""Real loopback console protocol and canonical Click command reuse; no game."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import threading
import unittest
from zipfile import ZipFile
from contextvars import ContextVar
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sdk_console
import xalkit


class ConsoleBrokerTests(unittest.TestCase):
    def test_system_log_keeps_raw_results_in_journal_and_readable_game_results_in_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            path = Path(directory) / '.local/xalkit/logs/events.jsonl'
            path.parent.mkdir(parents=True)
            records = [
                {'session_id': 'fixture', 'status': 'resource_deployed', 'event': 'Resource installed',
                 'result': {'target': 'private-installation', 'sha256': 'fixture-digest'}},
                {'session_id': 'fixture', 'status': 'game_request_completed', 'event': 'Heroes listed',
                 'action': 'heroes', 'game_pid': 123, 'game_created': 456,
                 'result': {'status': 'completed', 'result': 'Brem'}},
            ]
            content = ''.join(json.dumps(record) + '\n' for record in records)
            path.write_text(content, encoding='utf-8')
            broker.read_journal()
            self.assertEqual(broker.events[0]['message'], 'Resource installed')
            self.assertEqual(broker.events[0]['result_summary'], '')
            self.assertIn('Brem', broker.events[1]['result_summary'])
            self.assertEqual(path.read_text(encoding='utf-8'), content)

    def test_operation_boost_survives_journal_rotation_without_stopping_session(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            broker.performance_until = 600
            with patch.object(broker, 'read_journal', side_effect=FileNotFoundError('rotation')), \
                    patch.object(sdk_console.time, 'monotonic', return_value=500):
                self.assertTrue(broker.game_operation_active())
            with patch.object(broker, 'read_journal'), \
                    patch.object(sdk_console.time, 'monotonic', return_value=601):
                self.assertFalse(broker.game_operation_active())

    def test_operation_boost_uses_recent_owned_events_and_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            path = Path(directory) / '.local/xalkit/logs/events.jsonl'
            path.parent.mkdir(parents=True)
            base = {'status': 'game_request_started', 'action': 'map', 'game_pid': 123, 'game_created': 456}
            events = [dict(base, game_created=999, timestamp='1970-01-01T00:16:40Z'),
                      None, {'status': 3}, dict(base, timestamp=None),
                      dict(base, timestamp='1970-01-01T00:10:00Z'),
                      dict(base, timestamp='1970-01-01T01:00:00Z'),
                      dict(base, timestamp='1970-01-01T00:16:40'),
                      dict(base, timestamp='1970-01-01T00:16:40Z')]
            path.write_text('\n'.join(json.dumps(event) for event in events[:-1]) + '\n', encoding='utf8')
            with patch.object(sdk_console.time, 'time', return_value=1000), \
                    patch.object(sdk_console.time, 'monotonic', return_value=500):
                self.assertFalse(broker.game_operation_active())
                with path.open('a', encoding='utf8') as journal:
                    journal.write(json.dumps(events[-1]) + '\n')
                self.assertTrue(broker.game_operation_active())
            self.assertEqual(broker.performance_until, 620)
            with patch.object(sdk_console.time, 'monotonic', return_value=621):
                self.assertFalse(broker.game_operation_active())

    def test_restart_and_menu_share_registry_dispatch_and_human_output(self):
        from sdk_console_commands import command_registry, display_result
        names = {item['name'] for item in command_registry()}
        self.assertTrue({'map', 'restart', 'menu'}.issubset(names))
        with patch.object(xalkit, 'game_action', return_value={'map': 'WorkshopPolygon'}) as action:
            result = sdk_console.execute_registered(['restart', 'WorkshopPolygon'],
                {'pid': 123, 'created': 456}, 'fixture')
        action.assert_called_once_with(['map', 'WorkshopPolygon'], json_output=False)
        self.assertIn('WorkshopPolygon', display_result('restart', result)[0])
        with patch.object(xalkit, 'game_action', return_value={'menu_requested': True}) as action:
            result = sdk_console.execute_registered(['menu'], {'pid': 123, 'created': 456}, 'fixture')
        action.assert_called_once_with(['menu'], json_output=False)
        self.assertEqual(len(display_result('menu', result)), 1)

    def test_map_command_uses_shared_cli_help_completion_and_human_result(self):
        from sdk_console_commands import command_registry, display_result
        with tempfile.TemporaryDirectory() as directory:
            maps = Path(directory) / '.local/test-game/Maps'
            maps.mkdir(parents=True)
            (maps / 'WorkshopPolygon.h5m').write_bytes(b'fixture')
            (maps / 'Other.h5m').write_bytes(b'fixture')
            with patch.object(xalkit, 'settings', return_value={'workspace': directory}):
                self.assertEqual(xalkit.complete_maps('Work'), ['WorkshopPolygon'])
            command = next(item for item in command_registry() if item['name'] == 'map')
            self.assertIn('NAME', command['usage'])
            with patch.object(xalkit, 'game_action', return_value={'map': 'WorkshopPolygon'}) as action:
                result = sdk_console.execute_registered(['map', 'WorkshopPolygon'],
                    {'pid': 123, 'created': 456}, 'fixture')
            action.assert_called_once_with(['map', 'WorkshopPolygon'], json_output=False)
            self.assertEqual(result['map'], 'WorkshopPolygon')
            lines = display_result('map', result)
            self.assertEqual(len(lines), 1)
            self.assertIn('WorkshopPolygon', lines[0])
            self.assertNotIn('{', lines[0])

    def test_poll_preserves_registered_capture_error_and_formats_unknown_failure(self):
        from concurrent.futures import Future
        from xalkit_ui import CommandFailure, error
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            expected = error('SDK_GAME_CAPTURE_FAILED', diagnostic=str(Path(directory) / 'logs'))
            for identifier, failure in [('capture', CommandFailure(expected)), ('unknown', RuntimeError('private cause'))]:
                job = Future()
                job.set_exception(failure)
                broker.jobs[identifier] = job
                broker.seen[identifier] = ['screenshot']
                response = broker.dispatch({'token': broker.token, 'kind': 'poll', 'id': identifier})
                self.assertFalse(response['ok'])
                self.assertEqual(response['status'], 'failed')
                if identifier == 'capture':
                    self.assertEqual(response['error'], expected)
                else:
                    self.assertEqual(response['error']['details'][0]['reason'], 'SDK_GAME_COMMAND_FAILED')
                    self.assertIn(str(Path(directory) / '.local/xalkit/logs'), response['error']['message'])
                    self.assertNotIn('private cause', response['error']['message'])

    def test_registered_screenshot_callback_preserves_public_error_through_async_poll(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            backend = Mock()
            backend.expected_session = ContextVar('capture_error_owner', default=None)
            backend.main.side_effect = TimeoutError('private capture failure')
            with patch.object(xalkit, 'settings', return_value={'workspace': directory}), \
                    patch.object(xalkit, 'load_backend', return_value=backend):
                request = {'token': broker.token, 'kind': 'run', 'id': 'capture-error', 'arguments': ['screenshot']}
                self.assertTrue(broker.dispatch(request)['ok'])
                deadline = time.monotonic() + 3
                while True:
                    response = broker.dispatch({'token': broker.token, 'kind': 'poll', 'id': request['id']})
                    if response.get('status') != 'pending':
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
            self.assertFalse(response['ok'])
            self.assertEqual(response['error']['details'][0]['reason'], 'SDK_GAME_CAPTURE_FAILED')
            self.assertIn(str(Path(directory) / '.local/xalkit/logs'), response['error']['message'])
            self.assertNotIn('private capture failure', response['error']['message'])
            backend.main.assert_called_once_with(['screenshot'])

    def test_checkpoint_lost_begin_reply_can_retry_and_abort_with_client_known_id(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(Path(directory), 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            begin = {'token': broker.token, 'kind': 'ui_state_begin', 'id': 'known-state-id'}
            broker.dispatch(begin)  # Accepted response is intentionally discarded.
            self.assertEqual(broker.dispatch(begin), {'ok': True, 'id': 'known-state-id'})
            with self.assertRaisesRegex(ValueError, 'already in progress'):
                broker.dispatch({**begin, 'id': 'another-id'})
            abort = {'token': broker.token, 'kind': 'ui_state_abort', 'id': 'known-state-id'}
            self.assertTrue(broker.dispatch(abort)['ok'])
            self.assertTrue(broker.dispatch(abort)['ok'])
            self.assertIsNone(broker.ui_checkpoint_staging)
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                broker.dispatch(begin)  # Delayed begin arrives after acknowledged abort.
            self.assertTrue(broker.dispatch({**begin, 'id': 'next-state-id'})['ok'])

    def test_argument_completion_refreshes_heroes_without_a_previous_command_and_reads_creature_constants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / '.local/test-game/data'
            data.mkdir(parents=True)
            with ZipFile(data / 'data.pak', 'w') as archive:
                archive.writestr('scripts/common.lua', 'CREATURE_UNKNOWN=0\nCREATURE_ARCHER=3\nCREATURE_PEASANT=1\n')
            broker = sdk_console.ConsoleBroker(root, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            release = threading.Event()
            def metadata(arguments, owner, session, source):
                self.assertEqual(arguments, ['heroes'])
                self.assertEqual(source, 'completion')
                self.assertEqual(owner['created'], 456)
                release.wait(timeout=2)
                return {'status': 'completed', 'result': 'Brem Calid'}
            with patch.object(sdk_console, 'execute_registered', side_effect=metadata) as execute:
                first = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': 'army '})
                self.assertTrue(first['pending'])
                release.set()
                broker.hero_refresh.result(timeout=2)
                second = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': 'army '})
                self.assertEqual(second['candidates'], ['Brem', 'Calid'])
                self.assertFalse(second['pending'])
                execute.assert_called_once()
            creatures = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': 'army Brem arch'})
            self.assertEqual(creatures['candidates'], ['CREATURE_ARCHER'])
            self.assertEqual(creatures['replacements'], ['army Brem CREATURE_ARCHER'])
            self.assertTrue(creatures['description'])
            broker.hero_refresh_at = float('inf')
            for command in ('army --count 10 Br', 'army --count=10 Br', 'army --json Br', 'army -- Br'):
                with self.subTest(command=command):
                    response = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': command})
                    self.assertEqual(response['candidates'], ['Brem'])
            for command in ('army --count 10 Brem arch', 'army Brem --count 10 arch',
                            'army --json Brem arch', 'army --count=10 Brem arch'):
                with self.subTest(command=command):
                    response = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': command})
                    self.assertEqual(response['candidates'], ['CREATURE_ARCHER'])
                    self.assertTrue(response['replacements'][0].endswith('CREATURE_ARCHER'))
            for command in ('army --count Br', 'army Brem --count '):
                with self.subTest(command=command):
                    response = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': command})
                    self.assertEqual(response['candidates'], [])
            response = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': 'help ar'})
            self.assertEqual(response['candidates'], ['army'])
            self.assertEqual(response['replacements'], ['help army'])

    def test_ui_check_rejects_dead_or_changed_owner_before_window_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            for creation, wait in ((457, 258), (456, 0)):
                with self.subTest(creation=creation, wait=wait):
                    probe = Mock()
                    probe.creation_time.return_value = creation
                    kernel = Mock()
                    kernel.WaitForSingleObject.return_value = wait
                    with patch.object(sdk_console.ctypes, 'WinDLL') as windows:
                        with self.assertRaisesRegex(RuntimeError, 'not alive'):
                            sdk_console.check_ui(Path(directory), probe, kernel, 99, {'pid': 123, 'created': 456})
                    windows.assert_not_called()
            reports = list((Path(directory) / '.local/test-state').glob('sdk-console-*/report.json'))
            self.assertEqual(len(reports), 2)
            for report in reports:
                result = json.loads(report.read_text(encoding='utf-8'))
                self.assertFalse(result['passed'])
                self.assertEqual(result['owner'], {'pid': 123, 'created': 456})
                self.assertIn('failure', result)

    def test_ui_check_rejects_resolved_output_and_input_path_escape_before_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / 'workspace'
            workspace.mkdir()
            outside = Path(directory) / 'outside'
            outside.mkdir()
            original_resolve = Path.resolve
            for escaped in ('output', 'report', 'capture'):
                with self.subTest(escaped=escaped):
                    def resolve(path, *arguments, **options):
                        matches = (escaped == 'output' and path.name.startswith('sdk-console-') or
                                   escaped == 'report' and path.name == 'console-input-validation.json' or
                                   escaped == 'capture' and path.name == 'console-input-capture.bmp')
                        return outside / path.name if matches else original_resolve(path, *arguments, **options)
                    probe, kernel = Mock(), Mock()
                    with patch.object(Path, 'resolve', resolve), patch.object(sdk_console.ctypes, 'WinDLL') as windows:
                        with self.assertRaisesRegex(ValueError, 'inside their workspace'):
                            sdk_console.check_ui(workspace, probe, kernel, 99, {'pid': 123, 'created': 456})
                    windows.assert_not_called()
                    probe.creation_time.assert_not_called()
                    self.assertEqual(list(outside.iterdir()), [])

    def test_ui_validation_attributes_owner_to_broker_not_request_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.validation_enabled = True
            broker.owner = {'pid': 123, 'created': 456}
            broker.dispatch({'token': broker.token, 'kind': 'validation_result', 'result': {
                'scenario': 1, 'owner': {'pid': 999, 'created': 999}}})
            saved = json.loads((Path(directory) / '.local/xalkit/console-input-validation.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['owner'], {'pid': 123, 'created': 456})

    def test_trace_runs_once_and_returns_readable_output_without_transport_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            result = {'pid': 123, 'records': [{'level': 'warning', 'module': 'alpha', 'message': 'fixture warning'}],
                      'cursor': 1, 'dropped': 0}
            with patch.object(sdk_console, 'execute_registered', return_value=result) as execute:
                request = {'token': broker.token, 'kind': 'run', 'id': 'trace-once', 'arguments': ['trace']}
                broker.dispatch(request)
                broker.dispatch(request)
                broker.jobs['trace-once'].result(timeout=2)
                response = broker.dispatch({'token': broker.token, 'kind': 'poll', 'id': 'trace-once'})
                execute.assert_called_once()
                self.assertEqual(response['display_lines'][0], '[WARNING] alpha: fixture warning')
                self.assertFalse(any('pid' in line for line in response['display_lines']))
                self.assertEqual(response['result'], result)

    def test_help_and_invalid_level_do_not_execute_game_actions(self):
        with patch.object(xalkit, 'game_action') as action:
            help_result = sdk_console.execute_registered(['army', '--help'], {'pid': 123, 'created': 456}, 'fixture')
            self.assertTrue(any('--count' in line or 'count:' in line for line in help_result['help_lines']))
            with self.assertRaises(Exception):
                sdk_console.execute_registered(['level', 'Brem', '0'], {'pid': 123, 'created': 456}, 'fixture')
            action.assert_not_called()

    def test_unread_result_survives_cache_pruning_and_ack_releases_full_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            release = threading.Event()
            def delayed_command(*arguments):
                if not release.wait(timeout=2):
                    raise TimeoutError('Fixture command was not released')
                return {'value': 'kept'}
            with patch.object(sdk_console, 'execute_registered', side_effect=delayed_command) as execute:
                broker.dispatch({'token': broker.token, 'kind': 'run', 'id': 'held', 'arguments': ['status']})
                try:
                    self.assertEqual(broker.dispatch({'token': broker.token, 'kind': 'poll', 'id': 'held'})['status'], 'pending')
                    with self.assertRaisesRegex(ValueError, 'not ready'):
                        broker.dispatch({'token': broker.token, 'kind': 'ack', 'id': 'held'})
                    checkpoint = {'version': 1, 'owner': broker.owner, 'editor': 'status', 'pending': ['held'],
                                  'output': [], 'history': ['status'], 'minimum': 1, 'filter': '', 'visible': True,
                                  'event_cursor': 0, 'mirrored': [], 'last_result': None}
                    transaction = broker.dispatch({'token': broker.token, 'kind': 'ui_state_begin'})['id']
                    broker.dispatch({'token': broker.token, 'kind': 'ui_state_part', 'id': transaction,
                                     'index': 0, 'text': json.dumps(checkpoint)})
                    broker.dispatch({'token': broker.token, 'kind': 'ui_state_commit', 'id': transaction})
                    restored = broker.dispatch({'token': broker.token, 'kind': 'ui_state_get'})
                    self.assertEqual(json.loads(restored['text'])['pending'], ['held'])
                finally:
                    release.set()
                broker.jobs['held'].result(timeout=2)
                for index in range(100):
                    request_id = str(index)
                    broker.dispatch({'token': broker.token, 'kind': 'run', 'id': request_id, 'arguments': ['status']})
                    broker.jobs[request_id].result(timeout=2)
                    broker.dispatch({'token': broker.token, 'kind': 'ack', 'id': request_id})
                self.assertIn('held', broker.jobs)
                response = broker.dispatch({'token': broker.token, 'kind': 'poll', 'id': 'held'})
                self.assertEqual(response['result'], {'value': 'kept'})
                self.assertIn('held', broker.unacknowledged)
                self.assertLessEqual(len(broker.jobs), 96)
                self.assertEqual(execute.call_count, 101)
                broker.dispatch({'token': broker.token, 'kind': 'ack', 'id': 'held'})
                broker.dispatch({'token': broker.token, 'kind': 'ack', 'id': 'held'})
                # Fill the queue with results that the UI has not received.
                for index in range(96):
                    request_id = 'pending-' + str(index)
                    broker.dispatch({'token': broker.token, 'kind': 'run', 'id': request_id, 'arguments': ['status']})
                    broker.jobs[request_id].result(timeout=2)
                with self.assertRaisesRegex(ValueError, 'queue is full'):
                    broker.dispatch({'token': broker.token, 'kind': 'run', 'id': 'retry', 'arguments': ['status']})
                broker.dispatch({'token': broker.token, 'kind': 'ack', 'id': 'pending-0'})
                self.assertEqual(broker.dispatch({'token': broker.token, 'kind': 'run', 'id': 'retry',
                                                'arguments': ['status']})['status'], 'accepted')
    def test_completion_uses_cli_registry_and_own_completed_hero_roster(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            # This fixture isolates the journal cache; live metadata refresh is checked separately.
            broker.hero_refresh_at = float('inf')
            def complete(arguments):
                return broker.dispatch({'token': broker.token, 'kind': 'complete', 'arguments': arguments})['candidates']
            self.assertEqual(complete(['her']), ['heroes'])
            response = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': 'her'})
            self.assertEqual(response['replacements'], ['heroes'])
            response = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': 'army Hero1 CREATURE_ARCHER --c'})
            self.assertIn('army Hero1 CREATURE_ARCHER --count', response['replacements'])
            response = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': 'army "unfinished'})
            self.assertEqual(response['replacements'], [])
            self.assertIn('--count', complete(['army', 'Hero1', 'CREATURE_ARCHER', '--c']))
            journal = Path(directory) / '.local/xalkit/logs/events.jsonl'
            journal.parent.mkdir(parents=True)
            event = {'game_pid': 123, 'game_created': 456, 'status': 'game_request_completed', 'action': 'heroes',
                     'result': {'status': 'completed', 'result': 'Hero1 Astral'}, 'event': 'Heroes ready'}
            journal.write_text(json.dumps(event) + '\n', encoding='utf-8')
            self.assertEqual(complete(['army', 'H']), ['Hero1'])
            response = broker.dispatch({'token': broker.token, 'kind': 'complete', 'text': 'army '})
            self.assertIn('army Hero1', response['replacements'])
            with patch('xalkit.game_action') as action:
                broker.dispatch({'token': broker.token, 'kind': 'run', 'id': 'missing-hero', 'arguments': ['level', '20']})
                deadline = time.monotonic() + 2
                while True:
                    response = broker.dispatch({'token': broker.token, 'kind': 'poll', 'id': 'missing-hero'})
                    if response.get('status') != 'pending':
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.01)
                self.assertFalse(response['ok'])
                self.assertIn('level <hero> <target>', response['error']['message'])
                action.assert_not_called()
            self.assertEqual(complete(['teleport', 'A']), ['Astral'])

    def test_large_journal_results_are_byte_paged_without_replay_or_ui_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'sdk-session')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            path = Path(directory) / '.local/xalkit/logs/events.jsonl'
            path.parent.mkdir(parents=True)
            records = [{'session_id': 'sdk-session', 'event_id': str(index),
                        'event': 'т' * 2048, 'status': 'game_request_completed',
                        'result': {'text': 'т' * 20000}} for index in range(40)]
            path.write_text('\n'.join(json.dumps(record, ensure_ascii=False) for record in records) + '\n', encoding='utf-8')
            seen = []
            cursor = 0
            while len(seen) < 40:
                response = broker.dispatch({'kind': 'events', 'token': broker.token, 'after': cursor})
                self.assertLessEqual(len(json.dumps(response, ensure_ascii=False).encode('utf-8')), 60000)
                self.assertTrue(response['events'])
                seen.extend(event['id'] for event in response['events'])
                cursor = response['events'][-1]['sequence']
            self.assertEqual(seen, [str(index) for index in range(40)])
            with patch.object(sdk_console, 'execute_registered', return_value={'text': 'т' * 40000}) as execute:
                broker.dispatch({'kind': 'run', 'token': broker.token, 'id': 'large-result', 'arguments': ['status']})
                broker.jobs['large-result'].result(timeout=2)
                response = broker.dispatch({'kind': 'poll', 'token': broker.token, 'id': 'large-result'})
                self.assertTrue(response['result']['display_truncated'])
                self.assertLess(len(json.dumps(response).encode('utf-8')), 60000)
                execute.assert_called_once()

    def test_journal_mirrors_external_commands_and_preserves_module_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'sdk-session')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            journal = Path(directory) / '.local/xalkit/logs/events.jsonl'
            journal.parent.mkdir(parents=True)
            entries = [
                {'event_id': 'module', 'session_id': 'sdk-session', 'plugin': 'alpha', 'instance': 'first',
                 'status': 'applied', 'event': 'Module active', 'level': 'info'},
                {'event_id': 'command', 'session_id': 'external-cli', 'source': 'terminal',
                 'game_pid': 123, 'game_created': 456, 'status': 'game_request_started',
                 'arguments': ['heroes'], 'event': 'Request', 'level': 'info'},
                {'event_id': 'wrong-owner', 'source': 'terminal', 'game_pid': 123, 'game_created': 999,
                 'status': 'game_request_started', 'event': 'Do not mirror'},
                {'event_id': 'candidate', 'session_id': 'sdk-session', 'plugin': 'alpha',
                 'status': 'build_failed', 'event': 'Candidate failed', 'level': 'error'}]
            journal.write_text('\n'.join(json.dumps(entry) for entry in entries) + '\n', encoding='utf-8')
            response = broker.dispatch({'token': broker.token, 'kind': 'events', 'after': 0})
            self.assertEqual([event['id'] for event in response['events']], ['module', 'command', 'candidate'])
            self.assertEqual(response['modules'][0]['state'], 'active')
            self.assertEqual(response['events'][1]['message'], 'Request: heroes')
            after = response['events'][-1]['sequence']
            self.assertEqual(broker.dispatch({'token': broker.token, 'kind': 'events', 'after': after})['events'], [])
            with journal.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'session_id': 'sdk-session', 'plugin': 'alpha', 'instance': 'first',
                    'status': 'plugin_stopped', 'event': 'Stopped'}) + '\n')
            self.assertEqual(broker.dispatch({'token': broker.token, 'kind': 'events', 'after': after})['modules'][0]['state'], 'stopped')

    def test_catalog_and_executor_use_existing_cli_callbacks(self):
        catalog = sdk_console.command_registry()
        self.assertIn('heroes', {command['name'] for command in catalog})
        army = next(command for command in catalog if command['name'] == 'army')
        self.assertEqual([parameter['name'] for parameter in army['parameters']], ['hero', 'creature', 'count', 'json_output'])
        owner = {'pid': 123, 'created': 456}
        backend = Mock()
        backend.expected_session = ContextVar('fixture_owner', default=None)
        def read(arguments):
            self.assertEqual(backend.expected_session.get(), owner)
            return {'count': 10}
        backend.main.side_effect = read
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(xalkit, 'settings', return_value={'workspace': directory}), \
                patch.object(xalkit, 'load_backend', return_value=backend):
            result = sdk_console.execute_registered(['army', 'Hero1', 'CREATURE_ARCHER', '--count', '10'], owner, 'fixture')
        self.assertEqual(result, {'count': 10})
        backend.main.assert_called_once_with(['creature', 'Hero1', 'CREATURE_ARCHER', '--count', '10'])
        self.assertIsNone(backend.expected_session.get())

    def test_real_socket_authentication_async_completion_and_no_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = sdk_console.ConsoleBroker(directory, 'fixture')
            self.addCleanup(broker.close)
            broker.owner = {'pid': 123, 'created': 456}
            def exchange(request):
                with socket.create_connection(('127.0.0.1', broker.port), timeout=2) as client:
                    client.sendall((json.dumps(request) + '\n').encode('utf-8'))
                    return json.loads(client.makefile('rb').readline())
            unauthorized = exchange({'token': 'wrong', 'kind': 'catalog'})
            self.assertFalse(unauthorized['ok'])
            self.assertEqual(unauthorized['error']['status'], 'UNAUTHENTICATED')
            catalog = exchange({'token': broker.token, 'kind': 'catalog'})
            self.assertEqual(catalog['owner'], broker.owner)
            request = {'token': broker.token, 'kind': 'run', 'id': 'once', 'arguments': ['status']}
            with patch.object(sdk_console, 'execute_registered', return_value={'pid': 123}) as execute:
                self.assertEqual(exchange(request)['status'], 'accepted')
                self.assertEqual(exchange(request)['status'], 'accepted')
                deadline = time.monotonic() + 2
                while True:
                    result = exchange({'token': broker.token, 'kind': 'poll', 'id': 'once'})
                    if result.get('status') != 'pending':
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.01)
                self.assertEqual(result['result']['pid'], 123)
                execute.assert_called_once()
                with broker.lock:
                    del broker.jobs['once']
                self.assertFalse(exchange(request)['ok'])
                execute.assert_called_once()
            snapshot = {'version': 1, 'owner': broker.owner, 'editor': 'heroes', 'pending': ['once'],
                        'output': ['тест' * 20000], 'history': ['heroes'], 'minimum': 1, 'filter': '',
                        'visible': True, 'event_cursor': 0, 'mirrored': [], 'last_result': {'pid': 123},
                        'window_rect': [-700, 20, 40, 480],
                        'scroll_positions': [[34.5, 1234.0], [0, 987]], 'follow_scroll': [False, True]}
            encoded = json.dumps(snapshot, ensure_ascii=True)
            transaction = exchange({'token': broker.token, 'kind': 'ui_state_begin'})['id']
            for index, offset in enumerate(range(0, len(encoded), 16000)):
                response = exchange({'token': broker.token, 'kind': 'ui_state_part', 'id': transaction,
                                     'index': index, 'text': encoded[offset:offset + 16000]})
                self.assertTrue(response['ok'])
            self.assertEqual(exchange({'token': broker.token, 'kind': 'ui_state_get'})['size'], 0)
            self.assertTrue(exchange({'token': broker.token, 'kind': 'ui_state_commit', 'id': transaction})['ok'])
            restored = ''
            generation = broker.ui_checkpoint_version
            while len(restored) < len(encoded):
                part = exchange({'token': broker.token, 'kind': 'ui_state_get', 'offset': len(restored), 'version': generation})
                self.assertTrue(part['ok'])
                restored += part['text']
            self.assertEqual(json.loads(restored), snapshot)
            self.assertFalse(exchange({'token': broker.token, 'kind': 'ui_state_get', 'version': generation - 1})['ok'])
            transaction = exchange({'token': broker.token, 'kind': 'ui_state_begin'})['id']
            bad = json.dumps({**snapshot, 'owner': {'pid': 999, 'created': 456}, 'output': []})
            self.assertTrue(exchange({'token': broker.token, 'kind': 'ui_state_part', 'id': transaction,
                                      'index': 0, 'text': bad})['ok'])
            self.assertFalse(exchange({'token': broker.token, 'kind': 'ui_state_commit', 'id': transaction})['ok'])
            self.assertEqual(broker.ui_checkpoint, encoded)
            self.assertTrue(exchange({'token': broker.token, 'kind': 'ui_state_abort', 'id': transaction})['ok'])
            transaction = exchange({'token': broker.token, 'kind': 'ui_state_begin'})['id']
            bad = json.dumps({**snapshot, 'scroll_positions': [[0, float('nan')], [0, 0]], 'output': []})
            self.assertTrue(exchange({'token': broker.token, 'kind': 'ui_state_part', 'id': transaction,
                                      'index': 0, 'text': bad})['ok'])
            self.assertFalse(exchange({'token': broker.token, 'kind': 'ui_state_commit', 'id': transaction})['ok'])
            self.assertEqual(broker.ui_checkpoint, encoded)
            self.assertTrue(exchange({'token': broker.token, 'kind': 'ui_state_abort', 'id': transaction})['ok'])
            transaction = exchange({'token': broker.token, 'kind': 'ui_state_begin'})['id']
            bad = json.dumps({**snapshot, 'window_rect': [0, 0, 1, 1], 'output': []})
            self.assertTrue(exchange({'token': broker.token, 'kind': 'ui_state_part', 'id': transaction,
                                      'index': 0, 'text': bad})['ok'])
            self.assertFalse(exchange({'token': broker.token, 'kind': 'ui_state_commit', 'id': transaction})['ok'])
            self.assertEqual(broker.ui_checkpoint, encoded)
            self.assertTrue(exchange({'token': broker.token, 'kind': 'ui_state_abort', 'id': transaction})['ok'])
