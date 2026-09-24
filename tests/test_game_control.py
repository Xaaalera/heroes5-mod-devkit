"""Optional x86 emulation of the terminal bridge; never attaches to a game."""
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
import io
import json
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESP, UC_X86_REG_EDI, UC_X86_REG_EBP
    import keystone
except ImportError:
    Uc = None

specification = importlib.util.spec_from_file_location('game_control_test', Path(__file__).resolve().parents[1] / 'scripts/game_control.py')
control = importlib.util.module_from_spec(specification)
specification.loader.exec_module(control)


class GameControlCommandTests(unittest.TestCase):
    def test_serve_forwards_jsonl_without_reloading_the_game(self):
        with patch('sys.argv', ['game_control', 'serve']), \
             patch('sys.stdin', io.StringIO('{}\n{"command":"dev_probe"}\n')), \
             patch.object(control, 'execute', side_effect=[{'pid': 7, 'heartbeat': 4, 'mailbox_status': 3},
                                                           {'pid': 7, 'status': 'dispatched'}]) as execute, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            control.main()
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(replies[0], {'status': 'ready', 'protocol': 'game-control-jsonl-v1'})
        self.assertEqual(replies[1]['response']['heartbeat'], 4)
        self.assertEqual(replies[2]['response']['status'], 'dispatched')
        self.assertEqual(execute.call_count, 2)
        self.assertIsNone(execute.call_args_list[0].args[1])
        self.assertEqual(execute.call_args_list[1].args[1], 'dev_probe')

    def test_creature_target_is_idempotent_and_rejection_is_not_success(self):
        for before, actual, target in ((5, 12, 12), (12, 12, 12), (10, 1, 0)):
            with self.subTest(before=before, actual=actual, target=target):
                replies = [{'status': 'completed', 'result': str(before)}]
                if before != target:
                    replies += [{'status': 'dispatched'}, {'status': 'completed', 'result': str(actual)}]
                with patch('sys.argv', ['game_control', 'creature', 'Brem', 'CREATURE_PEASANT', '--count', str(target)]), \
                     patch.object(control, 'hero_state', return_value={}), \
                     patch.object(control, 'execute', side_effect=replies) as execute, \
                     patch('sys.stdout', new_callable=io.StringIO) as output:
                    if actual != target:
                        with self.assertRaisesRegex(RuntimeError, 'actual=1'):
                            control.main()
                        self.assertEqual(output.getvalue(), '')
                    else:
                        control.main()
                        self.assertEqual(json.loads(output.getvalue())['count'], target)
                    self.assertEqual(execute.call_count, 1 if before == target else 3)

    def test_resource_requires_readback(self):
        with patch('sys.argv', ['game_control', 'resource', '1', '6', '--amount', '50']), \
             patch.object(control, 'execute', side_effect=[{'result': '100'}, {}, {'result': '100'}]), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaisesRegex(RuntimeError, 'actual=100'):
                control.main()
            self.assertEqual(output.getvalue(), '')


