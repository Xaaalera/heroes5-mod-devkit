#pragma once
#include <windows.h>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <span>
#include <array>

namespace heroes5_sdk {
struct SelectorFixup { uint32_t offset; int32_t direction; };
struct BankSelectorPayload {
    uint32_t size = sizeof(BankSelectorPayload);
    uint32_t version = 1;
    const unsigned char* code = nullptr;
    uint32_t codeBytes = 0;
    const unsigned char* initialData = nullptr;
    uint32_t dataBytes = 0;
    const uint32_t* dataOffsets = nullptr;
    uint32_t dataOffsetCount = 0;
    const SelectorFixup* codeFixups = nullptr;
    uint32_t codeFixupCount = 0;
    const SelectorFixup* dataFixups = nullptr;
    uint32_t dataFixupCount = 0;
    uintptr_t sourceCode = 0;
    uintptr_t sourceData = 0;
    std::array<unsigned char, 32> dataSchema{};
    const char* packageSha256 = nullptr;
};
struct BankReplaceRequest {
    uint32_t size = sizeof(BankReplaceRequest);
    uint32_t version = 1;
    wchar_t path[4096]{};
    uint32_t applied = 0;
    uint32_t moduleReleased = 0;
};
inline bool SelectorModuleBytes(HMODULE module, const void* pointer, size_t bytes) {
    if (!bytes) { return true; }
    if (!module || !pointer) { return false; }
    const auto base = reinterpret_cast<uintptr_t>(module);
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(module);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0 || dos->e_lfanew > 4096) { return false; }
    const auto* image = reinterpret_cast<const IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    if (image->Signature != IMAGE_NT_SIGNATURE) { return false; }
    uintptr_t position = reinterpret_cast<uintptr_t>(pointer);
    if (position < base || position - base > image->OptionalHeader.SizeOfImage
        || bytes > image->OptionalHeader.SizeOfImage - (position - base)) { return false; }
    const auto end = position + bytes;
    while (position < end) {
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQuery(reinterpret_cast<void*>(position), &region, sizeof(region))
            || region.State != MEM_COMMIT || region.AllocationBase != module
            || (region.Protect & (PAGE_GUARD | PAGE_NOACCESS))
            || !(region.Protect & (PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY |
                                  PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY))) { return false; }
        const auto next = reinterpret_cast<uintptr_t>(region.BaseAddress) + region.RegionSize;
        if (next <= position) { return false; }
        position = next;
    }
    return true;
}
enum class SelectorAction : uint32_t { Status, Initialize, Replace, Invoke, Stop, ReadState, BindWindow, AttachHook, DetachHook };
// In-process trusted SDK transport. No borrowed buffers are retained by the
// service. A separate game-thread hook/controller must enforce game ownership.
struct SelectorRequest {
    uint32_t size = sizeof(SelectorRequest);
    uint32_t version = 1;
    SelectorAction action = SelectorAction::Status;
    uint64_t expectedGeneration = 0;
    uint64_t generation = 0;
    HWND window = nullptr;
    std::array<unsigned char, 32> dataSchema{};
    const unsigned char* bytes = nullptr;
    uint32_t byteCount = 0;
    const uint32_t* stateOffsets = nullptr;
    uint32_t stateOffsetCount = 0;
    const SelectorFixup* codeFixups = nullptr;
    uint32_t codeFixupCount = 0;
    const SelectorFixup* dataFixups = nullptr;
    uint32_t dataFixupCount = 0;
    uintptr_t sourceCode = 0;
    uintptr_t sourceData = 0;
    uintptr_t publicModel = 0;
    uintptr_t root = 0;
    uint32_t stateOffset = 0;
    unsigned char* output = nullptr;
    uint32_t outputBytes = 0;
};
// Trusted x86 generated selectors only. This owns memory, not a game hook.
// The resident host must stop hook admission and release game UI references
// before destroying this object. Calls are serialized and must not re-enter.
class SelectorRuntime {
    static_assert(sizeof(uintptr_t) == sizeof(uint32_t));
    std::mutex mutex_;
    unsigned char* data_ = nullptr;
    size_t dataBytes_ = 0;
    void* code_ = nullptr;
    void* retired_ = nullptr;

