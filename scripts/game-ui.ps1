param(
    [ValidateSet('capture', 'move', 'click', 'right-click', 'background', 'close')][string]$Action = 'capture',
    [Parameter(Mandatory=$true)][int]$GameProcessId,
    [int]$X = 0,
    [int]$Y = 0,
    [switch]$SystemInput,
    [switch]$OcrTiles,
    [long]$ReturnFocusWindow = 0,
    [ValidateRange(1, 3000)][int]$HoldMilliseconds = 100
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class WorkshopGameUI {
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct Point { public int X, Y; }
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool GetClipCursor(out Rect rect);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, IntPtr processId);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint source, uint target, bool attach);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr window);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr window, ref Point point);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint x, uint y, uint data, UIntPtr extra);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr window, IntPtr deviceContext, uint flags);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool PostMessageW(IntPtr window, uint message, UIntPtr wparam, IntPtr lparam);
}
'@
[WorkshopGameUI]::SetProcessDPIAware() | Out-Null
$project = if ($env:H5_WORKSPACE) { [IO.Path]::GetFullPath($env:H5_WORKSPACE) } else { Split-Path -Parent $PSScriptRoot }
$expected = [IO.Path]::GetFullPath((Join-Path $project '.local/test-game/bin/H5_Game.exe'))
$process = Get-Process -Id $GameProcessId
if ($process.Path -ne $expected) { throw 'Refusing UI automation outside the sandbox game.' }
$deadline = [DateTime]::UtcNow.AddSeconds(30)
while (($process.MainWindowHandle -eq [IntPtr]::Zero -or $process.MainWindowTitle -notlike '*Universe Mod*') -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 250
    $process.Refresh()
    if ($process.HasExited) { throw 'Game exited before creating its window.' }
}
$window = $process.MainWindowHandle
if ($window -eq [IntPtr]::Zero -or $process.MainWindowTitle -notlike '*Universe Mod*') { throw 'Game window is not ready.' }
function Get-GameBounds {
    $boundsDeadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $process.Refresh()
        if ($process.HasExited -or $process.MainWindowHandle -ne $window -or $process.Path -ne $expected) {
            throw 'Sandbox game window changed or exited.'
        }
        $rect = New-Object WorkshopGameUI+Rect
        if (-not [WorkshopGameUI]::GetClientRect($window, [ref]$rect)) { throw 'Cannot read game client bounds.' }
        if ($rect.Right -gt 0 -and $rect.Bottom -gt 0) {
            return @{ Width=$rect.Right; Height=$rect.Bottom }
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $boundsDeadline)
    throw 'Game client has no drawable area after 5 seconds; capture is unavailable.'
}

function Move-SystemCursor([int]$CursorX, [int]$CursorY) {
    $source = [WorkshopGameUI]::GetCurrentThreadId()
    $target = [WorkshopGameUI]::GetWindowThreadProcessId([WorkshopGameUI]::GetForegroundWindow(), [IntPtr]::Zero)
    $attached = $target -ne 0 -and $target -ne $source -and [WorkshopGameUI]::AttachThreadInput($source, $target, $true)
    try {
        [WorkshopGameUI]::ShowWindow($window, 9) | Out-Null
        [WorkshopGameUI]::BringWindowToTop($window) | Out-Null
        [WorkshopGameUI]::SetForegroundWindow($window) | Out-Null
    }
    finally { if ($attached) { [WorkshopGameUI]::AttachThreadInput($source, $target, $false) | Out-Null } }
    if ([WorkshopGameUI]::GetForegroundWindow() -ne $window) { throw 'Sandbox game did not gain focus; system input was not sent.' }
    $point = New-Object WorkshopGameUI+Point
    $point.X = $CursorX
    $point.Y = $CursorY
    if (-not [WorkshopGameUI]::ClientToScreen($window, [ref]$point) -or
        -not [WorkshopGameUI]::SetCursorPos($point.X, $point.Y)) { throw 'Cannot move the system cursor into the sandbox game.' }
}

