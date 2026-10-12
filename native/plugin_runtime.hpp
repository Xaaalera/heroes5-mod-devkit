#pragma once
#include <windows.h>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include "../game-api/include/h5/hooks.hpp"
#include "engine_hook_protocol.hpp"

// Trusted, SDK-authored payloads only. No C++ objects/exceptions cross this ABI.
// Payloads must not retain state pointers, create threads or install direct hooks.
// Query/DllMain must be side-effect-free. Status rejection rolls back POD state,
// not game effects; native crashes/exceptions are outside this recovery contract.
namespace heroes5_sdk {
constexpr uint32_t PluginAbi = 3;
constexpr uint32_t ContextPluginAbi = 4;
constexpr uint32_t HookPluginAbi = 5;
constexpr uint32_t InterceptionPluginAbi = 6;
constexpr size_t EngineHookCapacity = 16;
constexpr size_t StateCapacity = 4096;
constexpr uint32_t ContextCapacity = 1024 * 1024;
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
struct ContextPluginApi {
    PluginApi base;
    uint32_t residentSchema;
    uint32_t residentBytes;
};
struct EngineSubscription {
    uint32_t site = 0;
    uint32_t command = 0;
    bool operator==(const EngineSubscription&) const = default;
};
struct EngineSubscriptions {
    uint32_t count = 0;
    std::array<EngineSubscription, EngineHookCapacity> entries{};
    bool operator==(const EngineSubscriptions&) const = default;
};
struct HookPluginApi {
    ContextPluginApi base;
    EngineSubscriptions hooks;
};
struct EngineInterceptionSubscription {
    uint32_t site = 0;
    uint32_t command = 0;
    uint32_t sourceBytes = 0;
    unsigned char sourceExpected[16]{};
    EngineCallPolicy policy;
    uint32_t branchBytes = 0;
    unsigned char branchExpected[16]{};
    // Trusted pure handlers only: nested calls never commit POD state.
    uint32_t readOnlyReentry = 0;
    bool operator==(const EngineInterceptionSubscription&) const = default;
};
struct EngineInterceptions {
    uint32_t count = 0;
    std::array<EngineInterceptionSubscription, EngineHookCapacity> entries{};
    bool operator==(const EngineInterceptions&) const = default;
};
struct InterceptionPluginApi {
    HookPluginApi base;
    EngineInterceptions interceptions;
};
// Borrowed synchronously from the SDK owner. Never retain pointers or copy
// this envelope into PluginSnapshot. Captured register values may contain game
// addresses; they are valid only during this synchronous engine invocation.
struct EngineContext {
    uint32_t size = sizeof(EngineContext);
    uint32_t version = 2;
    uint32_t callSite = 0;
    HWND window = nullptr;
    uint32_t residentSchema = 0;
    uint32_t residentBytes = 0;
    unsigned char* residentData = nullptr;
    uint32_t hasRegisters = 0;
    h5::hooks::X86ObserverFrame registers{};
};
struct EngineInterceptionContext {
    EngineContext engine;
    EngineCallAction action = EngineCallAction::Continue;
    uint32_t result = 0;
    uint32_t size = sizeof(EngineInterceptionContext);
    uint32_t version = 1;
    uint32_t readOnly = 0;
};
struct WindowInputContext {
    EngineContext engine;
    uint32_t size = sizeof(WindowInputContext);
    uint32_t version = 1;
    uint32_t message = 0;
    WPARAM wparam = 0;
    LPARAM lparam = 0;
};
using QueryPlugin = const PluginApi* (__cdecl*)();
// Stable transport for state handoff between different core DLL generations.
// No code addresses, handles, locks or game pointers are part of this envelope.
struct PluginSnapshot {
    uint32_t magic = 0x54533548;
    uint32_t version = 2;
    uint32_t schema = 0;
    uint32_t bytes = 0;
    uint32_t generation = 0;
    uint32_t residentSchema = 0;
    uint32_t residentBytes = 0;
    std::array<unsigned char, StateCapacity> data{};
};
enum class RuntimeResult : uint32_t {
    Ok, LoadFailed, MissingEntry, IncompatibleAbi, IncompatibleState,
    NotLoaded, CallRejected
};

// Owns payload lifetime and POD state. Calls are serialized; only explicitly
// admitted pure interceptors may re-enter on the same thread, without POD commit.
// Game APIs require the owned main-thread boundary.
class PluginRuntime {
    SRWLOCK lock_ = SRWLOCK_INIT;
    HMODULE module_ = nullptr;
    PluginApi api_{};
    std::array<uint32_t, 2> residentSettings_{};
    EngineSubscriptions hooks_{};
    EngineInterceptions interceptions_{};
    const EngineContext* activeEngineContext_ = nullptr;
    alignas(std::max_align_t) std::array<unsigned char, StateCapacity> state_{};
    uint32_t generation_ = 0;
    volatile LONG dispatchThread_ = 0;
    inline static thread_local uint32_t activeDispatchDepth_ = 0;

