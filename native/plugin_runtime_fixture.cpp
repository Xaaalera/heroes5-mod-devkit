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
#ifndef FIXTURE_RESIDENT_SCHEMA
#define FIXTURE_RESIDENT_SCHEMA 1
#endif
namespace {
#ifdef FIXTURE_SERVICE_INTERCEPTS
uintptr_t serviceNestedSite=0,serviceManagementProbe=0;
#endif
uint32_t __cdecl Invoke(uint32_t command, uint32_t argument, void* memory,
    uint32_t bytes, uint32_t* result) {
    if (bytes != sizeof(uint32_t)) { return 0; }
    auto& counter = *static_cast<uint32_t*>(memory);
#if defined(FIXTURE_CONTEXT) || defined(FIXTURE_HOOKS)
    if (command == 9
#ifdef FIXTURE_HOOKS
        || command == 10 || command == 11
#endif
    ) {
        auto* context = reinterpret_cast<heroes5_sdk::EngineContext*>(argument);
        if (!context || context->residentBytes != 65536 || !context->residentData) { return 0; }
        auto* value = reinterpret_cast<uint32_t*>(context->residentData);
        ++*value;
        if (context->hasRegisters) {
            memcpy(value + 1, &context->registers, sizeof(context->registers));
            value[10] = context->hasRegisters;
        }
        value[11] = command; value[12] = context->callSite;
        ++counter;
        *result = *value;
        return 1;
    }
#endif
#ifdef FIXTURE_INTERCEPTS
    if (command == 13) {
        auto* context=reinterpret_cast<heroes5_sdk::EngineInterceptionContext*>(argument);
        if (!context || !context->engine.residentData) { return 0; }
        ++counter;
#ifdef FIXTURE_SERVICE_INTERCEPTS
        if (serviceManagementProbe) { reinterpret_cast<void (__cdecl*)()>(serviceManagementProbe)(); }
        uint32_t nativeCalls=0;
        const auto nested=reinterpret_cast<uint32_t (__cdecl*)(uint32_t*)>(serviceNestedSite)(&nativeCalls);
        context->action=heroes5_sdk::EngineCallAction::ReturnEax;
        context->result=nested==77 && nativeCalls==0 ? 41+FIXTURE_VERSION : 999;
        *result=counter;return 1;
#else
        context->action=context->engine.registers.eax==99 ? static_cast<heroes5_sdk::EngineCallAction>(99) :
            heroes5_sdk::EngineCallAction::ReturnEax;
        context->result=42;*result=counter;return 1;
#endif
    }
#ifdef FIXTURE_SERVICE_INTERCEPTS
    if (command==14) {
        auto* context=reinterpret_cast<heroes5_sdk::EngineInterceptionContext*>(argument);
        ++counter;context->action=heroes5_sdk::EngineCallAction::ReturnEax;context->result=77;*result=counter;return 1;
    }
#endif
#endif
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
    if (command == 12 && argument) {
        reinterpret_cast<void (__cdecl*)()>(argument)();
        *result = 12;
        return 1;
    }
    return 0;
}
#ifdef FIXTURE_INTERCEPTS
heroes5_sdk::InterceptionPluginApi api=[] {
    heroes5_sdk::InterceptionPluginApi value{};
    value.base.base.base={sizeof(value),heroes5_sdk::InterceptionPluginAbi,FIXTURE_SCHEMA,sizeof(uint32_t),
        Invoke,0,0,0,1,2,9};
    value.base.base.residentSchema=1;value.base.base.residentBytes=65536;
    value.interceptions.count=1;
    auto& entry=value.interceptions.entries[0];entry.site=5;entry.command=13;
    entry.sourceBytes=5;memset(entry.sourceExpected,0x90,5);
    entry.policy.allowReturn=1;entry.readOnlyReentry=1;
#ifdef FIXTURE_BAD_INTERCEPTION
    entry.policy.stackPopBytes=65;
#else
    entry.policy.stackPopBytes=8;
#endif
    return value;
}();
#elif defined(FIXTURE_HOOKS)
const heroes5_sdk::HookPluginApi api{
    {{sizeof(heroes5_sdk::HookPluginApi), heroes5_sdk::HookPluginAbi,
      FIXTURE_SCHEMA, sizeof(uint32_t), Invoke, 0, 0, 0,
#ifdef FIXTURE_BAD_ENGINE_BASE
      0,
#else
      1,
#endif
      2, 9}, 1, 65536},
#ifdef FIXTURE_DUPLICATE_HOOK
    {2, {{{3, 10}, {3, 11}}}}};
#else
    {2, {{{3, 10}, {4, 11}}}}};
#endif
#elif defined(FIXTURE_CONTEXT)
const heroes5_sdk::ContextPluginApi api{
    {sizeof(heroes5_sdk::ContextPluginApi), heroes5_sdk::ContextPluginAbi,
     FIXTURE_SCHEMA, sizeof(uint32_t), Invoke, 0, 0, 0, 1, 2, 9}, FIXTURE_RESIDENT_SCHEMA, 65536};