function Invoke-GameClick([int]$ClickX, [int]$ClickY, [switch]$RightButton) {
    $bounds = Get-GameBounds
    if ($ClickX -lt 0 -or $ClickY -lt 0 -or $ClickX -ge $bounds.Width -or $ClickY -ge $bounds.Height) {
        throw 'Click is outside the game client.'
    }
    if ($SystemInput) {
        Move-SystemCursor $ClickX $ClickY
        Start-Sleep -Milliseconds 100
        if ([WorkshopGameUI]::GetForegroundWindow() -ne $window) { throw 'Game lost focus before the click.' }
        $down = if ($RightButton) { 8 } else { 2 }
        $up = if ($RightButton) { 16 } else { 4 }
        try {
            [WorkshopGameUI]::mouse_event($down, 0, 0, 0, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds $HoldMilliseconds
        } finally { [WorkshopGameUI]::mouse_event($up, 0, 0, 0, [UIntPtr]::Zero) }
        return
    }
    if (-not [WorkshopGameUI]::PostMessageW($window, 0x200, [UIntPtr]::Zero, [IntPtr](($ClickY -shl 16) -bor $ClickX))) {
        throw 'Cannot post mouse-move to sandbox window.'
    }
    Start-Sleep -Milliseconds 100
    $downMessage = if ($RightButton) { 0x204 } else { 0x201 }
    $upMessage = if ($RightButton) { 0x205 } else { 0x202 }
    $buttonState = if ($RightButton) { 2 } else { 1 }
    if (-not [WorkshopGameUI]::PostMessageW($window, $downMessage, [UIntPtr]::new([uint32]$buttonState), [IntPtr](($ClickY -shl 16) -bor $ClickX))) {
        throw 'Cannot post mouse-down to sandbox window.'
    }
    Start-Sleep -Milliseconds $HoldMilliseconds
    if (-not [WorkshopGameUI]::PostMessageW($window, $upMessage, [UIntPtr]::Zero, [IntPtr](($ClickY -shl 16) -bor $ClickX))) {
        throw 'Cannot post mouse-up to sandbox window.'
    }
}

function Wait-WinRT($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait(15000)) { throw 'Windows OCR timed out.' }
    return $task.Result
}

function Read-ImageText([string]$ocrPath, [int]$offsetX = 0, [int]$offsetY = 0) {
    $file = Wait-WinRT ([Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]::GetFileFromPathAsync($ocrPath)) ([Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime])
    $stream = Wait-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime])
    try {
        $decoder = Wait-WinRT ([Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime])
        $software = Wait-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime])
        try {
            $language = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]::new('ru')
            $engine = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]::TryCreateFromLanguage($language)
            if ($null -eq $engine) { throw 'Russian Windows OCR is unavailable.' }
            $result = Wait-WinRT ($engine.RecognizeAsync($software)) ([Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime])
            $lines = @($result.Lines | ForEach-Object {
                $words = @($_.Words)
                $left = ($words | ForEach-Object { $_.BoundingRect.X } | Measure-Object -Minimum).Minimum
                $top = ($words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum
                $right = ($words | ForEach-Object { $_.BoundingRect.X + $_.BoundingRect.Width } | Measure-Object -Maximum).Maximum
                $bottom = ($words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum
                @{ Text=$_.Text; X=$offsetX+[int](($left+$right)/2); Y=$offsetY+[int](($top+$bottom)/2) }
            })
            return $lines
        } finally { $software.Dispose() }
    } finally { $stream.Dispose() }
}

