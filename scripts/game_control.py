"""Terminal mailbox for the pinned sandbox game's main thread."""
import argparse
import ctypes
from ctypes import wintypes
import importlib.util
import json
import re
from pathlib import Path
import struct
import sys
import time
import uuid

from workspace import workspace_root, game_installation

ROOT = workspace_root()
STATE = ROOT / '.local/test-state/game-control.json'
ENTRY = 0xd112db
ORIGINAL = bytes.fromhex('e8 a0 f6 ff ff')
MAGIC = b'H5CTRL01'
RESULT_ENTRY = 0x5dee75
RESULT_ORIGINAL = bytes.fromhex('8b 45 00 3b 45 04')
RESULT_KEY = '__workshop_rpc_result'
_probe_module = None
LEVELUP_MAGIC = b'H5LEVEL1'
LEVELUP_SITES = ((0x6a901a, bytes.fromhex('c74500bc33e300')),
                 (0x6aa430, bytes.fromhex('568bf18b46e8')))


def levelup_trampoline(address, data, created):
    """Observe the pinned CLevelUpBox lifetime without changing its events."""
    from keystone import Ks, KS_ARCH_X86, KS_MODE_32
    sequence, count, unsupported = data + 8, data + 12, data + 16
    slots = data + 32
    if created:
        original = LEVELUP_SITES[0][1]
        body = f'''
            pushfd
            pushad
            inc dword ptr [{sequence}]
            inc dword ptr [{data + 20}]
            mov eax, ebp
            mov edi, {slots}
            mov ecx, 4
        check_duplicate:
            cmp [edi], eax
            je invalid
            add edi, 4
            loop check_duplicate
            mov edi, {slots}
            mov ecx, 4
        find_empty:
            cmp dword ptr [edi], 0
            je insert
            add edi, 4
            loop find_empty
            jmp invalid
        insert:
            mov [edi], eax
            inc dword ptr [{count}]
            jmp done
        '''
    else:
        original = LEVELUP_SITES[1][1]
        body = f'''
            pushfd
            pushad
            inc dword ptr [{sequence}]
            inc dword ptr [{data + 24}]
            lea eax, [ecx - 0x148]
            mov edi, {slots}
            mov ecx, 4
        find_object:
            cmp [edi], eax
            je remove
            add edi, 4
            loop find_object
            jmp invalid
        remove:
            mov dword ptr [edi], 0
            dec dword ptr [{count}]
            jmp done
        '''
    body += f'''
        invalid:
            mov dword ptr [{unsupported}], 1
        done:
            inc dword ptr [{sequence}]
            popad
            popfd
    '''
    assembler = Ks(KS_ARCH_X86, KS_MODE_32)
    # Publish creation only after the original primary-vtable assignment.
    prefix = original if created else b''
    code = prefix + bytes(assembler.asm(body, address + len(prefix))[0])
    if not created:
        code += original
    site = LEVELUP_SITES[0 if created else 1][0]
    return code + b'\xe9' + struct.pack('<i', site + len(original) - address - len(code) - 5)


def install_levelup_observer(probe, kernel, process):
    for site, original in LEVELUP_SITES:
        if probe.read(kernel, process, site, len(original)) != original:
            raise RuntimeError('Level-up lifecycle entry differs')
    allocation = probe.checked(kernel.VirtualAllocEx(process, None, 8192, 0x3000, 0x04))
    data = allocation + 4096
    writable = []
    patched = []
    try:
        for index in range(2):
            code = levelup_trampoline(allocation + index * 512, data, index == 0)
            if len(code) > 512:
                raise RuntimeError('Level-up observer exceeds its code slot')
            probe.write(kernel, process, allocation + index * 512, code)
        probe.write(kernel, process, data, LEVELUP_MAGIC + bytes(40))
        old, ignored = wintypes.DWORD(), wintypes.DWORD()
        probe.checked(kernel.VirtualProtectEx(process, allocation, 4096, 0x20, ctypes.byref(old)))
        for site, original in LEVELUP_SITES:
            probe.checked(kernel.VirtualProtectEx(process, site, len(original), 0x40, ctypes.byref(old)))
            writable.append((site, original, old.value))
        patches = []
        for index, (site, original) in enumerate(LEVELUP_SITES):
            patch = b'\xe9' + struct.pack('<i', allocation + index * 512 - site - 5) + b'\x90' * (len(original) - 5)
            probe.write(kernel, process, site, patch)
            patched.append((site, original))
            patches.append(patch.hex())
        probe.checked(kernel.FlushInstructionCache(process, None, 0))
        return {'allocation': allocation, 'data': data, 'patches': patches}
    except Exception:
        for site, original in reversed(patched):
            probe.write(kernel, process, site, original)
        kernel.FlushInstructionCache(process, None, 0)
        probe.checked(kernel.VirtualFreeEx(process, allocation, 0, 0x8000))
        raise
    finally:
        for site, original, protection in reversed(writable):
            ignored = wintypes.DWORD()
            probe.checked(kernel.VirtualProtectEx(process, site, len(original), protection, ctypes.byref(ignored)))


