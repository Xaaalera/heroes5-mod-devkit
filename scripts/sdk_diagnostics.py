"""Owned-process diagnostics, crash monitoring and Windows resource budgets."""
import ctypes
import gzip
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import re


def input_profile_files(workspace, documents=None):
    """Inspect candidate binding files without changing any player settings."""
    if documents is None and os.name == 'nt':
        directory = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, directory) == 0:
            documents = Path(directory.value)
    profiles = [Path(workspace) / '.local/test-game/UniverseTeam/Universe Mod/Profiles']
    if documents is not None:
        profiles.extend(folder / 'Profiles' for folder in (Path(documents) / 'My Games').glob('Heroes of Might and Magic V*'))
    records = []
    for directory in profiles:
        settings = directory / 'global_a2.cfg'
        if not settings.is_file():
            continue
        match = re.search(r'(?m)^setvar profile_name\s*=\s*(\S+)\s*$', settings.read_text(encoding='utf-8'))
        if not match:
            continue
        target = (directory / match.group(1) / 'input_a2.cfg').resolve()
        if not target.is_relative_to(directory.resolve()):
            continue
        records.append({'profile': match.group(1), 'path': str(target), 'present': target.is_file()})
    return records


class OwnedBackgroundBudget:
    """Windows CPU quota for this retained SDK game, released in foreground."""
    def __init__(self, probe, kernel, owner):
        self.probe, self.kernel, self.owner = probe, kernel, dict(owner)
        self.job = None
        self.background = None
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        process = probe.checked(kernel.OpenProcess(0x101101, False, owner['pid']))
        try:
            if probe.creation_time(kernel, process) != owner['created']:
                raise RuntimeError('Background budget owner changed')
            self.job = probe.checked(kernel.CreateJobObjectW(None, None))
            probe.checked(kernel.AssignProcessToJobObject(self.job, process))
        except Exception:
            if self.job:
                kernel.CloseHandle(self.job)
                self.job = None
            raise
        finally:
            kernel.CloseHandle(process)

    def update(self, foreground_pid, active=False):
        background = foreground_pid != self.owner['pid'] and not active
        if background == self.background:
            return
        if not background and self.background is None:
            # A new job is already unlimited; Windows need not disable a quota
            # that has never been configured.
            self.background = False
            return
        # JOBOBJECT_CPU_RATE_CONTROL_INFORMATION: 2% of total CPU while
        # background, unlimited in foreground. No kill-on-close policy.
        information = (wintypes.DWORD * 2)(5 if background else 0, 200 if background else 10000)
        self.probe.checked(self.kernel.SetInformationJobObject(self.job, 15, information, ctypes.sizeof(information)))
        self.background = background

    def close(self):
        if self.job:
            if self.background:
                self.update(self.owner['pid'])
            self.kernel.CloseHandle(self.job)
            self.job = None


def read_plugin_trace(root, owner, after, minimum):
    """Borrow an already resident SDK core; never stop or reload a plugin."""
    from plugin_core import decode_diagnostics, owned_core_endpoint
    paths = owned_core_endpoint(root, owner)
    result = subprocess.run([str(paths['controller']), '--owned-read', str(owner['pid']), str(owner['created']),
        str((Path(root) / '.local/test-game/bin/H5_Game.exe').resolve()), str(paths['bridge'])],
        input=f'trace {after} {minimum}\nquit\n', capture_output=True, text=True, encoding='utf-8', timeout=15)
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    if result.returncode or len(replies) != 2 or replies[0] != {'ready': True} or replies[1].get('status') != 0:
        raise RuntimeError('Diagnostic read failed: ' + result.stderr)
    return decode_diagnostics(replies[1]['diagnostics'])


