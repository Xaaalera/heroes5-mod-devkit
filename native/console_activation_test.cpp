#include "console_module.hpp"
#include <cstdio>

int wmain(int count, wchar_t** arguments) {
    heroes5_sdk::ConsoleStopGate gate;
    if (!gate.Request() || !gate.Cancel() || gate.Commit()) { return 4; }
    if (!gate.Request() || !gate.Commit() || gate.Cancel() || gate.Request()) { return 5; }
    gate.Reset();
    if (!gate.Request() || !gate.Cancel()) { return 6; }
    if (count != 2) { return 1; }
    const auto library = LoadLibraryExW(arguments[1], nullptr,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!library) { return 2; }
    const auto attach = reinterpret_cast<DWORD (WINAPI*)(void*)>(GetProcAddress(library, "XalKitConsoleAttach"));
    const auto status = reinterpret_cast<volatile LONG*>(GetProcAddress(library, "XalKitConsoleStatus"));
    const auto state = reinterpret_cast<DWORD (WINAPI*)(void*)>(GetProcAddress(library, "XalKitConsoleState"));
    const auto detach = reinterpret_cast<DWORD (WINAPI*)(void*)>(GetProcAddress(library, "XalKitConsoleDetach"));
    heroes5_sdk::ConsoleConnection connection;
    bool rejected = attach && status && state && *status == 0 && attach(nullptr) != 0 && attach(&connection) != 0 && state(nullptr) == 0;
    if (rejected) {
        InterlockedExchange(status, 1);
        rejected = state(nullptr) == 1;
        InterlockedExchange(status, -1);
        rejected = rejected && state(nullptr) == UINT32_MAX && attach(&connection) != 0;
        InterlockedExchange(status, 0);
        rejected = rejected && detach && detach(nullptr) == 0 && detach(nullptr) == 0 && state(nullptr) == 0;
    }
    FreeLibrary(library);
    std::puts(rejected ? "Console inactive without SDK: PASS" : "Console activation boundary FAILED");
    return rejected ? 0 : 3;
}