#else
const heroes5_sdk::PluginApi api{sizeof(heroes5_sdk::PluginApi), FIXTURE_ABI,
    FIXTURE_SCHEMA, sizeof(uint32_t), Invoke,
    FIXTURE_VERSION == 2 ? WM_APP + 0x531 : 0,
    FIXTURE_VERSION == 2 ? 5 : 0, FIXTURE_VERSION == 2 ? 1 : 0,
    FIXTURE_ENGINE_SITE, FIXTURE_ENGINE_SITE ? 0xd10980 : 0, FIXTURE_ENGINE_SITE ? 7 : 0};
#endif
}
#ifdef FIXTURE_SERVICE_INTERCEPTS
extern "C" void __cdecl XkitFixtureConfigure(uint32_t primary,uint32_t nested,uint32_t managementProbe) {
    // This fixture has real interception subscriptions, without a synthetic
    // legacy primary site. It can now exercise complete production refresh.
    api.base.base.base.engineCallSite=0;
    api.base.base.base.engineOriginalTarget=0;
    api.base.base.base.engineCommand=0;
    auto& first=api.interceptions.entries[0];first.site=primary;first.policy.stackPopBytes=0;first.readOnlyReentry=0;
    constexpr unsigned char primaryBytes[]{0x8b,0x44,0x24,0x04,0xff,0x00,0xc3};
    first.sourceBytes=sizeof(primaryBytes);memset(first.sourceExpected,0,16);memcpy(first.sourceExpected,primaryBytes,sizeof(primaryBytes));
    api.interceptions.count=2;auto& second=api.interceptions.entries[1];second=first;
    second.site=nested;second.command=14;second.readOnlyReentry=1;
    constexpr unsigned char nestedBytes[]{0x8b,0x44,0x24,0x04,0xff,0x00,0x40,0xc3};
    second.sourceBytes=sizeof(nestedBytes);memcpy(second.sourceExpected,nestedBytes,sizeof(nestedBytes));
    serviceNestedSite=nested;serviceManagementProbe=managementProbe;
}
#pragma comment(linker,"/EXPORT:XkitFixtureConfigure=_XkitFixtureConfigure")
extern "C" uint32_t __cdecl Heroes5PluginFinalize(const heroes5_sdk::EngineContext* context) {
    if (!context || !context->residentData || context->residentBytes!=65536) { return 0; }
    auto* markers=reinterpret_cast<uint32_t*>(context->residentData);
    if (markers[100]) { return 0; }
    uint32_t nativeCalls=0;
    if (markers[102] &&
        (reinterpret_cast<uint32_t (__cdecl*)(uint32_t*)>(serviceNestedSite)(&nativeCalls)!=77 || nativeCalls)) { return 0; }
    ++markers[101];return 1;
}
#pragma comment(linker,"/EXPORT:Heroes5PluginFinalize=_Heroes5PluginFinalize")
extern "C" uint32_t __cdecl Heroes5PluginPrepareReplace(const heroes5_sdk::EngineContext* context) {
    if (!context || !context->residentData || context->residentBytes!=65536) { return 0; }
    auto* markers=reinterpret_cast<uint32_t*>(context->residentData);
    if (markers[100]) { return 0; }
    ++markers[108];return 1;
}
#pragma comment(linker,"/EXPORT:Heroes5PluginPrepareReplace=_Heroes5PluginPrepareReplace")
extern "C" uint32_t __cdecl Heroes5PluginWindowInput(const heroes5_sdk::WindowInputContext* context) {
    if (!context || context->size!=sizeof(*context) || context->version!=1 ||
        (context->message!=WM_LBUTTONDBLCLK && context->message!=WM_RBUTTONDBLCLK) ||
        !context->engine.residentData) { return 0; }
    if (context->message==WM_RBUTTONDBLCLK) {
        ++reinterpret_cast<uint32_t*>(context->engine.residentData)[110];return 1;
    }
    ++reinterpret_cast<uint32_t*>(context->engine.residentData)[109];
    // The owner pins runtime during input. A reentrant input message must
    // be forwarded without recursively dispatching this payload callback.
    SendMessageW(context->engine.window,WM_LBUTTONDBLCLK,0,0);
    uint32_t nativeCalls=0;
    if (reinterpret_cast<uint32_t (__cdecl*)(uint32_t*)>(serviceNestedSite)(&nativeCalls)!=77 || nativeCalls) { return 0; }
    auto* markers=reinterpret_cast<uint32_t*>(context->engine.residentData);
    ++markers[104];markers[105]=FIXTURE_VERSION;markers[106]=context->wparam;markers[107]=context->lparam;
    return 1;
}
#pragma comment(linker,"/EXPORT:Heroes5PluginWindowInput=_Heroes5PluginWindowInput")
#endif
extern "C" const heroes5_sdk::PluginApi* __cdecl Heroes5PluginQuery() {
#ifdef FIXTURE_INTERCEPTS
    return &api.base.base.base;
#elif defined(FIXTURE_HOOKS)
    return &api.base.base;
#elif defined(FIXTURE_CONTEXT)
    return &api.base;
#else
    return &api;
#endif
}
#pragma comment(linker, "/EXPORT:Heroes5PluginQuery=_Heroes5PluginQuery")
