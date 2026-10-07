#include <windows.h>
#include <array>
#include <filesystem>
#include <string>
#include "../game-api/include/h5/build.hpp"
#include "../game-api/include/h5/graphics_lifetime.hpp"

namespace {
using Factory = void* (WINAPI*)(UINT);
INIT_ONCE once = INIT_ONCE_STATIC_INIT;
Factory originalFactory = nullptr;
HMODULE originalModule = nullptr;
HANDLE originalFile = INVALID_HANDLE_VALUE;
volatile LONG resolvingThread = 0;


void Record(const char* message) {
    OutputDebugStringA(message);
}

BOOL CALLBACK Resolve(PINIT_ONCE, PVOID, PVOID*) {
    // Only the callback that actually wins InitOnce owns initialization.
    InterlockedExchange(&resolvingThread, static_cast<LONG>(GetCurrentThreadId()));
    struct OwnerReset {
        ~OwnerReset() { InterlockedExchange(&resolvingThread, 0); }
    } ownerReset;
    try {
        std::array<wchar_t, 32768> filename{};
        if (!GetModuleFileNameW(nullptr, filename.data(), static_cast<DWORD>(filename.size()))) { return TRUE; }
        const std::filesystem::path executable(filename.data());
        if (executable.filename() != L"H5_Game.exe") { return TRUE; }
        const auto directory = executable.parent_path();
        Record("factory_entered\n");
        for (const auto& item : std::array<std::pair<const wchar_t*, const char*>, 3>{{
            {L"H5_Game.exe", "88c9dc6107b9bced0649924a86360f1c56397ee00de0413f6f2b08f865ed5519"},
            {L"uni.dll", "aa5211151d9e9a8c135e180ff8832908d128ccae08a5145162bcdae4946c18ee"},
            {L"um.dll", "1956c00b371d22a3e1a644394ff3e7159b6ec36d660d5ffa36628fcf63fd0fc6"}}}) {
            if (h5::Sha256(directory / item.first) != item.second) { Record("game_identity_rejected\n"); return TRUE; }
        }
        const auto animation = GetModuleHandleW(L"granny2.dll");
        if (reinterpret_cast<uintptr_t>(animation) != 0x50000000 ||
            h5::Sha256(directory / L"granny2.dll") != "b40c1636c298ba0af1bfda8cc6db38c73924a2f85397976e40cf2fc2365aae00") {
            Record("animation_placement_rejected\n"); return TRUE;
        }
        MEMORY_BASIC_INFORMATION memory{};
        if (VirtualQuery(reinterpret_cast<void*>(0x50055080), &memory, sizeof(memory)) != sizeof(memory) ||
            memory.AllocationBase != animation || memory.Type != MEM_IMAGE || memory.State != MEM_COMMIT ||
            (memory.Protect & (PAGE_GUARD | PAGE_NOACCESS))) { Record("animation_data_rejected\n"); return TRUE; }
        Record("animation_placement_verified\n");
        const auto original = directory / L"d3d9.universe.dll";
        originalFile = CreateFileW(original.c_str(), GENERIC_READ, FILE_SHARE_READ,
            nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (originalFile == INVALID_HANDLE_VALUE ||
            h5::Sha256(originalFile) != h5::UniverseGraphicsSha256) {
            Record("original_identity_rejected\n"); return TRUE;
        }
        if (GetModuleHandleW(L"d3d9.universe.dll")) { Record("original_loaded_too_early\n"); return TRUE; }
        originalModule = LoadLibraryExW(original.c_str(), nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!originalModule) { Record("original_load_failed\n"); return TRUE; }
        const auto base = reinterpret_cast<uintptr_t>(originalModule);
        if (base > UINTPTR_MAX - 0x15000) { Record("original_image_bounds_rejected\n"); return TRUE; }
        const auto globalsAddress = base + 0x10f64;
        MEMORY_BASIC_INFORMATION globalsRegion{};
        std::array<uint32_t, 3> globals{};
        SIZE_T received = 0;
        if (VirtualQuery(reinterpret_cast<void*>(globalsAddress), &globalsRegion, sizeof(globalsRegion)) != sizeof(globalsRegion) ||
            globalsRegion.AllocationBase != originalModule || globalsRegion.Type != MEM_IMAGE || globalsRegion.State != MEM_COMMIT ||
            (globalsRegion.Protect & (PAGE_GUARD | PAGE_NOACCESS)) ||
            globalsAddress < reinterpret_cast<uintptr_t>(globalsRegion.BaseAddress) ||
            globalsRegion.RegionSize < sizeof(globals) ||
            globalsAddress - reinterpret_cast<uintptr_t>(globalsRegion.BaseAddress) > globalsRegion.RegionSize - sizeof(globals) ||
            !ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(globalsAddress), globals.data(), sizeof(globals), &received) ||
            received != sizeof(globals) || globals != std::array<uint32_t, 3>{}) {
            Record("original_graphics_state_rejected\n"); return TRUE;
        }
        const auto factory = reinterpret_cast<Factory>(GetProcAddress(originalModule, "Direct3DCreate9"));
        if (reinterpret_cast<uintptr_t>(factory) != base + 0x26b0) {
            Record("original_factory_identity_rejected\n"); return TRUE;
        }
        // The fresh pinned original has no graphics objects or escaped graphics
        // callbacks before its first factory. All facade callers serialize here.
        // Never publish the factory until the image transaction is confirmed.
        h5::detail::RepairGraphicsProxyImage(GetCurrentProcess(), original, originalFile, originalModule);
        Record("original_graphics_lifetime_verified\n");
        originalFactory = factory;
        Record(originalFactory ? "original_factory_resolved\n" : "original_factory_missing\n");
    } catch (...) {
        Record("resolution_exception\n");
    }
    return TRUE; // Cache a refusal; never repeatedly load against uncertain startup state.
}
}

extern "C" void* WINAPI LazyDirect3DCreate9(UINT version) {
    // Query every caller before it can wait on another initializing thread.
    // Missing native-query support refuses this unverified startup context.
    using LoaderCalloutQuery = BOOLEAN (NTAPI*)();
    const auto query = reinterpret_cast<LoaderCalloutQuery>(
        GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlIsThreadWithinLoaderCallout"));
    if (!query || query()) { Record("graphics_loader_context_refused\n"); return nullptr; }
    if (static_cast<DWORD>(InterlockedCompareExchange(&resolvingThread, 0, 0)) == GetCurrentThreadId()) {
        Record("graphics_recursive_factory_refused\n"); return nullptr;
    }
    if (!InitOnceExecuteOnce(&once, Resolve, nullptr, nullptr) || !originalFactory) { return nullptr; }
    Record("delegating_factory\n");
    const auto result = originalFactory(version);
    Record(result ? "original_object_returned\n" : "original_object_null\n");
    return result;
}