def decode_levelup_state(snapshot):
    if len(snapshot) != 48 or snapshot[:8] != LEVELUP_MAGIC:
        raise RuntimeError('Invalid level-up observer signature')
    sequence, count, unsupported, created, destroyed, reserved, *objects = struct.unpack('<10I', snapshot[8:])
    if sequence & 1:
        raise RuntimeError('Level-up observer is updating')
    if (unsupported or count > 4 or count != sum(bool(value) for value in objects)
            or len(set(value for value in objects if value)) != count or reserved
            or created < destroyed or created - destroyed != count):
        raise RuntimeError('Unsupported level-up observer lifetime')
    return {'available': True, 'stable': True, 'open': count > 0, 'objects': [value for value in objects if value],
            'created': created, 'destroyed': destroyed, 'sequence': sequence}


def levelup_state(probe):
    state = json.loads(STATE.read_text(encoding='utf-8'))
    observer = state.get('levelup_observer')
    if observer is None:
        return {'pid': state['pid'], 'available': False, 'stable': False}
    kernel = probe.api()
    process = probe.checked(kernel.OpenProcess(0x1010, False, state['pid']))
    try:
        if probe.creation_time(kernel, process) != state['created']:
            raise RuntimeError('Game-control PID changed')
        for index, (site, original) in enumerate(LEVELUP_SITES):
            if probe.read(kernel, process, site, len(original)).hex() != observer['patches'][index]:
                raise RuntimeError('Level-up observer hook changed')
        for attempt in range(3):
            first = probe.read(kernel, process, observer['data'], 48)
            if first != probe.read(kernel, process, observer['data'], 48) or struct.unpack_from('<I', first, 8)[0] & 1:
                continue
            result = decode_levelup_state(first)
            try:
                matches = all(probe.read(kernel, process, pointer, 4) == struct.pack('<I', 0xe333bc)
                              for pointer in result['objects'])
            except OSError:
                continue
            if not matches or first != probe.read(kernel, process, observer['data'], 48):
                continue
            return {'pid': state['pid'], **result}
        raise RuntimeError('Level-up observer did not stabilize')
    finally:
        kernel.CloseHandle(process)


def result_trampoline(address, data):
    from keystone import Ks, KS_ARCH_X86, KS_MODE_32
    checks = '\n'.join(f'cmp byte ptr [eax + {index}], {value}\njne original'
                       for index, value in enumerate((RESULT_KEY + ':').encode('ascii')))
    assembly = f'''
        pushfd
        pushad
        mov eax, dword ptr [edi + 4]
        sub eax, dword ptr [edi]
        cmp eax, {len(RESULT_KEY) + 1}
        jb original
        mov eax, dword ptr [edi]
        {checks}
        mov edx, dword ptr [edi + 4]
        sub edx, eax
        cmp edx, {len(RESULT_KEY) + 17}
        jne discard
        lea esi, [eax + {len(RESULT_KEY) + 1}]
        mov edi, {data + 64}
        mov ecx, 16
        cld
        repe cmpsb
        jne discard
        mov esi, dword ptr [ebp]
        mov ecx, dword ptr [ebp + 4]
        sub ecx, esi
        cmp ecx, 2047
        jbe bounded
        mov ecx, 2047
    bounded:
        mov dword ptr [{data + 24}], ecx
        mov edi, {data + 1024}
        cld
        rep movsb
        mov byte ptr [edi], 0
        mov dword ptr [{data + 28}], 1
    discard:
        popad
        popfd
        jmp 0x5dee8e
    original:
        popad
        popfd
        mov eax, dword ptr [ebp]
        cmp eax, dword ptr [ebp + 4]
        jmp 0x5dee7b
    '''
    return bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(assembly, address)[0])


def trampoline(address):
    from keystone import Ks, KS_ARCH_X86, KS_MODE_32
    data = address + 4096
    assembly = f'''
        call 0xd10980
        pushfd
        pushad
        inc dword ptr [{data + 8}]
        cmp dword ptr [{data + 12}], 1
        jne done
        mov dword ptr [{data + 12}], 2
        fxsave [{data + 256}]
        sub esp, 12
        cmp dword ptr [{data + 20}], 0
        jne script_command
        push {address + 8192}
        lea ecx, [esp + 4]
        call 0x456220
        mov ecx, esp
        call 0xc125f0
        jmp release_command
    script_command:
        push {address + 8192}
        lea ecx, [esp + 4]
        call 0x401ab0
        cmp dword ptr [{data + 20}], 24
        je event_command
        cmp dword ptr [{data + 20}], 25
        je exit_command
        mov edx, esp
        mov ecx, dword ptr [{data + 20}]
        dec ecx
        push 0
        push -1
        call 0xbcf150
        jmp release_command
    event_command:
        mov ecx, esp
        xor edx, edx
        push 0
        call 0x493ed0
        jmp release_command
    exit_command:
        xor ecx, ecx
        call 0x838c10
    release_command:
        push dword ptr [esp]
        call 0x879240
        add esp, 16
        fxrstor [{data + 256}]
        mov dword ptr [{data + 12}], 3
    done:
        popad
        popfd
        ret
    '''
    return bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(assembly, address)[0])