def read_dump_identity(path, owner):
    """Read only documented minidump identity/exception streams, with bounds."""
    if not path.exists() and path.with_suffix('.dmp.gz').exists():
        path = path.with_suffix('.dmp.gz')
    opener = gzip.open if path.name.endswith('.gz') else open
    with opener(path, 'rb') as source:
        source.seek(0, 2)
        file_bytes = source.tell()
        source.seek(0)
        header = source.read(32)
        if len(header) != 32 or header[:4] != b'MDMP':
            return None
        count, directory = struct.unpack_from('<II', header, 8)
        if count > 4096 or directory + count * 12 > file_bytes:
            return None
        source.seek(directory)
        entries = source.read(count * 12)
        identity = {}
        for index in range(count):
            kind, size, offset = struct.unpack_from('<III', entries, index * 12)
            if offset + size > file_bytes:
                return None
            if kind == 15 and size >= 24:
                source.seek(offset)
                declared_size, flags, pid, created = struct.unpack('<4I', source.read(16))
                if declared_size < 24 or declared_size > size:
                    return None
                if flags & 3 == 3:
                    identity.update(pid=pid, created=created)
            elif kind == 6:
                if size < 168:
                    return None
                source.seek(offset)
                stream = source.read(168)
                thread, _, exception = struct.unpack_from('<3I', stream)
                parameters = struct.unpack_from('<I', stream, 32)[0]
                context_size, context_offset = struct.unpack_from('<II', stream, 160)
                if parameters > 15 or context_size == 0 or context_offset + context_size > file_bytes:
                    return None
                identity.update(thread_id=thread, exception_code=exception)
        expected_created = owner['created'] // 10000000 - 11644473600
        if identity.get('pid') != owner['pid'] or identity.get('created') != expected_created or not identity.get('exception_code'):
            return None
        return {'path': str(path), **identity}


def read_owned_windows_events(owner, executable):
    """Match Application Error events to immutable process identity, never PID alone."""
    command = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$start = [DateTime]::FromFileTimeUtc([long]$env:XALKIT_EVENT_CREATED)
