#ifndef XKIT_ENGINE_HOOK_OWNER_HPP
#define XKIT_ENGINE_HOOK_OWNER_HPP
#include "../game-api/include/h5/script_observers.hpp"
#include <MinHook.h>
#include <span>
#include "engine_hook_protocol.hpp"

namespace heroes5_sdk {
inline bool EngineImageSite(uintptr_t site, uint32_t bytes) {
    const auto module = GetModuleHandleW(nullptr);
    if (!module || !site || bytes < 5 || bytes > 16) { return false; }
    const auto image = reinterpret_cast<uintptr_t>(module);
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(image);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew < sizeof(IMAGE_DOS_HEADER) ||
        dos->e_lfanew > 4096) { return false; }
    const auto* headers = reinterpret_cast<const IMAGE_NT_HEADERS*>(image + dos->e_lfanew);
    if (headers->Signature != IMAGE_NT_SIGNATURE ||
        headers->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC ||
        site < image || site + bytes < site || site + bytes > image + headers->OptionalHeader.SizeOfImage) { return false; }
    MEMORY_BASIC_INFORMATION region{};
    if (!VirtualQuery(reinterpret_cast<void*>(site), &region, sizeof(region)) ||
        region.State != MEM_COMMIT || region.Type != MEM_IMAGE || region.AllocationBase != module ||
        (region.Protect != PAGE_EXECUTE_READ && region.Protect != PAGE_EXECUTE_READWRITE &&
         region.Protect != PAGE_EXECUTE_WRITECOPY) ||
        site + bytes > reinterpret_cast<uintptr_t>(region.BaseAddress) + region.RegionSize) { return false; }
    const auto* sections = IMAGE_FIRST_SECTION(headers);
    for (uint32_t index = 0; index < headers->FileHeader.NumberOfSections; ++index) {
        const auto& section = sections[index];
        const auto begin = image + section.VirtualAddress;
        const auto end = begin + section.Misc.VirtualSize;
        if ((section.Characteristics & IMAGE_SCN_MEM_EXECUTE) && site >= begin && site + bytes <= end) { return true; }
    }
    return false;
}
// This metadata and the tiny entry stub remain owned by the process loader.
struct EngineGateControl {
    DWORD thread = 0;
    LONG interceptor = 0;
    EngineCallPolicy policy;
};
inline EngineGateControl& GateControl(h5::hooks::ScriptObservers* observers) {
    return *reinterpret_cast<EngineGateControl*>(observers + 1);
}
inline uint32_t __cdecl DispatchEngineGate(h5::hooks::ScriptObservers* observers, EngineCall* call) {
    const auto original = call->frame;
    auto& control = GateControl(observers);
    // savedEsp has its usual entry-stack relationship despite the stub's
    // private descriptor word below the caller's real return address.
    *reinterpret_cast<uint32_t*>(original.savedEsp) = original.flags;
    if (GetCurrentThreadId() == control.thread) {
        const auto interceptor = InterlockedCompareExchange(&control.interceptor, 0, 0);
        if (interceptor) {
            const auto callback = reinterpret_cast<EngineInterceptor>(static_cast<uintptr_t>(interceptor));
            const bool accepted = callback(call) == 1;
            if (accepted && call->action == EngineCallAction::ReturnEax && control.policy.allowReturn) {
                const auto result = call->result;
                call->frame = original;call->frame.eax = result;
                const uintptr_t caller = *reinterpret_cast<const uintptr_t*>(original.savedEsp + 4);
                const uintptr_t returnStack = original.savedEsp + 4 + control.policy.stackPopBytes;
                *reinterpret_cast<uintptr_t*>(returnStack) = caller;
                call->frame.savedEsp = static_cast<uint32_t>(returnStack);
                return 1;
            }
            if (accepted && call->action == EngineCallAction::Branch && control.policy.branch) {
                call->frame.savedEsp = original.savedEsp;
                constexpr uint32_t arithmeticFlags = 0x8d5;
                call->frame.flags = (original.flags & ~arithmeticFlags) | (call->frame.flags & arithmeticFlags);
                *reinterpret_cast<uintptr_t*>(original.savedEsp) = control.policy.branch;
                return 0;
            }
            // Refusal and unsupported outcomes preserve the complete input frame.
            call->frame = original;
        } else {
            for (auto& slot : observers->callbacks) {
                const auto callback = InterlockedCompareExchange(&slot, 0, 0);
                if (callback) {
                    reinterpret_cast<EngineObserver>(static_cast<uintptr_t>(callback))(&call->frame);
                }
            }
            call->frame = original;
        }
    }
    *reinterpret_cast<uintptr_t*>(original.savedEsp) = observers->originalTarget;
    return 0;
}
__declspec(naked) inline void EngineGateEntry() {
    __asm {
        pushfd
        pushad
        mov ebx, esp
        sub esp, 0x260
        and esp, 0xfffffff0
        fxsave [esp]
        cld
        lea ebp, [esp + 0x200]
        mov esi, ebx
        mov edi, ebp
        mov ecx, 9
        rep movsd
        add dword ptr [ebp + 12], 4
        mov dword ptr [ebp + 36], 0
        mov dword ptr [ebp + 40], 0
        mov eax, dword ptr [ebx + 36]
        push ebp
        push eax
        call DispatchEngineGate
        add esp, 8
        mov esi, ebp
        mov edi, ebx
        mov ecx, 9
        cld
        rep movsd
        fxrstor [esp]
        test eax, eax
        jnz return_to_caller
        mov esp, ebx
        popad
        popfd
        ret
    return_to_caller:
        mov esp, ebx
        popad
        popfd
        mov esp, dword ptr [esp - 24]
        ret
    }
}
inline std::array<unsigned char, 10> EngineGateCode(uintptr_t code, uintptr_t descriptor) {
    std::array<unsigned char, 10> bytes{0x68,0,0,0,0,0xe9,0,0,0,0};
    const auto pointer = static_cast<uint32_t>(descriptor);
    const auto jump = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(EngineGateEntry) - code - bytes.size());
    memcpy(bytes.data() + 1, &pointer, sizeof(pointer));
    memcpy(bytes.data() + 6, &jump, sizeof(jump));
    return bytes;
}

