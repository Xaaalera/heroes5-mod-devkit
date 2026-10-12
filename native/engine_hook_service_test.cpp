// Compile the production service into this isolated test host. No test export
// or startup override is added to the shipped loader.
#include "../game-api/include/h5/adventure_input.hpp"
#include "plugin_runtime.hpp"
#include "diagnostic_bus.hpp"
#include "console_module.hpp"
#include "selector_runtime.hpp"
#include "engine_hook_protocol.hpp"
#include "../game-api/include/h5/script_observers.hpp"
#include "../game-api/include/h5/camera_input.hpp"
#include "../game-api/include/h5/build.hpp"
#include "../game-api/include/h5/console.hpp"
#include "../game-api/include/h5/resident_buffers.hpp"
#include <atomic>
#include <cwchar>
#include <new>
#include <vector>

#include "mod_loader.cpp"
bool failQueuedHook=false,failHookRollback=false;
unsigned fixtureHookAttempts=0;
HHOOK WINAPI FixtureSetWindowHook(int kind,HOOKPROC callback,HINSTANCE module,DWORD thread) {
    ++fixtureHookAttempts;
    if (kind==WH_GETMESSAGE && failQueuedHook) { SetLastError(ERROR_ACCESS_DENIED);return nullptr; }
    return SetWindowsHookExW(kind,callback,module,thread);
}
BOOL WINAPI FixtureUnhookWindow(HHOOK hook) {
    if (failHookRollback) { SetLastError(ERROR_ACCESS_DENIED);return FALSE; }
    return UnhookWindowsHookEx(hook);
}
LRESULT WINAPI FixtureSendMessage(HWND window,UINT message,WPARAM parameter,LPARAM data);
// Reuse the production hot bridge in an isolated namespace. All its headers
// were included above so this does not create nested standard/API namespaces.
#define DllMain XkitBridgeFixtureDllMain
#define SetWindowsHookExW FixtureSetWindowHook
#define UnhookWindowsHookEx FixtureUnhookWindow
#define SendMessageW FixtureSendMessage
namespace bridge_fixture {
#include "plugin_bridge.cpp"
}
#undef DllMain
#undef SetWindowsHookExW
#undef UnhookWindowsHookEx
#undef SendMessageW

bool failPayloadRefreshOnce=false,rollbackSnapshotValid=false;
uint32_t rollbackInputDelta=0;
LRESULT WINAPI FixtureSendMessage(HWND window,UINT message,WPARAM parameter,LPARAM data) {
    if (failPayloadRefreshOnce && message==bridge_fixture::dispatchMessage && data &&
        reinterpret_cast<const uint32_t*>(data)[0]==UINT32_MAX-1) {
        failPayloadRefreshOnce=false;
        uint32_t before=0;
        {
            h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
            rollbackSnapshotValid=lease.Data()!=nullptr;
            if (lease.Data()) { before=reinterpret_cast<const uint32_t*>(lease.Data())[109]; }
        }
        // Deliver real owner-thread input in the refresh-failure/rollback gap.
        // The payload marks entry before invoking any dependency of its handler.
        SendMessageW(window,WM_LBUTTONDBLCLK,0,0);
        {
            h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
            rollbackSnapshotValid=rollbackSnapshotValid && lease.Data();
            if (lease.Data()) { rollbackInputDelta=reinterpret_cast<const uint32_t*>(lease.Data())[109]-before; }
        }
        bridge_fixture::pendingStatus.store(104,std::memory_order_release);
        return 0;
    }
    return SendMessageW(window,message,parameter,data);
}

#include <iostream>
#include <thread>