    static bool Adjust(unsigned char* bytes, size_t size, size_t offset, uint32_t delta) {
        if (size < sizeof(uint32_t) || offset > size - sizeof(uint32_t)) { return false; }
        uint32_t value = 0;
        std::memcpy(&value, bytes + offset, sizeof(value));
        value += delta;
        std::memcpy(bytes + offset, &value, sizeof(value));
        return true;
    }
public:
    SelectorRuntime() = default;
    SelectorRuntime(const SelectorRuntime&) = delete;
    SelectorRuntime& operator=(const SelectorRuntime&) = delete;
    ~SelectorRuntime() {
        std::lock_guard guard(mutex_);
        if (code_) { VirtualFree(code_, 0, MEM_RELEASE); }
        if (retired_) { VirtualFree(retired_, 0, MEM_RELEASE); }
        if (data_) { VirtualFree(data_, 0, MEM_RELEASE); }
    }
    bool Initialize(std::span<const unsigned char> bytes, const auto& offsets, uintptr_t sourceAddress) {
        std::lock_guard guard(mutex_);
        if (data_ || bytes.empty() || bytes.size() > 65536) { return false; }
        auto* candidate = static_cast<unsigned char*>(VirtualAlloc(nullptr, bytes.size(),
            MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
        if (!candidate) { return false; }
        std::memcpy(candidate, bytes.data(), bytes.size());
        for (const auto offset : offsets) {
            if (!Adjust(candidate, bytes.size(), offset, reinterpret_cast<uintptr_t>(candidate) - sourceAddress)) {
                VirtualFree(candidate, 0, MEM_RELEASE); return false;
            }
        }
        data_ = candidate; dataBytes_ = bytes.size();
        return true;
    }
    bool Replace(std::span<const unsigned char> bytes, const auto& codeFixups,
        const auto& dataFixups, uintptr_t sourceCode, uintptr_t sourceData) {
        std::lock_guard guard(mutex_);
        if (!data_ || bytes.empty() || bytes.size() > 4096) { return false; }
        // A failed OS retirement must not accumulate generations indefinitely.
        if (retired_) {
            if (!VirtualFree(retired_, 0, MEM_RELEASE)) { return false; }
            retired_ = nullptr;
        }
        auto* candidate = static_cast<unsigned char*>(VirtualAlloc(nullptr, 4096,
            MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
        if (!candidate) { return false; }
        std::memcpy(candidate, bytes.data(), bytes.size());
        bool valid = true;
        for (const auto& fixup : codeFixups) {
            const uint32_t delta = reinterpret_cast<uintptr_t>(candidate) - sourceCode;
            valid = valid && (fixup.direction == 1 || fixup.direction == -1)
                && Adjust(candidate, bytes.size(), fixup.offset, fixup.direction > 0 ? delta : 0u - delta);
        }
        for (const auto& fixup : dataFixups) {
            const uint32_t delta = reinterpret_cast<uintptr_t>(data_) - sourceData;
            valid = valid && (fixup.direction == 1 || fixup.direction == -1)
                && Adjust(candidate, bytes.size(), fixup.offset, fixup.direction > 0 ? delta : 0u - delta);
        }
        DWORD protection = 0;
        if (!valid || !VirtualProtect(candidate, 4096, PAGE_EXECUTE_READ, &protection)
            || !FlushInstructionCache(GetCurrentProcess(), candidate, bytes.size())) {
            VirtualFree(candidate, 0, MEM_RELEASE); return false;
        }
        const auto previous = code_;
        code_ = candidate;
        if (previous && !VirtualFree(previous, 0, MEM_RELEASE)) { retired_ = previous; }
        return true; // Candidate activated; any retained old block cannot execute.
    }
    bool Invoke(uintptr_t publicModel, uintptr_t& root) {
        std::lock_guard guard(mutex_);
        if (!code_) { return false; }
        using Callback = uintptr_t (__cdecl*)(uintptr_t);
        root = reinterpret_cast<Callback>(code_)(publicModel);
        return true;
    }
    bool Ready() {
        std::lock_guard guard(mutex_);
        return code_ != nullptr;
    }
    bool Stop() {
        std::lock_guard guard(mutex_);
        if (retired_ && !VirtualFree(retired_, 0, MEM_RELEASE)) { return false; }
        retired_ = nullptr;
        if (code_ && !VirtualFree(code_, 0, MEM_RELEASE)) { return false; }
        code_ = nullptr;
        return true; // Retained data deliberately survives remove/readd.
    }
    bool ReadState(size_t offset, std::span<unsigned char> output) {
        std::lock_guard guard(mutex_);
        if (!data_ || offset > dataBytes_ || output.size() > dataBytes_ - offset) { return false; }
        std::memcpy(output.data(), data_ + offset, output.size());
        return true;
    }
};
}