// Instantiate only in the process-lifetime loader. The caller owns build/site
// admission; this owns relocation and resident dispatch, not plugin callbacks.
// All changes run on the bound window thread, outside observer callbacks.
class EngineHookOwner {
    struct Entry {
        uintptr_t site = 0;
        std::array<unsigned char, 16> expected{};
        size_t bytes = 0;
        DWORD protection = 0;
        h5::hooks::ScriptObservers* observers = nullptr;
        uintptr_t trampoline = 0;
        bool enabled = false;
        bool uncertain = false;
    };
    HWND window_;
    DWORD thread_;
    std::array<Entry, 64> entries_{};

    bool OnOwnerThread() const {
        DWORD process = 0;
        return thread_ && GetCurrentThreadId() == thread_ &&
            GetWindowThreadProcessId(window_, &process) == thread_ && process == GetCurrentProcessId();
    }
    bool Verify(const Entry& entry) const {
        if (entry.uncertain || !entry.observers) { return false; }
        const auto code = reinterpret_cast<uintptr_t>(entry.observers) - h5::hooks::ScriptObserverDescriptorOffset;
        const auto expectedCode = EngineGateCode(code, reinterpret_cast<uintptr_t>(entry.observers));
        std::array<unsigned char,32> digest{};
        if (!h5::hooks::ObserverMemory(code, expectedCode.size(), PAGE_EXECUTE_READ, reinterpret_cast<void*>(code)) ||
            !h5::hooks::ObserverMemory(reinterpret_cast<uintptr_t>(entry.observers),
                sizeof(*entry.observers) + sizeof(EngineGateControl), PAGE_READWRITE, reinterpret_cast<void*>(code)) ||
            entry.observers->version != 3 || entry.observers->size != sizeof(*entry.observers) ||
            entry.observers->callSite != entry.site || entry.observers->originalTarget != entry.trampoline ||
            entry.observers->codeBytes != expectedCode.size() || entry.observers->originalCallOffset != 0 ||
            GateControl(entry.observers).thread != thread_ ||
            memcmp(reinterpret_cast<const void*>(code), expectedCode.data(), expectedCode.size()) != 0 ||
            !h5::hooks::ObserverCodeDigest(code, expectedCode.size(), digest) || digest != entry.observers->codeDigest) {
            return false;
        }
        // Windows turns a written executable COW image page into a private
        // executable writable page. Accept that transition only for a site
        // originally admitted as WRITECOPY; ordinary RX sites remain strict.
        const bool privateCopy = entry.protection == PAGE_EXECUTE_WRITECOPY;
        const bool siteMemory = h5::hooks::ObserverMemory(entry.site, entry.bytes, entry.protection) ||
            (privateCopy && h5::hooks::ObserverMemory(entry.site, entry.bytes, PAGE_EXECUTE_READWRITE));
        if (entry.enabled) {
            if (!siteMemory) { return false; }
            int32_t relative = 0;
            memcpy(&relative, reinterpret_cast<const void*>(entry.site + 1), sizeof(relative));
            return siteMemory && *reinterpret_cast<const unsigned char*>(entry.site) == 0xe9 &&
                entry.site + 5 + relative == code;
        }
        return siteMemory &&
            std::memcmp(reinterpret_cast<void*>(entry.site), entry.expected.data(), entry.bytes) == 0;
    }
public:
    explicit EngineHookOwner(HWND window) : window_(window), thread_(GetWindowThreadProcessId(window, nullptr)) {}
    EngineHookOwner(const EngineHookOwner&) = delete;
    EngineHookOwner& operator=(const EngineHookOwner&) = delete;
    // Resident code and MinHook trampolines deliberately live until exit. Never
    // unload the backend DLL while these entries exist, even when disabled.
    h5::hooks::ScriptObservers* Prepare(uintptr_t site, std::span<const unsigned char> expected) {
        if (!OnOwnerThread() || expected.size() < 5 || expected.size() > 16) { return nullptr; }
        Entry* available = nullptr;
        for (auto& entry : entries_) {
            if (entry.site == site) {
                return entry.bytes == expected.size() &&
                    std::memcmp(entry.expected.data(), expected.data(), expected.size()) == 0 && Verify(entry)
                    ? entry.observers : nullptr;
            }
            if (!entry.site && !available) { available = &entry; }
        }
        MEMORY_BASIC_INFORMATION region{};
        if (!available || !VirtualQuery(reinterpret_cast<void*>(site), &region, sizeof(region)) ||
            (region.Protect != PAGE_EXECUTE_READ && region.Protect != PAGE_EXECUTE_WRITECOPY &&
             !(region.Protect == PAGE_EXECUTE_READWRITE && EngineImageSite(site, expected.size()))) ||
            !h5::hooks::ObserverMemory(site, expected.size(), region.Protect) ||
            std::memcmp(reinterpret_cast<void*>(site), expected.data(), expected.size()) != 0) { return nullptr; }
        // Universe may already have written another instruction on this image
        // page. Admit that state only in the main executable and with the exact
        // requested signature; writable private allocations remain unsupported.
        const auto initialized = MH_Initialize();
        if (initialized != MH_OK && initialized != MH_ERROR_ALREADY_INITIALIZED) { return nullptr; }
        auto* observers = h5::hooks::CreateScriptObserverStorage(site, site, true, thread_);
        if (!observers) { return nullptr; }
        const auto code = reinterpret_cast<uintptr_t>(observers) - h5::hooks::ScriptObserverDescriptorOffset;
        void* trampoline = nullptr;
        if (MH_CreateHook(reinterpret_cast<void*>(site), reinterpret_cast<void*>(code), &trampoline) != MH_OK) {
            VirtualFree(reinterpret_cast<void*>(code), 0, MEM_RELEASE);
            return nullptr;
        }
        available->site = site;
        available->bytes = expected.size();
        available->protection = region.Protect;
        std::memcpy(available->expected.data(), expected.data(), expected.size());
        available->observers = observers;
        available->trampoline = reinterpret_cast<uintptr_t>(trampoline);
        // Hook is disabled. Seal the real relocated continuation before enable.
        DWORD protection = 0;
        if (!VirtualProtect(reinterpret_cast<void*>(code), 4096, PAGE_READWRITE, &protection)) {
            available->uncertain = true; return nullptr;
        }
        const auto bytes = EngineGateCode(code, reinterpret_cast<uintptr_t>(observers));
        memset(reinterpret_cast<void*>(code), 0xcc, 4096);
        memcpy(reinterpret_cast<void*>(code), bytes.data(), bytes.size());
        observers->codeBytes = static_cast<uint32_t>(bytes.size());
        observers->originalCallOffset = 0;
        observers->originalTarget = static_cast<uint32_t>(available->trampoline);
        GateControl(observers).thread = thread_;
        const bool hashed = h5::hooks::ObserverCodeDigest(code, observers->codeBytes, observers->codeDigest);
        const bool protectedCode = VirtualProtect(reinterpret_cast<void*>(code), 4096, PAGE_EXECUTE_READ, &protection) != 0;
        const bool flushed = FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(code), observers->codeBytes) != 0;
        if (!hashed || !protectedCode || !flushed) { available->uncertain = true; return nullptr; }
        return observers;
    }
    bool SetEnabled(uintptr_t site, bool enabled) {
        if (!OnOwnerThread()) { return false; }
        for (auto& entry : entries_) {
            if (entry.site != site) { continue; }
            if (!Verify(entry)) { return false; }
            if (entry.enabled == enabled) { return true; }
            const auto result = enabled ? MH_EnableHook(reinterpret_cast<void*>(site)) : MH_DisableHook(reinterpret_cast<void*>(site));
            if (result != MH_OK) { entry.uncertain = true; return false; }
            entry.enabled = enabled;
            if (!Verify(entry)) { entry.uncertain = true; return false; }
            return true;
        }
        return false;
    }
    bool CanObserve(uintptr_t site) const {
        for (const auto& entry : entries_) {
            if (entry.site == site && entry.observers) {
                return InterlockedCompareExchange(&GateControl(entry.observers).interceptor,0,0) == 0;
            }
        }
        return false;
    }
    LONG* SetInterceptor(uintptr_t site, const EngineCallPolicy& policy, EngineInterceptor callback) {
        if (!OnOwnerThread() || !callback || policy.allowReturn > 1 || policy.stackPopBytes > 64 ||
            policy.stackPopBytes % 4 || (!policy.allowReturn && policy.stackPopBytes)) { return nullptr; }
        for (auto& entry : entries_) {
            if (entry.site != site || !Verify(entry)) { continue; }
            if (policy.branch >= site && policy.branch < site + entry.bytes) { return nullptr; }
            for (const auto& slot : entry.observers->callbacks) { if (slot) { return nullptr; } }
            auto& control = GateControl(entry.observers);
            if (control.interceptor) { return nullptr; }
            control.policy = policy;
            InterlockedExchange(&control.interceptor, static_cast<LONG>(reinterpret_cast<uintptr_t>(callback)));
            return &control.interceptor;
        }
        return nullptr;
    }
    bool RetireInterceptor(uintptr_t site, LONG* slot, EngineInterceptor callback) {
        if (!OnOwnerThread() || !slot || !callback) { return false; }
        for (auto& entry : entries_) {
            if (entry.site == site && entry.observers && slot == &GateControl(entry.observers).interceptor) {
                return InterlockedCompareExchange(slot,0,static_cast<LONG>(reinterpret_cast<uintptr_t>(callback))) ==
                    static_cast<LONG>(reinterpret_cast<uintptr_t>(callback));
            }
        }
        return false;
    }
    bool Retire(uintptr_t site, LONG* slot, void (__cdecl* callback)(const h5::hooks::X86ObserverFrame*)) {
        if (!OnOwnerThread()) { return false; }
        for (auto& entry : entries_) {
            if (entry.site != site || !entry.observers) { continue; }
            for (auto& candidate : entry.observers->callbacks) {
                if (&candidate == slot) { return h5::hooks::RemoveContextObserver(slot, callback); }
            }
        }
        return false;
    }
};
}
#endif
