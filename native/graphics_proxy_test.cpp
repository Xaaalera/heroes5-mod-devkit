#include <windows.h>
#include <array>
#include <atomic>
#include <iostream>
#include <thread>

using Factory = void* (WINAPI*)(UINT);

#ifdef XKIT_GRAPHICS_CALLOUT_FIXTURE
namespace { DWORD result = 0; }
extern "C" __declspec(dllexport) DWORD WINAPI CalloutResult() { return result; }
BOOL WINAPI DllMain(HINSTANCE, DWORD reason, void*) {
    if (reason == DLL_PROCESS_ATTACH) {
        using Query = BOOLEAN (NTAPI*)();
        const auto query = reinterpret_cast<Query>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlIsThreadWithinLoaderCallout"));
        const auto factory = reinterpret_cast<Factory>(GetProcAddress(GetModuleHandleW(L"d3d9.dll"), "Direct3DCreate9"));
        const auto hostFactory = reinterpret_cast<Factory>(GetProcAddress(GetModuleHandleW(nullptr), "TestGraphicsFactory"));
        if (query && query() && factory && hostFactory && factory(32) == nullptr && hostFactory(32) == nullptr) { result = 1; }
    }
    return TRUE;
}
#else
// Compile the unchanged production implementation into this test translation
// unit as well, to observe its private InitOnce via the documented check API.
#include "graphics_proxy.cpp"
#pragma comment(linker, "/EXPORT:TestGraphicsFactory=_LazyDirect3DCreate9@4")
int wmain(int count, wchar_t** arguments) {
    if (count != 3) { return 1; }
    const auto facade = LoadLibraryExW(arguments[1], nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!facade) { return 2; }
    const auto factory = reinterpret_cast<Factory>(GetProcAddress(facade, "Direct3DCreate9"));
    if (!factory) { return 3; }
    const auto fixture = LoadLibraryExW(arguments[2], nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    const auto query = fixture ? reinterpret_cast<DWORD (WINAPI*)()>(GetProcAddress(fixture, "_CalloutResult@0")) : nullptr;
    if (!query || query() != 1) { return 4; }
    BOOL pending = FALSE;
    if (InitOnceBeginInitialize(&once, INIT_ONCE_CHECK_ONLY, &pending, nullptr)) { return 6; }
    // Simulate the owner already published by Resolve on this thread. A nested
    // entry must return before it tries to consume/wait on InitOnce.
    InterlockedExchange(&resolvingThread, static_cast<LONG>(GetCurrentThreadId()));
    const auto nestedResult = LazyDirect3DCreate9(32);
    InterlockedExchange(&resolvingThread, 0);
    if (nestedResult || InitOnceBeginInitialize(&once, INIT_ONCE_CHECK_ONLY, &pending, nullptr)) { return 8; }
    std::atomic<bool> start{false};
    std::atomic<unsigned> refusals{0};
    std::array<std::thread, 16> callers;
    for (auto& caller : callers) {
        caller = std::thread([&]() {
            while (!start.load(std::memory_order_acquire)) { SwitchToThread(); }
            if (factory(32) == nullptr && LazyDirect3DCreate9(32) == nullptr) { refusals.fetch_add(1); }
        });
    }
    start.store(true, std::memory_order_release);
    for (auto& caller : callers) { caller.join(); }
    if (refusals != callers.size() || factory(32) != nullptr || GetModuleHandleW(L"d3d9.universe.dll")) { return 5; }
    if (!InitOnceBeginInitialize(&once, INIT_ONCE_CHECK_ONLY, &pending, nullptr) || pending ||
        InterlockedCompareExchange(&resolvingThread, 0, 0) != 0) { return 7; }
    FreeLibrary(fixture);
    FreeLibrary(facade);
    std::cout << "PASS: loader callout leaves InitOnce incomplete; 16 concurrent unsupported-host refusals complete InitOnce; owner cleared; no original module loaded\n";
    return 0;
}
#endif