try {
    $events = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=$start} -MaxEvents 128 -ErrorAction Stop)
} catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $events = @() } else { throw }
}
$records = @($events | ForEach-Object {
    $document = [xml]$_.ToXml()
    $data = @{}
    foreach ($field in $document.Event.EventData.Data) { $data[[string]$field.Name] = [string]$field.'#text' }
    @{timestamp=$_.TimeCreated.ToUniversalTime().ToString('o'); record_id=$_.RecordId; data=$data}
})
ConvertTo-Json -InputObject $records -Depth 4 -Compress
"""
    environment = {**os.environ, 'XALKIT_EVENT_CREATED': str(int(owner['created']))}
    result = subprocess.run(['powershell', '-NoProfile', '-Command', command], env=environment,
                            capture_output=True, text=True, encoding='utf-8', check=True, timeout=15)
    records = json.loads(result.stdout)
    expected_path = Path(executable).resolve()
    matched = []
    for record in records:
        data = record['data']
        try:
            same_process = (int(data.get('ProcessId', ''), 0), int(data.get('ProcessCreationTime', ''), 0)) == \
                           (owner['pid'], owner['created'])
        except (TypeError, ValueError):
            continue
        if not same_process or Path(data.get('AppPath', '')).resolve() != expected_path:
            continue
        matched.append({'event_id': 1000, 'record_id': record['record_id'], 'timestamp': record['timestamp'],
                        'game_pid': owner['pid'], 'game_created': owner['created'],
                        'module': data.get('ModuleName', ''), 'exception_code': data.get('ExceptionCode', ''),
                        'report_id': data.get('IntegratorReportId', '')})
    return matched


def verify_microsoft_tool(executable):
    executable = executable.resolve(strict=True)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    command = ("$signature = Get-AuthenticodeSignature -LiteralPath $env:XALKIT_DIAGNOSTIC_EXECUTABLE; "
               "@{status=[string]$signature.Status; signer=$signature.SignerCertificate.Subject} | ConvertTo-Json -Compress")
    environment = {**os.environ, 'XALKIT_DIAGNOSTIC_EXECUTABLE': str(executable)}
    result = subprocess.run(['powershell', '-NoProfile', '-Command', command], env=environment,
                            capture_output=True, text=True, encoding='utf-8', check=True, timeout=15)
    signature = json.loads(result.stdout)
    if signature.get('status') != 'Valid' or 'CN=Microsoft Corporation,' not in signature.get('signer', ''):
        raise RuntimeError('Crash monitor must have a valid Microsoft signature')
    if hashlib.sha256(executable.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Crash monitor changed during signature verification')
    return executable, digest


class OwnedCrashMonitor:
    def __init__(self, probe, kernel, handle, owner, expected_executable, executable, output, graphics_facade_hash=None):
        self.kernel = kernel
        self.handle = None
        self.process = None
        self.log = None
        self.owner = dict(owner)
        self.output = output.resolve()
        self.executable, self.tool_digest = verify_microsoft_tool(executable)
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        if probe.creation_time(kernel, handle) != owner['created'] or kernel.WaitForSingleObject(handle, 0) != 258:
            raise RuntimeError('Crash monitor requires the live immutable owned game')
        query = kernel.QueryFullProcessImageNameW
        query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        name = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(name))
        probe.checked(query(handle, 0, name, ctypes.byref(length)))
        if Path(name.value).resolve() != expected_executable.resolve(strict=True):
            raise RuntimeError('Crash monitor game executable differs from owned sandbox')
        for filename, expected in probe.HASHES.items():
            actual = hashlib.sha256((expected_executable.parent / filename).read_bytes()).hexdigest()
            if filename == 'd3d9.dll' and graphics_facade_hash is not None and actual == graphics_facade_hash:
                if (len(graphics_facade_hash) != 64 or any(value not in '0123456789abcdef' for value in graphics_facade_hash) or
                        hashlib.sha256((expected_executable.parent / 'd3d9.universe.dll').read_bytes()).hexdigest() != expected):
                    raise RuntimeError('Crash monitor refuses unsupported graphics chain')
                continue
            if actual != expected:
                raise RuntimeError('Crash monitor refuses unsupported game binaries')
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.DuplicateHandle.restype = wintypes.BOOL
        current = kernel.GetCurrentProcess()
        duplicated = wintypes.HANDLE()
        probe.checked(kernel.DuplicateHandle(current, handle, current, ctypes.byref(duplicated), 0, False, 2))
        self.handle = duplicated
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            self.log = (self.output / 'procdump.raw.log').open('wb')
            self.process = subprocess.Popen([str(self.executable), '-accepteula', '-e', '-b', '-ma',
                str(owner['pid']), str(self.output)], stdout=self.log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            if self.log:
                self.log.close()
            kernel.CloseHandle(self.handle)
            self.handle = None
            raise

    def snapshot(self):
        raw = (self.output / 'procdump.raw.log').read_bytes()
        if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            decoded = raw.decode('utf-16', errors='replace')
        elif b'\x00' in raw:
            # ProcDump emits an ASCII banner followed by a UTF16LE body.
            split = max(0, raw.index(b'\x00') - 1)
            decoded = raw[:split].decode('utf-8', errors='replace') + raw[split:].decode('utf-16-le', errors='replace')
        else:
            decoded = raw.decode('utf-8', errors='replace')
        (self.output / 'procdump.log').write_text(decoded, encoding='utf-8')
        captured = [identity for path in sorted(self.output.glob('*.dmp'))
                    if (identity := read_dump_identity(path, self.owner)) is not None]
        return {'backend': 'Microsoft Sysinternals ProcDump', 'game_pid': self.owner['pid'],
                'game_created': self.owner['created'], 'monitor_pid': self.process.pid,
                'monitor_exit_code': self.process.poll(), 'tool_sha256': self.tool_digest,
                'diagnostic_log': str(self.output / 'procdump.log'),
                'dumps': [str(path) for path in sorted(self.output.glob('*.dmp'))], 'captured_exceptions': captured,
                'capture_status': 'dump_captured' if captured else 'monitoring' if self.process.poll() is None else
                                  'stopped' if self.process.poll() == 0 else 'failed'}

    def close(self):
        if self.handle is None:
            result = self.snapshot()
            if result['monitor_exit_code'] != 0 and result['capture_status'] != 'dump_captured':
                raise RuntimeError('Crash monitor failed; exit code ' + str(result['monitor_exit_code']) +
                                   '; inspect ' + result['diagnostic_log'])
            return result
        try:
            if self.process.poll() is None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    subprocess.run([str(self.executable), '-cancel', str(self.owner['pid'])],
                                   capture_output=True, check=True, timeout=5)
                    self.process.wait(timeout=5)
            result = self.snapshot()
            if result['monitor_exit_code'] != 0 and result['capture_status'] != 'dump_captured':
                raise RuntimeError('Crash monitor failed; exit code ' + str(result['monitor_exit_code']) +
                                   '; inspect ' + result['diagnostic_log'])
            return result
        finally:
            # Keep provenance pinned on uncertain stop so this PID cannot be
            # reused while a debugger may still be attached.
            if self.process.poll() is not None:
                self.log.close()
                self.kernel.CloseHandle(self.handle)
                self.handle = None
