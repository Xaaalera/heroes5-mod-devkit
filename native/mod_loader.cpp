#include "player_launch.hpp"
#include "graphics_build_identity.hpp"
#include "selector_runtime.hpp"
#include <unknwn.h>
#include <cstring>
#include <algorithm>
#include <vector>
#include <new>
#include <cwchar>
#include "../game-api/include/h5/hooks.hpp"
#include <MinHook.h>

namespace {
using CreateInput = HRESULT (WINAPI*)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);
INIT_ONCE inputOnce = INIT_ONCE_STATIC_INIT;
INIT_ONCE modsOnce = INIT_ONCE_STATIC_INIT;
CreateInput createInput = nullptr;
char failureReason[256]{};
LONG reportingFailure = 0;
SRWLOCK selectorServiceLock = SRWLOCK_INIT;
// Process-lifetime storage: cached game UI references cannot be destroyed in
// DllMain. The pinned loader survives core/plugin generations; Stop retires code.
heroes5_sdk::SelectorRuntime* bankSelector = nullptr;
uint64_t selectorGeneration = 0;
std::array<unsigned char, 32> selectorDataSchema{};
DWORD selectorInvocationThread = 0;
HWND selectorGameWindow = nullptr;
bool selectorHookActive = false;
bool selectorHookUncertain = false;
bool selectorHookCreated = false;
uintptr_t selectorStockRootGlobal = 0;
uintptr_t selectorResume = h5::hooks::BankLayout.Resume();
volatile LONG selectorExecutingThread = 0;
volatile LONG verifiedGameStartupThread = 0;
// Startup classification: zero is unknown, -1 is a non-game forwarding host.
}

// Diagnostic state only; the game never needs to call this export.
extern "C" {
__declspec(dllexport) volatile LONG Heroes5ModsStatus = 0;
}