function Read-GameScreen([switch]$DialogOnly) {
    $bounds = Get-GameBounds
    $path = Join-Path $project '.local/test-state/game-ui.png'
    $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $deviceContext = $graphics.GetHdc()
        try {
            if (-not [WorkshopGameUI]::PrintWindow($window, $deviceContext, 3)) { throw 'Cannot capture game window.' }
        } finally { $graphics.ReleaseHdc($deviceContext) }
        $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        if ($DialogOnly) {
            $offsetX = [int]($bounds.Width * 0.3)
            $offsetY = [int]($bounds.Height * 0.2)
            $area = New-Object System.Drawing.Rectangle $offsetX, $offsetY, ([int]($bounds.Width * 0.4)), ([int]($bounds.Height * 0.55))
            $crop = $bitmap.Clone($area, $bitmap.PixelFormat)
            $ocrPath = Join-Path $project '.local/test-state/game-ui-dialog.png'
            try { $crop.Save($ocrPath, [System.Drawing.Imaging.ImageFormat]::Png) } finally { $crop.Dispose() }
            $lines = @(Read-ImageText $ocrPath $offsetX $offsetY)
        } else {
            $lines = @(Read-ImageText $path)
            if ($OcrTiles) {
                # Busy terrain can defeat OCR layout detection on a whole frame.
                # Read overlapping strips of the SAME bitmap, preserving the full
                # screenshot and returning all coordinates in client space.
                $tileWidth = [int][Math]::Ceiling($bounds.Width * 0.6)
                foreach ($offsetX in @(0, ([int](($bounds.Width - $tileWidth) / 2)), ($bounds.Width - $tileWidth))) {
                    $area = New-Object System.Drawing.Rectangle $offsetX, 0, $tileWidth, $bounds.Height
                    $crop = $bitmap.Clone($area, $bitmap.PixelFormat)
                    $ocrPath = Join-Path $project ('.local/test-state/game-ui-tile-' + $offsetX + '.png')
                    try { $crop.Save($ocrPath, [System.Drawing.Imaging.ImageFormat]::Png) } finally { $crop.Dispose() }
                    $lines += @(Read-ImageText $ocrPath $offsetX 0)
                }
            }
        }
        return @{ Image=$path; Width=$bounds.Width; Height=$bounds.Height; Lines=$lines; OcrTiles=[bool]($OcrTiles -and -not $DialogOnly) }
    } finally { $graphics.Dispose(); $bitmap.Dispose() }
}

