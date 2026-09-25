#include "player_launch.hpp"
#include <unknwn.h>
#include <cstring>

namespace {
using CreateInput = HRESULT (WINAPI*)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);
INIT_ONCE inputOnce = INIT_ONCE_STATIC_INIT;
INIT_ONCE modsOnce = INIT_ONCE_STATIC_INIT;
CreateInput createInput = nullptr;
char failureReason[256]{};
LONG reportingFailure = 0;
}

// Diagnostic state only; the game never needs to call this export.
extern "C" {
__declspec(dllexport) volatile LONG Heroes5ModsStatus = 0;
}

namespace {
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
        if (_wcsicmp(executable.filename().c_str(), L"H5_Game.exe") != 0) { return TRUE; }
        universe_player::VerifyGame(executable);
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