def install(probe, kernel, process, pid, observe=False):
    """Called only before the owned child process is first resumed."""
    if probe.read(kernel, process, ENTRY, len(ORIGINAL)) != ORIGINAL:
        raise RuntimeError('Main-loop call differs')
    if probe.read(kernel, process, RESULT_ENTRY, len(RESULT_ORIGINAL)) != RESULT_ORIGINAL:
        raise RuntimeError('Script result entry differs')
    for site, original in LEVELUP_SITES:
        if probe.read(kernel, process, site, len(original)) != original:
            raise RuntimeError('Level-up lifecycle entry differs')
    if observe and probe.read(kernel, process, 0x568234, 5) != bytes.fromhex('8d 44 24 20 50'):
        raise RuntimeError('Lua position-result site differs')
    if observe and probe.read(kernel, process, 0x5688e4, 8) != bytes.fromhex('89 44 24 14 8d 44 24 14'):
        raise RuntimeError('Lua creature-number result site differs')
    if observe and probe.read(kernel, process, 0x568228, 7) != bytes.fromhex('8b 10 8d 4c 24 1c 51'):
        raise RuntimeError('Lua position actor site differs')
    allocation = probe.checked(kernel.VirtualAllocEx(process, None, 32768 if observe else 16384, 0x3000, 0x04))
    code = trampoline(allocation)
    result_code = result_trampoline(allocation + 1024, allocation + 4096)
    if len(code) > 1024 or len(result_code) > 3072:
        raise RuntimeError('Control trampoline exceeds its RX page')
    probe.write(kernel, process, allocation, code)
    probe.write(kernel, process, allocation + 1024, result_code)
    probe.write(kernel, process, allocation + 4096, MAGIC + bytes(24))
    old, ignored = wintypes.DWORD(), wintypes.DWORD()
    probe.checked(kernel.VirtualProtectEx(process, allocation, 4096, 0x20, ctypes.byref(old)))
    probe.checked(kernel.VirtualProtectEx(process, ENTRY, 5, 0x40, ctypes.byref(old)))
    patch = b'\xe8' + struct.pack('<i', allocation - ENTRY - 5)
    probe.write(kernel, process, ENTRY, patch)
    probe.checked(kernel.VirtualProtectEx(process, ENTRY, 5, old.value, ctypes.byref(ignored)))
    probe.checked(kernel.VirtualProtectEx(process, RESULT_ENTRY, 6, 0x40, ctypes.byref(old)))
    result_patch = b'\xe9' + struct.pack('<i', allocation + 1024 - RESULT_ENTRY - 5) + b'\x90'
    probe.write(kernel, process, RESULT_ENTRY, result_patch)
    probe.checked(kernel.VirtualProtectEx(process, RESULT_ENTRY, 6, old.value, ctypes.byref(ignored)))
    if observe:
        from keystone import Ks, KS_ARCH_X86, KS_MODE_32
        observer = allocation + 16384
        records = allocation + 20480
        assembly = f'''
            pushfd
            pushad
            cmp dword ptr [{records}], 1
            jne observation_done
            mov edi, dword ptr [{records + 4}]
            cmp edi, 7
            jb observation_record
            mov dword ptr [{records + 8}], 1
            jmp observation_done
        observation_record:
            imul edi, 112
            add edi, {records + 16}
            mov eax, dword ptr [esp + 64]
            mov dword ptr [edi], eax
            mov eax, dword ptr [esp + 68]
            mov dword ptr [edi + 4], eax
            mov ecx, dword ptr [esi + 4]
            sub ecx, dword ptr [esi]
            cmp ecx, 96
            jbe observation_name
            mov ecx, 96
        observation_name:
            mov dword ptr [edi + 104], ecx
            mov esi, dword ptr [esi]
            add edi, 8
            cld
            rep movsb
            inc dword ptr [{records + 4}]
        observation_done:
            popad
            popfd
            lea eax, [esp + 0x20]
            push eax
            jmp 0x568239
        '''
        observer_code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(assembly, observer)[0])
        if len(observer_code) > 512:
            raise RuntimeError('Position observer exceeds its reserved code region')
        probe.write(kernel, process, observer, observer_code)
        # Only the after-Start script calls GetCreatureNumber while armed.
        # The Lua binding has the returned number in EAX and name vector in EBP.
        quantity_assembly = f'''
            pushfd
            pushad
            cmp dword ptr [{records}], 1
            jne quantity_done
            mov edi, dword ptr [{records + 800}]
            cmp edi, 7
            jb quantity_record
            mov dword ptr [{records + 804}], 1
            jmp quantity_done
        quantity_record:
            imul edi, 112
            add edi, {records + 816}
            mov eax, dword ptr [esp + 28]
            mov dword ptr [edi], eax
            mov ecx, dword ptr [ebp + 4]
            sub ecx, dword ptr [ebp]
            cmp ecx, 96
            jbe quantity_name
            mov ecx, 96
        quantity_name:
            mov dword ptr [edi + 104], ecx
            mov esi, dword ptr [ebp]
            add edi, 8
            cld
            rep movsb
            inc dword ptr [{records + 800}]
        quantity_done:
            popad
            popfd
            mov dword ptr [esp + 0x14], eax
            lea eax, [esp + 0x14]
            jmp 0x5688ec
        '''
        quantity_code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(quantity_assembly, observer + 512)[0])
        if len(quantity_code) > 3584:
            raise RuntimeError('Creature-number observer exceeds its reserved code region')
        probe.write(kernel, process, observer + 512, quantity_code)
        actor_assembly = f'''
            pushfd
            pushad
            cmp dword ptr [{records}], 1
            jne actor_done
            mov edi, dword ptr [{records + 4}]
            cmp edi, 7
            jae actor_done
            shl edi, 3
            add edi, {records + 1600}
            mov eax, dword ptr [esp + 28]
            mov dword ptr [edi], eax
            mov eax, dword ptr [eax]
            mov dword ptr [edi + 4], eax
        actor_done:
            popad
            popfd
            mov edx, dword ptr [eax]
            lea ecx, [esp + 0x1c]
            push ecx
            jmp 0x56822f
        '''
        actor_code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(actor_assembly, observer + 1024)[0])
        if len(quantity_code) > 512 or len(actor_code) > 3072:
            raise RuntimeError('Actor observers exceed their reserved code regions')
        probe.write(kernel, process, observer + 1024, actor_code)
        probe.checked(kernel.VirtualProtectEx(process, observer, 4096, 0x20, ctypes.byref(old)))
        probe.checked(kernel.VirtualProtectEx(process, 0x568234, 5, 0x40, ctypes.byref(old)))
        probe.write(kernel, process, 0x568234, b'\xe9' + struct.pack('<i', observer - 0x568234 - 5))
        probe.checked(kernel.VirtualProtectEx(process, 0x568234, 5, old.value, ctypes.byref(ignored)))
        probe.checked(kernel.VirtualProtectEx(process, 0x5688e4, 8, 0x40, ctypes.byref(old)))
        probe.write(kernel, process, 0x5688e4, b'\xe9' + struct.pack('<i', observer + 512 - 0x5688e4 - 5) + b'\x90' * 3)
        probe.checked(kernel.VirtualProtectEx(process, 0x5688e4, 8, old.value, ctypes.byref(ignored)))
        probe.checked(kernel.VirtualProtectEx(process, 0x568228, 7, 0x40, ctypes.byref(old)))
        probe.write(kernel, process, 0x568228, b'\xe9' + struct.pack('<i', observer + 1024 - 0x568228 - 5) + b'\x90' * 2)
        probe.checked(kernel.VirtualProtectEx(process, 0x568228, 7, old.value, ctypes.byref(ignored)))
    probe.checked(kernel.FlushInstructionCache(process, None, 0))
    state = {'pid': pid, 'created': probe.creation_time(kernel, process),
             'allocation': allocation, 'patch': patch.hex(), 'result_patch': result_patch.hex(),
             'exe_sha256': probe.HASHES['H5_Game.exe'], 'observe_deployment': observe}
    state['levelup_observer'] = install_levelup_observer(probe, kernel, process)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
    temporary.replace(STATE)