namespace {
bool MatchesSelectorWindow(HWND window, DWORD startupThread) {
    DWORD process = 0;
    const DWORD thread = GetWindowThreadProcessId(window, &process);
    wchar_t windowClass[64]{};
    return window && IsWindow(window) && IsWindowVisible(window) && !GetWindow(window, GW_OWNER)
        && process == GetCurrentProcessId() && thread == startupThread
        && GetClassNameW(window, windowClass, 64)
        && (std::wcscmp(windowClass, L"H5") == 0 || std::wcscmp(windowClass, L"H5UNI") == 0);
}
uintptr_t __cdecl DispatchBankLayout(uintptr_t publicModel, uintptr_t stockRoot) {
    const LONG startupThread = InterlockedCompareExchange(&verifiedGameStartupThread, 0, 0);
    if (!startupThread || startupThread == -1 || GetCurrentThreadId() != static_cast<DWORD>(startupThread)
        || !TryAcquireSRWLockExclusive(&selectorServiceLock)) { return stockRoot; }
    InterlockedExchange(&selectorExecutingThread, static_cast<LONG>(GetCurrentThreadId()));
    uintptr_t root = stockRoot;
    try {
        if (MatchesSelectorWindow(selectorGameWindow, static_cast<DWORD>(startupThread))
                && !selectorHookUncertain && selectorHookActive && bankSelector) { bankSelector->Invoke(publicModel, root); }
    } catch (const std::exception&) { root = stockRoot; }
    InterlockedExchange(&selectorExecutingThread, 0);
    ReleaseSRWLockExclusive(&selectorServiceLock);
    return root;
}
__declspec(naked) void BankLayoutThunk() {
    __asm {
        pushfd
        pushad
        mov ebp, esp
        sub esp, 528
        and esp, -16
        fxsave [esp]
        mov eax, dword ptr [selectorStockRootGlobal]
        mov eax, dword ptr [eax]
        mov dword ptr [ebp + 8], eax
        push eax
        push dword ptr [ebp]
        cld
        call DispatchBankLayout
        add esp, 8
        mov dword ptr [ebp + 8], eax
        fxrstor [esp]
        mov esp, ebp
        popad
        popfd
        jmp dword ptr [selectorResume]
    }
}
bool SetBankLayoutHook(bool enabled) {
    const auto& site = h5::hooks::BankLayout;
    std::array<unsigned char, sizeof(site.expected)> original{};
    std::copy(std::begin(site.expected), std::end(site.expected), original.begin());
    auto patch = original;
    patch[0] = 0xe9; // MinHook changes five bytes; the sixth original byte stays.
    const uint32_t displacement = reinterpret_cast<uintptr_t>(BankLayoutThunk) - site.address - 5;
    std::memcpy(patch.data() + 1, &displacement, sizeof(displacement));
    const auto& expected = selectorHookActive ? patch : original;
    const auto& replacement = enabled ? patch : original;
    auto* address = reinterpret_cast<void*>(site.address);
    MEMORY_BASIC_INFORMATION region{};
    if (!VirtualQuery(address, &region, sizeof(region)) || region.State != MEM_COMMIT
        || region.Protect != PAGE_EXECUTE_READ
        || site.address + expected.size() > reinterpret_cast<uintptr_t>(region.BaseAddress) + region.RegionSize
        || std::memcmp(address, expected.data(), expected.size()) != 0) { return false; }
    if (selectorHookActive == enabled) { return true; }
    std::memcpy(&selectorStockRootGlobal, site.expected + 2, sizeof(uint32_t));
    if (!selectorHookCreated) {
        const auto initialized = MH_Initialize();
        if (initialized != MH_OK && initialized != MH_ERROR_ALREADY_INITIALIZED) { return false; }
        void* trampoline = nullptr;
        if (MH_CreateHook(address, reinterpret_cast<void*>(BankLayoutThunk), &trampoline) != MH_OK) { return false; }
        selectorHookCreated = true;
    }
    const auto queued = enabled ? MH_QueueEnableHook(address) : MH_QueueDisableHook(address);
    if (queued != MH_OK) { return false; }
    if (MH_ApplyQueued() != MH_OK || std::memcmp(address, replacement.data(), replacement.size()) != 0
        || !VirtualQuery(address, &region, sizeof(region)) || region.Protect != PAGE_EXECUTE_READ) {
        selectorHookUncertain = true;
        return false;
    }
    selectorHookActive = enabled;
    return true;
}
BOOL CALLBACK ResolveSystemInput(PINIT_ONCE, PVOID, PVOID*) {
    std::array<wchar_t, MAX_PATH> directory{};
    const auto length = GetSystemDirectoryW(directory.data(), static_cast<UINT>(directory.size()));
    if (length == 0 || length >= directory.size()) { return FALSE; }
    const auto path = std::filesystem::path(directory.data()) / L"dinput8.dll";
    const auto library = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!library) { return FALSE; }
    createInput = reinterpret_cast<CreateInput>(GetProcAddress(library, "DirectInput8Create"));
    // The system input module stays loaded for the lifetime of its COM objects.
    return createInput != nullptr;
}

