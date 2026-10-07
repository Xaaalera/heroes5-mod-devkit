#include "plugin_runtime.hpp"
#include "diagnostic_bus.hpp"
#ifndef FIXTURE_VERSION
#define FIXTURE_VERSION 1
#endif
#ifndef FIXTURE_ABI
#define FIXTURE_ABI 3
#endif
#ifndef FIXTURE_ENGINE_SITE
#define FIXTURE_ENGINE_SITE 0
#endif
#ifndef FIXTURE_SCHEMA
#define FIXTURE_SCHEMA 1
#endif
namespace {
uint32_t __cdecl Invoke(uint32_t command, uint32_t argument, void* memory,
    uint32_t bytes, uint32_t* result) {
    if (bytes != sizeof(uint32_t)) { return 0; }
    auto& counter = *static_cast<uint32_t*>(memory);
    if (command == 0) { *result = FIXTURE_VERSION; return 1; }
    if (command == 1) { counter += argument; *result = counter; return 1; }
    if (command == 2 && FIXTURE_VERSION == 2) { *result = 42; return 1; }
    if (command == 3) { counter = 999; return 0; }
    if (command == 4) { *result = GetCurrentThreadId(); return 1; }
    if (command == 5) { counter += argument; *result = FIXTURE_VERSION * 1000 + counter; return 1; }
    if (command == 6) {
        const auto* events = reinterpret_cast<const HANDLE*>(argument);
        SetEvent(events[0]);
        if (WaitForSingleObject(events[1], 5000) != WAIT_OBJECT_0) { return 0; }
        *result = FIXTURE_VERSION; return 1;
    }
    if (command == 7) {
        counter = FIXTURE_VERSION * 1000000 + (counter % 1000000) + 1;
        *result = counter; return 1;
    }
    if (command == 8) {
        *result = FIXTURE_VERSION;
        return heroes5_sdk::Trace(heroes5_sdk::LogLevel::Info,
            FIXTURE_VERSION == 2 ? "fixture-two" : "fixture-one", "module callback active") ? 1 : 0;
    }
    return 0;
}
const heroes5_sdk::PluginApi api{sizeof(heroes5_sdk::PluginApi), FIXTURE_ABI,
    FIXTURE_SCHEMA, sizeof(uint32_t), Invoke,
    FIXTURE_VERSION == 2 ? WM_APP + 0x531 : 0,
    FIXTURE_VERSION == 2 ? 5 : 0, FIXTURE_VERSION == 2 ? 1 : 0,
    FIXTURE_ENGINE_SITE, FIXTURE_ENGINE_SITE ? 0xd10980 : 0, FIXTURE_ENGINE_SITE ? 7 : 0};
}
extern "C" const heroes5_sdk::PluginApi* __cdecl Heroes5PluginQuery() { return &api; }
#pragma comment(linker, "/EXPORT:Heroes5PluginQuery=_Heroes5PluginQuery")