def execute(probe, command=None, timeout=10, result_required=False, mode=None):
    state = json.loads(STATE.read_text(encoding='utf-8'))
    kernel = probe.api()
    process = probe.checked(kernel.OpenProcess(0x1038, False, state['pid']))
    try:
        if probe.creation_time(kernel, process) != state['created']:
            raise RuntimeError('Game-control PID changed')
        if probe.read(kernel, process, ENTRY, 5).hex() != state['patch']:
            raise RuntimeError('Game-control hook changed')
        if probe.read(kernel, process, RESULT_ENTRY, 6).hex() != state['result_patch']:
            raise RuntimeError('Game-control result hook changed')
        data = state['allocation'] + 4096
        if probe.read(kernel, process, data, 8) != MAGIC:
            raise RuntimeError('Game-control signature changed')
        if command is None:
            heartbeat, status = struct.unpack('<II', probe.read(kernel, process, data + 8, 8))
            return {'pid': state['pid'], 'heartbeat': heartbeat, 'mailbox_status': status}
        token = uuid.uuid4().hex[:16]
        command = command.replace(RESULT_KEY, RESULT_KEY + ':' + token)
        context = mode if mode is not None else {'@': 2, '#': 1, '$': 22}.get(command[:1], 0)
        payload = command[1:] if context and mode is None else command
        encoded = (payload + '\0').encode('ascii' if context else 'utf-16-le')
        if not command or '\0' in command or len(encoded) > 8192:
            raise ValueError('Expected 1..4095 UTF-16 code units without embedded NUL')
        # Serialize terminal writers; publish ready only after the full payload.
        lock = STATE.with_suffix('.lock')
        lock_handle = lock.open('x', encoding='ascii')
        try:
            status = struct.unpack('<I', probe.read(kernel, process, data + 12, 4))[0]
            if status in (1, 2):
                raise RuntimeError('Previous command is still pending; inspect status before retrying')
            probe.write(kernel, process, state['allocation'] + 8192, encoded)
            probe.write(kernel, process, data + 20, struct.pack('<I', context))
            probe.write(kernel, process, data + 24, bytes(8))
            probe.write(kernel, process, data + 64, token.encode('ascii'))
            probe.write(kernel, process, data + 12, struct.pack('<I', 1))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                status = struct.unpack('<I', probe.read(kernel, process, data + 12, 4))[0]
                response_size, response_ready = struct.unpack('<II', probe.read(kernel, process, data + 24, 8))
                if status == 3 and (not result_required or response_ready):
                    if result_required:
                        response = probe.read(kernel, process, data + 1024, min(response_size, 2047)).decode('utf-8', errors='replace')
                        return {'pid': state['pid'], 'status': 'completed', 'result': response}
                    return {'pid': state['pid'], 'status': 'dispatched', 'command': command,
                            'note': 'Dispatcher returned; verify the game-side effect separately.'}
                time.sleep(0.05)
            raise TimeoutError('Command remains pending; it was not cancelled or resubmitted')
        finally:
            lock_handle.close()
            lock.unlink()
    finally:
        kernel.CloseHandle(process)