    struct Guard {
        SRWLOCK* lock;
        explicit Guard(SRWLOCK* value) : lock(value) { AcquireSRWLockExclusive(lock); }
        ~Guard() { ReleaseSRWLockExclusive(lock); }
    };
    struct EngineContextScope {
        const EngineContext*& current;
        const EngineContext* previous;
        EngineContextScope(const EngineContext*& slot, const EngineContext& context)
            : current(slot), previous(slot) { current = &context; }
        ~EngineContextScope() { current = previous; }
    };
    struct PayloadDispatchScope {
            volatile LONG* thread;
            LONG previousThread;
            explicit PayloadDispatchScope(volatile LONG* value) : thread(value) {
                ++activeDispatchDepth_;
                previousThread = InterlockedExchange(thread, static_cast<LONG>(GetCurrentThreadId()));
            }
            ~PayloadDispatchScope() { InterlockedExchange(thread, previousThread); --activeDispatchDepth_; }
        };
    RuntimeResult InvokeLocked(uint32_t command, uint32_t argument, uint32_t& result,
        EngineInterceptionContext* interception = nullptr, const EngineCallPolicy* policy = nullptr,
        bool commitState = true) {
        alignas(std::max_align_t) auto candidateState = state_;
        uint32_t candidateResult = 0;
        PayloadDispatchScope dispatchScope(&dispatchThread_);
        if (api_.dispatch(command, argument, candidateState.data(), api_.stateBytes, &candidateResult) != 1) {
            return RuntimeResult::CallRejected;
        }
        if (interception && (!policy ||
            (interception->action != EngineCallAction::Continue &&
             !(interception->action == EngineCallAction::ReturnEax && policy->allowReturn) &&
             !(interception->action == EngineCallAction::Branch && policy->branch)))) {
            return RuntimeResult::CallRejected;
        }
        if (commitState) { state_ = candidateState; }
        result = candidateResult;
        return RuntimeResult::Ok;
    }
    bool ValidEngineContext(const EngineContext& context) const {
        if (context.size != sizeof(EngineContext) || context.version != 2 ||
            context.hasRegisters > 1 || !context.window || !context.residentData ||
            context.residentSchema != residentSettings_[0] || context.residentBytes != residentSettings_[1]) {
            return false;
        }
        DWORD process = 0;
        if (GetWindowThreadProcessId(context.window, &process) != GetCurrentThreadId() ||
            process != GetCurrentProcessId()) { return false; }
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQuery(context.residentData, &region, sizeof(region)) ||
            region.State != MEM_COMMIT || region.Type != MEM_PRIVATE ||
            region.Protect != PAGE_READWRITE || region.AllocationBase != context.residentData ||
            context.residentBytes > region.RegionSize) { return false; }
        return true;
    }
    RuntimeResult InvokeInterceptorLocked(EngineContext& context, EngineCall& call,
        const EngineInterceptionSubscription& admitted, bool nested) {
        if (!module_) { return RuntimeResult::NotLoaded; }
        const EngineInterceptionSubscription* binding = nullptr;
        for (uint32_t index = 0; index < interceptions_.count; ++index) {
            if (interceptions_.entries[index] == admitted) { binding = &interceptions_.entries[index]; break; }
        }
        if (api_.abi != InterceptionPluginAbi || !binding || context.callSite != binding->site ||
            context.hasRegisters != 1 || !ValidEngineContext(context) || (nested && !binding->readOnlyReentry)) {
            return RuntimeResult::CallRejected;
        }
        EngineInterceptionContext borrowed{context};
        borrowed.readOnly=nested ? 1u : 0u;
        EngineContextScope borrowedScope(activeEngineContext_,borrowed.engine);
        uint32_t ignored = 0;
        const auto status = InvokeLocked(binding->command, reinterpret_cast<uint32_t>(&borrowed), ignored,
            &borrowed, &binding->policy, !nested);
        if (status == RuntimeResult::Ok) {
            call.frame = borrowed.engine.registers;call.action = borrowed.action;call.result = borrowed.result;
        }
        return status;
    }
public:
    static bool CallingPayloadOnCurrentThread() { return activeDispatchDepth_ != 0; }
    bool DispatchingOnCurrentThread() {
        return InterlockedCompareExchange(&dispatchThread_, 0, 0) == static_cast<LONG>(GetCurrentThreadId());
    }
    // Borrow only while this thread is already executing this payload. The
    // outer host call owns the resident-buffer and module-lifetime leases.
    const EngineContext* CurrentEngineContext() {
        return DispatchingOnCurrentThread() ? activeEngineContext_ : nullptr;
    }
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
        const bool basic = descriptor && descriptor->size == sizeof(PluginApi) && descriptor->abi == PluginAbi;
        const bool contextual = descriptor && descriptor->size == sizeof(ContextPluginApi) &&
            descriptor->abi == ContextPluginAbi;
        const bool intercepting = descriptor && descriptor->size == sizeof(InterceptionPluginApi) &&
            descriptor->abi == InterceptionPluginAbi;
        const bool multi = intercepting || (descriptor && descriptor->size == sizeof(HookPluginApi) && descriptor->abi == HookPluginAbi);
        if (!descriptor || (!basic && !contextual && !multi) ||
            !descriptor->dispatch || descriptor->stateBytes > StateCapacity ||
            descriptor->showEventResult > 1 ||
            (descriptor->eventMessage && (descriptor->eventMessage < WM_APP ||
             descriptor->eventMessage >= 0xc000 || !descriptor->eventCommand)) ||
            (!descriptor->engineCallSite && (descriptor->engineOriginalTarget || descriptor->engineCommand)) ||
            (descriptor->engineCallSite && (!descriptor->engineOriginalTarget || !descriptor->engineCommand))) {
            FreeLibrary(candidate); return RuntimeResult::IncompatibleAbi;
        }
        const auto next = *descriptor;
        std::array<uint32_t, 2> nextResident{};
        if (contextual || multi) {
            const auto* contextApi = reinterpret_cast<const ContextPluginApi*>(descriptor);
            if (!contextApi->residentSchema || !contextApi->residentBytes ||
                contextApi->residentBytes > ContextCapacity || (!descriptor->engineCallSite && !multi)) {
                FreeLibrary(candidate); return RuntimeResult::IncompatibleAbi;
            }
            nextResident = {contextApi->residentSchema, contextApi->residentBytes};
        }
        EngineSubscriptions nextHooks{};
        if (multi) {
            nextHooks = reinterpret_cast<const HookPluginApi*>(descriptor)->hooks;
            if ((!nextHooks.count && !intercepting) || nextHooks.count > EngineHookCapacity) {
                FreeLibrary(candidate); return RuntimeResult::IncompatibleAbi;
            }
            for (uint32_t index = 0; index < nextHooks.count; ++index) {
                const auto& entry = nextHooks.entries[index];
                if (!entry.site || !entry.command || entry.site == next.engineCallSite ||
                    entry.command == next.engineCommand || (next.eventMessage && entry.command == next.eventCommand)) {
                    FreeLibrary(candidate); return RuntimeResult::IncompatibleAbi;
                }
                for (uint32_t previous = 0; previous < index; ++previous) {
                    if (entry.site == nextHooks.entries[previous].site) {
                        FreeLibrary(candidate); return RuntimeResult::IncompatibleAbi;
                    }
                }
            }
            for (uint32_t index = nextHooks.count; index < EngineHookCapacity; ++index) {
                if (nextHooks.entries[index].site || nextHooks.entries[index].command) {
                    FreeLibrary(candidate); return RuntimeResult::IncompatibleAbi;
                }
            }
        }
        EngineInterceptions nextInterceptions{};
        if (intercepting) {
            nextInterceptions = reinterpret_cast<const InterceptionPluginApi*>(descriptor)->interceptions;
            if (!nextInterceptions.count || nextInterceptions.count > EngineHookCapacity) {
                FreeLibrary(candidate);return RuntimeResult::IncompatibleAbi;
            }
            for (uint32_t index=0;index<nextInterceptions.count;++index) {
                const auto& entry=nextInterceptions.entries[index];
                bool valid=entry.site && entry.command && entry.sourceBytes>=5 && entry.sourceBytes<=16 &&
                    entry.site!=next.engineCallSite &&
                    entry.command!=next.engineCommand && (!next.eventMessage || entry.command!=next.eventCommand) &&
                    entry.policy.allowReturn<=1 && entry.policy.stackPopBytes<=64 && !(entry.policy.stackPopBytes%4) &&
                    (entry.policy.allowReturn || !entry.policy.stackPopBytes) && entry.readOnlyReentry<=1 &&
                    (entry.policy.branch ? entry.branchBytes>=5 && entry.branchBytes<=16 : !entry.branchBytes);
                for (uint32_t byte=entry.sourceBytes;valid && byte<16;++byte) { valid=!entry.sourceExpected[byte]; }
                for (uint32_t byte=entry.branchBytes;valid && byte<16;++byte) { valid=!entry.branchExpected[byte]; }
                for (uint32_t prior=0;valid && prior<index;++prior) {
                    valid=entry.site!=nextInterceptions.entries[prior].site && entry.command!=nextInterceptions.entries[prior].command;
                }
                for (uint32_t observer=0;valid && observer<nextHooks.count;++observer) {
                    valid=entry.site!=nextHooks.entries[observer].site && entry.command!=nextHooks.entries[observer].command;
                }
                if (!valid) { FreeLibrary(candidate);return RuntimeResult::IncompatibleAbi; }
            }
            for (uint32_t index=nextInterceptions.count;index<EngineHookCapacity;++index) {
                if (nextInterceptions.entries[index]!=EngineInterceptionSubscription{}) {
                    FreeLibrary(candidate);return RuntimeResult::IncompatibleAbi;
                }
            }
        }
        Guard guard(&lock_);
        if (module_ && (next.stateSchema != api_.stateSchema || next.stateBytes != api_.stateBytes ||
            nextResident != residentSettings_)) {
            FreeLibrary(candidate); return RuntimeResult::IncompatibleState;
        }
        const auto previous = module_;
        module_ = candidate;
        api_ = next;
        residentSettings_ = nextResident;
        hooks_ = nextHooks;
        interceptions_ = nextInterceptions;
        ++generation_;
        if (previous) { FreeLibrary(previous); }
        return RuntimeResult::Ok;
    }
    RuntimeResult Invoke(uint32_t command, uint32_t argument, uint32_t& result) {
        Guard guard(&lock_);
        if (!module_) { return RuntimeResult::NotLoaded; }
        bool engineCommand = api_.engineCallSite && command == api_.engineCommand;
        for (uint32_t index = 0; index < hooks_.count; ++index) {
            engineCommand = engineCommand || command == hooks_.entries[index].command;
        }
        for (uint32_t index=0;index<interceptions_.count;++index) {
            engineCommand=engineCommand || command==interceptions_.entries[index].command;
        }
        if ((api_.abi == ContextPluginAbi || api_.abi == HookPluginAbi || api_.abi == InterceptionPluginAbi) && engineCommand) {
            return RuntimeResult::CallRejected; // Clients cannot forge a borrowed engine pointer.
        }
        return InvokeLocked(command, argument, result);
    }
    RuntimeResult InvokeEngine(EngineContext& context, uint32_t& result) {
        Guard guard(&lock_);
        if (!module_) { return RuntimeResult::NotLoaded; }
        uint32_t command = api_.engineCallSite && context.callSite == api_.engineCallSite ? api_.engineCommand : 0;
        bool requiresRegisters = false;
        for (uint32_t index = 0; index < hooks_.count; ++index) {
            if (context.callSite == hooks_.entries[index].site) {
                command = hooks_.entries[index].command;
                requiresRegisters = true;
            }
        }
        if ((api_.abi != ContextPluginAbi && api_.abi != HookPluginAbi && api_.abi != InterceptionPluginAbi) || !command || context.size != sizeof(EngineContext) ||
            context.version != 2 || context.hasRegisters > 1 || (requiresRegisters && context.hasRegisters != 1) ||
            !context.window || !context.residentData ||
            context.residentSchema != residentSettings_[0] ||
            context.residentBytes != residentSettings_[1]) { return RuntimeResult::CallRejected; }
        if (!ValidEngineContext(context)) { return RuntimeResult::CallRejected; }
        EngineContextScope borrowedScope(activeEngineContext_,context);
        return InvokeLocked(command, static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&context)), result);
    }
    RuntimeResult InvokeInterceptor(EngineContext& context, EngineCall& call,
        const EngineInterceptionSubscription& admitted) {
        if (DispatchingOnCurrentThread()) { return InvokeInterceptorLocked(context,call,admitted,true); }
        Guard guard(&lock_);
        return InvokeInterceptorLocked(context,call,admitted,false);
    }
    EngineInterceptions InterceptionSettings() {
        Guard guard(&lock_);return interceptions_;
    }
    bool HasEngineFinalizer() {
        Guard guard(&lock_);
        return module_ && GetProcAddress(module_,"Heroes5PluginFinalize");
    }
    RuntimeResult InvokeWindowInput(WindowInputContext& context) {
        if (DispatchingOnCurrentThread()) { return RuntimeResult::CallRejected; }
        Guard guard(&lock_);
        if (!module_) { return RuntimeResult::NotLoaded; }
        using InputHandler=uint32_t (__cdecl*)(const WindowInputContext*);
        const auto handler=reinterpret_cast<InputHandler>(GetProcAddress(module_,"Heroes5PluginWindowInput"));
        if (!handler) { return RuntimeResult::Ok; }
        if (context.size!=sizeof(context) || context.version!=1 || (context.message!=WM_LBUTTONDBLCLK && context.message!=WM_RBUTTONDBLCLK) ||
            !ValidEngineContext(context.engine)) { return RuntimeResult::CallRejected; }
        EngineContextScope contextScope(activeEngineContext_,context.engine);
        PayloadDispatchScope dispatchScope(&dispatchThread_);
        return handler(&context)==1 ? RuntimeResult::Ok : RuntimeResult::CallRejected;
    }
    RuntimeResult FinalizeEngine(EngineContext& context, bool replacing = false) {
        Guard guard(&lock_);
        if (!module_) { return RuntimeResult::NotLoaded; }
        using Finalizer=uint32_t (__cdecl*)(const EngineContext*);
        auto finalizer=reinterpret_cast<Finalizer>(replacing ? GetProcAddress(module_,"Heroes5PluginPrepareReplace") : nullptr);
        if (!finalizer) { finalizer=reinterpret_cast<Finalizer>(GetProcAddress(module_,"Heroes5PluginFinalize")); }
        if (!finalizer) { return RuntimeResult::Ok; }
        DWORD process=0;
        if (context.size!=sizeof(context) || context.version!=2 || !context.window ||
            GetWindowThreadProcessId(context.window,&process)!=GetCurrentThreadId() || process!=GetCurrentProcessId() ||
            (residentSettings_[0] ? !ValidEngineContext(context) :
             context.residentSchema || context.residentBytes || context.residentData)) { return RuntimeResult::CallRejected; }
        EngineContextScope contextScope(activeEngineContext_,context);
        PayloadDispatchScope dispatchScope(&dispatchThread_);
        return finalizer(&context)==1 ? RuntimeResult::Ok : RuntimeResult::CallRejected;
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
        snapshot.residentSchema = residentSettings_[0];
        snapshot.residentBytes = residentSettings_[1];
        std::memcpy(snapshot.data.data(), state_.data(), snapshot.bytes);
        return RuntimeResult::Ok;
    }
    RuntimeResult ImportState(const PluginSnapshot& snapshot) {
        Guard guard(&lock_);
        if (!module_) { return RuntimeResult::NotLoaded; }
        if (snapshot.magic != 0x54533548 || snapshot.version != 2 || snapshot.bytes > StateCapacity ||
            snapshot.schema != api_.stateSchema || snapshot.bytes != api_.stateBytes ||
            snapshot.residentSchema != residentSettings_[0] || snapshot.residentBytes != residentSettings_[1] ||
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
    std::array<uint32_t, 2> ResidentSettings() {
        Guard guard(&lock_);
        return residentSettings_;
    }
    EngineSubscriptions HookSettings() {
        Guard guard(&lock_);
        return hooks_;
    }
};
}
