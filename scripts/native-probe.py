"""Per-process x86 UI probe/reference visibility. No guard reads or on-disk patches."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
from zipfile import ZipFile
from game_launch import map_arguments


from workspace import workspace_root, game_installation

ROOT = workspace_root()
GAME = ROOT / '.local/test-game'
STATE = ROOT / '.local/test-state/native-probe.json'
HASHES = {
    'H5_Game.exe': '88c9dc6107b9bced0649924a86360f1c56397ee00de0413f6f2b08f865ed5519',
    'uni.dll': 'aa5211151d9e9a8c135e180ff8832908d128ccae08a5145162bcdae4946c18ee',
    'um.dll': '1956c00b371d22a3e1a644394ff3e7159b6ec36d660d5ffa36628fcf63fd0fc6',
    'd3d9.dll': '5eb152357f99d53397b764384d5cf9a0f6aece733ced30a34186ac57fb15be25',
}
ENTRY = 0x5f8050
ORIGINAL = bytes.fromhex('83 ec 70 57 8b fa')
MAGIC = b'H5UIcnt1'
LAYOUT_ENTRY = 0x5f8800
LAYOUT_ORIGINAL = bytes.fromhex('8b 2d 68 96 fd 00')


def layout_data(counter, routes):
    """Pack a bounded public-title routing table and one cache slot per family."""
    if not routes or len(routes) > 64:
        raise ValueError('Expected 1..64 public title routes')
    data = bytearray(1024 + 512 * len(routes))
    data[:8] = MAGIC
    name = b'WorkshopReferenceArmy'
    struct.pack_into('<III', data, 64, counter + 96, counter + 96 + len(name), counter + 97 + len(name))
    data[96:96 + len(name) + 1] = name + b'\0'
    families = sorted({route['family'] for route in routes})
    if len(families) > 64:
        raise ValueError('Too many window families')
    seen = set()
    for index, route in enumerate(routes):
        title = route['title'].encode('utf-16-le')
        window_id = route['window_id'].encode('ascii')
        if not title or len(title) > 256 or len(window_id) > 63 or title in seen:
            raise ValueError('Invalid or duplicate public title route')
        seen.add(title)
        offset = 1024 + index * 512
        struct.pack_into('<I', data, offset, len(title))
        struct.pack_into('<III', data, offset + 16, counter + offset + 352,
                         counter + offset + 352 + len(window_id), counter + offset + 353 + len(window_id))
        struct.pack_into('<I', data, offset + 28, counter + 256 + families.index(route['family']) * 4)
        data[offset + 64:offset + 64 + len(title)] = title
        data[offset + 352:offset + 352 + len(window_id)] = window_id
    return bytes(data)


def layout_trampoline(address, counter, routes, *, return_to_caller=False):
    from keystone import Ks, KS_ARCH_X86, KS_MODE_32
    # Select a separate root before the native updater fills/layouts it.
    # EDI is the public tooltip model here. Never inspect its guard fields.
    # The two strategies are the untouched default root and the registered
    # reference template. The prototype uses the public title as its key.
    # A managed host may CALL the selector and regain control for retirement.
    # Managed mode is cdecl uint32_t(uint32_t publicTooltip): EAX is the selected
    # root, saved registers/flags survive, and the caller removes its argument.
    instructions = f'''
        {'' if return_to_caller else 'mov ebp, dword ptr [0xfd9668]'}
        pushfd
        pushad
        {'mov edi, dword ptr [esp + 40]; mov ebp, dword ptr [0xfd9668]; mov dword ptr [esp + 28], ebp' if return_to_caller else ''}
        lock inc dword ptr [{counter + 8}]
        mov ecx, edi
        mov eax, dword ptr [ecx]
        call dword ptr [eax]
        test eax, eax
        jz done
        mov ebp, dword ptr [eax]
        test ebp, ebp
        jz done
        mov edx, dword ptr [eax + 4]
        sub edx, ebp
        mov ebx, {counter + 1024}
    next_route:
        cmp edx, dword ptr [ebx]
        jne advance_route
        mov esi, ebp
        lea edi, [ebx + 64]
        mov ecx, edx
        shr ecx, 1
        cld
        repe cmpsw
        je matched
    advance_route:
        add ebx, 512
        cmp ebx, {counter + 1024 + len(routes) * 512}
        jb next_route
        jmp done
    matched:
        lock inc dword ptr [{counter + 12}]
        mov eax, dword ptr [ebx + 28]
        mov ebp, dword ptr [eax]
        test ebp, ebp
        jnz select_root
        mov ecx, 0x1109bc00
        call 0xa4aa50
        test eax, eax
        jz done
        mov esi, eax
        mov edi, dword ptr [esi]
        lea ecx, [ebx + 16]
        call 0x575700
        test eax, eax
        jz done
        push eax
        mov ecx, esi
        call dword ptr [edi + 40]
        test eax, eax
        jz done
        mov ebp, eax
        mov eax, dword ptr [ebp + 4]
        mov eax, dword ptr [eax + 4]
        add dword ptr [ebp + eax + 8], 1
        mov edx, dword ptr [ebx + 28]
        mov dword ptr [edx], ebp
        mov eax, dword ptr [ebp + 4]
        mov eax, dword ptr [eax + 4]
        lea eax, [ebp + eax + 4]
        push 0
        push 0xf33380
        push 0xf272c0
        push 0
        push eax
        call 0xd119fc
        add esp, 20
        test eax, eax
        jz done
        mov esi, eax
        mov edx, dword ptr [esi]
        push 10
        push 1
        push {counter + 64}
        mov ecx, esi
        call dword ptr [edx]
    select_root:
        mov eax, dword ptr [ebp + 4]
        mov eax, dword ptr [eax + 4]
        test byte ptr [ebp + eax + 11], 0x80
        jnz done
        mov dword ptr [esp + {28 if return_to_caller else 8}], ebp
        lock inc dword ptr [{counter + 16}]
    done:
        popad
        popfd
        {'ret' if return_to_caller else f'jmp {LAYOUT_ENTRY + len(LAYOUT_ORIGINAL)}'}
    '''
    return bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(instructions, addr=address)[0])


def trampoline(address, counter):
    # pushfd; pushad; lock inc dword [counter]; popad; popfd; stolen instructions; jmp back.
    code = b'\x9c\x60\xf0\xff\x05' + struct.pack('<I', counter) + b'\x61\x9d' + ORIGINAL
    return code + b'\xe9' + struct.pack('<i', ENTRY + len(ORIGINAL) - (address + len(code) + 5))


class Startup(C.Structure):
    _fields_ = [('cb', W.DWORD), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR), ('title', W.LPWSTR),
                ('x', W.DWORD), ('y', W.DWORD), ('xsize', W.DWORD), ('ysize', W.DWORD),
                ('xchars', W.DWORD), ('ychars', W.DWORD), ('fill', W.DWORD), ('flags', W.DWORD),
                ('show', W.WORD), ('reserved_size', W.WORD), ('reserved_data', C.c_void_p),
                ('stdin', W.HANDLE), ('stdout', W.HANDLE), ('stderr', W.HANDLE)]


class Process(C.Structure):
    _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE), ('pid', W.DWORD), ('tid', W.DWORD)]


def api():
    kernel = C.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateProcessW': (W.BOOL, [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD,
                                  C.c_void_p, W.LPCWSTR, C.POINTER(Startup), C.POINTER(Process)]),
        'ReadProcessMemory': (W.BOOL, [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]),
        'WriteProcessMemory': (W.BOOL, [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]),
        'VirtualAllocEx': (C.c_void_p, [W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD, W.DWORD]),
        'VirtualProtectEx': (W.BOOL, [W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD, C.POINTER(W.DWORD)]),
        'VirtualFreeEx': (W.BOOL, [W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD]),
        'FlushInstructionCache': (W.BOOL, [W.HANDLE, C.c_void_p, C.c_size_t]),
        'ResumeThread': (W.DWORD, [W.HANDLE]), 'CloseHandle': (W.BOOL, [W.HANDLE]),
        'TerminateProcess': (W.BOOL, [W.HANDLE, W.UINT]),
        'WaitForSingleObject': (W.DWORD, [W.HANDLE, W.DWORD]),
        'OpenProcess': (W.HANDLE, [W.DWORD, W.BOOL, W.DWORD]),
        'GetProcessTimes': (W.BOOL, [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4),
        'QueryFullProcessImageNameW': (W.BOOL, [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(kernel, name)
        function.restype, function.argtypes = result, arguments
    return kernel


def checked(result):
    if not result:
        raise C.WinError(C.get_last_error())
    return result


def read(kernel, process, address, size):
    buffer = C.create_string_buffer(size)
    count = C.c_size_t()
    checked(kernel.ReadProcessMemory(process, address, buffer, size, C.byref(count)))
    if count.value != size:
        raise RuntimeError('Short process read')
    return buffer.raw


def write(kernel, process, address, data):
    buffer = C.create_string_buffer(data)
    count = C.c_size_t()
    checked(kernel.WriteProcessMemory(process, address, buffer, len(data), C.byref(count)))
    if count.value != len(data):
        raise RuntimeError('Short process write')


def creation_time(kernel, process):
    times = [W.FILETIME() for _ in range(4)]
    checked(kernel.GetProcessTimes(process, *(C.byref(value) for value in times)))
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def launch(kernel, army_layout=False, map_name=None, control=False, native_loader=False,
           observe_deployment=False, background=False, before_resume=None, launch_gate=None, graphics_facade_hash=None):
    if graphics_facade_hash is not None and (launch_gate is None or
            len(graphics_facade_hash) != 64 or any(value not in '0123456789abcdef' for value in graphics_facade_hash)):
        raise ValueError('A sealed graphics identity requires the owned SDK launch gate')
    if launch_gate is not None and native_loader:
        raise ValueError('Choose one owned launch helper.')
    if observe_deployment and not control:
        raise ValueError('Deployment observation requires --control.')
    if background and native_loader:
        raise ValueError('Background startup is only supported by the ordinary DLL-loaded launch.')
    running = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq H5_Game.exe', '/FO', 'CSV', '/NH'],
                             check=True, capture_output=True)
    if b'h5_game.exe' in running.stdout.lower():
        raise RuntimeError('Close the game before launching the diagnostic.')
    if not (ROOT / '.local/test-state/prepared.json').exists():
        raise RuntimeError('Prepare the sandbox first.')
    for name, expected in HASHES.items():
        actual = hashlib.sha256((GAME / 'bin' / name).read_bytes()).hexdigest()
        if name == 'd3d9.dll' and graphics_facade_hash is not None and actual == graphics_facade_hash:
            if hashlib.sha256((GAME / 'bin/d3d9.universe.dll').read_bytes()).hexdigest() != expected:
                raise RuntimeError('Unsupported retained graphics binary')
            continue
        if actual != expected:
            raise RuntimeError('Unsupported binary: ' + name)
    entry, original = (LAYOUT_ENTRY, LAYOUT_ORIGINAL) if army_layout else (ENTRY, ORIGINAL)
    routes = []
    if army_layout:
        import keystone  # Fail before starting a child if the assembler is unavailable.
        artifact = GAME / 'UserMODs/workshop-army-reference.h5u'
        deployment = json.loads((ROOT / '.local/test-state/army-reference.deployment.json').read_text())
        if hashlib.sha256(artifact.read_bytes()).hexdigest() not in deployment['owned_hashes']:
            raise RuntimeError('Deploy the owned army-reference layout first.')
        with ZipFile(artifact) as layout:
            paths = {name.lower() for name in layout.namelist()}
            if ('ui/workshoparmy/routes.json' not in paths or
                    any(name.startswith('ui/tooltips/commonadvobjtooltip/') for name in paths)):
                raise RuntimeError('Deploy the isolated reference window, not the old shared-panel prototype.')
            routes = json.loads(layout.read('UI/WorkshopArmy/routes.json').decode('utf-8'))
            layout_data(0, routes)
    info = Process()
    startup = Startup()
    startup.cb = C.sizeof(startup)
    if background:
        startup.flags = 0x1  # STARTF_USESHOWWINDOW
        startup.show = 8     # SW_SHOWNA: show without activating
    executable = str(GAME / 'bin/H5_Game.exe')
    map_options = map_arguments(GAME, map_name) if map_name else []
    command = C.create_unicode_buffer(subprocess.list2cmdline([executable, *map_options]))
    loader = None
    resumed = False
    expected_creation = None
    try:
        if native_loader or launch_gate is not None:
            loader_path = Path(launch_gate) if launch_gate is not None else ROOT / '.local/dist/deployment-preview-native/workshop_preview_loader.exe'
            loader_arguments = [str(loader_path), '--game', executable, '--prepare-stdin']
            if background and launch_gate is not None:
                loader_arguments.append('--background')
            loader = subprocess.Popen(loader_arguments,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding='utf-8')
            prepared = loader.stdout.readline().strip().split()
            fields = 4 if launch_gate is not None else 3
            if len(prepared) != fields or prepared[0] != 'PREPARED' or not all(value.isdecimal() for value in prepared[1:]):
                raise RuntimeError('Native launcher did not prepare a child: ' + ' '.join(prepared))
            info.pid, info.tid = int(prepared[1]), int(prepared[2])
            if launch_gate is not None:
                expected_creation = int(prepared[3])
        else:
            checked(kernel.CreateProcessW(executable, command, None, None, False, 4, None,
                                          str(GAME / 'bin'), C.byref(startup), C.byref(info)))
        if loader is not None:
            info.process = checked(kernel.OpenProcess(0x1038, False, info.pid))
            if expected_creation is not None:
                if creation_time(kernel, info.process) != expected_creation:
                    raise RuntimeError('Owned launch creation mismatch before preparation')
                filename = C.create_unicode_buffer(32768)
                filename_size = W.DWORD(len(filename))
                checked(kernel.QueryFullProcessImageNameW(info.process, 0, filename, C.byref(filename_size)))
                if Path(filename.value).resolve() != Path(executable).resolve():
                    raise RuntimeError('Owned launch path mismatch before preparation')
        if read(kernel, info.process, entry, len(original)) != original:
            raise RuntimeError('Entry bytes/base differ; refusing the probe.')
        # Reserve Granny's non-ASLR preferred base by loading it before the
        # ASLR-enabled Universe d3d9 import. Only this suspended sandbox's
        # import descriptors change; all on-disk game hashes remain intact.
        graphics_import = bytes.fromhex('80b9b10000000000000000000ccfb10020a5a000')
        granny_import = bytes.fromhex('b8bab100000000000000000062d5b10058a6a000')
        if (read(kernel, info.process, 0x11d90f0, 20) != graphics_import or
                read(kernel, info.process, 0x11d912c, 20) != granny_import):
            raise RuntimeError('Import descriptors differ; refusing the startup repair.')
        import_protection = W.DWORD()
        checked(kernel.VirtualProtectEx(info.process, 0x11d90f0, 80, 4, C.byref(import_protection)))
        try:
            write(kernel, info.process, 0x11d90f0, granny_import)
            write(kernel, info.process, 0x11d912c, graphics_import)
        finally:
            ignored_protection = W.DWORD()
            checked(kernel.VirtualProtectEx(info.process, 0x11d90f0, 80,
                                            import_protection.value, C.byref(ignored_protection)))
        allocation = checked(kernel.VirtualAllocEx(info.process, None, 65536, 0x3000, 4))
        if allocation + 65536 >= 0x80000000:
            raise RuntimeError('Allocation exceeds supported x86 address range.')
        counter = allocation + 4096
        startup_patch = None
        if map_options:
            # Registered engine option pwl_press_any_key_enabled: skip the
            # post-load acknowledgement in this test process only.
            if read(kernel, info.process, 0xf39868, 1) != b'\x01':
                raise RuntimeError('Unexpected loading acknowledgement default.')
            write(kernel, info.process, 0xf39868, b'\x00')
            startup_entry = 0xd112a7
            if read(kernel, info.process, startup_entry, 5) != bytes.fromhex('68 34 a8 e3 00'):
                raise RuntimeError('Startup command differs; refusing automatic map startup.')
            startup_text = allocation + 60000
            startup_command = ('advmap ' + map_options[1] + '\0').encode('utf-16-le')
            if len(startup_command) > 4000:
                raise RuntimeError('Map startup command is too long.')
            write(kernel, info.process, startup_text, startup_command)
            protection = W.DWORD()
            checked(kernel.VirtualProtectEx(info.process, startup_entry, 5, 0x40, C.byref(protection)))
            startup_patch = b'\x68' + struct.pack('<I', startup_text)
            write(kernel, info.process, startup_entry, startup_patch)
            ignored = W.DWORD()
            checked(kernel.VirtualProtectEx(info.process, startup_entry, 5, protection.value, C.byref(ignored)))
        code = layout_trampoline(allocation, counter, routes) if army_layout else trampoline(allocation, counter + 8)
        if len(code) > 4096:
            raise RuntimeError('Generated code exceeds its RX page')
        write(kernel, info.process, allocation, code)
        write(kernel, info.process, counter, MAGIC + bytes(12))
        if army_layout:
            write(kernel, info.process, counter, layout_data(counter, routes))
        old = W.DWORD()
        checked(kernel.VirtualProtectEx(info.process, allocation, 4096, 0x20, C.byref(old)))
        checked(kernel.VirtualProtectEx(info.process, entry, len(original), 0x40, C.byref(old)))
        patch = b'\xe9' + struct.pack('<i', allocation - entry - 5) + b'\x90' * (len(original) - 5)
        write(kernel, info.process, entry, patch)
        restore = W.DWORD()
        checked(kernel.VirtualProtectEx(info.process, entry, len(original), old.value, C.byref(restore)))
        checked(kernel.FlushInstructionCache(info.process, None, 0))
        state = {'pid': info.pid, 'created': creation_time(kernel, info.process),
                 'counter': counter, 'entry': entry, 'allocation': allocation, 'patch_size': len(original),
                 'mode': 'army_layout' if army_layout else 'counter',
                 'exe_sha256': HASHES['H5_Game.exe'], 'native_loader': native_loader,
                 'guarded_loader': launch_gate is not None}
        if startup_patch:
            state['startup'] = {'entry': startup_entry, 'patch': startup_patch.hex(), 'map': map_name}
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
        temporary.replace(STATE)
        if control:
            import game_control
            game_control.install(sys.modules[__name__], kernel, info.process, info.pid,
                                 observe=native_loader or observe_deployment)
        if before_resume is not None:
            before_resume(dict(state), kernel)
        if loader is not None:
            resumed = True
            output, errors = loader.communicate('resume\n', timeout=60)
            if loader.returncode != 0 or 'before game entry' not in output:
                raise RuntimeError('Native launcher failed: ' + output + errors)
            if launch_gate is not None:
                state['image_placement_verified'] = True
                temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
                temporary.replace(STATE)
        else:
            if kernel.ResumeThread(info.thread) == 0xffffffff:
                raise C.WinError(C.get_last_error())
            resumed = True
        return {'pid': info.pid, 'status': 'probe_started', 'calls': 0, 'map_arguments': map_options,
                'native_loader': native_loader, 'background_requested': background,
                'image_placement_verified': state.get('image_placement_verified', False)}
    finally:
        # Only terminate our own never-resumed child on setup failure, never a running game.
        try:
            if not resumed:
                if loader is not None:
                    original_error = sys.exc_info()[1]
                    try:
                        loader.communicate('cancel\n', timeout=10)
                    except Exception as cleanup_error:
                        if original_error is None:
                            raise
                        print(json.dumps({'event': 'owned_launch_cleanup_unconfirmed',
                                          'reason': str(cleanup_error)}), file=sys.stderr)
                elif info.process:
                    checked(kernel.TerminateProcess(info.process, 1))
                    if kernel.WaitForSingleObject(info.process, 5000) != 0:
                        raise RuntimeError('Never-resumed owned child termination is unconfirmed')
        finally:
            if info.thread:
                kernel.CloseHandle(info.thread)
            if info.process:
                kernel.CloseHandle(info.process)


def status(kernel):
    state = json.loads(STATE.read_text(encoding='utf-8'))
    process = checked(kernel.OpenProcess(0x1010, False, state['pid']))
    try:
        if creation_time(kernel, process) != state['created']:
            raise RuntimeError('PID was reused; this is not the probe process.')
        entry = state['entry']
        expected = b'\xe9' + struct.pack('<i', state['allocation'] - entry - 5) + b'\x90' * (state.get('patch_size', 6) - 5)
        if read(kernel, process, entry, len(expected)) != expected:
            raise RuntimeError('Another module changed the probe entry; counts are not valid.')
        if 'startup' in state:
            startup = state['startup']
            if read(kernel, process, startup['entry'], 5) != bytes.fromhex(startup['patch']):
                raise RuntimeError('Startup command patch changed.')
        data = read(kernel, process, state['counter'], 20 if state.get('mode') == 'army_layout' else 12)
        if data[:8] != MAGIC:
            raise RuntimeError('Probe allocation signature differs.')
        if state.get('mode') == 'army_layout':
            calls, matches, found = struct.unpack('<III', data[8:])
            return {'pid': state['pid'], 'tooltip_calls': calls, 'bank_matches': matches,
                    'reference_window_selected': found}
        return {'pid': state['pid'], 'army_render_calls': struct.unpack('<I', data[8:])[0]}
    finally:
        kernel.CloseHandle(process)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['launch', 'status'])
    parser.add_argument('--army-layout', action='store_true')
    parser.add_argument('--menu', action='store_true', help='Stay in the menu instead of starting the test map.')
    parser.add_argument('--map', help='Start a sandbox map through the final startup command.')
    parser.add_argument('--control', action='store_true', help='Install the terminal command mailbox.')
    parser.add_argument('--native-loader', action='store_true', help='Start through the packaged native launcher.')
    parser.add_argument('--background', action='store_true',
                        help='Request no-activation startup; verify off-screen position separately.')
    parser.add_argument('--observe-deployment', action='store_true',
                        help='Add the after-Start observer to --control without a separate launcher.')
    arguments = parser.parse_args()
    sys.stderr.reconfigure(encoding='utf-8')
    from sdk_logging import EventLog
    events = EventLog(ROOT / '.local/xalkit/logs', console=True, stream=sys.stderr)
    try:
        with events.operation('native-probe ' + arguments.command):
            result = (launch(api(), arguments.army_layout, None if arguments.menu else arguments.map,
                             arguments.control, arguments.native_loader, arguments.observe_deployment,
                             arguments.background)
                      if arguments.command == 'launch' else status(api()))
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')
    finally:
        events.close()
