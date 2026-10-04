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
import os
import subprocess
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESP, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESI, UC_X86_REG_EFLAGS
    import keystone
except ImportError:
    Uc = None

specification = importlib.util.spec_from_file_location('game_control_test', Path(__file__).resolve().parents[1] / 'scripts/game_control.py')
control = importlib.util.module_from_spec(specification)
specification.loader.exec_module(control)


class GameControlCommandTests(unittest.TestCase):
    def test_levelup_install_failure_restores_code_and_releases_allocation(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import ctypes
        from ctypes import wintypes
        memory = {site: original for site, original in control.LEVELUP_SITES}
        allocation = 0x200000
        protection_calls = []
        def protect(process, address, size, protection, old):
            protection_calls.append((address, protection))
            ctypes.cast(old, ctypes.POINTER(wintypes.DWORD)).contents.value = 0x20
            return True
        def write(kernel, process, address, data):
            if address == control.LEVELUP_SITES[1][0] and data[0] == 0xe9:
                raise RuntimeError('injected patch write failure')
            memory[address] = data
        kernel = SimpleNamespace(VirtualAllocEx=Mock(return_value=allocation), VirtualProtectEx=protect,
                                 VirtualFreeEx=Mock(return_value=True), FlushInstructionCache=Mock(return_value=True))
        probe = SimpleNamespace(read=lambda kernel, process, address, size: memory[address], write=write,
                                checked=lambda result: result)
        with self.assertRaisesRegex(RuntimeError, 'patch write failure'):
            control.install_levelup_observer(probe, kernel, 1)
        for site, original in control.LEVELUP_SITES:
            self.assertEqual(memory[site], original)
            self.assertIn((site, 0x20), protection_calls)
        kernel.VirtualFreeEx.assert_called_once_with(1, allocation, 0, 0x8000)

    def test_levelup_decoder_requires_coherent_bounded_lifetimes(self):
        valid = control.LEVELUP_MAGIC + struct.pack('<10I', 2, 1, 0, 1, 0, 0, 1234, 0, 0, 0)
        self.assertTrue(control.decode_levelup_state(valid)['open'])
        empty = control.LEVELUP_MAGIC + bytes(40)
        self.assertFalse(control.decode_levelup_state(empty)['open'])
        for index, value in ((0, 3), (1, 5), (2, 1), (3, 0), (4, 2), (5, 1), (7, 1234)):
            words = list(struct.unpack('<10I', valid[8:])); words[index] = value
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                control.decode_levelup_state(control.LEVELUP_MAGIC + struct.pack('<10I', *words))
        with self.assertRaises(RuntimeError):
            control.decode_levelup_state(b'wrong')

    @unittest.skipUnless(os.name == 'nt', 'Windows bitmap validation requires PowerShell/System.Drawing')
    def test_capture_rejects_blank_and_two_colour_surfaces_before_ocr(self):
        script_path = Path(__file__).resolve().parents[1] / 'scripts/game-ui.ps1'
        script = r'''
Add-Type -AssemblyName System.Drawing
$tokens = $null; $errors = $null
$syntax = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Capture script has parse errors' }
$function = $syntax.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-RenderedGameFrame' }, $true)
Invoke-Expression $function.Extent.Text
$bitmap = New-Object System.Drawing.Bitmap 64,64
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.Clear([System.Drawing.Color]::Black)
    $black = Test-RenderedGameFrame $bitmap
    $graphics.Clear([System.Drawing.Color]::White)
    $white = Test-RenderedGameFrame $bitmap
    $graphics.Clear([System.Drawing.Color]::Black)
    $graphics.FillRectangle([System.Drawing.Brushes]::White,0,0,8,64)
    $twoColour = Test-RenderedGameFrame $bitmap
    $graphics.FillRectangle([System.Drawing.Brushes]::Red,24,24,16,16)
    $content = Test-RenderedGameFrame $bitmap
    @{black=$black;white=$white;two_colour=$twoColour;content=$content} | ConvertTo-Json -Compress
} finally { $graphics.Dispose(); $bitmap.Dispose() }
'''
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / 'capture-check.ps1'
            harness.write_text(script, encoding='utf-8')
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(harness), str(script_path)],
                                    capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout),
                         {'black': False, 'white': False, 'two_colour': False, 'content': True})

    def test_runtime_decoder_distinguishes_pending_queues_and_rejects_corrupt_chains(self):
        memory = {}
        def put(address, *values):
            for index, value in enumerate(values):memory[address+index*4] = value
        def read(address, size):
            return struct.pack('<' + str(size//4) + 'I', *(memory[address+index*4] for index in range(size//4)))
        put(0xfd2d9c,8000);put(8000,8000)
        put(0x1022bb8,1000,1004);put(1000,2000);put(2000,0,0,3000)
        put(3000+0x28,4000,4004);put(4000,5000)
        put(5000,5100,1,6000,6004,6004,7000);put(7000,7000)
        put(5100,0,21,6100,6100,6100,7100);put(7100,7100)
        state=control.decode_runtime_state(read)
        self.assertFalse(state['event_commands_pending'])
        self.assertEqual(state['script_contexts']['adventure']['registered_listener_count'],1)
        self.assertEqual(state['script_contexts']['combat_console']['registered_listener_count'],0)
        put(8000,8004);put(7000,7010)
        state=control.decode_runtime_state(read)
        self.assertTrue(state['event_commands_pending'])
        self.assertTrue(state['script_contexts']['adventure']['script_commands_pending'])
        put(5000,5000,2)
        with self.assertRaisesRegex(RuntimeError,'bucket chain'):control.decode_runtime_state(read)

    def test_teleport_follows_owned_hero_only_after_stable_position(self):
        start = {'x': 0, 'y': 0, 'floor': 0}
        target = {'x': 6, 'y': 7, 'floor': 1}
        for settled in (target, {'x': 6, 'y': 8, 'floor': 1}):
            with self.subTest(settled=settled), \
                 patch('sys.argv', ['game_control', 'teleport', 'Brem', '6', '7', '--floor', '1']), \
                 patch.object(control, 'hero_state', side_effect=[start, target, dict(settled)]), \
                 patch.object(control, 'execute', return_value={}) as execute, \
                 patch.object(control.time, 'sleep'), \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                if settled == target:
                    control.main()
                    self.assertTrue(json.loads(output.getvalue())['camera_follow_requested'])
                    self.assertEqual(execute.call_count, 2)
                    self.assertIn("GetObjectOwner('Brem')==GetCurrentPlayer()", execute.call_args_list[1].args[1])
                    self.assertIn('MoveCamera(6,7,1,50,1.57079632679,0,1,1)', execute.call_args_list[1].args[1])
                else:
                    with self.assertRaisesRegex(RuntimeError, 'Hero position changed'):
                        control.main()
                    self.assertEqual(execute.call_count, 1)

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

    def test_reused_command_dispatcher_returns_results_without_stdout_and_revalidates(self):
        with patch.object(control, 'execute', side_effect=[{'pid': 7}, RuntimeError('PID was reused')]) as execute, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(control.main(['status']), {'pid': 7})
            with self.assertRaisesRegex(RuntimeError, 'PID was reused'):
                control.main(['status'])
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(output.getvalue(), '')

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
    def test_levelup_lifecycle_observer_balances_objects_and_preserves_original_instructions(self):
        machine = Uc(UC_ARCH_X86, UC_MODE_32)
        code, data = 0x100000, 0x200000
        for address, size in ((code, 4096), (data, 8192), (0x300000, 4096), (0x400000, 4096), (0x600000, 0x100000)):
            machine.mem_map(address, size)
        machine.mem_write(data, control.LEVELUP_MAGIC + bytes(40))
        for created in (True, False):
            machine.mem_write(code + (0 if created else 512), control.levelup_trampoline(code + (0 if created else 512), data, created))
        for created, pointer, expected in ((True, 0x400000, 1), (True, 0x400400, 2),
                                           (False, 0x400400, 1), (False, 0x400000, 0)):
            with self.subTest(created=created, pointer=pointer):
                registers = {UC_X86_REG_EAX: 11, UC_X86_REG_EBX: 22, UC_X86_REG_EDX: 33,
                             UC_X86_REG_EDI: 44, UC_X86_REG_ESI: 55, UC_X86_REG_EBP: pointer,
                             UC_X86_REG_ECX: pointer + 0x148, UC_X86_REG_ESP: 0x300800}
                for register, value in registers.items(): machine.reg_write(register, value)
                machine.reg_write(UC_X86_REG_EFLAGS, 0x246)
                machine.mem_write(pointer + 0x130, struct.pack('<I', 0xbeef))
                index = 0 if created else 1
                site, original = control.LEVELUP_SITES[index]
                machine.emu_start(code + index * 512, site + len(original), count=1000)
                snapshot = control.decode_levelup_state(bytes(machine.mem_read(data, 48)))
                self.assertEqual(len(snapshot['objects']), expected)
                self.assertEqual(machine.reg_read(UC_X86_REG_EFLAGS), 0x246)
                for register, value in registers.items():
                    if not created and register in (UC_X86_REG_EAX, UC_X86_REG_ESI, UC_X86_REG_ESP): continue
                    self.assertEqual(machine.reg_read(register), value)
                if created:
                    self.assertEqual(struct.unpack('<I', machine.mem_read(pointer, 4))[0], 0xe333bc)
                else:
                    self.assertEqual(machine.reg_read(UC_X86_REG_EAX), 0xbeef)
                    self.assertEqual(machine.reg_read(UC_X86_REG_ESI), pointer + 0x148)
                    self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x3007fc)

    def test_position_quantity_and_actor_observers_require_arming_and_bound_records(self):
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

        assembly = source.split("        quantity_assembly = f'''", 1)[1].split("'''", 1)[0]
        assembly = re.sub(r'\{records(?: \+ (\d+))?\}',
                          lambda match: str(0x305000 + int(match.group(1) or 0)), assembly)
        code = bytes(keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_32).asm(assembly, 0x300200)[0])
        for armed, count, length in ((0, 0, 45), (1, 0, 45), (1, 6, 200), (1, 7, 45)):
            with self.subTest(quantity_armed=armed, quantity_count=count, name_length=length):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 65536)
                machine.mem_write(0x300200, code)
                machine.mem_write(0x304ff0, b'\xa5' * 1632)
                machine.mem_write(0x305000, struct.pack('<4I', armed, 0, 0, 123))
                machine.mem_write(0x305320, struct.pack('<2I', count, 0))
                machine.mem_write(0x204000, struct.pack('<2I', 0x205000, 0x205000 + length))
                machine.mem_write(0x205000, b'n' * length)
                machine.reg_write(UC_X86_REG_ESP, 0x208000)
                machine.reg_write(UC_X86_REG_EBP, 0x204000)
                machine.reg_write(UC_X86_REG_EAX, 321)
                machine.reg_write(UC_X86_REG_ESI, 0x1111)
                machine.reg_write(UC_X86_REG_EDI, 0xabcd)
                machine.reg_write(UC_X86_REG_EFLAGS, 0x646)
                machine.emu_start(0x300200, 0x5688ec, count=1000)
                self.assertEqual(struct.unpack('<2I', machine.mem_read(0x305320, 8)),
                                 (count + int(armed and count < 7), int(armed and count >= 7)))
                if armed and count < 7:
                    record = 0x305330 + count * 112
                    self.assertEqual(struct.unpack('<I', machine.mem_read(record, 4))[0], 321)
                    self.assertEqual(bytes(machine.mem_read(record + 8, min(length, 96))), b'n' * min(length, 96))
                    self.assertEqual(struct.unpack('<I', machine.mem_read(record + 104, 4))[0], min(length, 96))
                else:
                    self.assertEqual(bytes(machine.mem_read(0x305330, 784)), b'\xa5' * 784)
                self.assertEqual(bytes(machine.mem_read(0x305010, 784)), b'\xa5' * 784)
                self.assertEqual(bytes(machine.mem_read(0x305640, 16)), b'\xa5' * 16)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x208000)
                self.assertEqual(machine.reg_read(UC_X86_REG_EAX), 0x208014)
                self.assertEqual(struct.unpack('<I', machine.mem_read(0x208014, 4))[0], 321)
                self.assertEqual(machine.reg_read(UC_X86_REG_EBP), 0x204000)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESI), 0x1111)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDI), 0xabcd)
                self.assertEqual(machine.reg_read(UC_X86_REG_EFLAGS), 0x646)

        assembly = source.split("        actor_assembly = f'''", 1)[1].split("'''", 1)[0]
        assembly = re.sub(r'\{records(?: \+ (\d+))?\}',
                          lambda match: str(0x305000 + int(match.group(1) or 0)), assembly)
        code = bytes(keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_32).asm(assembly, 0x300400)[0])
        for armed, count in ((0, 0), (1, 0), (1, 6), (1, 7)):
            with self.subTest(actor_armed=armed, actor_count=count):
                machine = Uc(UC_ARCH_X86, UC_MODE_32)
                machine.mem_map(0x200000, 65536)
                machine.mem_map(0x300000, 65536)
                machine.mem_write(0x300400, code)
                machine.mem_write(0x305000, struct.pack('<4I', armed, count, 0, 123))
                machine.mem_write(0x305630, b'\xa5' * 88)
                machine.mem_write(0x204000, struct.pack('<I', 0xe4fba4))
                machine.reg_write(UC_X86_REG_EAX, 0x204000)
                machine.reg_write(UC_X86_REG_EDI, 0xabcd)
                machine.reg_write(UC_X86_REG_ESP, 0x208000)
                machine.reg_write(UC_X86_REG_EFLAGS, 0x246)
                machine.emu_start(0x300400, 0x56822f, count=1000)
                if armed and count < 7:
                    self.assertEqual(struct.unpack('<2I', machine.mem_read(0x305640 + count * 8, 8)),
                                     (0x204000, 0xe4fba4))
                else:
                    self.assertEqual(bytes(machine.mem_read(0x305640, 56)), b'\xa5' * 56)
                self.assertEqual(bytes(machine.mem_read(0x305630, 16)), b'\xa5' * 16)
                self.assertEqual(bytes(machine.mem_read(0x305678, 16)), b'\xa5' * 16)
                self.assertEqual(machine.reg_read(UC_X86_REG_EAX), 0x204000)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDX), 0xe4fba4)
                self.assertEqual(machine.reg_read(UC_X86_REG_ECX), 0x20801c)
                self.assertEqual(machine.reg_read(UC_X86_REG_EDI), 0xabcd)
                self.assertEqual(machine.reg_read(UC_X86_REG_ESP), 0x207ffc)
                self.assertEqual(machine.reg_read(UC_X86_REG_EFLAGS), 0x246)

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
