#pragma once
#include <windows.h>
#include <cstdint>
#include <atomic>

namespace heroes5_sdk {
struct ConsoleStopGate {
    std::atomic<uint32_t> phase{0};
    bool Request() { uint32_t expected = 0; return phase.compare_exchange_strong(expected, 1); }
    bool Commit() { uint32_t expected = 1; return phase.compare_exchange_strong(expected, 2); }
    bool Cancel() { uint32_t expected = 1; return phase.compare_exchange_strong(expected, 0); }
    void Reset() { phase.store(0); } // Only after the UI thread joined.
};
struct ConsoleConnection {
    uint32_t size = sizeof(ConsoleConnection);
    uint32_t version = 1;
    HWND gameWindow = nullptr;
    HMODULE sdkModule = nullptr;
};
}