if ($Action -eq 'move') {
    $bounds = Get-GameBounds
    if ($X -lt 0 -or $Y -lt 0 -or $X -ge $bounds.Width -or $Y -ge $bounds.Height) {
        throw 'Move is outside the game client.'
    }
    if ($SystemInput) { Move-SystemCursor $X $Y; return }
    if (-not [WorkshopGameUI]::PostMessageW($window, 0x200, [UIntPtr]::Zero, [IntPtr](($Y -shl 16) -bor $X))) {
        throw 'Cannot post mouse-move to sandbox window.'
    }
    return
}
if ($Action -eq 'click') { Invoke-GameClick $X $Y; return }
if ($Action -eq 'right-click') { Invoke-GameClick $X $Y -RightButton; return }
if ($Action -eq 'capture') { Read-GameScreen | ConvertTo-Json -Depth 5; return }
if ($Action -eq 'background') {
    # NOACTIVATE does not deactivate a window that is already foreground.
    # Restore only the pre-launch foreground window, never a guessed app.
    if ([WorkshopGameUI]::GetForegroundWindow() -eq $window) {
        $returnWindow = [IntPtr]::new($ReturnFocusWindow)
        if ($returnWindow -eq [IntPtr]::Zero -or $returnWindow -eq $window -or
            -not [WorkshopGameUI]::IsWindow($returnWindow)) {
            throw 'Cannot background the focused game without its pre-launch foreground window.'
        }
        $sourceThread = [WorkshopGameUI]::GetCurrentThreadId()
        $gameThread = [WorkshopGameUI]::GetWindowThreadProcessId($window, [IntPtr]::Zero)
        $attached = [WorkshopGameUI]::AttachThreadInput($sourceThread, $gameThread, $true)
        try { [WorkshopGameUI]::SetForegroundWindow($returnWindow) | Out-Null }
        finally { if ($attached) { [WorkshopGameUI]::AttachThreadInput($sourceThread, $gameThread, $false) | Out-Null } }
        Start-Sleep -Milliseconds 150
    }
    if ([WorkshopGameUI]::GetForegroundWindow() -eq $window) {
        throw 'Game still owns foreground input; off-screen positioning refused.'
    }
    $rect = New-Object WorkshopGameUI+Rect
    if (-not [WorkshopGameUI]::GetWindowRect($window, [ref]$rect)) { throw 'Cannot read sandbox window position.' }
    $outsideX = [WorkshopGameUI]::GetSystemMetrics(76) - ($rect.Right - $rect.Left) - 64
    # Keep the renderer drawable, but unreachable by the shared physical mouse.
    # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE: never activate or resize it.
    if (-not [WorkshopGameUI]::SetWindowPos($window, [IntPtr]::Zero, $outsideX, $rect.Top, 0, 0, 0x15)) {
        throw 'Cannot position the sandbox window outside the desktop.'
    }
    if (-not [WorkshopGameUI]::GetWindowRect($window, [ref]$rect) -or
        $rect.Right -gt [WorkshopGameUI]::GetSystemMetrics(76)) { throw 'Sandbox window remains on the shared desktop.' }
    Start-Sleep -Milliseconds 150
    $clip = New-Object WorkshopGameUI+Rect
    if ([WorkshopGameUI]::GetForegroundWindow() -eq $window -or
        -not [WorkshopGameUI]::GetClipCursor([ref]$clip) -or $clip.Right -le [WorkshopGameUI]::GetSystemMetrics(76)) {
        throw 'Game reclaimed foreground/cursor clipping; background check refused.'
    }
    @{ Status='background_window'; Pid=$GameProcessId; X=$rect.Left; Y=$rect.Top } | ConvertTo-Json
    return
}

function Wait-ExitConfirmation([int]$Timeout = 25) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Timeout)
    do {
        $screen = Read-GameScreen -DialogOnly
        $prompt = @($screen.Lines | Where-Object {
            $_.Text -match 'хот.*выйти' -and $_.X -gt $screen.Width * 0.3 -and $_.X -lt $screen.Width * 0.7 -and $_.Y -lt $screen.Height * 0.6
        })
        $cancel = @($screen.Lines | Where-Object {
            $_.Text -match 'Отмена$' -and $_.X -gt $screen.Width * 0.5 -and $_.X -lt $screen.Width * 0.7 -and $_.Y -lt $screen.Height * 0.8
        })
        $matches = @($screen.Lines | Where-Object {
            ($_.Text -replace '[^\p{L}]', '') -match '^[ОоOo][КкKk]$' -and $_.X -gt $screen.Width * 0.3 -and $_.X -lt $screen.Width * 0.5 -and $_.Y -lt $screen.Height * 0.8
        })
        if ($prompt.Count -eq 1 -and $cancel.Count -eq 1 -and $matches.Count -eq 1) { return $matches[0] }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Exit confirmation was not identified within 25 seconds; see .local/test-state/game-ui.png'
}

try {
# Retain the process handle so success means this process actually exited.
$null = $process.Handle
if (-not $process.CloseMainWindow()) { throw 'Game refused the normal close request.' }
$target = Wait-ExitConfirmation
Invoke-GameClick $target.X $target.Y
if (-not $process.WaitForExit(15000)) { throw 'Game did not exit after confirmation.' }
$report = @{ Status='game_closed'; Pid=$GameProcessId }
} catch {
    @{ Status='failed'; Action=$Action; Pid=$GameProcessId; Error=$_.Exception.Message } |
        ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $project '.local/test-state/game-ui.json')
    throw
}
$report | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $project '.local/test-state/game-ui.json')
$report | ConvertTo-Json