namespace {
uint32_t observations = 0, replacementObservations = 0;
void RequireService(bool value, const char* reason) {
    if (!value) { throw std::runtime_error(reason); }
}
uint32_t interceptions = 0;
uint32_t __cdecl InterceptService(heroes5_sdk::EngineCall* call) {
    ++interceptions;call->action=heroes5_sdk::EngineCallAction::ReturnEax;call->result=42;return 1;
}
void __cdecl ObserveService(const h5::hooks::X86ObserverFrame*) { ++observations; }
void __cdecl ObserveReplacement(const h5::hooks::X86ObserverFrame*) { ++replacementObservations; }
__declspec(naked) void ServiceTarget(uint32_t*) {
    __asm { mov eax, dword ptr [esp + 4] }
    __asm { inc dword ptr [eax] }
    __asm { ret }
}
}
__declspec(naked) void ServiceNestedTarget(uint32_t*) {
    __asm { mov eax,dword ptr [esp+4] }
    __asm { inc dword ptr [eax] }
    __asm { inc eax }
    __asm { ret }
}
#pragma code_seg(push, ".xkitbr")
__declspec(naked) void ServiceBranch() {
    __asm { mov eax, 0x12345678 }
    __asm { ret }
}
#pragma code_seg(pop)
uint32_t managementRefusals=0;
void __cdecl ProbeManagementReentry() {
    uint32_t request[4]{UINT32_MAX-1,0,0,0};
    bridge_fixture::dispatchMessage=WM_APP+0x714;
    const auto done=CreateEventW(nullptr,TRUE,FALSE,nullptr);
    RequireService(done!=nullptr,"Management worker event creation failed");
    DWORD status=0;
    std::thread waiting([&] {
        bridge_fixture::Guard guard;
        status=bridge_fixture::SendToMain(request);
        SetEvent(done);
    });
    const auto deadline=GetTickCount64()+2000;
    while (bridge_fixture::pending.load(std::memory_order_acquire)!=request && GetTickCount64()<deadline) { Sleep(1); }
    while (WaitForSingleObject(done,0)!=WAIT_OBJECT_0 && GetTickCount64()<deadline) {
        MSG message{};
        while (PeekMessageW(&message,nullptr,0,0,PM_REMOVE)) { TranslateMessage(&message);DispatchMessageW(&message); }
        Sleep(1);
    }
    RequireService(WaitForSingleObject(done,0)==WAIT_OBJECT_0,"Management worker did not receive busy acknowledgement");
    waiting.join();CloseHandle(done);
    RequireService(status==ERROR_BUSY && !bridge_fixture::pending.load(std::memory_order_acquire) &&
        bridge_fixture::nativeInterceptionSettings.count==2 && bridge_fixture::nativeInterceptionSlots[0] &&
        bridge_fixture::nativeInterceptionSlots[1] && bridge_fixture::runtime->DispatchingOnCurrentThread(),
        "Management reentry changed subscriptions or outer dispatch state");
    ++managementRefusals;
}
int wmain(int count,wchar_t** arguments) {
    try {
        RequireService(count==3,"Expected two interception payload generations");
        WNDCLASSW windowClass{};
        windowClass.lpfnWndProc = DefWindowProcW;
        windowClass.hInstance = GetModuleHandleW(nullptr);
        windowClass.lpszClassName = L"H5";
        RequireService(RegisterClassW(&windowClass) != 0, "Fixture class failed");
        const auto window = CreateWindowExW(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, L"H5",
            L"SDK isolated service fixture", WS_POPUP | WS_VISIBLE, 0, 0, 1, 1,
            nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
        RequireService(window != nullptr, "Fixture window failed");
        failQueuedHook=true;failHookRollback=true;
        RequireService(!bridge_fixture::AttachMainWindowHook() && bridge_fixture::mainHook &&
            !bridge_fixture::windowInputHook && bridge_fixture::mainWindow==window,
            "Unconfirmed paired-hook rollback lost callback ownership");
        const auto failedAttempts=fixtureHookAttempts;
        RequireService(!bridge_fixture::AttachMainWindowHook() && fixtureHookAttempts==failedAttempts,
            "Unconfirmed paired-hook rollback retried attachment");
        bridge_fixture::hookThread=GetCurrentThread();
        bridge_fixture::hookReady=CreateEventW(nullptr,TRUE,TRUE,nullptr);
        RequireService(bridge_fixture::EnsureMainThreadHook(false)==101,"Incomplete hook pair reported ready");
        CloseHandle(bridge_fixture::hookReady);bridge_fixture::hookReady=nullptr;bridge_fixture::hookThread=nullptr;
        failQueuedHook=false;failHookRollback=false;
        RequireService(UnhookWindowsHookEx(bridge_fixture::mainHook)!=0,"Retained rollback hook cleanup failed");
        bridge_fixture::mainHook=nullptr;bridge_fixture::mainWindow=nullptr;bridge_fixture::windowThread=0;
        // Simulate verified startup only in this executable's private service.
        InterlockedExchange(&verifiedGameStartupThread, GetCurrentThreadId());
        heroes5_sdk::SelectorRequest selectorBinding;selectorBinding.action=heroes5_sdk::SelectorAction::BindWindow;
        selectorBinding.window=window;
        RequireService(Heroes5BankSelectorControl(&selectorBinding)==ERROR_SUCCESS,"Cold selector owner binding failed");
        uint32_t selectorData=123;
        constexpr unsigned char selectorCode[]{0x8b,0x44,0x24,0x04,0xff,0x00,0xc3};
        heroes5_sdk::BankSelectorPayload selectorPayload;
        selectorPayload.initialData=reinterpret_cast<const unsigned char*>(&selectorData);selectorPayload.dataBytes=sizeof(selectorData);
        selectorPayload.sourceData=reinterpret_cast<uintptr_t>(&selectorData);
        selectorPayload.code=selectorCode;selectorPayload.codeBytes=sizeof(selectorCode);
        selectorPayload.sourceCode=reinterpret_cast<uintptr_t>(selectorCode);selectorPayload.dataSchema[0]=17;
        const uint32_t invalidOffset=4;selectorPayload.dataOffsets=&invalidOffset;selectorPayload.dataOffsetCount=1;
        RequireService(heroes5_sdk::ApplySelectorPayload(Heroes5BankSelectorControl,selectorPayload)==ERROR_INVALID_DATA,
            "Invalid first selector candidate initialized data");
        heroes5_sdk::SelectorRequest selectorStatus;
        RequireService(Heroes5BankSelectorControl(&selectorStatus)==ERROR_NOT_READY,"Failed cold data initialization became ready");
        selectorPayload.dataOffsets=nullptr;selectorPayload.dataOffsetCount=0;
        DWORD coldStatus=ERROR_NOT_READY;
        std::thread coldWorker([&] { coldStatus=heroes5_sdk::ApplySelectorPayload(Heroes5BankSelectorControl,selectorPayload); });
        coldWorker.join();RequireService(coldStatus==ERROR_SUCCESS,"Cold selector data/program admission failed");
        uint32_t selectorCalls=0;
        selectorStatus={};RequireService(Heroes5BankSelectorControl(&selectorStatus)==ERROR_SUCCESS,"Cold selector status failed");
        selectorStatus.expectedGeneration=selectorStatus.generation;selectorStatus.action=heroes5_sdk::SelectorAction::Invoke;
        selectorStatus.publicModel=reinterpret_cast<uintptr_t>(&selectorCalls);
        RequireService(Heroes5BankSelectorControl(&selectorStatus)==ERROR_SUCCESS && selectorCalls==1,
            "Worker-initialized selector did not execute on game owner thread");
        selectorPayload.dataSchema[0]=18;
        RequireService(heroes5_sdk::ApplySelectorPayload(Heroes5BankSelectorControl,selectorPayload)==ERROR_INVALID_DATA,
            "Incompatible selector replaced retained data");
        RequireService(Heroes5BankSelectorControl(&selectorStatus)==ERROR_SUCCESS && selectorCalls==2,
            "Rejected selector changed previous code");
        constexpr unsigned char expected[]{0x8b, 0x44, 0x24, 0x04, 0xff, 0x00, 0xc3};
        const auto site = reinterpret_cast<uintptr_t>(&ServiceTarget);
        RequireService(h5::hooks::EngineSiteSignature(site).empty(), "Fixture unexpectedly catalogued");
        heroes5_sdk::EngineHookRequestV2 request;
        request.base.window = window; request.base.site = site; request.base.callback = ObserveService;
        request.expectedBytes = sizeof(expected); memcpy(request.expected, expected, sizeof(expected));
        request.expected[0] = 0x90;
        RequireService(Heroes5ModsEngineHookV2(&request) == ERROR_INVALID_DATA &&
            request.base.callbackRetired && !request.base.slot, "Bad signature did not roll back");
        request.expected[0] = expected[0]; request.expected[15] = 1;
        RequireService(Heroes5ModsEngineHookV2(&request) == ERROR_INVALID_PARAMETER &&
            request.base.callbackRetired && !request.base.slot, "Nonzero signature tail accepted");
        request.expected[15] = 0;
        DWORD foreignStatus = 0;
        std::thread foreign([&] { foreignStatus = Heroes5ModsEngineHookV2(&request); });
        foreign.join();
        RequireService(foreignStatus == ERROR_INVALID_PARAMETER && !request.base.slot,
            "Foreign thread attached a hook");
        RequireService(Heroes5ModsEngineHookV2(&request) == ERROR_SUCCESS && request.base.slot &&
            !request.base.callbackRetired, "Valid dynamic attachment failed");
        uint32_t calls = 0;
        ServiceTarget(&calls);
        RequireService(calls == 1 && observations == 1, "Callback or original function failed");
        std::thread worker([&] { ServiceTarget(&calls); }); worker.join();
        RequireService(calls == 2 && observations == 1, "Worker did not forward original only");
        request.base.action = heroes5_sdk::EngineHookAction::Detach;
        RequireService(Heroes5ModsEngineHookV2(&request) == ERROR_SUCCESS &&
            request.base.callbackRetired && !request.base.slot, "Retirement failed");
        ServiceTarget(&calls);
        RequireService(calls == 3 && observations == 1, "Retired callback still active");
        request.base.action = heroes5_sdk::EngineHookAction::Attach;
        request.base.callback = ObserveReplacement;
        RequireService(Heroes5ModsEngineHookV2(&request) == ERROR_SUCCESS, "Replacement attachment failed");
        ServiceTarget(&calls);
        RequireService(calls == 4 && observations == 1 && replacementObservations == 1,
            "Replacement callback failed");
        heroes5_sdk::EngineHookRequestV3 interception;
        interception.signature.base.window=window;interception.signature.base.site=site;
        interception.signature.expectedBytes=sizeof(expected);
        memcpy(interception.signature.expected,expected,sizeof(expected));
        interception.interceptor=InterceptService;interception.policy.allowReturn=1;
        RequireService(Heroes5ModsEngineHookV3(&interception)==ERROR_SHARING_VIOLATION &&
            interception.signature.base.callbackRetired && !interception.signature.base.slot,
            "Interceptor conflicted with live observers");
        request.base.action = heroes5_sdk::EngineHookAction::Detach;
        RequireService(Heroes5ModsEngineHookV2(&request) == ERROR_SUCCESS && request.base.callbackRetired,
            "Replacement retirement failed");
        interception.policy.branch=1;interception.branchBytes=5;
        RequireService(Heroes5ModsEngineHookV3(&interception)==ERROR_INVALID_DATA &&
            !interception.signature.base.slot,"Unstable/non-image branch admitted");
        interception.policy.branch=0;interception.branchBytes=0;
        interception.policy.stackPopBytes=65;
        RequireService(Heroes5ModsEngineHookV3(&interception)==ERROR_INVALID_PARAMETER,
            "Payload-chosen invalid cleanup admitted");
        interception.policy.stackPopBytes=0;
        interception.policy.branch=reinterpret_cast<uintptr_t>(&ServiceBranch);
        interception.branchBytes=5;
        memcpy(interception.branchExpected,reinterpret_cast<const void*>(interception.policy.branch),5);
        for (const DWORD denied : {PAGE_NOACCESS,PAGE_READWRITE,PAGE_EXECUTE_READ|PAGE_GUARD}) {
            DWORD previous=0,ignored=0;
            RequireService(VirtualProtect(reinterpret_cast<void*>(interception.policy.branch),5,denied,&previous),
                "Branch protection fixture setup failed");
            RequireService(Heroes5ModsEngineHookV3(&interception)==ERROR_INVALID_DATA &&
                !interception.signature.base.slot,"Unreadable/non-executable branch admitted or read");
            RequireService(VirtualProtect(reinterpret_cast<void*>(interception.policy.branch),5,previous,&ignored),
                "Branch protection fixture restoration failed");
        }
        foreignStatus=0;
        std::thread foreignInterceptor([&] { foreignStatus=Heroes5ModsEngineHookV3(&interception); });
        foreignInterceptor.join();
        RequireService(foreignStatus==ERROR_INVALID_PARAMETER && !interception.signature.base.slot,
            "Foreign thread installed interceptor");
        RequireService(Heroes5ModsEngineHookV3(&interception)==ERROR_SUCCESS &&
            interception.signature.base.slot && !interception.signature.base.callbackRetired,
            "Production V3 attachment failed");
        const auto interceptedTarget=reinterpret_cast<uint32_t (__cdecl*)(uint32_t*)>(site);
        RequireService(interceptedTarget(&calls)==42 && calls==4 && interceptions==1,
            "Production V3 did not replace native result");
        request.base.action=heroes5_sdk::EngineHookAction::Attach;
        RequireService(Heroes5ModsEngineHookV2(&request)==ERROR_SHARING_VIOLATION &&
            request.base.callbackRetired && !request.base.slot,"Observer entered exclusive interceptor site");
        // Retirement depends on admitted slot/callback ownership, not a reread
        // of branch bytes which may have changed since attachment.
        interception.policy.branch=1;interception.branchBytes=5;
        interception.signature.base.action=heroes5_sdk::EngineHookAction::Detach;
        RequireService(Heroes5ModsEngineHookV3(&interception)==ERROR_SUCCESS &&
            interception.signature.base.callbackRetired && !interception.signature.base.slot,
            "Production V3 retirement failed");
        ServiceTarget(&calls);RequireService(calls==5 && interceptions==1,"Production retired interceptor still active");

        bridge_fixture::mainWindow=window;bridge_fixture::windowThread=GetCurrentThreadId();
        bridge_fixture::engineHookControlV3=Heroes5ModsEngineHookV3;
        bridge_fixture::mainHook=SetWindowsHookExW(WH_CALLWNDPROC,bridge_fixture::MainThreadHook,nullptr,GetCurrentThreadId());
        RequireService(bridge_fixture::mainHook!=nullptr,"Production management hook setup failed");
        bridge_fixture::windowInputHook=SetWindowsHookExW(WH_GETMESSAGE,bridge_fixture::WindowInputHook,nullptr,GetCurrentThreadId());
        RequireService(bridge_fixture::windowInputHook!=nullptr,"Production queued input hook setup failed");
        bridge_fixture::runtime=new heroes5_sdk::PluginRuntime;
        RequireService(bridge_fixture::FinalizePayloadOnMain(),"Empty infrastructure core could not retire without a payload");
        using Configure=void (__cdecl*)(uint32_t,uint32_t,uint32_t);
        for (unsigned generation=0;generation<2;++generation) {
            const auto module=LoadLibraryW(arguments[generation+1]);
            RequireService(module!=nullptr,"Interception payload fixture load failed");
            const auto configure=reinterpret_cast<Configure>(GetProcAddress(module,"XkitFixtureConfigure"));
            RequireService(configure!=nullptr,"Interception fixture configuration missing");
            configure(static_cast<uint32_t>(site),reinterpret_cast<uint32_t>(&ServiceNestedTarget),
                reinterpret_cast<uint32_t>(&ProbeManagementReentry));
            RequireService(bridge_fixture::runtime->Replace(arguments[generation+1])==heroes5_sdk::RuntimeResult::Ok,
                "Bridge payload replacement failed");
            RequireService(FreeLibrary(module)!=0,"Fixture owner reference release failed");
            RequireService(bridge_fixture::RefreshNativeEngineHooks(),"Production bridge interception refresh failed");
            const auto nativeBefore=calls;
            RequireService(interceptedTarget(&calls)==42+generation && calls==nativeBefore,
                "Bridge/payload/nested service interception failed");
            uint32_t stateCounter=0;
            RequireService(bridge_fixture::runtime->Invoke(1,0,stateCounter)==heroes5_sdk::RuntimeResult::Ok &&
                stateCounter==generation+1,"Nested call committed state or HMR lost state");
            RequireService(!bridge_fixture::runtime->CurrentEngineContext() && !bridge_fixture::nativeDispatchActive,
                "Borrowed context/dispatch scope escaped callback");
            SendMessageW(window,WM_LBUTTONDBLCLK,MK_LBUTTON,MAKELPARAM(12,34));
            {
                h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,true);
                auto* markers=reinterpret_cast<uint32_t*>(lease.WritableData());
                RequireService(markers && markers[104]==generation*3+1 && markers[105]==generation+1 &&
                    markers[106]==MK_LBUTTON && markers[107]==MAKELPARAM(12,34),
                    "Owner window input/HMR state or borrowed callback failed");
                heroes5_sdk::WindowInputContext invalid;invalid.engine.window=window;
                invalid.engine.residentData=lease.WritableData();invalid.engine.residentSchema=1;
                invalid.engine.residentBytes=lease.Bytes();invalid.message=WM_LBUTTONDBLCLK;
                invalid.version=2;
                RequireService(bridge_fixture::runtime->InvokeWindowInput(invalid)==heroes5_sdk::RuntimeResult::CallRejected,
                    "Unknown input envelope admitted");invalid.version=1;
                heroes5_sdk::RuntimeResult wrongThread=heroes5_sdk::RuntimeResult::Ok;
                std::thread foreignInput([&] { wrongThread=bridge_fixture::runtime->InvokeWindowInput(invalid); });
                foreignInput.join();
                RequireService(wrongThread==heroes5_sdk::RuntimeResult::CallRejected && markers[104]==generation*3+1,
                    "Foreign thread entered window input callback");
            }
            const auto transportHeld=CreateEventW(nullptr,TRUE,FALSE,nullptr);
            const auto transportRelease=CreateEventW(nullptr,TRUE,FALSE,nullptr);
            RequireService(transportHeld && transportRelease,"Transport contention fixture setup failed");
            std::thread transport([&] {
                bridge_fixture::Guard guard;SetEvent(transportHeld);
                WaitForSingleObject(transportRelease,3000);
            });
            RequireService(WaitForSingleObject(transportHeld,2000)==WAIT_OBJECT_0,"Transport fixture did not hold lock");
            SendMessageW(window,WM_LBUTTONDBLCLK,0,0);
            SetEvent(transportRelease);transport.join();CloseHandle(transportHeld);CloseHandle(transportRelease);
            SendMessageW(window,WM_MOUSEMOVE,0,0);
            {
                h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
                const auto* markers=reinterpret_cast<const uint32_t*>(lease.Data());
                RequireService(markers && markers[104]==generation*3+2,"Input lost during held worker transport");
            }
            const auto transitionHeld=CreateEventW(nullptr,TRUE,FALSE,nullptr);
            const auto transitionRelease=CreateEventW(nullptr,TRUE,FALSE,nullptr);
            RequireService(transitionHeld && transitionRelease,"Transition fixture events failed");
            std::thread transition([&] {
                bridge_fixture::Guard guard;
                bridge_fixture::PayloadTransitionGuard retirement;
                SetEvent(transitionHeld);WaitForSingleObject(transitionRelease,3000);
            });
            const bool transitionStarted=WaitForSingleObject(transitionHeld,2000)==WAIT_OBJECT_0;
            SendMessageW(window,WM_LBUTTONDBLCLK,0,0);
            bool admittedDuringRetirement=false;
            {
                h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
                admittedDuringRetirement=reinterpret_cast<const uint32_t*>(lease.Data())[104]!=generation*3+2;
            }
            SetEvent(transitionRelease);transition.join();CloseHandle(transitionHeld);CloseHandle(transitionRelease);
            RequireService(transitionStarted && !admittedDuringRetirement,"Input entered a retiring payload");
            RequireService(!bridge_fixture::payloadTransition.load(),"Payload transition did not release admission");
            const auto beforePausedNative=calls;
            { bridge_fixture::PayloadTransitionGuard retirement;interceptedTarget(&calls); }
            RequireService(calls==beforePausedNative+1,"Retiring interceptor did not preserve original game call");
            RequireService(PostMessageW(window,WM_LBUTTONDBLCLK,MK_LBUTTON,MAKELPARAM(45,67)),"Queued input setup failed");
            MSG queued{};
            RequireService(PeekMessageW(&queued,window,WM_LBUTTONDBLCLK,WM_LBUTTONDBLCLK,PM_NOREMOVE),"Queued input missing");
            {
                h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
                RequireService(reinterpret_cast<const uint32_t*>(lease.Data())[104]==generation*3+2,
                    "Peek without removal dispatched input");
            }
            RequireService(PeekMessageW(&queued,window,WM_LBUTTONDBLCLK,WM_LBUTTONDBLCLK,PM_REMOVE),"Input removal failed");
            DispatchMessageW(&queued);
            {
                h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
                RequireService(reinterpret_cast<const uint32_t*>(lease.Data())[104]==(generation+1)*3,
                    "Queued input lost or dispatched twice");
            }
            RequireService(PostMessageW(window,WM_RBUTTONDBLCLK,MK_RBUTTON,MAKELPARAM(45,67)),
                "Queued right double-click setup failed");
            RequireService(PeekMessageW(&queued,window,WM_RBUTTONDBLCLK,WM_RBUTTONDBLCLK,PM_REMOVE),
                "Queued right double-click missing");
            DispatchMessageW(&queued);
            {
                h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
                RequireService(reinterpret_cast<const uint32_t*>(lease.Data())[110]==generation+1,
                    "Right double-click lost, dispatched twice or lost across HMR");
            }
            RequireService(!bridge_fixture::runtime->CurrentEngineContext() && !bridge_fixture::runtime->DispatchingOnCurrentThread(),
                "Window input leaked borrowed context/dispatch marker");
            if (generation) { RequireService(!GetModuleHandleW(arguments[1]),"Retired payload generation still loaded"); }
        }
        RequireService(managementRefusals==2,"Management reentry probe missed a payload generation");
        const auto previousPayloadPin=LoadLibraryW(arguments[2]);
        const auto candidatePin=LoadLibraryW(arguments[1]);
        RequireService(previousPayloadPin && candidatePin,"Rollback payload fixture pins failed");
        const auto configureCandidate=reinterpret_cast<Configure>(GetProcAddress(candidatePin,"XkitFixtureConfigure"));
        RequireService(configureCandidate!=nullptr,"Rollback fixture configuration missing");
        configureCandidate(static_cast<uint32_t>(site),reinterpret_cast<uint32_t>(&ServiceNestedTarget),
            reinterpret_cast<uint32_t>(&ProbeManagementReentry));
        bridge_fixture::activePath=arguments[2];failPayloadRefreshOnce=true;
        const auto rollbackStatus=bridge_fixture::Heroes5PluginReplace(const_cast<wchar_t*>(arguments[1]));
        if (rollbackStatus!=104) { throw std::runtime_error("Rollback status " + std::to_string(rollbackStatus)); }
        RequireService(!failPayloadRefreshOnce,"Candidate refresh failure seam was not reached");
        RequireService(rollbackSnapshotValid,"Rollback input snapshot missing");
        RequireService(!rollbackInputDelta,"Failed candidate refresh admitted input");
        RequireService(!bridge_fixture::payloadTransition.load(),"Rollback left payload admission paused");
        {
            h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
            RequireService(lease.Data() && reinterpret_cast<const uint32_t*>(lease.Data())[108]==2,
                "Rollback unloaded candidate without owner-thread retirement");
        }
        RequireService(FreeLibrary(candidatePin) && FreeLibrary(previousPayloadPin),"Rollback fixture pins did not release");
        RequireService(!GetModuleHandleW(arguments[1]) && GetModuleHandleW(arguments[2]),
            "Rollback did not unload candidate and restore prior payload");
        RequireService(bridge_fixture::FinalizePayloadOnMain(true),"Source replacement preparation failed");
        {
            h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
            const auto* markers=reinterpret_cast<const uint32_t*>(lease.Data());
            RequireService(markers && markers[108]==3 && markers[101]==0,
                "Source HMR used full-stop cleanup instead of replacement preparation");
        }
        const auto previousSlot=bridge_fixture::uiSlot;
        const auto firstInterception=bridge_fixture::nativeInterceptionSlots[0];
        uint32_t requestedSlot=previousSlot+1;
        RequireService(bridge_fixture::Heroes5PluginSetSlot(&requestedSlot)==107 &&
            bridge_fixture::uiSlot==previousSlot && bridge_fixture::nativeInterceptionSlots[0]==firstInterception,
            "Active interceptor moved its resident buffer identity");
        heroes5_sdk::PluginSnapshot liveSnapshot;
        RequireService(bridge_fixture::runtime->ExportState(liveSnapshot)==heroes5_sdk::RuntimeResult::Ok,
            "Active state snapshot fixture failed");
        const auto actualHook=bridge_fixture::mainHook;bridge_fixture::mainHook=nullptr;
        const auto restoreStatus=bridge_fixture::Heroes5PluginRestoreV2(&liveSnapshot);
        bridge_fixture::mainHook=actualHook;
        RequireService(restoreStatus==108 && bridge_fixture::runtime->Generation()==liveSnapshot.generation &&
            bridge_fixture::nativeInterceptionSlots[0]==firstInterception,
            "State restored while interceptor-only callbacks were active");
        const auto activeRuntime=bridge_fixture::runtime;
        RequireService(bridge_fixture::StopRuntime(nullptr)==104 && bridge_fixture::runtime==activeRuntime &&
            bridge_fixture::nativeInterceptionSlots[0] && bridge_fixture::nativeInterceptionSlots[1],
            "Interceptor-only runtime stopped without an owner retirement boundary");
        uint32_t retirement[4]{UINT32_MAX,0,0,0};
        bridge_fixture::pending.store(retirement,std::memory_order_release);
        { bridge_fixture::PayloadTransitionGuard transition;
          SendMessageW(window,bridge_fixture::dispatchMessage,0,reinterpret_cast<LPARAM>(retirement)); }
        bridge_fixture::pending.store(nullptr,std::memory_order_release);
        RequireService(bridge_fixture::pendingStatus.load()==0 && !bridge_fixture::nativeInterceptionSlots[0],
            "HMR handoff did not retire callbacks");
        {
            h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,true);
            auto* markers=reinterpret_cast<uint32_t*>(lease.WritableData());
            RequireService(markers && markers[101]==0,"HMR handoff finalized retained payload resources");
            markers[100]=1;
        }
        RequireService(bridge_fixture::RefreshNativeEngineHooks(),"Finalizer fixture reattachment failed");
        retirement[1]=1;
        bridge_fixture::pending.store(retirement,std::memory_order_release);
        SendMessageW(window,bridge_fixture::dispatchMessage,0,reinterpret_cast<LPARAM>(retirement));
        bridge_fixture::pending.store(nullptr,std::memory_order_release);
        RequireService(bridge_fixture::pendingStatus.load()==104 && bridge_fixture::nativeInterceptionSlots[0],
            "Refused finalizer retired live callbacks");
        {
            h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,true);
            auto* markers=reinterpret_cast<uint32_t*>(lease.WritableData());markers[100]=0;markers[102]=1;
            RequireService(markers[101]==0,"Refused finalizer released resources");
        }
        bridge_fixture::pending.store(retirement,std::memory_order_release);
        { bridge_fixture::PayloadTransitionGuard transition;
          SendMessageW(window,bridge_fixture::dispatchMessage,0,reinterpret_cast<LPARAM>(retirement)); }
        bridge_fixture::pending.store(nullptr,std::memory_order_release);
        RequireService(bridge_fixture::pendingStatus.load()==0 && !bridge_fixture::nativeInterceptionSlots[0] &&
            !bridge_fixture::runtime->CurrentEngineContext() && !bridge_fixture::runtime->DispatchingOnCurrentThread(),
            "Final stop failed to finalize and retire callbacks");
        {
            h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
            const auto* markers=reinterpret_cast<const uint32_t*>(lease.Data());
            RequireService(markers && markers[101]==1,"Final stop did not release payload resources exactly once");
        }
        RequireService(bridge_fixture::StopRuntime(nullptr)==104 && bridge_fixture::runtime==activeRuntime,
            "Payload with preserved resident ownership unloaded without owner-thread finalization");
        {
            h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,true);
            reinterpret_cast<uint32_t*>(lease.WritableData())[102]=0;
        }
        bridge_fixture::hookStop=CreateEventW(nullptr,TRUE,FALSE,nullptr);
        bridge_fixture::hookReady=CreateEventW(nullptr,TRUE,TRUE,nullptr);
        bridge_fixture::hookThread=CreateThread(nullptr,0,[](void* stop)->DWORD {
            return WaitForSingleObject(static_cast<HANDLE>(stop),2000)==WAIT_OBJECT_0 ? 0u : 1u;
        },bridge_fixture::hookStop,0,nullptr);
        RequireService(bridge_fixture::hookThread!=nullptr,"Final stop owner fixture failed");
        DWORD stopStatus=999;std::atomic<bool> stopped{false};
        std::thread stopWorker([&] { stopStatus=bridge_fixture::StopRuntime(nullptr);stopped.store(true); });
        const auto stopDeadline=GetTickCount64()+3000;
        while (!stopped.load() && GetTickCount64()<stopDeadline) {
            MSG message{};
            while (PeekMessageW(&message,nullptr,0,0,PM_REMOVE)) { TranslateMessage(&message);DispatchMessageW(&message); }
            Sleep(1);
        }
        stopWorker.join();
        RequireService(stopStatus==0 && !bridge_fixture::runtime,"Final stop without subscriptions skipped resident finalizer");
        {
            h5::resident::BufferLease lease(window,bridge_fixture::uiSlot+1,1,false);
            const auto* markers=reinterpret_cast<const uint32_t*>(lease.Data());
            RequireService(markers && markers[101]==2,"Owner-thread final stop skipped preserved resident resources");
        }
        RequireService(UnhookWindowsHookEx(bridge_fixture::mainHook)!=0,"Management hook retirement failed");
        RequireService(UnhookWindowsHookEx(bridge_fixture::windowInputHook)!=0,"Queued input hook retirement failed");
        bridge_fixture::windowInputHook=nullptr;
        bridge_fixture::mainHook=nullptr;
        const auto nativeBefore=calls;ServiceTarget(&calls);
        RequireService(calls==nativeBefore+1,"Bridge retirement did not restore native forwarding");
        RequireService(DestroyWindow(window) != 0, "Fixture cleanup failed");
        std::cout << "PASS: production V2 dynamic signature, rollback, thread guard, replacement and retirement\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
