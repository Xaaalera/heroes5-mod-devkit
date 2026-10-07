#pragma once
#include <windows.h>
#include <array>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <string_view>

namespace heroes5_sdk {
enum class LogLevel : uint32_t { Debug, Info, Warning, Error };
constexpr uint32_t DiagnosticCapacity = 256;
struct DiagnosticRecord {
    uint64_t sequence;
    uint64_t milliseconds;
    LogLevel level;
    char module[64];
    char message[512];
    uint32_t reserved;
};
struct DiagnosticSnapshot {
    uint64_t oldestSequence = 0;
    uint64_t nextSequence = 0;
    uint32_t dropped = 0;
    uint32_t count = 0;
    std::array<DiagnosticRecord, 64> records{};
};
struct DiagnosticReadRequest {
    uint32_t size = sizeof(DiagnosticReadRequest);
    uint32_t version = 1;
    uint64_t after = 0;
    LogLevel minimum = LogLevel::Debug;
    uint32_t reserved = 0;
    DiagnosticSnapshot snapshot{};
};
static_assert(sizeof(DiagnosticReadRequest) <= 65536);
static_assert(sizeof(DiagnosticRecord) == 600);
static_assert(sizeof(DiagnosticSnapshot) == 38424);
static_assert(sizeof(DiagnosticReadRequest) == 38448);

// Process-local kernel storage shared by independently built DLLs. Only POD
// crosses the mapping; no callback, allocator or module address is retained.
// Construct/use outside DllMain and descriptor queries. Logging makes no game
// calls. Messages must be bounded UTF-8, and publication never waits for a lock.
class DiagnosticBus {
    struct Storage {
        uint32_t magic;
        uint32_t version;
        uint32_t bytes;
        uint32_t valid;
        uint64_t nextSequence;
        volatile LONG dropped;
        uint32_t reserved;
        std::array<DiagnosticRecord, DiagnosticCapacity> records;
    };
    HANDLE mutex_ = nullptr;
    HANDLE mapping_ = nullptr;
    Storage* storage_ = nullptr;

    bool Acquire() noexcept {
        if (!storage_ || !mutex_) { return false; }
        const auto result = WaitForSingleObject(mutex_, 0);
        if (result == WAIT_ABANDONED) {
            // The previous publisher may have died mid-record. Do not accept
            // partial state or silently reset another reader's sequence.
            storage_->valid = 0;
            ReleaseMutex(mutex_);
            return false;
        }
        if (result != WAIT_OBJECT_0) { return false; }
        if (!storage_->valid) { ReleaseMutex(mutex_); return false; }
        return true;
    }
    static bool ValidText(std::string_view text, size_t capacity) noexcept {
        return !text.empty() && text.size() < capacity && text.find('\0') == text.npos &&
            MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(),
                               static_cast<int>(text.size()), nullptr, 0) != 0;
    }
public:
    DiagnosticBus() noexcept {
        FILETIME created{}, exited{}, kernel{}, user{};
        if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user)) { return; }
        const auto identity = (uint64_t(created.dwHighDateTime) << 32) | created.dwLowDateTime;
        std::array<wchar_t, 128> mutexName{}, mappingName{};
        swprintf_s(mutexName.data(), mutexName.size(), L"Local\\XalKit.Diagnostics.v1.%lu.%llu.lock",
                   GetCurrentProcessId(), identity);
        swprintf_s(mappingName.data(), mappingName.size(), L"Local\\XalKit.Diagnostics.v1.%lu.%llu.data",
                   GetCurrentProcessId(), identity);
        mutex_ = CreateMutexW(nullptr, FALSE, mutexName.data());
        if (!mutex_) { return; }
        // Initialization is outside game callbacks. An unavailable/colliding
        // object is rejected; later log writes remain nonblocking.
        const auto wait = WaitForSingleObject(mutex_, 1000);
        if (wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED) { return; }
        mapping_ = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE,
                                     0, sizeof(Storage), mappingName.data());
        const bool fresh = mapping_ && GetLastError() != ERROR_ALREADY_EXISTS;
        if (mapping_) {
            storage_ = static_cast<Storage*>(MapViewOfFile(mapping_, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Storage)));
        }
        if (storage_ && fresh) {
            std::memset(storage_, 0, sizeof(Storage));
            storage_->magic = 0x31444758;
            storage_->version = 1;
            storage_->bytes = sizeof(Storage);
            storage_->nextSequence = 1;
            storage_->valid = 1;
        }
        if (storage_ && (storage_->magic != 0x31444758 || storage_->version != 1 ||
                        storage_->bytes != sizeof(Storage) || !storage_->valid)) {
            UnmapViewOfFile(storage_);
            storage_ = nullptr;
        }
        if (storage_ && !fresh && wait == WAIT_ABANDONED) {
            storage_->valid = 0;
            UnmapViewOfFile(storage_);
            storage_ = nullptr;
        }
        ReleaseMutex(mutex_);
    }
    ~DiagnosticBus() {
        if (storage_) { UnmapViewOfFile(storage_); }
        if (mapping_) { CloseHandle(mapping_); }
        if (mutex_) { CloseHandle(mutex_); }
    }
    DiagnosticBus(const DiagnosticBus&) = delete;
    DiagnosticBus& operator=(const DiagnosticBus&) = delete;

    bool Write(LogLevel level, std::string_view module, std::string_view message) noexcept {
        if (static_cast<uint32_t>(level) > static_cast<uint32_t>(LogLevel::Error) ||
            !ValidText(module, sizeof(DiagnosticRecord::module)) ||
            !ValidText(message, sizeof(DiagnosticRecord::message))) { return false; }
        if (!Acquire()) {
            if (storage_) { InterlockedIncrement(&storage_->dropped); }
            return false;
        }
        if (storage_->nextSequence == UINT64_MAX) { ReleaseMutex(mutex_); return false; }
        const auto sequence = storage_->nextSequence;
        auto& record = storage_->records[(sequence - 1) % DiagnosticCapacity];
        record = {};
        record.sequence = sequence;
        record.milliseconds = GetTickCount64();
        record.level = level;
        std::memcpy(record.module, module.data(), module.size());
        std::memcpy(record.message, message.data(), message.size());
        ++storage_->nextSequence;
        ReleaseMutex(mutex_);
        return true;
    }
    bool Read(uint64_t after, LogLevel minimum, DiagnosticSnapshot& snapshot) noexcept {
        if (static_cast<uint32_t>(minimum) > static_cast<uint32_t>(LogLevel::Error) || !Acquire()) { return false; }
        snapshot = {};
        snapshot.nextSequence = storage_->nextSequence;
        snapshot.oldestSequence = snapshot.nextSequence > DiagnosticCapacity ?
            snapshot.nextSequence - DiagnosticCapacity : 1;
        snapshot.dropped = static_cast<uint32_t>(InterlockedCompareExchange(&storage_->dropped, 0, 0));
        for (auto sequence = snapshot.oldestSequence; sequence < snapshot.nextSequence; ++sequence) {
            const auto& record = storage_->records[(sequence - 1) % DiagnosticCapacity];
            if (sequence > after && record.level >= minimum) {
                snapshot.records[snapshot.count++] = record;
                if (snapshot.count == snapshot.records.size()) { break; }
            }
        }
        ReleaseMutex(mutex_);
        return true;
    }
};

inline DiagnosticBus& Diagnostics() {
    static DiagnosticBus bus;
    return bus;
}
inline bool Trace(LogLevel level, std::string_view module, std::string_view message) {
    return Diagnostics().Write(level, module, message);
}
}