@unittest.skipIf(Uc is None, 'Optional Unicorn/Keystone not installed')
class GameControlTests(unittest.TestCase):
    def test_position_observer_requires_arming_and_bounds_records(self):
        from unicorn.x86_const import UC_X86_REG_ESI, UC_X86_REG_EFLAGS
        source = (Path(__file__).resolve().parents[1] / 'scripts/game_control.py').read_text()
        assembly = source.split("        assembly = f'''", 1)[1].split("'''", 1)[0]
        assembly = re.sub(r'\{records(?: \+ (\d+))?\}',
                          lambda match: str(0x305000 + int(match.group(1) or 0)), assembly)
        code = bytes(keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_32).asm(assembly, 0x300000)[0])
        for armed, count, length in ((0, 0, 45), (1, 0, 45), (1, 6, 200), (1, 7, 45)):
            with self.subTest(armed=armed, count=count, name_length=length):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 65536)
                machine.mem_write(0x300000, code)
                machine.mem_write(0x304ff0, b'\xa5' * 832)
                machine.mem_write(0x305000, struct.pack('<4I', armed, count, 0, 123))
                machine.mem_write(0x204000, struct.pack('<2I', 0x205000, 0x205000 + length))
                machine.mem_write(0x205000, b'n' * length)
                machine.mem_write(0x20801c, struct.pack('<2i', 13, 8))
                machine.reg_write(UC_X86_REG_ESP, 0x208000)
                machine.reg_write(UC_X86_REG_ESI, 0x204000)
                machine.reg_write(UC_X86_REG_EDI, 0xabcd)
                machine.reg_write(UC_X86_REG_EFLAGS, 0x646)
                machine.emu_start(0x300000, 0x568239, count=1000)
                header = struct.unpack('<4I', machine.mem_read(0x305000, 16))
                self.assertEqual(header, (armed, count + int(armed and count < 7), int(armed and count >= 7), 123))
                if armed and count < 7:
                    record = 0x305010 + count * 112
                    self.assertEqual(struct.unpack('<2i', machine.mem_read(record, 8)), (13, 8))
                    self.assertEqual(bytes(machine.mem_read(record + 8, min(length, 96))), b'n' * min(length, 96))
                    self.assertEqual(struct.unpack('<I', machine.mem_read(record + 104, 4))[0], min(length, 96))
                else:
                    self.assertEqual(bytes(machine.mem_read(0x305010, 784)), b'\xa5' * 784)
                self.assertEqual(bytes(machine.mem_read(0x304ff0, 16)), b'\xa5' * 16)
                self.assertEqual(bytes(machine.mem_read(0x305320, 16)), b'\xa5' * 16)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x207ffc)
                self.assertEqual(machine.reg_read(UC_X86_REG_EAX), 0x208020)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESI), 0x204000)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDI), 0xabcd)
                self.assertEqual(machine.reg_read(UC_X86_REG_EFLAGS), 0x646)

    def test_main_thread_mailbox_dispatches_once_with_native_arguments(self):
        for mode, destination in ((0, 0xc125f0), (2, 0xbcf150), (24, 0x493ed0), (25, 0x838c10)):
            with self.subTest(mode=mode):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 16384)
                for page in (0x401000, 0x456000, 0x493000, 0x838000, 0x879000, 0xbcf000, 0xc12000, 0xd10000):
                    machine.mem_map(page, 4096)
                machine.mem_write(0x300000, control.trampoline(0x300000))
                machine.mem_write(0x30100c, struct.pack('<I', 1))
                machine.mem_write(0x301014, struct.pack('<I', mode))
                for function, code in ((0xd10980, b'\xc3'), (0x456220, b'\xc2\x04\x00'),
                                       (0x401ab0, b'\xc2\x04\x00'), (0xc125f0, b'\xc3'),
                                       (0xbcf150, b'\xc2\x08\x00'), (0x493ed0, b'\xc2\x04\x00'),
                                       (0x838c10, b'\xc3'), (0x879240, b'\xc3')):
                    machine.mem_write(function, code)
                calls = []
                def observe(machine, address, size, user_data):
                    if address == destination:
                        stack = machine.reg_read(UC_X86_REG_ESP)
                        calls.append((machine.reg_read(UC_X86_REG_ECX), machine.reg_read(UC_X86_REG_EDX),
                                      bytes(machine.mem_read(stack + 4, 8))))
                machine.hook_add(UC_HOOK_CODE, observe)
                for _ in range(2):
                    machine.mem_write(0x208000, struct.pack('<I', 0x200100))
                    machine.reg_write(UC_X86_REG_ESP, 0x208000)
                    machine.reg_write(UC_X86_REG_ECX, 0x1234)
                    machine.reg_write(UC_X86_REG_EDX, 0x5678)
                    machine.emu_start(0x300000, 0x200100, count=200)
                    self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x208004)
                    self.assertEqual(machine.reg_read(UC_X86_REG_ECX), 0x1234)
                    self.assertEqual(machine.reg_read(UC_X86_REG_EDX), 0x5678)
                self.assertEqual(len(calls), 1)
                self.assertEqual(struct.unpack('<II', machine.mem_read(0x301008, 8)), (2, 3))
                if mode == 2:
                    self.assertEqual(calls[0][0], 1)
                    self.assertEqual(calls[0][2], struct.pack('<iI', -1, 0))
                elif mode == 24:
                    self.assertEqual(calls[0][1], 0)
                    self.assertEqual(calls[0][2][:4], bytes(4))
                elif mode == 25:
                    self.assertEqual(calls[0][0], 0)

    def test_result_hook_copies_only_owned_key_and_bounds_response(self):
        for key, value, accepted in ((control.RESULT_KEY + ':0123456789abcdef', b'14|49|0', True),
                                      (control.RESULT_KEY + ':0123456789abcdef', b'x' * 3000, True),
                                      (control.RESULT_KEY + ':fedcba9876543210', b'stale', False),
                                      ('another_game_variable', b'private', False)):
            with self.subTest(key=key, size=len(value)):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 16384)
                machine.mem_map(0x5de000, 4096)
                machine.mem_write(0x300400, control.result_trampoline(0x300400, 0x301000))
                machine.mem_write(0x301040, b'0123456789abcdef')
                encoded = key.encode('ascii')
                machine.mem_write(0x201000, encoded)
                machine.mem_write(0x202000, value)
                machine.mem_write(0x200200, struct.pack('<II', 0x201000, 0x201000 + len(encoded)))
                machine.mem_write(0x200220, struct.pack('<II', 0x202000, 0x202000 + len(value)))
                machine.reg_write(UC_X86_REG_EDI, 0x200200)
                machine.reg_write(UC_X86_REG_EBP, 0x200220)
                machine.reg_write(UC_X86_REG_ESP, 0x208000)
                reserved = key.startswith(control.RESULT_KEY + ':')
                machine.emu_start(0x300400, 0x5dee8e if reserved else 0x5dee7b, count=6000)
                size, ready = struct.unpack('<II', machine.mem_read(0x301018, 8))
                self.assertEqual(ready, int(accepted))
                if accepted:
                    self.assertEqual(size, min(2047, len(value)))
                    self.assertEqual(bytes(machine.mem_read(0x301400, size + 1)), value[:size] + b'\0')
                self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x208000)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDI), 0x200200)
                self.assertEqual(machine.reg_read(UC_X86_REG_EBP), 0x200220)
