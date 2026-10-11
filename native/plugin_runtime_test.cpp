#include "plugin_runtime.hpp"
#include <iostream>
#include <stdexcept>
#include <thread>

void Require(bool value, const char* reason) {
    if (!value) { throw std::runtime_error(reason); }
}
int wmain(int count, wchar_t** arguments) {
    try {
        Require(count == 12, "Expected legacy/context/hook and incompatible fixture payloads");
        using heroes5_sdk::RuntimeResult;
        heroes5_sdk::PluginRuntime runtime;
        uint32_t output = 123;
        Require(runtime.Invoke(0, 0, output) == RuntimeResult::NotLoaded, "Unloaded call accepted");
        Require(runtime.Replace(arguments[1]) == RuntimeResult::Ok, "Initial load failed");
        heroes5_sdk::WindowInputContext unusedInput;
        Require(runtime.InvokeWindowInput(unusedInput)==RuntimeResult::Ok,
            "Legacy payload without optional window-input export became incompatible");
        Require(runtime.Invoke(0, 0, output) == RuntimeResult::Ok && output == 1, "Wrong initial code");
        Require(runtime.Invoke(1, 7, output) == RuntimeResult::Ok && output == 7, "Initial state missing");
        Require(runtime.Invoke(2, 0, output) == RuntimeResult::CallRejected, "New command existed in v1");
        Require(runtime.Replace(arguments[3]) == RuntimeResult::IncompatibleAbi, "Bad ABI accepted");
        Require(runtime.Replace(arguments[4]) == RuntimeResult::IncompatibleState, "Bad state accepted");
        Require(runtime.Generation() == 1, "Rejected payload changed generation");
        Require(runtime.Replace(std::filesystem::path(arguments[1]).parent_path() / L"absent-payload.dll")
            == RuntimeResult::LoadFailed, "Missing payload accepted");
        Require(runtime.Invoke(0, 0, output) == RuntimeResult::Ok && output == 1, "Rejected load replaced working code");
        static heroes5_sdk::PluginRuntime* dispatchProbe = nullptr;
        static bool insideDispatch = false;
        dispatchProbe = &runtime;
        const auto probeDispatch = +[] { insideDispatch = dispatchProbe->DispatchingOnCurrentThread(); };
        Require(!runtime.DispatchingOnCurrentThread() &&
            runtime.Invoke(12, static_cast<uint32_t>(reinterpret_cast<uintptr_t>(probeDispatch)), output) == RuntimeResult::Ok &&
            insideDispatch && !runtime.DispatchingOnCurrentThread(), "Dispatch ownership marker did not bracket payload execution");
        for (unsigned iteration = 0; iteration < 10; ++iteration) {
            Require(runtime.Replace(arguments[2]) == RuntimeResult::Ok, "Version two reload failed");
            Require(runtime.Invoke(0, 0, output) == RuntimeResult::Ok && output == 2, "Changed code missing");
            Require(runtime.Invoke(2, 0, output) == RuntimeResult::Ok && output == 42, "New function missing");
            Require(runtime.Invoke(3, 0, output) == RuntimeResult::CallRejected && output == 42,
                "Rejected call changed result");
            Require(runtime.Invoke(1, 0, output) == RuntimeResult::Ok && output == 7,
                "State lost on reload or rejected call");
            Require(runtime.Replace(arguments[1]) == RuntimeResult::Ok, "Version one reload failed");
        }
        Require(runtime.Generation() == 21, "Repeated reload count wrong");
        HANDLE events[] = {CreateEventW(nullptr, TRUE, FALSE, nullptr), CreateEventW(nullptr, TRUE, FALSE, nullptr)};
        HANDLE replacing = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        HANDLE replaced = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        Require(events[0] && events[1] && replacing && replaced, "Cannot allocate concurrency test events");
        RuntimeResult callStatus{}, replaceStatus{}; uint32_t callValue = 0;
        std::thread caller([&] { callStatus = runtime.Invoke(6, reinterpret_cast<uint32_t>(events), callValue); });
        const bool entered = WaitForSingleObject(events[0], 2000) == WAIT_OBJECT_0;
        std::thread reloader([&] {
            SetEvent(replacing); replaceStatus = runtime.Replace(arguments[2]); SetEvent(replaced);
        });
        const bool attempted = WaitForSingleObject(replacing, 2000) == WAIT_OBJECT_0;
        const bool waited = WaitForSingleObject(replaced, 100) == WAIT_TIMEOUT;
        SetEvent(events[1]); caller.join(); reloader.join();
        CloseHandle(events[0]); CloseHandle(events[1]); CloseHandle(replacing); CloseHandle(replaced);
        Require(entered && attempted && waited, "Reload did not wait for active payload call");
        Require(callStatus == RuntimeResult::Ok && callValue == 1 && replaceStatus == RuntimeResult::Ok,
            "Active old call or replacement failed");
        Require(runtime.Invoke(0, 0, output) == RuntimeResult::Ok && output == 2, "New code missing after quiescence");
        heroes5_sdk::PluginSnapshot snapshot;
        Require(runtime.ExportState(snapshot) == RuntimeResult::Ok, "State export failed");
        heroes5_sdk::PluginRuntime replacement;
        Require(replacement.Replace(arguments[2]) == RuntimeResult::Ok, "Replacement core payload load failed");
        Require(replacement.ImportState(snapshot) == RuntimeResult::Ok, "Cross-core state handoff failed");
        Require(replacement.Invoke(1, 0, output) == RuntimeResult::Ok && output == 7, "Cross-core state changed");
        Require(replacement.Generation() == snapshot.generation + 1, "Core handoff reused generation");
        auto incompatible = snapshot; incompatible.schema = 999;
        Require(replacement.ImportState(incompatible) == RuntimeResult::IncompatibleState, "Wrong schema snapshot accepted");
        incompatible = snapshot; incompatible.version = 999;
        Require(replacement.ImportState(incompatible) == RuntimeResult::IncompatibleState, "Wrong transport version accepted");
        Require(replacement.Invoke(1, 0, output) == RuntimeResult::Ok && output == 7, "Rejected restore changed state");
        heroes5_sdk::PluginRuntime contextual;
        Require(contextual.Replace(arguments[5]) == RuntimeResult::Ok, "Context ABI rejected");
        Require(contextual.Invoke(9, 1, output) == RuntimeResult::CallRejected, "Client forged engine pointer");
        const auto window = CreateWindowExW(0, L"STATIC", L"engine context fixture", WS_POPUP,
            0, 0, 32, 32, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
        auto* storage = static_cast<unsigned char*>(VirtualAlloc(nullptr, 65536,
            MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
        Require(window && storage, "Context owner unavailable");
        heroes5_sdk::EngineContext context;
        context.window = window; context.callSite = 1;
        context.residentSchema = 1; context.residentBytes = 65536; context.residentData = storage;
        context.version = 1;
        Require(contextual.InvokeEngine(context, output) == RuntimeResult::CallRejected,
            "Old context envelope version accepted");
        context.version = 2;
        context.hasRegisters = 2;
        Require(contextual.InvokeEngine(context, output) == RuntimeResult::CallRejected,
            "Invalid register availability accepted");
        context.hasRegisters = 0;
        Require(contextual.InvokeEngine(context, output) == RuntimeResult::Ok && output == 1,
            "Host context not delivered");
        auto* originalData = context.residentData;
        context.residentData += 4;
        Require(contextual.InvokeEngine(context, output) == RuntimeResult::CallRejected,
            "Interior pointer accepted as owned allocation");
        context.residentData = originalData;
        bool foreignAccepted = false;
        std::thread foreign([&] {
            uint32_t ignored = 0;
            foreignAccepted = contextual.InvokeEngine(context, ignored) == RuntimeResult::Ok;
        });
        foreign.join();
        Require(!foreignAccepted, "Foreign thread accessed game context");
        context.residentSchema = 2;
        Require(contextual.InvokeEngine(context, output) == RuntimeResult::CallRejected,
            "Wrong host schema accepted");
        context.residentSchema = 1;
        const auto previousGeneration = contextual.Generation();
        Require(contextual.Replace(arguments[6]) == RuntimeResult::IncompatibleState &&
            contextual.Generation() == previousGeneration && !GetModuleHandleW(arguments[6]) &&
            *reinterpret_cast<uint32_t*>(storage) == 1,
            "Incompatible resident schema retired active code or changed host state");
        Require(contextual.Replace(arguments[5]) == RuntimeResult::Ok &&
            contextual.InvokeEngine(context, output) == RuntimeResult::Ok && output == 2,
            "Host data lost during payload replacement");
        heroes5_sdk::PluginSnapshot contextSnapshot;
        Require(contextual.ExportState(contextSnapshot) == RuntimeResult::Ok &&
            contextSnapshot.version == 2 && contextSnapshot.residentSchema == 1 &&
            contextSnapshot.residentBytes == 65536, "Resident contract missing from core handoff");
        heroes5_sdk::PluginRuntime nextContextCore;
        Require(nextContextCore.Replace(arguments[5]) == RuntimeResult::Ok,
            "Next contextual core could not load payload");
        auto wrongResident = contextSnapshot;
        ++wrongResident.residentSchema;
        Require(nextContextCore.ImportState(wrongResident) == RuntimeResult::IncompatibleState,
            "Cross-core schema mismatch accepted");
        wrongResident = contextSnapshot;
        --wrongResident.residentBytes;
        Require(nextContextCore.ImportState(wrongResident) == RuntimeResult::IncompatibleState,
            "Cross-core buffer size mismatch accepted");
        wrongResident = contextSnapshot;
        wrongResident.version = 1;
        Require(nextContextCore.ImportState(wrongResident) == RuntimeResult::IncompatibleState,
            "Old snapshot transport accepted without resident contract");
        heroes5_sdk::PluginSnapshot rejectedState;
        Require(nextContextCore.ExportState(rejectedState) == RuntimeResult::Ok &&
            nextContextCore.Generation() == 1 && rejectedState.data[0] == 0 &&
            *reinterpret_cast<uint32_t*>(storage) == 2, "Rejected core restore changed state");
        heroes5_sdk::PluginRuntime incompatibleContextCore;
        Require(incompatibleContextCore.Replace(arguments[6]) == RuntimeResult::Ok &&
            incompatibleContextCore.ImportState(contextSnapshot) == RuntimeResult::IncompatibleState,
            "New core with incompatible payload accepted resident state");
        Require(nextContextCore.ImportState(contextSnapshot) == RuntimeResult::Ok &&
            nextContextCore.Generation() == contextSnapshot.generation + 1 &&
            nextContextCore.InvokeEngine(context, output) == RuntimeResult::Ok && output == 3,
            "Compatible new core did not retain host buffer or restore payload state");
        Require(nextContextCore.Invoke(1, 0, output) == RuntimeResult::Ok && output == 3,
            "Restored payload counter diverged from retained resident counter");
        heroes5_sdk::PluginRuntime hooked;
        Require(hooked.Replace(arguments[7]) == RuntimeResult::Ok && hooked.HookSettings().count == 2,
            "ABI5 hook table admission failed");
        Require(hooked.Invoke(10, 1, output) == RuntimeResult::CallRejected,
            "External client forged additional engine callback");
        context.callSite = 3; context.hasRegisters = 0;
        Require(hooked.InvokeEngine(context, output) == RuntimeResult::CallRejected,
            "Native hook accepted missing register frame");
        context.hasRegisters = 1;
        Require(hooked.InvokeEngine(context, output) == RuntimeResult::Ok && output == 4 &&
            reinterpret_cast<uint32_t*>(storage)[11] == 10 && reinterpret_cast<uint32_t*>(storage)[12] == 3,
            "First hook routed to wrong command/site");
        context.callSite = 4;
        Require(hooked.InvokeEngine(context, output) == RuntimeResult::Ok && output == 5 &&
            reinterpret_cast<uint32_t*>(storage)[11] == 11, "Second hook routed incorrectly");
        context.callSite = 5;
        Require(hooked.InvokeEngine(context, output) == RuntimeResult::CallRejected &&
            *reinterpret_cast<uint32_t*>(storage) == 5, "Unregistered hook changed host state");
        Require(hooked.Replace(arguments[8]) == RuntimeResult::IncompatibleAbi && hooked.Generation() == 1 &&
            hooked.HookSettings().entries[1].site == 4 && !GetModuleHandleW(arguments[8]),
            "Duplicate hook sites displaced active payload");
        Require(hooked.Invoke(1, 0, output) == RuntimeResult::Ok && output == 2,
            "Rejected hook admission changed active POD state");
        Require(hooked.Replace(arguments[9]) == RuntimeResult::IncompatibleAbi && hooked.Generation() == 1,
            "Zero base site with live base command admitted");
        context.callSite = 0;
        Require(hooked.InvokeEngine(context, output) == RuntimeResult::CallRejected &&
            *reinterpret_cast<uint32_t*>(storage) == 5, "Zero hook site reached payload code");
        heroes5_sdk::PluginRuntime intercepting;
        Require(intercepting.Replace(arguments[10])==RuntimeResult::Ok &&
            intercepting.InterceptionSettings().count==1,"ABI6 interceptor admission failed");
        Require(intercepting.Invoke(13,1,output)==RuntimeResult::CallRejected,"Worker forged interceptor context pointer");
        context.callSite=5;context.hasRegisters=1;context.registers={};
        const auto admitted=intercepting.InterceptionSettings().entries[0];
        heroes5_sdk::EngineCall call{context.registers};
        Require(intercepting.InvokeInterceptor(context,call,admitted)==RuntimeResult::Ok &&
            call.action==heroes5_sdk::EngineCallAction::ReturnEax && call.result==42,"Interceptor result routing failed");
        auto stale=admitted;stale.policy.stackPopBytes=0;
        Require(intercepting.InvokeInterceptor(context,call,stale)==RuntimeResult::CallRejected,
            "Old native binding invoked a changed payload policy");
        context.registers.eax=99;call={context.registers};
        Require(intercepting.InvokeInterceptor(context,call,admitted)==RuntimeResult::CallRejected &&
            call.action==heroes5_sdk::EngineCallAction::Continue,"Unsupported outcome was committed");
        Require(intercepting.Invoke(1,0,output)==RuntimeResult::Ok && output==1,"Rejected interceptor changed POD state");
        context.registers.eax=0;
        static heroes5_sdk::PluginRuntime* nestedRuntime=nullptr;
        static heroes5_sdk::EngineContext* nestedContext=nullptr;
        static heroes5_sdk::EngineInterceptionSubscription nestedBinding;
        static RuntimeResult nestedStatus=RuntimeResult::CallRejected;
        static bool restoredMarker=false;
        static bool borrowedContextObserved=false;
        nestedRuntime=&intercepting;nestedContext=&context;nestedBinding=admitted;
        const auto nestedProbe=+[] {
            heroes5_sdk::EngineCall nestedCall{nestedContext->registers};
            nestedStatus=nestedRuntime->InvokeInterceptor(*nestedContext,nestedCall,nestedBinding);
            restoredMarker=nestedRuntime->DispatchingOnCurrentThread();
            // This probe enters through a plain command, not an engine context;
            // the inner borrowed context must be gone after the nested call.
            borrowedContextObserved=nestedRuntime->CurrentEngineContext()==nullptr;
        };
        Require(intercepting.Invoke(12,reinterpret_cast<uint32_t>(nestedProbe),output)==RuntimeResult::Ok &&
            nestedStatus==RuntimeResult::Ok && restoredMarker && borrowedContextObserved &&
            !intercepting.DispatchingOnCurrentThread() && !intercepting.CurrentEngineContext(),
            "Pure nested interception deadlocked or lost outer ownership marker");
        Require(intercepting.Invoke(1,0,output)==RuntimeResult::Ok && output==1,"Nested read-only call committed POD state");
        Require(intercepting.Replace(arguments[11])==RuntimeResult::IncompatibleAbi &&
            intercepting.Generation()==1,"Invalid interceptor metadata displaced working module");
        VirtualFree(storage, 0, MEM_RELEASE);
        DestroyWindow(window);
        std::cout << "PASS: ABI3 compatibility, owned context ABI4, blocked pointer forgery, state handoff\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
