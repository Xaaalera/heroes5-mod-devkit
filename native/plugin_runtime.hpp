#pragma once
#include <windows.h>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>

// Trusted, SDK-authored payloads only. No C++ objects/exceptions cross this ABI.
// Payloads must not retain state pointers, create threads or install direct hooks.
// Query/DllMain must be side-effect-free. Status rejection rolls back POD state,
// not game effects; native crashes/exceptions are outside this recovery contract.
namespace heroes5_sdk {
constexpr uint32_t PluginAbi = 3;
constexpr size_t StateCapacity = 4096;
using Dispatch = uint32_t (__cdecl*)(uint32_t command, uint32_t argument,
    void* state, uint32_t stateBytes, uint32_t* result);
struct PluginApi {
    uint32_t size;
    uint32_t abi;
    uint32_t stateSchema;
    uint32_t stateBytes;
    Dispatch dispatch;
    uint32_t eventMessage;
    uint32_t eventCommand;
    uint32_t showEventResult;
    uint32_t engineCallSite;
    uint32_t engineOriginalTarget;
    uint32_t engineCommand;
};
using QueryPlugin = const PluginApi* (__cdecl*)();
// Stable transport for state handoff between different core DLL generations.
// No code addresses, handles, locks or game pointers are part of this envelope.
struct PluginSnapshot {
    uint32_t magic = 0x54533548;
    uint32_t version = 1;
    uint32_t schema = 0;
    uint32_t bytes = 0;
    uint32_t generation = 0;
    std::array<unsigned char, StateCapacity> data{};
};
enum class RuntimeResult : uint32_t {
    Ok, LoadFailed, MissingEntry, IncompatibleAbi, IncompatibleState,
    NotLoaded, CallRejected
};

// Owns payload lifetime and POD state. All calls are serialized. Callbacks must
// not re-enter this runtime; game APIs require a separate main-thread boundary.
class PluginRuntime {
    SRWLOCK lock_ = SRWLOCK_INIT;
    HMODULE module_ = nullptr;
    PluginApi api_{};
    alignas(std::max_align_t) std::array<unsigned char, StateCapacity> state_{};
    uint32_t generation_ = 0;

    struct Guard {
        SRWLOCK* lock;
        explicit Guard(SRWLOCK* value) : lock(value) { AcquireSRWLockExclusive(lock); }
        ~Guard() { ReleaseSRWLockExclusive(lock); }
    };
public:
    PluginRuntime() = default;
    PluginRuntime(const PluginRuntime&) = delete;
    PluginRuntime& operator=(const PluginRuntime&) = delete;
    ~PluginRuntime() {
        Guard guard(&lock_);
        if (module_) { FreeLibrary(module_); }
    }
    RuntimeResult Replace(const std::filesystem::path& path) {
        const auto absolute = std::filesystem::absolute(path);
        const auto candidate = LoadLibraryExW(absolute.c_str(), nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!candidate) { return RuntimeResult::LoadFailed; }
        const auto query = reinterpret_cast<QueryPlugin>(GetProcAddress(candidate, "Heroes5PluginQuery"));
        if (!query) { FreeLibrary(candidate); return RuntimeResult::MissingEntry; }
        const auto* descriptor = query();
        if (!descriptor || descriptor->size != sizeof(PluginApi) || descriptor->abi != PluginAbi ||
            !descriptor->dispatch || descriptor->stateBytes > StateCapacity ||
            descriptor->showEventResult > 1 ||
            (descriptor->eventMessage && (descriptor->eventMessage < WM_APP ||
             descriptor->eventMessage >= 0xc000 || !descriptor->eventCommand)) ||
            (descriptor->engineCallSite && (!descriptor->engineOriginalTarget || !descriptor->engineCommand))) {
            FreeLibrary(candidate); return RuntimeResult::IncompatibleAbi;
        }
        const auto next = *descriptor;
        Guard guard(&lock_);
        if (module_ && (next.stateSchema != api_.stateSchema || next.stateBytes != api_.stateBytes)) {
            FreeLibrary(candidate); return RuntimeResult::IncompatibleState;
        }
        const auto previous = module_;
        module_ = candidate;
        api_ = next;
        ++generation_;
        if (previous) { FreeLibrary(previous); }
        return RuntimeResult::Ok;
    }
    RuntimeResult Invoke(uint32_t command, uint32_t argument, uint32_t& result) {
        Guard guard(&lock_);
        if (!module_) { return RuntimeResult::NotLoaded; }
        alignas(std::max_align_t) auto candidateState = state_;
        uint32_t candidateResult = 0;
        if (api_.dispatch(command, argument, candidateState.data(), api_.stateBytes, &candidateResult) != 1) {
            return RuntimeResult::CallRejected;
        }
        state_ = candidateState;
        result = candidateResult;
        return RuntimeResult::Ok;
    }
    uint32_t Generation() {
        Guard guard(&lock_);
        return generation_;
    }
    RuntimeResult ExportState(PluginSnapshot& snapshot) {
        Guard guard(&lock_);
        if (!module_) { return RuntimeResult::NotLoaded; }
        snapshot = PluginSnapshot{};
        snapshot.schema = api_.stateSchema;
        snapshot.bytes = api_.stateBytes;
        snapshot.generation = generation_;
        std::memcpy(snapshot.data.data(), state_.data(), snapshot.bytes);
        return RuntimeResult::Ok;
    }
    RuntimeResult ImportState(const PluginSnapshot& snapshot) {
        Guard guard(&lock_);
        if (!module_) { return RuntimeResult::NotLoaded; }
        if (snapshot.magic != 0x54533548 || snapshot.version != 1 || snapshot.bytes > StateCapacity ||
            snapshot.schema != api_.stateSchema || snapshot.bytes != api_.stateBytes ||
            snapshot.generation == UINT32_MAX) { return RuntimeResult::IncompatibleState; }
        std::memcpy(state_.data(), snapshot.data.data(), snapshot.bytes);
        generation_ = snapshot.generation + 1;
        return RuntimeResult::Ok;
    }
    std::array<uint32_t, 3> EventSettings() {
        Guard guard(&lock_);
        return {api_.eventMessage, api_.eventCommand, api_.showEventResult};
    }
    std::array<uint32_t, 3> EngineSettings() {
        Guard guard(&lock_);
        return {api_.engineCallSite, api_.engineOriginalTarget, api_.engineCommand};
    }
};
}
