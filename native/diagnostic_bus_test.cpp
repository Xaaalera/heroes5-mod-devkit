#include "diagnostic_bus.hpp"
#include "plugin_runtime.hpp"
#include <cstdio>

int wmain(int count, wchar_t** arguments) {
    if (count != 3) { return 1; }
    heroes5_sdk::DiagnosticBus owner;
    heroes5_sdk::DiagnosticSnapshot snapshot;
    const auto check = [](bool valid, const char* message) {
        if (!valid) { std::fprintf(stderr, "%s\n", message); }
        return valid;
    };
    if (!check(owner.Read(0, heroes5_sdk::LogLevel::Debug, snapshot) && snapshot.count == 0, "empty bus")) { return 2; }
    {
        heroes5_sdk::PluginRuntime first, second;
        uint32_t result = 0;
        if (!check(first.Replace(arguments[1]) == heroes5_sdk::RuntimeResult::Ok &&
                   second.Replace(arguments[2]) == heroes5_sdk::RuntimeResult::Ok &&
                   first.Invoke(8, 0, result) == heroes5_sdk::RuntimeResult::Ok && result == 1 &&
                   second.Invoke(8, 0, result) == heroes5_sdk::RuntimeResult::Ok && result == 2,
                   "two simultaneous DLL producers")) { return 3; }
        if (!check(first.Replace(arguments[2]) == heroes5_sdk::RuntimeResult::Ok &&
                   first.Invoke(8, 0, result) == heroes5_sdk::RuntimeResult::Ok && result == 2,
                   "replacement retains shared journal")) { return 3; }
    }
    if (!check(owner.Read(0, heroes5_sdk::LogLevel::Debug, snapshot) && snapshot.count == 3 &&
               std::strcmp(snapshot.records[0].module, "fixture-one") == 0 &&
               std::strcmp(snapshot.records[1].module, "fixture-two") == 0,
               "records survive payload replacement and unload")) { return 4; }
    if (!check(!owner.Write(static_cast<heroes5_sdk::LogLevel>(99), "bad", "message") &&
               !owner.Write(heroes5_sdk::LogLevel::Info, "", "message") &&
               !owner.Write(heroes5_sdk::LogLevel::Info, "bad", std::string(512, 'x')) &&
               !owner.Write(heroes5_sdk::LogLevel::Info, "bad", std::string_view("\xff", 1)),
               "invalid messages rejected")) { return 5; }
    for (uint32_t index = 0; index < 300; ++index) {
        if (!owner.Write(index % 2 ? heroes5_sdk::LogLevel::Warning : heroes5_sdk::LogLevel::Debug,
                         "overflow-fixture", "bounded ring")) { return 6; }
    }
    if (!check(owner.Read(0, heroes5_sdk::LogLevel::Warning, snapshot) && snapshot.count == 64 &&
               snapshot.oldestSequence == 48 && snapshot.nextSequence == 304,
               "bounded ring and severity filter")) { return 7; }
    const auto cursor = snapshot.records[snapshot.count - 1].sequence;
    if (!check(owner.Read(cursor, heroes5_sdk::LogLevel::Warning, snapshot) && snapshot.count == 64 &&
               snapshot.records[0].sequence > cursor, "paged sequence read")) { return 8; }
    std::puts("Shared diagnostics: two DLL generations, unload, bounds, filtering and paging PASS");
    return 0;
}