BOOL CALLBACK InitializeMods(PINIT_ONCE, PVOID, PVOID*) {
    try {
        std::array<wchar_t, 32768> filename{};
        const auto length = GetModuleFileNameW(nullptr, filename.data(), static_cast<DWORD>(filename.size()));
        if (length == 0 || length >= filename.size()) { throw std::runtime_error("Cannot identify game executable."); }
        const std::filesystem::path executable(filename.data());
        // The editor can also import DirectInput from the same directory.
        if (_wcsicmp(executable.filename().c_str(), L"H5_Game.exe") != 0) {
            InterlockedExchange(&verifiedGameStartupThread, -1);
            return TRUE;
        }
        universe_player::VerifyGame(executable, xkit::GraphicsFacadeSha256);
        InterlockedExchange(&verifiedGameStartupThread, static_cast<LONG>(GetCurrentThreadId()));
        InterlockedOr(&Heroes5ModsStatus, 1);
        struct Module { const wchar_t* filename; const char* entry; LONG flag; };
        const Module modules[] = {
            {L"WorkshopDeploymentPreview.dll", "WorkshopDeploymentPreviewInstall", 2},
            {L"WorkshopBankReference.dll", "WorkshopBankReferenceInstall", 4},
        };
        for (const auto& module : modules) {
            const auto path = executable.parent_path() / L"Heroes5Mods" / module.filename;
            if (!std::filesystem::is_regular_file(path)) { continue; }
            const auto library = LoadLibraryExW(path.c_str(), nullptr,
                LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
            if (!library) { throw std::runtime_error("Cannot load a Heroes5Mods plugin DLL."); }
            const auto initialize = reinterpret_cast<DWORD (__cdecl*)()>(GetProcAddress(library, module.entry));
            if (!initialize || initialize() != 1) {
                // Never unload a library that might have installed a hook.
                throw std::runtime_error("A Heroes5Mods plugin refused initialization. Check its files and game version.");
            }
            InterlockedOr(&Heroes5ModsStatus, module.flag);
        }
        const auto pluginDirectory = executable.parent_path() / L"Heroes5Mods" / L"Plugins";
        if (std::filesystem::is_directory(pluginDirectory)) {
            std::vector<std::filesystem::path> plugins;
            for (const auto& entry : std::filesystem::directory_iterator(pluginDirectory)) {
                if (entry.is_regular_file() && _wcsicmp(entry.path().extension().c_str(), L".dll") == 0) {
                    plugins.push_back(entry.path());
                }
            }
            std::sort(plugins.begin(), plugins.end());
            uint32_t slot = 0;
            for (const auto& path : plugins) {
                const auto library = LoadLibraryExW(path.c_str(), nullptr,
                    LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
                if (!library) { throw std::runtime_error("Cannot load an SDK plugin DLL."); }
                const auto configure = reinterpret_cast<DWORD (WINAPI*)(void*)>(GetProcAddress(library, "Heroes5PluginSetSlot"));
                if (configure && configure(&slot) != 0) { throw std::runtime_error("Cannot assign SDK plugin UI slot."); }
                ++slot;
                const auto initialize = reinterpret_cast<DWORD (__cdecl*)()>(GetProcAddress(library, "Heroes5PluginInstall"));
                if (!initialize || initialize() != 1) {
                    throw std::runtime_error("An SDK plugin refused startup.");
                }
            }
            InterlockedOr(&Heroes5ModsStatus, 8);
        }
    } catch (const std::exception& error) {
        InterlockedOr(&Heroes5ModsStatus, static_cast<LONG>(0x80000000u));
        strncpy_s(failureReason, error.what(), _TRUNCATE);
    }
    return TRUE;
}
}

// Work happens on the game's DirectInput call, never under DllMain loader lock.
extern "C" HRESULT WINAPI ForwardDirectInput8Create(HINSTANCE instance, DWORD version,
    REFIID interfaceId, LPVOID* output, LPUNKNOWN outer) {
    if (!InitOnceExecuteOnce(&inputOnce, ResolveSystemInput, nullptr, nullptr)) { return E_FAIL; }
    InitOnceExecuteOnce(&modsOnce, InitializeMods, nullptr, nullptr);
    if (static_cast<ULONG>(InterlockedCompareExchange(&Heroes5ModsStatus, 0, 0)) & 0x80000000u) {
        // A modal dialog can re-enter the game's input initialization. Finish
        // InitOnce first and reject nested calls without opening another dialog.
        if (InterlockedCompareExchange(&reportingFailure, 1, 0) == 0) {
            MessageBoxA(nullptr, failureReason, "Heroes V mods: game startup cancelled", MB_OK | MB_ICONERROR);
            ExitProcess(ERROR_DLL_INIT_FAILED);
        }
        return E_FAIL;
    }
    return createInput(instance, version, interfaceId, output, outer);
}

#pragma comment(linker, "/EXPORT:DirectInput8Create=_ForwardDirectInput8Create@20")

extern "C" DWORD WINAPI Heroes5BankSelectorControl(void* memory) {
    if (!memory) { return ERROR_INVALID_PARAMETER; }
    auto& request = *static_cast<heroes5_sdk::SelectorRequest*>(memory);
    if (request.size != sizeof(request) || request.version != 1 ||
        request.byteCount > 65536 || request.stateOffsetCount > 16384 ||
        request.codeFixupCount > 1024 || request.dataFixupCount > 1024 || request.outputBytes > 65536 ||
        (request.byteCount && !request.bytes) || (request.stateOffsetCount && !request.stateOffsets) ||
        (request.codeFixupCount && !request.codeFixups) || (request.dataFixupCount && !request.dataFixups) ||
        (request.outputBytes && !request.output)) { return ERROR_INVALID_PARAMETER; }
    if (request.action > heroes5_sdk::SelectorAction::DetachHook) { return ERROR_INVALID_PARAMETER; }
    if (static_cast<DWORD>(InterlockedCompareExchange(&selectorExecutingThread, 0, 0)) == GetCurrentThreadId()) {
        return ERROR_BUSY; // Reentrant game UI cannot acquire its own service lock.
    }
    AcquireSRWLockExclusive(&selectorServiceLock);
    DWORD result = ERROR_NOT_READY;
    try {
        using heroes5_sdk::SelectorAction;
        const LONG startupThread = InterlockedCompareExchange(&verifiedGameStartupThread, 0, 0);
        if (request.action != SelectorAction::Status && request.expectedGeneration != selectorGeneration) {
            result = ERROR_REVISION_MISMATCH;
        } else if (request.action == SelectorAction::Replace && bankSelector && request.dataSchema != selectorDataSchema) {
            result = ERROR_INVALID_DATA;
        } else if (selectorHookUncertain && request.action != SelectorAction::Status
                && request.action != SelectorAction::ReadState) {
            result = ERROR_WRITE_FAULT;
        } else if (request.action == SelectorAction::Invoke && startupThread == 0) {
            result = ERROR_NOT_READY;
        } else if (request.action == SelectorAction::Invoke && startupThread != -1 &&
                !MatchesSelectorWindow(selectorGameWindow, static_cast<DWORD>(startupThread))) {
            result = ERROR_INVALID_WINDOW_HANDLE;
        } else if (request.action == SelectorAction::Invoke && bankSelector &&
                GetCurrentThreadId() != (startupThread == -1 ? selectorInvocationThread
                    : static_cast<DWORD>(startupThread))) {
            result = ERROR_INVALID_THREAD_ID;
        } else if ((request.action == SelectorAction::AttachHook || request.action == SelectorAction::DetachHook)
                && (startupThread == 0 || startupThread == -1
                    || GetCurrentThreadId() != static_cast<DWORD>(startupThread)
                    || !MatchesSelectorWindow(selectorGameWindow, static_cast<DWORD>(startupThread)))) {
            result = ERROR_INVALID_THREAD_ID;
        } else if ((request.action == SelectorAction::Replace || request.action == SelectorAction::Stop
                || request.action == SelectorAction::BindWindow || request.action == SelectorAction::AttachHook
                || request.action == SelectorAction::DetachHook)
                && selectorGeneration == UINT64_MAX) {
            result = ERROR_ARITHMETIC_OVERFLOW;
        } else if (request.action == SelectorAction::BindWindow) {
            if (startupThread == 0 || startupThread == -1) { result = ERROR_NOT_SUPPORTED; }
            else if (!MatchesSelectorWindow(request.window, static_cast<DWORD>(startupThread))) {
                result = ERROR_INVALID_WINDOW_HANDLE;
            } else if (selectorGameWindow && selectorGameWindow != request.window) {
                result = ERROR_ALREADY_INITIALIZED;
            } else {
                if (!selectorGameWindow) { selectorGameWindow = request.window; ++selectorGeneration; }
                result = ERROR_SUCCESS;
            }
        } else if (request.action == SelectorAction::Initialize && !bankSelector) {
            HMODULE resident = nullptr;
            if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                    reinterpret_cast<LPCWSTR>(&Heroes5BankSelectorControl), &resident)) {
                result = GetLastError();
            } else {
                auto* candidate = new (std::nothrow) heroes5_sdk::SelectorRuntime;
                if (!candidate) { result = ERROR_NOT_ENOUGH_MEMORY; }
                else if (!candidate->Initialize({request.bytes, request.byteCount},
                        std::span<const uint32_t>(request.stateOffsets, request.stateOffsetCount), request.sourceData)) {
                    delete candidate; result = ERROR_INVALID_DATA;
                } else {
                    bankSelector = candidate; selectorInvocationThread = GetCurrentThreadId();
                    selectorDataSchema = request.dataSchema;
                    ++selectorGeneration; result = ERROR_SUCCESS;
                }
            }
        } else if (bankSelector) {
            switch (request.action) {
            case SelectorAction::Status: result = ERROR_SUCCESS; break;
            case SelectorAction::Initialize: result = ERROR_ALREADY_INITIALIZED; break;
            case SelectorAction::Replace:
                result = bankSelector->Replace({request.bytes, request.byteCount},
                    std::span<const heroes5_sdk::SelectorFixup>(request.codeFixups, request.codeFixupCount),
                    std::span<const heroes5_sdk::SelectorFixup>(request.dataFixups, request.dataFixupCount),
                    request.sourceCode, request.sourceData) ? ERROR_SUCCESS : ERROR_INVALID_DATA;
                if (result == ERROR_SUCCESS) { ++selectorGeneration; }
                break;
            case SelectorAction::Invoke:
                InterlockedExchange(&selectorExecutingThread, static_cast<LONG>(GetCurrentThreadId()));
                result = bankSelector->Invoke(request.publicModel, request.root) ? ERROR_SUCCESS : ERROR_NOT_READY;
                InterlockedExchange(&selectorExecutingThread, 0);
                break;
            case SelectorAction::Stop:
                result = selectorHookActive ? ERROR_BUSY : (bankSelector->Stop() ? ERROR_SUCCESS : ERROR_BUSY);
                if (result == ERROR_SUCCESS) { ++selectorGeneration; }
                break;
            case SelectorAction::ReadState:
                result = bankSelector->ReadState(request.stateOffset, {request.output, request.outputBytes})
                    ? ERROR_SUCCESS : ERROR_INVALID_DATA; break;
            case SelectorAction::BindWindow: break; // Handled before owner dispatch.
            case SelectorAction::AttachHook:
                result = bankSelector->Ready() && SetBankLayoutHook(true) ? ERROR_SUCCESS : ERROR_INVALID_DATA;
                if (selectorHookUncertain) { result = ERROR_WRITE_FAULT; }
                if (result == ERROR_SUCCESS) { ++selectorGeneration; }
                break;
            case SelectorAction::DetachHook:
                result = SetBankLayoutHook(false) ? ERROR_SUCCESS : ERROR_INVALID_DATA;
                if (selectorHookUncertain) { result = ERROR_WRITE_FAULT; }
                if (result == ERROR_SUCCESS) { ++selectorGeneration; }
                break;
            }
        }
    } catch (const std::exception&) { result = ERROR_UNHANDLED_EXCEPTION; }
    InterlockedExchange(&selectorExecutingThread, 0);
    request.generation = selectorGeneration;
    ReleaseSRWLockExclusive(&selectorServiceLock);
    return result;
}
#pragma comment(linker, "/EXPORT:Heroes5BankSelectorControl=_Heroes5BankSelectorControl@4")
