#ifndef XKIT_ENGINE_HOOK_PROTOCOL_HPP
#define XKIT_ENGINE_HOOK_PROTOCOL_HPP
#include <windows.h>
#include "../game-api/include/h5/hooks.hpp"
namespace heroes5_sdk {
using EngineObserver = void (__cdecl*)(const h5::hooks::X86ObserverFrame*);
// Interceptors return only EAX or choose one admitted native continuation.
// Stack cleanup is fixed by the owner policy, never supplied by a callback.
enum class EngineCallAction : uint32_t { Continue, ReturnEax, Branch };
struct EngineCall {
    h5::hooks::X86ObserverFrame frame;
    EngineCallAction action = EngineCallAction::Continue;
    uint32_t result = 0;
};
using EngineInterceptor = uint32_t (__cdecl*)(EngineCall*);
struct EngineCallPolicy {
    uint32_t allowReturn = 0;
    uint32_t stackPopBytes = 0;
    uintptr_t branch = 0;
    bool operator==(const EngineCallPolicy&) const = default;
};

enum class EngineHookAction : uint32_t { Attach, Detach };
// Trusted synchronous game-thread transport to the process-lifetime loader.
// Callback code must stay loaded until callbackRetired is confirmed.
struct EngineHookRequest {
    uint32_t size = sizeof(EngineHookRequest);
    uint32_t version = 1;
    EngineHookAction action = EngineHookAction::Attach;
    HWND window = nullptr;
    uintptr_t site = 0;
    EngineObserver callback = nullptr;
    LONG* slot = nullptr;
    uint32_t callbackRetired = 0;
};
// V2 transports a copied signature; no pointer into a replaceable DLL survives.
struct EngineHookRequestV2 {
    EngineHookRequest base;
    uint32_t expectedBytes = 0;
    unsigned char expected[16]{};
    EngineHookRequestV2() {
        base.size = sizeof(EngineHookRequestV2);
        base.version = 2;
    }
};
struct EngineHookRequestV3 {
    EngineHookRequestV2 signature;
    EngineCallPolicy policy;
    EngineInterceptor interceptor = nullptr;
    uint32_t branchBytes = 0;
    unsigned char branchExpected[16]{};
    EngineHookRequestV3() {
        signature.base.size = sizeof(EngineHookRequestV3);
        signature.base.version = 3;
    }
};

}
#endif