def decode_runtime_state(read):
    """Read the native event queue and console contexts without executing Lua."""
    def words(address, count):
        return struct.unpack('<' + str(count) + 'I', read(address, count * 4))
    def lookup(begin, end, key):
        if begin == end == 0:
            return None
        if not begin or end <= begin or (end - begin) % 4 or (end - begin) // 4 > 8192:
            raise RuntimeError('Invalid native context bucket vector')
        node = words(begin + (key % ((end - begin) // 4)) * 4, 1)[0]
        visited = set()
        while node:
            if node in visited or len(visited) >= 256:
                raise RuntimeError('Invalid native context bucket chain')
            visited.add(node)
            following, identifier = words(node, 2)
            if identifier == key:
                return node + 8
            node = following
        return None
    event_root = words(0xfd2d9c, 1)[0]
    event_pending = None if not event_root else words(event_root, 1)[0] != event_root
    begin, end = words(0x1022bb8, 2)
    manager_slot = lookup(begin, end, 0)
    manager = words(manager_slot, 1)[0] if manager_slot else 0
    contexts = {}
    for label, identifier in (('adventure', 1), ('combat_console', 21)):
        entry = lookup(*words(manager + 0x28, 2), identifier) if manager else None
        if entry is None:
            contexts[label] = {'registered_listener_count': 0, 'script_commands_pending': None}
            continue
        vm_begin, vm_end, vm_capacity, queue = words(entry, 4)
        if vm_end < vm_begin or vm_end > vm_capacity or (vm_end - vm_begin) % 4 or (vm_end - vm_begin) // 4 > 256:
            raise RuntimeError('Invalid native script VM vector')
        contexts[label] = {'registered_listener_count': (vm_end - vm_begin) // 4,
                           'script_commands_pending': None if not queue else words(queue, 1)[0] != queue}
    return {'event_commands_pending': event_pending, 'script_contexts': contexts}


def runtime_state(probe):
    identity = execute(probe)
    state = json.loads(STATE.read_text(encoding='utf-8'))
    kernel = probe.api()
    process = probe.checked(kernel.OpenProcess(0x1010, False, state['pid']))
    try:
        if identity['pid'] != state['pid'] or probe.creation_time(kernel, process) != state['created']:
            raise RuntimeError('Runtime query process identity changed')
        for attempt in range(3):
            try:
                snapshot = decode_runtime_state(lambda address, size: probe.read(kernel, process, address, size))
                repeated = decode_runtime_state(lambda address, size: probe.read(kernel, process, address, size))
                if snapshot == repeated:
                    return {'pid': state['pid'], 'stable': True, **snapshot}
            except (OSError, RuntimeError):
                if attempt == 2:
                    raise
        return {'pid': state['pid'], 'stable': False}
    finally:
        kernel.CloseHandle(process)


def hero_state(probe, name):
    if not re.fullmatch(r'[A-Za-z0-9_ -]{1,80}', name):
        raise ValueError('Invalid hero identifier')
    script = (f"@if IsHeroAlive('{name}') then local x,y,f=GetObjectPosition('{name}'); "
              f"SetGameVar('{RESULT_KEY}',x..'|'..y..'|'..f..'|'..GetHeroLevel('{name}')"
              f"..'|'..GetHeroStat('{name}',7)); else SetGameVar('{RESULT_KEY}','not_found'); end")
    response = execute(probe, script, result_required=True)
    if response['result'] == 'not_found':
        raise ValueError('Hero not found: ' + name)
    values = [int(value) for value in response.pop('result').split('|')]
    response.update(hero=name, x=values[0], y=values[1], floor=values[2], level=values[3], movement=values[4])
    return response


def main(argv=None):
    global _probe_module
    if _probe_module is None:
        specification = importlib.util.spec_from_file_location('control_probe', Path(__file__).resolve().with_name('native-probe.py'))
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        _probe_module = module
    probe = _probe_module
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=10)
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('status', 'runtime-state', 'levelup-state', 'heroes', 'confirm', 'results', 'quit'):
        actions.add_parser(action)
    for action in ('console', 'eval', 'event'):
        actions.add_parser(action).add_argument('text')
    actions.add_parser('serve', help='Keep a JSONL terminal session attached to the running sandbox game')
    actions.add_parser('deployment-observation', help='Read the armed after-start position observer').add_argument('--arm', action='store_true')
    actions.add_parser('hero').add_argument('name')
    actions.add_parser('objects', help='Read the generated polygon object catalog, not live game state')
    actions.add_parser('day', help='Read the current adventure day')
    item = actions.add_parser('creature', help='Read or set total hero creatures of one type')
    item.add_argument('name')
    item.add_argument('creature', help='Lua constant, e.g. CREATURE_PEASANT')
    item.add_argument('--count', type=int)
    item = actions.add_parser('resource', help='Read or set one player resource')
    item.add_argument('player', type=int, choices=range(1, 9))
    item.add_argument('resource', type=int, choices=range(7))
    item.add_argument('--amount', type=int)
    for action in ('teleport', 'move'):
        item = actions.add_parser(action)
        item.add_argument('name')
        item.add_argument('x', type=int)
        item.add_argument('y', type=int)
        item.add_argument('--floor', type=int, default=0)
    item = actions.add_parser('level')
    item.add_argument('name')
    item.add_argument('target', type=int)
    item.add_argument('--choice', type=int, choices=range(1, 5), default=1)
    item = actions.add_parser('interact')
    item.add_argument('name')
    item.add_argument('object')
    actions.add_parser('finish').add_argument('--winner', type=int, choices=(0, 1), default=0)
    arguments = parser.parse_args(argv)
    if arguments.timeout <= 0 or arguments.timeout > 60:
        parser.error('Timeout must be in (0,60] seconds')
    action = arguments.action
    if action == 'serve':
        print(json.dumps({'status': 'ready', 'protocol': 'game-control-jsonl-v1'}, ensure_ascii=False), flush=True)
        for raw_line in sys.stdin:
            try:
                request = json.loads(raw_line)
                if not isinstance(request, dict) or set(request) - {'command', 'mode', 'result_required', 'timeout'}:
                    raise ValueError('Expected an object with command, mode, result_required, and timeout only')
                command = request.get('command')
                if command is not None and not isinstance(command, str):
                    raise ValueError('command must be a string or omitted for status')
                mode = request.get('mode')
                if mode is not None and mode not in (0, 1, 2, 22, 24, 25):
                    raise ValueError('mode must be one of 0, 1, 2, 22, 24, or 25')
                result_required = request.get('result_required', False)
                if not isinstance(result_required, bool):
                    raise ValueError('result_required must be boolean')
                timeout = request.get('timeout', arguments.timeout)
                if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
                    raise ValueError('timeout must be in (0,60] seconds')
                response = execute(probe, command, timeout, result_required=result_required, mode=mode)
                print(json.dumps({'ok': True, 'response': response}, ensure_ascii=False), flush=True)
            except (OSError, RuntimeError, TimeoutError, ValueError, json.JSONDecodeError) as error:
                print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), flush=True)
        return
    if action == 'runtime-state':
        result = runtime_state(probe)
    elif action == 'levelup-state':
        result = levelup_state(probe)
    elif action == 'deployment-observation':
        execute(probe)  # Validate the existing control PID, creation time and hooks.
        state = json.loads(STATE.read_text(encoding='utf-8'))
        if not state.get('observe_deployment'):
            raise RuntimeError('Launch with --control --native-loader to enable this observer')
        kernel = probe.api()
        process = probe.checked(kernel.OpenProcess(0x1038, False, state['pid']))
        try:
            if probe.creation_time(kernel, process) != state['created']:
                raise RuntimeError('Observer PID was reused')
            observer = state['allocation'] + 16384
            if probe.read(kernel, process, 0x568234, 5) != b'\xe9' + struct.pack('<i', observer - 0x568234 - 5):
                raise RuntimeError('Position observer hook changed')
            records = state['allocation'] + 20480
            probe.write(kernel, process, records, bytes(4))
            sequence = struct.unpack('<I', probe.read(kernel, process, records + 12, 4))[0]
            if arguments.arm:
                sequence += 1
                probe.write(kernel, process, records + 4, struct.pack('<3I', 0, 0, sequence) + bytes(1640))
                probe.write(kernel, process, records, struct.pack('<I', 1))
                result = {'pid': state['pid'], 'status': 'armed', 'sequence': sequence}
            else:
                data = probe.read(kernel, process, records, 1656)
                count, overflow = struct.unpack_from('<2I', data, 4)
                if count > 7 or overflow:
                    raise RuntimeError('Position observer overflow; result is not complete')
                quantity_count, quantity_overflow = struct.unpack_from('<2I', data, 800)
                if quantity_count > 7 or quantity_overflow:
                    raise RuntimeError('Creature-number observer overflow; result is not complete')
                quantities = {}
                for index in range(quantity_count):
                    start = 816 + index * 112
                    length = struct.unpack_from('<I', data, start + 104)[0]
                    if length > 96:
                        raise RuntimeError('Invalid creature-number name length')
                    name = data[start + 8:start + 8 + length].decode('ascii')
                    quantity = struct.unpack_from('<I', data, start)[0]
                    if name in quantities or quantity > 100000000:
                        raise RuntimeError('Invalid or duplicate creature-number observation')
                    quantities[name] = quantity
                units = []
                for index in range(count):
                    start = 16 + index * 112
                    length = struct.unpack_from('<I', data, start + 104)[0]
                    if length > 96:
                        raise RuntimeError('Invalid observed name length')
                    name = data[start + 8:start + 8 + length].decode('ascii')
                    match = re.fullmatch(r'defender-creature-\d+-(CREATURE_[A-Z0-9_]+)', name)
                    if not match:
                        raise RuntimeError('Unexpected observed unit name: ' + name)
                    unit = {'creature': match.group(1), 'cell': list(struct.unpack_from('<2i', data, start)), 'unit': name}
                    if name in quantities:
                        unit['quantity'] = quantities[name]
                    actor, table = struct.unpack_from('<2I', data, 1600 + index * 8)
                    if actor and table:
                        unit['actor_vtable'] = table
                        unit['stat_getters'] = {
                            label: struct.unpack('<I', probe.read(kernel, process, table + offset, 4))[0]
                            for label, offset in (('attack', 0x198), ('defence', 0x19c),
                                                  ('quantity', 0x1d8), ('min_damage', 0x1ec),
                                                  ('max_damage', 0x1f0), ('initiative', 0x1bc))}
                    units.append(unit)
                result = {'pid': state['pid'], 'status': 'captured', 'sequence': sequence, 'units': units}
        finally:
            kernel.CloseHandle(process)
    elif action == 'objects':
        report = json.loads((ROOT / '.local/test-state/test-map.json').read_text(encoding='utf-8'))
        result = {'source': 'generated_map_report', 'live': False, 'map_sha256': report['sha256'],
                  'objects': report['objects']}
    elif action == 'day':
        result = execute(probe, f"@SetGameVar('{RESULT_KEY}',''..GetDate(DAY))", result_required=True)
        result['day'] = int(result.pop('result'))
    elif action == 'creature':
        if not re.fullmatch(r'CREATURE_[A-Z0-9_]{1,70}', arguments.creature):
            parser.error('Expected a CREATURE_* Lua constant')
        if arguments.count is not None and not 0 <= arguments.count <= 1000000:
            parser.error('Count must be 0..1000000')
        hero_state(probe, arguments.name)
        creature = arguments.creature
        query = (f"@if {creature} then SetGameVar('{RESULT_KEY}',''..GetHeroCreatures('{arguments.name}',{creature})); "
                 f"else SetGameVar('{RESULT_KEY}','unknown_creature'); end")
        result = execute(probe, query, result_required=True)
        if result['result'] == 'unknown_creature':
            parser.error('Unknown creature constant')
        before = int(result.pop('result'))
        count = before
        if arguments.count is not None and before != arguments.count:
            function = 'AddHeroCreatures' if arguments.count > before else 'RemoveHeroCreatures'
            execute(probe, f"@{function}('{arguments.name}',{creature},{abs(arguments.count - before)})")
            result = execute(probe, query, result_required=True)
            count = int(result.pop('result'))
            if count != arguments.count:
                raise RuntimeError(f'Creature count not reached: before={before}, actual={count}, requested={arguments.count}')
        result.update(hero=arguments.name, creature=creature, before=before, count=count)
    elif action == 'resource':
        if arguments.amount is not None and not 0 <= arguments.amount <= 100000000:
            parser.error('Amount must be 0..100000000')
        query = f"@SetGameVar('{RESULT_KEY}',''..GetPlayerResource({arguments.player},{arguments.resource}))"
        result = execute(probe, query, result_required=True)
        before = int(result.pop('result'))
        amount = before
        if arguments.amount is not None and before != arguments.amount:
            execute(probe, f'@SetPlayerResource({arguments.player},{arguments.resource},{arguments.amount})')
            result = execute(probe, query, result_required=True)
            amount = int(result.pop('result'))
            if amount != arguments.amount:
                raise RuntimeError(f'Resource amount not reached: actual={amount}, requested={arguments.amount}')
        result.update(player=arguments.player, resource=arguments.resource, before=before, amount=amount)
    elif action == 'hero':
        result = hero_state(probe, arguments.name)
    elif action in ('teleport', 'move'):
        hero_state(probe, arguments.name)
        function = 'SetObjectPosition' if action == 'teleport' else 'MoveHeroRealTime'
        script = f"{function}('{arguments.name}',{arguments.x},{arguments.y},{arguments.floor})"
        execute(probe, '@' + script)
        deadline = time.monotonic() + arguments.timeout
        while True:
            result = hero_state(probe, arguments.name)
            if (result['x'], result['y'], result['floor']) == (arguments.x, arguments.y, arguments.floor):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Hero did not reach destination: ' + json.dumps(result))
            time.sleep(0.1)
        if action == 'teleport':
            # Coordinates update before subsequent walking is accepted in this build.
            # Empirical settling interval, not an engine readiness signal.
            time.sleep(2)
            result = hero_state(probe, arguments.name)
            if (result['x'], result['y'], result['floor']) != (arguments.x, arguments.y, arguments.floor):
                raise RuntimeError('Hero position changed while teleport settled: ' + json.dumps(result))
            execute(probe, f"@if GetObjectOwner('{arguments.name}')==GetCurrentPlayer() then "
                    f"MoveCamera({arguments.x},{arguments.y},{arguments.floor},50,1.57079632679,0,1,1); end")
            result['camera_follow_requested'] = True
    elif action == 'level':
        if not 1 <= arguments.target <= 40:
            parser.error('Target level must be 1..40')
        result = hero_state(probe, arguments.name)
        for _ in range(max(0, arguments.target - result['level'])):
            expected_level = result['level'] + 1
            execute(probe, f"@LevelUpHero('{arguments.name}')")
            deadline = time.monotonic() + arguments.timeout
            while result['level'] < expected_level:
                execute(probe, 'skill_' + str(arguments.choice), mode=24)
                execute(probe, 'MB_Button_Ok', mode=24)
                result = hero_state(probe, arguments.name)
                if time.monotonic() >= deadline and result['level'] < expected_level:
                    raise TimeoutError('Level selection did not complete')
                time.sleep(0.1)
        if result['level'] != arguments.target:
            raise RuntimeError('Requested level not confirmed: ' + json.dumps(result))
    elif action == 'interact':
        hero_state(probe, arguments.name)
        if not re.fullmatch(r'[A-Za-z0-9_ -]{1,100}', arguments.object):
            parser.error('Invalid object identifier')
        result = execute(probe, f"@MakeHeroInteractWithObject('{arguments.name}','{arguments.object}')")
    elif action == 'finish':
        result = execute(probe, f'@Finish({arguments.winner})')
    elif action in ('event', 'confirm', 'results'):
        event = {'confirm': 'confirm_placement', 'results': 'enter_pressed'}.get(action, getattr(arguments, 'text', None))
        result = execute(probe, event, mode=24)
    elif action == 'quit':
        state = json.loads(STATE.read_text(encoding='utf-8'))
        kernel = probe.api()
        process = probe.checked(kernel.OpenProcess(0x100000, False, state['pid']))
        try:
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            execute(probe, 'exit', mode=25)
            if kernel.WaitForSingleObject(process, int(arguments.timeout * 1000)) != 0:
                raise TimeoutError('Native exit requested but process has not exited')
            result = {'pid': state['pid'], 'status': 'game_closed'}
        finally:
            kernel.CloseHandle(process)
    else:
        command = getattr(arguments, 'text', None)
        if action == 'heroes':
            command = 'GetAllNames(0)'
        if action in ('eval', 'heroes'):
            command = "@SetGameVar('" + RESULT_KEY + "', ''..(" + command + '))'
        result = execute(probe, command, arguments.timeout, result_required=action in ('eval', 'heroes'))
    if argv is None:
        print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    main()
