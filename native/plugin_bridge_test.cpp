// Game-free machine test of the exact bridge implementation.
#include "plugin_bridge.cpp"
#include <iostream>
#include <stdexcept>

namespace {
alignas(16) unsigned char callerFx[512]{};
alignas(16) unsigned char observedFx[512]{};
alignas(16) uint32_t xmmSeed[4]{0x12345678, 0x90abcdef, 0xfedcba09, 0x87654321};
uint32_t observedRegisters[8]{};
uint32_t observedFlags = 0;
uintptr_t callerStack = 0, selectedTarget = 0;
uint32_t returned = 0;
uint32_t secondObserverCalls = 0;
void __cdecl SecondObserver() {
    ++secondObserverCalls;
    __asm { fninit }
    __asm { fldz }
    __asm { pxor xmm0, xmm0 }
    __asm { std }
}

__declspec(naked) void OriginalTarget() {
    __asm {
        pushfd
        pushad
        mov esi, esp
        mov edi, offset observedRegisters
        mov ecx, 8
        cld
        rep movsd
        mov eax, [esp + 32]
        mov observedFlags, eax
        fxsave [observedFx]
        popad
        popfd
        mov eax, [esp + 4]
        ret
    }
}
__declspec(naked) void SeedAndCall() {
    __asm {
        pushfd
        pushad
        mov callerStack, esp
        fxsave [callerFx]
        fninit
        fld1
        fld1
        fld1
        fld1
        fld1
        fld1
        fld1
        fld1
        movdqa xmm0, [xmmSeed]
        movdqa xmm1, [xmmSeed]
        movdqa xmm2, [xmmSeed]
        movdqa xmm3, [xmmSeed]
        movdqa xmm4, [xmmSeed]
        movdqa xmm5, [xmmSeed]
        movdqa xmm6, [xmmSeed]
        movdqa xmm7, [xmmSeed]
        mov eax, 11111111h
        mov ebx, 22222222h
        mov ecx, 33333333h
        mov edx, 44444444h
        mov esi, 55555555h
        mov edi, 66666666h
        mov ebp, 77777777h
        push 1234abcdh
        push 603h
        popfd
        call dword ptr [selectedTarget]
        add esp, 4
        mov returned, eax
        fxrstor [callerFx]
        mov esp, callerStack
        popad
        popfd
        ret
    }
}
void Require(bool condition, const char* reason) {
    if (!condition) { throw std::runtime_error(reason); }
}
}
int wmain(int count, wchar_t** arguments) {
    try {
        Require(count == 2, "Expected payload path");
        const auto instance = GetModuleHandleW(nullptr);
        WNDCLASSW gameClass{};
        gameClass.lpfnWndProc = DefWindowProcW;
        gameClass.hInstance = instance;
        gameClass.lpszClassName = L"H5";
        Require(RegisterClassW(&gameClass) != 0, "Game window class registration failed");
        gameClass.lpszClassName = L"H5UNI";
        Require(RegisterClassW(&gameClass) != 0, "Universe window class registration failed");
        const auto auxiliaryWindow = CreateWindowW(L"STATIC", L"Auxiliary", WS_POPUP | WS_VISIBLE,
            0, 0, 1, 1, nullptr, nullptr, instance, nullptr);
        const auto gameWindow = CreateWindowW(L"H5", L"Game fixture", WS_POPUP,
            0, 0, 1, 1, nullptr, nullptr, instance, nullptr);
        Require(auxiliaryWindow && gameWindow, "Window fixtures could not be created");
        h5::ConsoleCommandRequest consoleRequest;
        std::wcscpy(consoleRequest.text, L"help");
        Require(!h5::DispatchConsoleCommand(gameWindow, consoleRequest),
            "Console binding accepted a non-game host");
        consoleRequest.version = 2;
        Require(Heroes5PluginConsoleCommand(&consoleRequest) == ERROR_INVALID_PARAMETER,
            "Unknown console request version accepted");
        Require(Heroes5PluginConsoleCommand(nullptr) == ERROR_INVALID_PARAMETER,
            "Missing console request accepted");
        consoleRequest.version = 1;
        std::fill(std::begin(consoleRequest.text), std::end(consoleRequest.text), L'a');
        Require(Heroes5PluginConsoleCommand(&consoleRequest) == ERROR_INVALID_PARAMETER,
            "Unterminated console command accepted");
        std::vector<HWND> candidates;
        Require(FindMainWindow(auxiliaryWindow, reinterpret_cast<LPARAM>(&candidates)) == TRUE &&
            candidates.empty() && !mainHook && !mainWindow,
            "Auxiliary window selected as game dispatch target");
        Require(FindMainWindow(gameWindow, reinterpret_cast<LPARAM>(&candidates)) == TRUE &&
            candidates.empty() && !mainHook && !mainWindow,
            "Hidden game window selected as dispatch target");
        ShowWindow(gameWindow, SW_SHOWNOACTIVATE);
        Require(AttachMainWindowHook() && mainWindow == gameWindow &&
            windowThread == GetCurrentThreadId() && mainHook,
            "Visible game window was not selected");
        Require(UnhookWindowsHookEx(mainHook) != 0, "Window fixture hook cleanup failed");
        mainHook = nullptr; mainWindow = nullptr; windowThread = 0;
        const auto universeWindow = CreateWindowW(L"H5UNI", L"Universe fixture", WS_POPUP | WS_VISIBLE,
            0, 0, 1, 1, nullptr, nullptr, instance, nullptr);
        Require(universeWindow != nullptr, "Universe fixture creation failed");
        Require(!AttachMainWindowHook() && !mainHook && !mainWindow,
            "Ambiguous game windows accepted");
        ShowWindow(gameWindow, SW_HIDE);
        Require(AttachMainWindowHook() && mainWindow == universeWindow &&
            windowThread == GetCurrentThreadId() && mainHook, "Universe main window rejected");
        Require(UnhookWindowsHookEx(mainHook) != 0, "Universe hook cleanup failed");
        mainHook = nullptr; mainWindow = nullptr; windowThread = 0;
        DestroyWindow(universeWindow);
        DestroyWindow(gameWindow);
        DestroyWindow(auxiliaryWindow);
        UnregisterClassW(L"H5", instance);
        UnregisterClassW(L"H5UNI", instance);
        uint32_t prepare = 1;
        Require(Heroes5PluginConnect(&prepare) == 0, "Empty host prepare failed");
        heroes5_sdk::PluginSnapshot empty;
        Require(Heroes5PluginRestore(&empty) == 0, "Canonical empty snapshot rejected");
        for (unsigned field = 0; field < 6; ++field) {
            auto corrupted = empty;
            if (field == 0) { corrupted.magic ^= 1; }
            if (field == 1) { ++corrupted.version; }
            if (field == 2) { ++corrupted.schema; }
            if (field == 3) { ++corrupted.bytes; }
            if (field == 4) { ++corrupted.generation; }
            if (field == 5) { corrupted.data[0] = 1; }
            Require(Heroes5PluginRestore(&corrupted) == static_cast<DWORD>(heroes5_sdk::RuntimeResult::IncompatibleState),
                "Malformed empty snapshot accepted");
        }
        Require(Heroes5PluginStop(nullptr) == 0, "Empty host cleanup failed");
        runtime = new heroes5_sdk::PluginRuntime;
        Require(runtime->Replace(arguments[1]) == heroes5_sdk::RuntimeResult::Ok, "Payload load failed");
        windowThread = GetCurrentThreadId();
        engineSettings[2] = 7;
        originalEngineTarget = reinterpret_cast<uintptr_t>(OriginalTarget);
        selectedTarget = originalEngineTarget;
        SeedAndCall();
        std::array<unsigned char, 512> baselineFx{};
        std::memcpy(baselineFx.data(), observedFx, sizeof(observedFx));
        std::array<uint32_t, 8> baselineRegisters{};
        std::memcpy(baselineRegisters.data(), observedRegisters, sizeof(observedRegisters));
        const auto baselineFlags = observedFlags;
        Require(returned == 0x1234abcd, "Baseline stack argument mismatch");
        selectedTarget = reinterpret_cast<uintptr_t>(EngineCallThunk);
        SeedAndCall();
        Require(returned == 0x1234abcd, "Thunk shifted stack arguments");
        Require(std::memcmp(baselineRegisters.data(), observedRegisters, sizeof(observedRegisters)) == 0,
            "Thunk changed GPR/ESP");
        Require(baselineFlags == observedFlags, "Thunk changed flags");
        Require(std::memcmp(baselineFx.data(), observedFx, sizeof(observedFx)) == 0, "Thunk changed x87/SSE state");
        uint32_t result = 0;
        Require(runtime->Invoke(1, 0, result) == heroes5_sdk::RuntimeResult::Ok && result == 1000001,
            "Payload callback did not execute");
        auto* code = static_cast<unsigned char*>(VirtualAlloc(nullptr, 4096, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
        Require(code != nullptr, "Test code allocation failed");
        const auto site = reinterpret_cast<uint32_t>(code);
        const auto original = CallBytes(site, originalEngineTarget);
        std::memcpy(code, original.data(), 5);
        DWORD protection = 0;
        Require(VirtualProtect(code, 4096, PAGE_EXECUTE_READ, &protection) != 0, "Test RX failed");
        auto wrong = original; wrong[1] ^= 1;
        const auto patch = CallBytes(site, selectedTarget);
        Require(!WriteCall(site, wrong, patch) && std::memcmp(code, original.data(), 5) == 0,
            "Wrong signature modified code");
        Require(WriteCall(site, original, patch), "Valid call patch failed");
        engineSettings = {site, static_cast<uint32_t>(originalEngineTarget), 7};
        Require(ClearEngineHook() && std::memcmp(code, original.data(), 5) == 0, "Original bytes not restored");
        auto* observers = h5::hooks::CreateScriptObserverStorage(site, originalEngineTarget);
        Require(observers != nullptr, "Native owner preparation failed");
        const auto ownerCode = reinterpret_cast<uintptr_t>(observers) - h5::hooks::ScriptObserverDescriptorOffset;
        const auto ownerCall = CallBytes(site, ownerCode);
        Require(WriteCall(site, original, ownerCall) && h5::hooks::FindScriptObservers(site, originalEngineTarget) == observers,
            "Native owner publication/recognition failed");
        auto* firstSlot = h5::hooks::AddScriptObserver(*observers, OnEngineCall);
        auto* secondSlot = h5::hooks::AddScriptObserver(*observers, SecondObserver);
        Require(firstSlot && secondSlot && firstSlot != secondSlot, "Two native subscribers failed");
        engineSettings = {site, static_cast<uint32_t>(originalEngineTarget), 7};
        sharedObservers = observers; sharedObserverSlot = firstSlot;
        selectedTarget = ownerCode;
        SeedAndCall();
        Require(returned == 0x1234abcd, "Native owner shifted stack arguments");
        Require(std::memcmp(baselineRegisters.data(), observedRegisters, sizeof(observedRegisters)) == 0 &&
            observedFlags == baselineFlags && std::memcmp(baselineFx.data(), observedFx, sizeof(observedFx)) == 0,
            "Native owner changed GPR/flags/x87/SSE");
        Require(runtime->Invoke(1, 0, result) == heroes5_sdk::RuntimeResult::Ok && result == 1000002 && secondObserverCalls == 1,
            "Both native subscribers did not execute");
        Require(ClearEngineHook() && !*firstSlot && *secondSlot, "Removing first subscriber disturbed second");
        SeedAndCall();
        Require(secondObserverCalls == 2 && runtime->Invoke(1, 0, result) == heroes5_sdk::RuntimeResult::Ok && result == 1000002,
            "Removed callback executed or surviving callback stopped");
        Require(h5::hooks::RemoveScriptObserver(secondSlot, SecondObserver), "Second unsubscribe failed");
        SeedAndCall();
        Require(secondObserverCalls == 2 && returned == 0x1234abcd &&
            h5::hooks::FindScriptObservers(site, originalEngineTarget) == observers,
            "Empty resident owner stopped forwarding original calls");
        Require(WriteCall(site, ownerCall, original), "Local test owner detach failed");
        VirtualFree(reinterpret_cast<void*>(ownerCode), 0, MEM_RELEASE);
        // Exit uses a separate message and cannot reinterpret a payload command.
        uint32_t highCommand[4]{UINT32_MAX - 2, 0, 0, 0};
        mainWindow = reinterpret_cast<HWND>(1);
        dispatchMessage = WM_APP + 1; exitMessage = WM_APP + 2;
        CWPSTRUCT message{reinterpret_cast<LPARAM>(highCommand), 0, dispatchMessage, mainWindow};
        pending = highCommand;
        MainThreadHook(0, 0, reinterpret_cast<LPARAM>(&message));
        pending = nullptr;
        Require(pendingStatus == static_cast<DWORD>(heroes5_sdk::RuntimeResult::CallRejected),
            "Payload command was reinterpreted as an exit request");
        mainWindow = nullptr;
        MEMORY_BASIC_INFORMATION region{};
        VirtualQuery(code, &region, sizeof(region));
        Require(region.Protect == PAGE_EXECUTE_READ, "Code protection not restored");
        VirtualFree(code, 0, MEM_RELEASE);
        delete runtime; runtime = nullptr;
        std::cout << "PASS: thunk/native-owner GPR/ESP/flags/x87/SSE, stack argument, independent subscribers and guards\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
