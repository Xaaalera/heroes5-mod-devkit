#include "plugin_runtime.hpp"
#include "diagnostic_bus.hpp"

namespace {
constexpr uint32_t DisplayValue = 42;
constexpr char ModuleName[] = "native-counter";
struct State { uint32_t counter = 0; };

uint32_t __cdecl Dispatch(uint32_t command, uint32_t argument, void* memory,
    uint32_t bytes, uint32_t* result) {
    if (!memory || !result || bytes != sizeof(State)) { return 0; }
    static bool announced = false;
    if (!announced) {
        announced = heroes5_sdk::Trace(heroes5_sdk::LogLevel::Info, ModuleName, "plugin callback active");
    }
    auto& state = *static_cast<State*>(memory);
    if (command == 0) { *result = DisplayValue; return 1; }
    if (command == 1) { state.counter += argument; *result = state.counter; return 1; }
    if (command == 2) { *result = DisplayValue; return 1; }
    return 0;
}
const heroes5_sdk::PluginApi api{
    .size = sizeof(heroes5_sdk::PluginApi),
    .abi = heroes5_sdk::PluginAbi,
    .stateSchema = 1,
    .stateBytes = sizeof(State),
    .dispatch = Dispatch,
    .eventMessage = WM_APP + 0x531,
    .eventCommand = 2,
    .showEventResult = 1,
    .engineCallSite = 0,
    .engineOriginalTarget = 0,
    .engineCommand = 0,
};
}

extern "C" const heroes5_sdk::PluginApi* __cdecl Heroes5PluginQuery() { return &api; }
#pragma comment(linker, "/EXPORT:Heroes5PluginQuery=_Heroes5PluginQuery")
