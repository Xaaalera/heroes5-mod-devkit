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
#ifndef H5_CORE_VERSION
#define H5_CORE_VERSION 1
#endif

namespace {
SRWLOCK bridgeLock = SRWLOCK_INIT;
SRWLOCK runtimeLifetimeLock = SRWLOCK_INIT;
volatile LONG nativeReentrantEvents = 0;
bool nativeDispatchActive = false; // Bound game thread only.
std::atomic<bool> payloadTransition{false};
heroes5_sdk::PluginRuntime* runtime = nullptr;
HHOOK mainHook = nullptr;
HHOOK windowInputHook = nullptr;
HWND mainWindow = nullptr;
UINT dispatchMessage = 0;
UINT exitMessage = 0;
UINT consoleMessage = 0;
UINT selectorMessage = 0;
std::atomic<uint32_t*> pending{nullptr};
std::atomic<DWORD> pendingStatus{0};
HANDLE hookThread = nullptr;
HANDLE hookReady = nullptr;
HANDLE hookStop = nullptr;
HHOOK eventHook = nullptr;
HWND resultWindow = nullptr;
std::array<uint32_t, 3> eventSettings{};
std::filesystem::path activePath;
std::array<uint32_t, 3> engineSettings{};
uintptr_t originalEngineTarget = 0;
h5::hooks::ScriptObservers* sharedObservers = nullptr;
LONG* sharedObserverSlot = nullptr;
heroes5_sdk::EngineSubscriptions nativeHookSettings{};
std::array<LONG*, heroes5_sdk::EngineHookCapacity> nativeHookSlots{};
using EngineHookControl = DWORD (WINAPI*)(void*);
EngineHookControl engineHookControl = nullptr;
EngineHookControl engineHookControlV2 = nullptr;
EngineHookControl engineHookControlV3 = nullptr;
heroes5_sdk::EngineInterceptions nativeInterceptionSettings{};
std::array<LONG*,heroes5_sdk::EngineHookCapacity> nativeInterceptionSlots{};
h5::hooks::CameraInputObservers* cameraInputObservers = nullptr;
bool uiInputGateJoined = false;
constexpr wchar_t UiInputGateUsers[] = L"Heroes5.UI.KeyGateUsers.v1";
LONG* cameraInputSlot = nullptr;
DWORD windowThread = 0;
uint32_t uiSlot = 0;
bool ownsUiClip = false;
constexpr wchar_t ClipUsers[] = L"Heroes5SDK.ClipUsers";
constexpr wchar_t ClipAdded[] = L"Heroes5SDK.ClipAdded";
constexpr wchar_t ActiveConsole[] = L"Heroes5SDK.ActiveConsole.v1";
struct ConsoleGuard {
    HANDLE mutex = nullptr;
    bool acquired = false;
    ConsoleGuard() {
        const auto name = L"Local\\XalKit.Console.Lifecycle." + std::to_wstring(GetCurrentProcessId());
        mutex = CreateMutexW(nullptr, FALSE, name.c_str());
        if (mutex) {
            const auto result = WaitForSingleObject(mutex, 5000);
            acquired = result == WAIT_OBJECT_0;
            if (result == WAIT_ABANDONED) { ReleaseMutex(mutex); }
        }
    }
    ~ConsoleGuard() { if (acquired) { ReleaseMutex(mutex); } if (mutex) { CloseHandle(mutex); } }
};
struct Guard {
    Guard() { AcquireSRWLockExclusive(&bridgeLock); }
    ~Guard() { ReleaseSRWLockExclusive(&bridgeLock); }
};
// bridgeLock serializes management workers; game-thread callbacks deliberately
// bypass it. Pause their payload admission through retirement and activation.
struct PayloadTransitionGuard {
    PayloadTransitionGuard() { payloadTransition.store(true,std::memory_order_release); }
    ~PayloadTransitionGuard() { payloadTransition.store(false,std::memory_order_release); }
};
struct RuntimeLifetimeGuard {
    bool shared;
    explicit RuntimeLifetimeGuard(bool read) : shared(read) {
        if (shared) { AcquireSRWLockShared(&runtimeLifetimeLock); }
        else { AcquireSRWLockExclusive(&runtimeLifetimeLock); }
    }
    ~RuntimeLifetimeGuard() {
        if (shared) { ReleaseSRWLockShared(&runtimeLifetimeLock); }
        else { ReleaseSRWLockExclusive(&runtimeLifetimeLock); }
    }
};
bool AcquireUiClip() {
    const auto users = reinterpret_cast<uintptr_t>(GetPropW(mainWindow, ClipUsers));
    const auto style = GetWindowLongPtrW(mainWindow, GWL_STYLE);
    if (!users && !(style & WS_CLIPCHILDREN)) {
        SetLastError(0);
        if (!SetWindowLongPtrW(mainWindow, GWL_STYLE, style | WS_CLIPCHILDREN) && GetLastError()) { return false; }
        if (!SetPropW(mainWindow, ClipAdded, reinterpret_cast<HANDLE>(1))) {
            SetWindowLongPtrW(mainWindow, GWL_STYLE, style); return false;
        }
    }
    if (!SetPropW(mainWindow, ClipUsers, reinterpret_cast<HANDLE>(users + 1))) {
        if (!users && RemovePropW(mainWindow, ClipAdded)) { SetWindowLongPtrW(mainWindow, GWL_STYLE, style); }
        return false;
    }
    ownsUiClip = true;
    return true;
}
void ReleaseUiClip() {
    if (!ownsUiClip) { return; }
    const auto users = reinterpret_cast<uintptr_t>(GetPropW(mainWindow, ClipUsers));
    if (users > 1) { SetPropW(mainWindow, ClipUsers, reinterpret_cast<HANDLE>(users - 1)); }
    else {
        RemovePropW(mainWindow, ClipUsers);
        if (RemovePropW(mainWindow, ClipAdded)) {
            SetWindowLongPtrW(mainWindow, GWL_STYLE, GetWindowLongPtrW(mainWindow, GWL_STYLE) & ~WS_CLIPCHILDREN);
        }
    }
    ownsUiClip = false;
}
void __cdecl DispatchEngineFrame(const h5::hooks::X86ObserverFrame* frame) {
    if (GetCurrentThreadId() == windowThread && TryAcquireSRWLockExclusive(&bridgeLock)) {
        uint32_t result = 0;
        if (runtime && engineSettings[2]) {
            const auto resident = runtime->ResidentSettings();
            if (!resident[0]) { runtime->Invoke(engineSettings[2], 0, result); }
            else if (h5::resident::EnsureBuffer(mainWindow, uiSlot + 1, resident[0], resident[1])) {
                h5::resident::BufferLease lease(mainWindow, uiSlot + 1, resident[0], true);
                heroes5_sdk::EngineContext context;
                context.callSite = engineSettings[0];
                context.window = mainWindow;
                context.residentSchema = resident[0];
                context.residentBytes = lease.Bytes();
                context.residentData = lease.WritableData();
                if (frame) { context.hasRegisters = 1; context.registers = *frame; }
                runtime->InvokeEngine(context, result);
            }
        }
        ReleaseSRWLockExclusive(&bridgeLock);
    }
}
// Only registered in the validated PUSHFD/PUSHAD resident dispatcher, whose
// EBP points at its saved frame. The direct EngineCallThunk uses the same layout.
__declspec(naked) void OnEngineCall() {
    __asm {
        push ebp
        call DispatchEngineFrame
        add esp, 4
        ret
    }
}
template<size_t Index> void __cdecl OnNativeEngineFrame(const h5::hooks::X86ObserverFrame* frame) {
    if (!frame || GetCurrentThreadId() != windowThread) { return; }
    if (nativeDispatchActive) { InterlockedIncrement(&nativeReentrantEvents); return; }
    if (payloadTransition.load(std::memory_order_acquire)) { return; }
    struct DispatchScope {
        DispatchScope() { nativeDispatchActive = true; }
        ~DispatchScope() { nativeDispatchActive = false; }
    } dispatchScope;
    // Transport may hold bridgeLock while synchronously waiting for this thread.
    // Protect only runtime lifetime here; its own lock serializes payload swap.
    RuntimeLifetimeGuard lifetime(true);
    if (runtime && runtime->DispatchingOnCurrentThread()) {
        // Reentry violates the current payload contract; do not deadlock or
        // silently claim complete capture. A primitive collector is still needed.
        InterlockedIncrement(&nativeReentrantEvents);
        return;
    }
    if (runtime && Index < nativeHookSettings.count && nativeHookSlots[Index]) {
        const auto resident = runtime->ResidentSettings();
        if (h5::resident::EnsureBuffer(mainWindow, uiSlot + 1, resident[0], resident[1])) {
            h5::resident::BufferLease lease(mainWindow, uiSlot + 1, resident[0], true);
            heroes5_sdk::EngineContext context;
            context.window = mainWindow;
            context.callSite = nativeHookSettings.entries[Index].site;
            context.residentSchema = resident[0]; context.residentBytes = lease.Bytes();
            context.residentData = lease.WritableData();
            context.hasRegisters = 1; context.registers = *frame;
            uint32_t result = 0;
            runtime->InvokeEngine(context, result);
        }
    }
}
constexpr std::array<heroes5_sdk::EngineObserver, heroes5_sdk::EngineHookCapacity> nativeCallbacks{
    OnNativeEngineFrame<0>, OnNativeEngineFrame<1>, OnNativeEngineFrame<2>, OnNativeEngineFrame<3>,
    OnNativeEngineFrame<4>, OnNativeEngineFrame<5>, OnNativeEngineFrame<6>, OnNativeEngineFrame<7>,
    OnNativeEngineFrame<8>, OnNativeEngineFrame<9>, OnNativeEngineFrame<10>, OnNativeEngineFrame<11>,
    OnNativeEngineFrame<12>, OnNativeEngineFrame<13>, OnNativeEngineFrame<14>, OnNativeEngineFrame<15>};
template<size_t Index> uint32_t DispatchNativeInterception(heroes5_sdk::EngineCall* call) {
    if (!runtime || Index>=nativeInterceptionSettings.count || !nativeInterceptionSlots[Index]) { return 0; }
    const auto admitted=nativeInterceptionSettings.entries[Index];
    if (runtime->DispatchingOnCurrentThread()) {
        const auto* outer=runtime->CurrentEngineContext();
        if (!admitted.readOnlyReentry || !outer) { InterlockedIncrement(&nativeReentrantEvents);return 0; }
        auto context=*outer;
        context.callSite=admitted.site;context.registers=call->frame;context.hasRegisters=1;
        return runtime->InvokeInterceptor(context,*call,admitted)==heroes5_sdk::RuntimeResult::Ok ? 1u : 0u;
    }
    const auto resident=runtime->ResidentSettings();
    if (!h5::resident::EnsureBuffer(mainWindow,uiSlot+1,resident[0],resident[1])) { return 0; }
    h5::resident::BufferLease lease(mainWindow,uiSlot+1,resident[0],true);
    heroes5_sdk::EngineContext context;
    context.window=mainWindow;context.callSite=admitted.site;
    context.residentSchema=resident[0];context.residentBytes=lease.Bytes();context.residentData=lease.WritableData();
    context.hasRegisters=1;context.registers=call->frame;
    return runtime->InvokeInterceptor(context,*call,admitted)==heroes5_sdk::RuntimeResult::Ok ? 1u : 0u;
}
template<size_t Index> uint32_t __cdecl OnNativeEngineInterception(heroes5_sdk::EngineCall* call) {
    if (!call || GetCurrentThreadId()!=windowThread) { return 0; }
    if (nativeDispatchActive) {
        // The outer event already pins runtime and owns the writable buffer.
        // Never recursively acquire either SRW lease for a nested pure handler.
        return DispatchNativeInterception<Index>(call);
    }
    RuntimeLifetimeGuard lifetime(true);
    // Preserve nested pure calls made by the already-admitted payload/finalizer.
    if (payloadTransition.load(std::memory_order_acquire) &&
        (!runtime || !runtime->DispatchingOnCurrentThread())) { return 0; }
    struct DispatchScope {
        DispatchScope() { nativeDispatchActive=true; }
        ~DispatchScope() { nativeDispatchActive=false; }
    } scope;
    return DispatchNativeInterception<Index>(call);
}
constexpr std::array<heroes5_sdk::EngineInterceptor,heroes5_sdk::EngineHookCapacity> nativeInterceptors{
    OnNativeEngineInterception<0>,OnNativeEngineInterception<1>,OnNativeEngineInterception<2>,OnNativeEngineInterception<3>,
    OnNativeEngineInterception<4>,OnNativeEngineInterception<5>,OnNativeEngineInterception<6>,OnNativeEngineInterception<7>,
    OnNativeEngineInterception<8>,OnNativeEngineInterception<9>,OnNativeEngineInterception<10>,OnNativeEngineInterception<11>,
    OnNativeEngineInterception<12>,OnNativeEngineInterception<13>,OnNativeEngineInterception<14>,OnNativeEngineInterception<15>};
DWORD SendEngineInterception(heroes5_sdk::EngineHookRequestV3& request) {
    auto& base=request.signature.base;
    base.callbackRetired=base.action==heroes5_sdk::EngineHookAction::Attach && !base.slot ? 1u : 0u;
    if (!engineHookControlV3) { return ERROR_NOT_SUPPORTED; }
    if (base.action==heroes5_sdk::EngineHookAction::Attach &&
        (request.signature.expectedBytes<5 || request.signature.expectedBytes>16)) { return ERROR_NOT_SUPPORTED; }
    return engineHookControlV3(&request);
}
bool ClearNativeInterceptions() {
    for (uint32_t index=0;index<nativeInterceptionSettings.count;++index) {
        if (!nativeInterceptionSlots[index]) { continue; }
        if (GetCurrentThreadId()!=windowThread) { return false; }
        heroes5_sdk::EngineHookRequestV3 request;
        request.signature.base.action=heroes5_sdk::EngineHookAction::Detach;
        request.signature.base.window=mainWindow;
        request.signature.base.site=nativeInterceptionSettings.entries[index].site;
        request.signature.base.slot=nativeInterceptionSlots[index];request.interceptor=nativeInterceptors[index];
        const auto status=SendEngineInterception(request);
        if (request.signature.base.callbackRetired) { nativeInterceptionSlots[index]=nullptr; }
        if (status!=ERROR_SUCCESS || !request.signature.base.callbackRetired) { return false; }
    }
    nativeInterceptionSettings={};return true;
}
bool RefreshNativeInterceptions() {
    const auto settings=runtime->InterceptionSettings();
    if (!settings.count) { return true; }
    if (GetCurrentThreadId()!=windowThread) { return false; }
    if (!engineHookControlV3) {
        const auto loader=GetModuleHandleW(L"dinput8.dll");
        engineHookControlV3=loader ? reinterpret_cast<EngineHookControl>(GetProcAddress(loader,"Heroes5ModsEngineHookV3")) : nullptr;
    }
    if (!engineHookControlV3) {
        heroes5_sdk::Trace(heroes5_sdk::LogLevel::Error,"native-hooks","The loader needs the function interception service.");
        return false;
    }
    const auto resident=runtime->ResidentSettings();
    if (!h5::resident::EnsureBuffer(mainWindow,uiSlot+1,resident[0],resident[1])) { return false; }
    nativeInterceptionSettings=settings;
    for (uint32_t index=0;index<settings.count;++index) {
        const auto& entry=settings.entries[index];
        heroes5_sdk::EngineHookRequestV3 request;
        request.signature.base.window=mainWindow;request.signature.base.site=entry.site;
        request.signature.expectedBytes=entry.sourceBytes;
        memcpy(request.signature.expected,entry.sourceExpected,16);
        request.policy=entry.policy;request.interceptor=nativeInterceptors[index];
        request.branchBytes=entry.branchBytes;memcpy(request.branchExpected,entry.branchExpected,16);
        const auto status=SendEngineInterception(request);
        nativeInterceptionSlots[index]=request.signature.base.slot;
        if (status!=ERROR_SUCCESS || !nativeInterceptionSlots[index]) {
            if (request.signature.base.callbackRetired) { nativeInterceptionSlots[index]=nullptr; }
            ClearNativeInterceptions();return false;
        }
    }
    return true;
}
DWORD SendEngineHookRequest(heroes5_sdk::EngineHookRequest& request) {
    if (!engineHookControlV2) { return engineHookControl ? engineHookControl(&request) : ERROR_NOT_READY; }
    const auto expected = h5::hooks::EngineSiteSignature(request.site);
    if (expected.empty() || expected.size() > 16) {
        request.callbackRetired = request.action == heroes5_sdk::EngineHookAction::Attach && !request.slot ? 1u : 0u;
        return ERROR_NOT_SUPPORTED;
    }
    heroes5_sdk::EngineHookRequestV2 transport;
    transport.base = request;
    transport.base.size = sizeof(transport); transport.base.version = 2;
    transport.expectedBytes = static_cast<uint32_t>(expected.size());
    memcpy(transport.expected, expected.data(), expected.size());
    const auto status = engineHookControlV2(&transport);
    request.slot = transport.base.slot;
    request.callbackRetired = transport.base.callbackRetired;
    return status;
}
bool ClearNativeEngineHooks() {
    for (uint32_t index = 0; index < nativeHookSettings.count; ++index) {
        if (!nativeHookSlots[index]) { continue; }
        if (!engineHookControl || GetCurrentThreadId() != windowThread) { return false; }
        heroes5_sdk::EngineHookRequest request;
        request.action = heroes5_sdk::EngineHookAction::Detach;
        request.window = mainWindow; request.site = nativeHookSettings.entries[index].site;
        request.callback = nativeCallbacks[index]; request.slot = nativeHookSlots[index];
        const auto status = SendEngineHookRequest(request);
        if (request.callbackRetired) { nativeHookSlots[index] = nullptr; }
        if (status != ERROR_SUCCESS || !request.callbackRetired) { return false; }
    }
    nativeHookSettings = {};
    return true;
}
bool RefreshNativeObservers() {
    const auto settings = runtime->HookSettings();
    bool complete = settings == nativeHookSettings;
    for (uint32_t index = 0; index < settings.count; ++index) { complete = complete && nativeHookSlots[index]; }
    if (complete) { return true; }
    if (!ClearNativeEngineHooks()) { return false; }
    if (!settings.count) { return true; }
    if (GetCurrentThreadId() != windowThread) { return false; }
    if (!engineHookControl) {
        const auto loader = GetModuleHandleW(L"dinput8.dll");
        engineHookControl = loader ? reinterpret_cast<EngineHookControl>(GetProcAddress(loader, "Heroes5ModsEngineHook")) : nullptr;
        engineHookControlV2 = loader ? reinterpret_cast<EngineHookControl>(GetProcAddress(loader, "Heroes5ModsEngineHookV2")) : nullptr;
    }
    if (!engineHookControl) {
        heroes5_sdk::Trace(heroes5_sdk::LogLevel::Error, "native-hooks", "The loader has no native event service.");
        return false;
    }
    const auto resident = runtime->ResidentSettings();
    if (!h5::resident::EnsureBuffer(mainWindow, uiSlot + 1, resident[0], resident[1])) { return false; }
    nativeHookSettings = settings;
    for (uint32_t index = 0; index < settings.count; ++index) {
        heroes5_sdk::EngineHookRequest request;
        request.window = mainWindow; request.site = settings.entries[index].site;
        request.callback = nativeCallbacks[index];
        const auto status = SendEngineHookRequest(request);
        nativeHookSlots[index] = request.slot;
        if (status != ERROR_SUCCESS || !request.slot) {
            const auto message = std::string("Placement event subscription rejected; Windows status ") + std::to_string(status) + ".";
            heroes5_sdk::Trace(heroes5_sdk::LogLevel::Error, "native-hooks", message.c_str());
            if (request.callbackRetired) { nativeHookSlots[index] = nullptr; }
            // Any remaining slots stay recorded; failed cleanup prevents unload.
            ClearNativeEngineHooks();
            return false;
        }
    }
    return true;
}
bool RefreshNativeEngineHooks() {
    const auto observers=runtime->HookSettings();
    const auto interceptions=runtime->InterceptionSettings();
    bool complete=observers==nativeHookSettings && interceptions==nativeInterceptionSettings;
    for (uint32_t index=0;index<observers.count;++index) { complete=complete && nativeHookSlots[index]; }
    for (uint32_t index=0;index<interceptions.count;++index) { complete=complete && nativeInterceptionSlots[index]; }
    if (complete) { return true; }
    // Retire both old modes before rebinding sites which change their contract.
    if (!ClearNativeInterceptions() || !ClearNativeEngineHooks()) { return false; }
    if (!RefreshNativeObservers()) { return false; }
    if (!RefreshNativeInterceptions()) { ClearNativeEngineHooks();return false; }
    return true;
}
__declspec(naked) void EngineCallThunk() {
    __asm {
        pushfd
        pushad
        mov ebp, esp
        sub esp, 528
        and esp, -16
        fxsave [esp]
        cld
        call OnEngineCall
        fxrstor [esp]
        mov esp, ebp
        popad
        popfd
        jmp dword ptr [originalEngineTarget]
    }
}
std::array<unsigned char, 5> CallBytes(uint32_t site, uintptr_t target) {
    std::array<unsigned char, 5> bytes{0xe8, 0, 0, 0, 0};
    const auto displacement = static_cast<uint32_t>(target - site - 5);
    std::memcpy(bytes.data() + 1, &displacement, sizeof(displacement));
    return bytes;
}
template <size_t Size>
bool WriteCall(uint32_t site, const std::array<unsigned char, Size>& expected,
    const std::array<unsigned char, Size>& replacement) {
    auto* address = reinterpret_cast<void*>(site);
    MEMORY_BASIC_INFORMATION region{};
    if (!VirtualQuery(address, &region, sizeof(region)) || region.State != MEM_COMMIT ||
        (region.Protect & (PAGE_GUARD | PAGE_NOACCESS)) ||
        !(region.Protect & (PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY)) ||
        site + static_cast<uint64_t>(Size) > reinterpret_cast<uintptr_t>(region.BaseAddress) + region.RegionSize ||
        std::memcmp(address, expected.data(), expected.size()) != 0) { return false; }
    DWORD protection = 0;
    if (!VirtualProtect(address, Size, PAGE_EXECUTE_READWRITE, &protection)) { return false; }
    std::memcpy(address, replacement.data(), replacement.size());
    const bool flushed = FlushInstructionCache(GetCurrentProcess(), address, Size) != 0;
    DWORD ignored = 0;
    const bool restored = VirtualProtect(address, Size, protection, &ignored) != 0;
    if (!flushed || !restored) {
        if (VirtualProtect(address, Size, PAGE_EXECUTE_READWRITE, &ignored)) {
            std::memcpy(address, expected.data(), expected.size());
            FlushInstructionCache(GetCurrentProcess(), address, Size);
            VirtualProtect(address, Size, protection, &ignored);
        }
        return false;
    }
    return true;
}
bool ClearEngineHook() {
    if (!engineSettings[0]) { return true; }
    if (sharedObserverSlot) {
        if (h5::hooks::FindScriptObservers(engineSettings[0], engineSettings[1]) != sharedObservers ||
            !h5::hooks::RemoveScriptObserver(sharedObserverSlot, OnEngineCall)) { return false; }
        sharedObserverSlot = nullptr; sharedObservers = nullptr; engineSettings = {};
        return true;
    }
    if (!WriteCall(engineSettings[0], CallBytes(engineSettings[0], reinterpret_cast<uintptr_t>(EngineCallThunk)),
        CallBytes(engineSettings[0], engineSettings[1]))) { return false; }
    engineSettings = {};
    return true;
}
bool RefreshEngineHook() {
    const auto settings = runtime->EngineSettings();
    if (settings == engineSettings) { return true; }
    if (!ClearEngineHook()) { return false; }
    if (!settings[0]) { return true; }
    if (settings[0] != h5::hooks::ScriptDispatchCall.address ||
        settings[1] != h5::hooks::ScriptDispatchTarget) { return false; }
    auto* observers = h5::hooks::FindScriptObservers(settings[0], settings[1]);
    if (!observers) {
        const auto original = CallBytes(settings[0], settings[1]);
        if (!h5::hooks::ObserverMemory(settings[0], original.size(), PAGE_EXECUTE_READ) ||
            std::memcmp(reinterpret_cast<void*>(settings[0]), original.data(), original.size()) != 0) { return false; }
        observers = h5::hooks::CreateScriptObserverStorage(settings[0], settings[1]);
        if (!observers) { return false; }
        const auto code = reinterpret_cast<uintptr_t>(observers) - h5::hooks::ScriptObserverDescriptorOffset;
        if (!WriteCall(settings[0], original, CallBytes(settings[0], code))) {
            // Publication failure can leave instruction-cache/protection state
            // uncertain. Retain prepared code rather than risk a dangling CALL.
            return false;
        }
    }
    auto* slot = h5::hooks::AddScriptObserver(*observers, OnEngineCall);
    if (!slot) { return false; }
    sharedObservers = observers; sharedObserverSlot = slot; engineSettings = settings;
    return true;
}
h5::ui::InputPanelCapture ReadUiCapture() {
    auto capture = h5::ui::ReadInputPanels(mainWindow);
    h5::ui::ReadPanelCapture(static_cast<HWND>(GetPropW(mainWindow, L"XalKit.Console.Frame.v1")), capture);
    h5::ui::PublishKeyboardFocus(mainWindow, capture);
    return capture;
}
void __cdecl CaptureUiCameraInput() {
    if (!cameraInputObservers) { return; }
    const auto capture = ReadUiCapture();
    h5::hooks::CaptureCameraInput(*cameraInputObservers, capture.mouse, capture.keyboard);
}
bool RefreshCameraInput() {
    if (cameraInputSlot) { return true; }
    const auto& site = h5::hooks::AdventureCameraInput;
    auto* storage = h5::hooks::FindCameraInputObservers(site.address);
    if (!storage) {
        std::array<unsigned char, 6> original{};
        std::memcpy(original.data(), site.expected, original.size());
        if (!h5::hooks::ObserverMemory(site.address, original.size(), PAGE_EXECUTE_READ) ||
            std::memcmp(reinterpret_cast<void*>(site.address), original.data(), original.size())) { return false; }
        storage = h5::hooks::CreateCameraInputObservers(site.address);
        if (!storage) { return false; }
        const auto code = reinterpret_cast<uintptr_t>(storage) - h5::hooks::ScriptObserverDescriptorOffset;
        const auto call = CallBytes(static_cast<uint32_t>(site.address), code);
        std::array<unsigned char, 6> replacement{};
        std::copy(call.begin(), call.end(), replacement.begin());
        replacement.back() = 0x90;
        // Retain prepared storage on an uncertain publication result.
        if (!WriteCall(static_cast<uint32_t>(site.address), original, replacement)) { return false; }
    }
    cameraInputObservers = storage;
    cameraInputSlot = h5::hooks::AddScriptObserver(storage->observers, CaptureUiCameraInput);
    return cameraInputSlot != nullptr;
}
bool ClearCameraInput() {
    if (!cameraInputSlot) { return true; }
    if (h5::hooks::FindCameraInputObservers(h5::hooks::AdventureCameraInput.address) != cameraInputObservers ||
        !h5::hooks::RemoveScriptObserver(cameraInputSlot, CaptureUiCameraInput)) { return false; }
    cameraInputSlot = nullptr;
    cameraInputObservers = nullptr;
    return true;
}
LRESULT CALLBACK WindowEventHook(int code, WPARAM parameter, LPARAM data) {
    if (code >= 0 && parameter == PM_REMOVE) {
        auto* message = reinterpret_cast<MSG*>(data);
        if (message->hwnd == mainWindow && eventSettings[0] &&
            message->message == eventSettings[0] &&
            (message->lParam == 0 || message->lParam == reinterpret_cast<LPARAM>(&eventSettings))) {
            if (!TryAcquireSRWLockExclusive(&bridgeLock)) {
                // Preserve the private event while a worker owns the bridge.
                // Blocking here would deadlock synchronous main-thread calls.
                PostMessageW(message->hwnd, message->message, message->wParam, message->lParam);
                return CallNextHookEx(nullptr, code, parameter, data);
            }
            uint32_t output = 0;
            if (runtime && runtime->Invoke(eventSettings[1], static_cast<uint32_t>(message->wParam), output)
                == heroes5_sdk::RuntimeResult::Ok && resultWindow) {
                // Numeric diagnostic output is language-independent.
                SetWindowTextW(resultWindow, std::to_wstring(output).c_str());
            }
            ReleaseSRWLockExclusive(&bridgeLock);
        }
    }
    return CallNextHookEx(nullptr, code, parameter, data);
}
bool ClearEvents() {
    if (!ClearNativeInterceptions()) { return false; }
    if (!ClearNativeEngineHooks()) { return false; }
    if (!ClearCameraInput()) { return false; }
    if (!ClearEngineHook()) { return false; }
    if (uiInputGateJoined) {
        const auto users = reinterpret_cast<uintptr_t>(GetPropW(mainWindow, UiInputGateUsers));
        if (users > 1) {
            if (!SetPropW(mainWindow, UiInputGateUsers, reinterpret_cast<HANDLE>(users - 1))) { return false; }
        }
        else {
            RemovePropW(mainWindow, UiInputGateUsers);
            RemovePropW(mainWindow, h5::ui::KeyboardFocusProperty);
            if (auto* gate = h5::hooks::FindAdventureInputGate(h5::hooks::AdventureInputTail.address, h5::hooks::AdventureInputTarget)) {
                wcscpy_s(gate->property, L"XalKit.Console.Frame.v1");
            }
        }
        uiInputGateJoined = false;
    }
    if (eventHook && !UnhookWindowsHookEx(eventHook)) { return false; }
    eventHook = nullptr;
    if (resultWindow && !DestroyWindow(resultWindow)) { return false; }
    ReleaseUiClip();
    resultWindow = nullptr; eventSettings = {};
    return true;
}
bool RefreshEvents() {
    const auto& inputSite = h5::hooks::AdventureInputTail;
    const bool gameInputSupported = h5::hooks::FindAdventureInputGate(inputSite.address, h5::hooks::AdventureInputTarget) ||
        (h5::hooks::ObserverMemory(inputSite.address, sizeof(inputSite.expected), PAGE_EXECUTE_READ) &&
         !std::memcmp(reinterpret_cast<void*>(inputSite.address), inputSite.expected, sizeof(inputSite.expected)));
    if (!gameInputSupported && ReadUiCapture().present) { return false; }
    if (gameInputSupported) {
        if (GetCurrentThreadId()!=windowThread) { return false; }
        const auto& site=h5::hooks::AdventureInputTail;
        auto* gate=h5::hooks::FindAdventureInputGate(site.address,h5::hooks::AdventureInputTarget);
        if (gate && gate->gameWindow!=reinterpret_cast<uintptr_t>(mainWindow)) { return false; }
        const auto priorCode=gate ? reinterpret_cast<uintptr_t>(gate)-h5::hooks::ScriptObserverDescriptorOffset : 0;
        const auto currentCode=gate ? h5::hooks::AdventureInputCode(priorCode,reinterpret_cast<uintptr_t>(gate),
            h5::hooks::AdventureInputTarget) : std::vector<unsigned char>{};
        if (!gate || std::memcmp(reinterpret_cast<void*>(priorCode),currentCode.data(),currentCode.size())) {
            std::array<unsigned char,5> original{};
            if (gate) {
                original=CallBytes(static_cast<uint32_t>(site.address),priorCode);
                original[0]=0xe9;
            }
            else {
                std::memcpy(original.data(),site.expected,original.size());
            }
            if (std::memcmp(reinterpret_cast<void*>(site.address),original.data(),original.size())) { return false; }
            gate=h5::hooks::CreateAdventureInputGate(h5::hooks::AdventureInputTarget,mainWindow);
            if (!gate) { return false; }
            const auto code=reinterpret_cast<uintptr_t>(gate)-h5::hooks::ScriptObserverDescriptorOffset;
            auto replacement=CallBytes(static_cast<uint32_t>(site.address),code);
            replacement[0]=0xe9;
            if (!WriteCall(static_cast<uint32_t>(site.address),original,replacement)) { return false; }
            // Owner-thread replacement leaves the prior sealed allocation intact.
            // No callback or code pointer into a replaceable module is retained.
        }
        if (gate->gameWindow!=reinterpret_cast<uintptr_t>(mainWindow)) { return false; }
        if (!uiInputGateJoined) {
            const auto users = reinterpret_cast<uintptr_t>(GetPropW(mainWindow, UiInputGateUsers));
            if (users >= 1024 || !SetPropW(mainWindow, UiInputGateUsers, reinterpret_cast<HANDLE>(users + 1))) { return false; }
            uiInputGateJoined = true;
        }
        wcscpy_s(gate->property, h5::ui::KeyboardFocusProperty);
        ReadUiCapture();
    }
    if (gameInputSupported && !RefreshCameraInput()) { return false; }
    if (!RefreshEngineHook()) {
        heroes5_sdk::Trace(heroes5_sdk::LogLevel::Error, "native-hooks", "The game-loop observer could not attach.");
        return false;
    }
    if (!RefreshNativeEngineHooks()) { return false; }
    const auto settings = runtime->EventSettings();
    if (settings == eventSettings) { return true; }
    if (eventHook && !UnhookWindowsHookEx(eventHook)) { return false; }
    eventHook = nullptr;
    if (resultWindow && !DestroyWindow(resultWindow)) { return false; }
    ReleaseUiClip();
    resultWindow = nullptr; eventSettings = {};
    if (!settings[0]) { return true; }
    eventHook = SetWindowsHookExW(WH_GETMESSAGE, WindowEventHook, nullptr, GetCurrentThreadId());
    if (!eventHook) { return false; }
    if (settings[2]) {
        if (!AcquireUiClip()) { ClearEvents(); return false; }
        resultWindow = CreateWindowExW(0, L"STATIC", L"0", WS_CHILD | WS_VISIBLE | SS_CENTER,
            16, 16 + static_cast<int>(uiSlot) * 32, 120, 28, mainWindow, nullptr, GetModuleHandleW(nullptr), nullptr);
        if (!resultWindow) { ClearEvents(); return false; }
    }
    eventSettings = settings;
    return true;
}
DWORD ControlBankSelectorOnMain(heroes5_sdk::SelectorAction action, uint32_t* result = nullptr, uint32_t statistic = 0) {
    if (GetCurrentThreadId() != windowThread) { return ERROR_INVALID_THREAD_ID; }
    wchar_t executable[32768]{};
    const DWORD length = GetModuleFileNameW(nullptr, executable, 32768);
    if (!length || length >= 32768) { return ERROR_BAD_PATHNAME; }
    const auto loaderPath = std::filesystem::path(executable).parent_path() / L"dinput8.dll";
    const auto loader = GetModuleHandleW(loaderPath.c_str());
    using Control = DWORD (WINAPI*)(void*);
    const auto control = loader ? reinterpret_cast<Control>(GetProcAddress(loader, "Heroes5BankSelectorControl")) : nullptr;
    if (!control) { return ERROR_NOT_SUPPORTED; }
    heroes5_sdk::SelectorRequest binding;
    const DWORD status = control(&binding);
    if (status != ERROR_SUCCESS && status != ERROR_NOT_READY) { return status; }
    binding.action = action;
    binding.expectedGeneration = binding.generation;
    binding.window = mainWindow;
    if (action == heroes5_sdk::SelectorAction::ReadState && result) {
        if (statistic > 5) { return ERROR_INVALID_PARAMETER; }
        if (statistic >= 4) {
            // Generated bank data reserves 64 cache slots before the routes.
            uint32_t cachedRoots[64]{};
            binding.stateOffset = 256;
            binding.output = reinterpret_cast<unsigned char*>(cachedRoots);
            binding.outputBytes = sizeof(cachedRoots);
            const DWORD readStatus = control(&binding);
            if (readStatus != ERROR_SUCCESS) { return readStatus; }
            uint32_t count = 0;
            uint32_t fingerprint = 2166136261u;
            for (const uint32_t root : cachedRoots) {
                if (root) { ++count; }
                fingerprint = (fingerprint ^ root) * 16777619u;
            }
            *result = statistic == 4 ? count : fingerprint;
            return ERROR_SUCCESS;
        }
        binding.stateOffset = 8 + statistic * sizeof(uint32_t);
        binding.output = reinterpret_cast<unsigned char*>(result);
        binding.outputBytes = sizeof(*result);
    }
    return control(&binding);
}
bool FinalizePayloadOnMain(bool replacing = false) {
    if (!runtime || GetCurrentThreadId()!=windowThread) { return false; }
    if (!runtime->HasEngineFinalizer()) { return true; }
    const auto resident=runtime->ResidentSettings();
    heroes5_sdk::EngineContext context;context.window=mainWindow;
    context.residentSchema=resident[0];context.residentBytes=resident[1];
    if (!resident[0]) { return runtime->FinalizeEngine(context,replacing)==heroes5_sdk::RuntimeResult::Ok; }
    if (!h5::resident::EnsureBuffer(mainWindow,uiSlot+1,resident[0],resident[1])) { return false; }
    h5::resident::BufferLease lease(mainWindow,uiSlot+1,resident[0],true);
    context.residentData=lease.WritableData();context.residentBytes=lease.Bytes();
    return runtime->FinalizeEngine(context,replacing)==heroes5_sdk::RuntimeResult::Ok;
}
void DispatchWindowInputOnMain(const CWPSTRUCT& message) {
    if (mainWindow && GetCurrentThreadId() == windowThread &&
        (message.message == WM_SETFOCUS || message.message == WM_KILLFOCUS ||
         message.message == WM_SHOWWINDOW || message.message == WM_NCDESTROY ||
         message.message == WM_LBUTTONDOWN)) {
        auto capture = ReadUiCapture();
        if (message.hwnd == mainWindow && message.message == WM_LBUTTONDOWN && capture.keyboard && !capture.mouse) {
            SetFocus(mainWindow); ReadUiCapture();
        }
    }
    if (message.hwnd!=mainWindow || message.message!=WM_LBUTTONDBLCLK ||
        GetCurrentThreadId()!=windowThread || nativeDispatchActive ||
        payloadTransition.load(std::memory_order_acquire) ||
        heroes5_sdk::PluginRuntime::CallingPayloadOnCurrentThread()) { return; }
    // Transport may be waiting for this thread while owning bridgeLock.
    // Pin lifetime independently, just as native engine callbacks do.
    RuntimeLifetimeGuard lifetime(true);
    if (runtime && !runtime->DispatchingOnCurrentThread()) {
        struct DispatchScope {
            DispatchScope() { nativeDispatchActive=true; }
            ~DispatchScope() { nativeDispatchActive=false; }
        } dispatchScope;
        const auto resident=runtime->ResidentSettings();
        if (resident[0] && h5::resident::EnsureBuffer(mainWindow,uiSlot+1,resident[0],resident[1])) {
            h5::resident::BufferLease lease(mainWindow,uiSlot+1,resident[0],true);
            heroes5_sdk::WindowInputContext context;context.engine.window=mainWindow;
            context.engine.residentSchema=resident[0];context.engine.residentData=lease.WritableData();
            context.engine.residentBytes=lease.Bytes();context.message=message.message;
            context.wparam=message.wParam;context.lparam=message.lParam;
            runtime->InvokeWindowInput(context);
        }
    }
}
LRESULT CALLBACK WindowInputHook(int code, WPARAM parameter, LPARAM data) {
    if (code>=0 && parameter==PM_REMOVE && data) {
        const auto& message=*reinterpret_cast<const MSG*>(data);
        const CWPSTRUCT event{message.lParam,message.wParam,message.message,message.hwnd};
        DispatchWindowInputOnMain(event);
    }
    return CallNextHookEx(nullptr,code,parameter,data);
}
LRESULT CALLBACK MainThreadHook(int code, WPARAM parameter, LPARAM data) {
    if (code>=0 && data) { DispatchWindowInputOnMain(*reinterpret_cast<const CWPSTRUCT*>(data)); }
    const auto request = pending.load(std::memory_order_acquire);
    if (code >= 0 && request) {
        const auto* message = reinterpret_cast<const CWPSTRUCT*>(data);
        if (message->hwnd == mainWindow && (message->message == dispatchMessage ||
            message->message == exitMessage || message->message == consoleMessage || message->message == selectorMessage) &&
            message->lParam == reinterpret_cast<LPARAM>(request)) {
            // A native UI call can pump messages while payload/runtime locks
            // are held. Decline management reentry instead of reacquiring them.
            if (nativeDispatchActive || (runtime && runtime->DispatchingOnCurrentThread())) {
                pendingStatus.store(ERROR_BUSY,std::memory_order_release);
                return CallNextHookEx(nullptr,code,parameter,data);
            }
            if (message->message == exitMessage) {
                const auto& exit = h5::hooks::ExitRequest;
                if (GetCurrentThreadId() != windowThread ||
                    !h5::hooks::ObserverMemory(exit.address, sizeof(exit.expected), PAGE_EXECUTE_READ) ||
                    std::memcmp(reinterpret_cast<void*>(exit.address), exit.expected, sizeof(exit.expected)) != 0) {
                    pendingStatus.store(104, std::memory_order_release); return CallNextHookEx(nullptr, code, parameter, data);
                }
                // The pinned routine appends an exit request to the game queue.
                // It accepts an optional context in ECX and no stack arguments.
                reinterpret_cast<void (__thiscall*)(void*)>(exit.address)(nullptr);
                pendingStatus.store(0, std::memory_order_release);
                return CallNextHookEx(nullptr, code, parameter, data);
            }
            if (message->message == consoleMessage) {
                const auto* command = reinterpret_cast<const h5::ConsoleCommandRequest*>(request[1]);
                const bool dispatched=command && (request[0] ? h5::DispatchGameText(mainWindow,*command,request[0])
                                                               : h5::DispatchConsoleCommand(mainWindow,*command));
                pendingStatus.store(dispatched ? 0u : 104u,
                    std::memory_order_release);
                return CallNextHookEx(nullptr, code, parameter, data);
            }
            if (message->message == selectorMessage) {
                const auto action = static_cast<heroes5_sdk::SelectorAction>(request[0]);
                const bool allowed = action == heroes5_sdk::SelectorAction::BindWindow
                    || action == heroes5_sdk::SelectorAction::AttachHook || action == heroes5_sdk::SelectorAction::DetachHook
                    || action == heroes5_sdk::SelectorAction::ReadState;
                pendingStatus.store(allowed ? ControlBankSelectorOnMain(action, &request[2], request[1])
                    : ERROR_INVALID_PARAMETER, std::memory_order_release);
                return CallNextHookEx(nullptr, code, parameter, data);
            }
            DWORD status = 0;
            switch (request[0]) {
            case UINT32_MAX: status = request[1] && !FinalizePayloadOnMain(request[1]==2) ? 104u : (ClearEvents() ? 0u : 104u); break;
            case UINT32_MAX - 1: status = RefreshEvents() ? 0u : 104u; break;
            default:
                status = !RefreshEvents() ? 104u : static_cast<DWORD>(runtime->Invoke(request[0], request[1], request[2]));
                break;
            }
            request[3] = runtime->Generation();
            pendingStatus.store(status, std::memory_order_release);
        }
    }
    return CallNextHookEx(nullptr, code, parameter, data);
}
BOOL CALLBACK FindMainWindow(HWND window, LPARAM candidatesAddress) {
    DWORD process = 0;
    const auto thread = GetWindowThreadProcessId(window, &process);
    if (process != GetCurrentProcessId() || !IsWindowVisible(window) || GetWindow(window, GW_OWNER)) {
        return TRUE;
    }
    wchar_t windowClass[64]{};
    if (!GetClassNameW(window, windowClass, 64) ||
        (std::wcscmp(windowClass, L"H5") != 0 && std::wcscmp(windowClass, L"H5UNI") != 0)) {
        return TRUE;
    }
    auto& candidates = *reinterpret_cast<std::vector<HWND>*>(candidatesAddress);
    candidates.push_back(window);
    return candidates.size() < 2;
}
bool AttachMainWindowHook() {
    if (mainHook || windowInputHook) { return mainHook && windowInputHook; }
    std::vector<HWND> candidates;
    EnumWindows(FindMainWindow, reinterpret_cast<LPARAM>(&candidates));
    if (candidates.size() != 1) { return false; }
    const auto window = candidates.front();
    DWORD process = 0;
    const auto thread = GetWindowThreadProcessId(window, &process);
    if (!thread || process != GetCurrentProcessId()) { return false; }
    const auto hook = SetWindowsHookExW(WH_CALLWNDPROC, MainThreadHook, nullptr, thread);
    if (!hook) { return false; }
    const auto input=SetWindowsHookExW(WH_GETMESSAGE,WindowInputHook,nullptr,thread);
    if (!input) {
        if (!UnhookWindowsHookEx(hook)) {
            // Retain ownership of a callback whose rollback is unconfirmed.
            mainWindow=window;mainHook=hook;windowThread=thread;
        }
        return false;
    }
    mainWindow = window; mainHook = hook; windowInputHook=input; windowThread = thread;
    return true;
}
DWORD WINAPI HookOwner(void*) {
    for (unsigned attempt = 0; attempt < 100 && !mainHook; ++attempt) {
        AttachMainWindowHook();
        if (!mainHook) { Sleep(25); }
    }
    SetEvent(hookReady);
    WaitForSingleObject(hookStop, INFINITE);
    if (windowInputHook && !UnhookWindowsHookEx(windowInputHook)) { return 103; }
    windowInputHook=nullptr;
    if (mainHook && !UnhookWindowsHookEx(mainHook)) { return 103; }
    mainHook = nullptr; mainWindow = nullptr;
    return 0;
}
DWORD SendToMain(uint32_t* request, bool exitRequest = false, bool consoleRequest = false, bool selectorRequest = false) {
    pendingStatus = 102;
    pending.store(request, std::memory_order_release);
    SendMessageW(mainWindow, selectorRequest ? selectorMessage : (consoleRequest ? consoleMessage :
        (exitRequest ? exitMessage : dispatchMessage)), 0, reinterpret_cast<LPARAM>(request));
    pending = nullptr;
    return pendingStatus.load(std::memory_order_acquire);
}
}
extern "C" DWORD WINAPI Heroes5PluginReplace(void* path) {
    Guard guard;
    PayloadTransitionGuard transition;
    if (!path) { return static_cast<DWORD>(heroes5_sdk::RuntimeResult::LoadFailed); }
    try {
        // The resident core keeps POD history alive across complete payload
        // unload and HMR; the bus never retains the payload's code pointers.
        heroes5_sdk::Diagnostics();
        if (!runtime) {
            RuntimeLifetimeGuard lifetime(false);
            runtime = new heroes5_sdk::PluginRuntime;
        }
        const auto nextPath = std::filesystem::absolute(static_cast<const wchar_t*>(path));
        // Payload-owned windows and callbacks must retire on their game thread
        // before Replace can unload the previous image. Core replacement already
        // uses this boundary; ordinary source HMR needs the same lifetime rule.
        if (mainHook && runtime->HasEngineFinalizer()) {
            uint32_t retirement[4]{UINT32_MAX, 2, 0, 0};
            if (SendToMain(retirement) != 0) { return 104; }
        }
        const auto replaced = runtime->Replace(nextPath);
        if (replaced != heroes5_sdk::RuntimeResult::Ok) {
            uint32_t refresh[4]{UINT32_MAX - 1, 0, 0, 0};
            if (mainHook && SendToMain(refresh) != 0) { return 104; }
            return static_cast<DWORD>(replaced);
        }
        if (mainHook) {
            uint32_t request[4]{UINT32_MAX - 1, 0, 0, 0};
            if (SendToMain(request) != 0) {
                // A partially activated candidate may own native UI/resources.
                // Retire it and clear its callbacks before any rollback unload.
                uint32_t retirement[4]{UINT32_MAX, 2, 0, 0};
                if (SendToMain(retirement) != 0 || activePath.empty() ||
                    runtime->Replace(activePath) != heroes5_sdk::RuntimeResult::Ok ||
                    SendToMain(request) != 0) { return 106; }
                return 104;
            }
        }
        activePath = nextPath;
        return 0;
    } catch (...) { return static_cast<DWORD>(heroes5_sdk::RuntimeResult::LoadFailed); }
}
// Four uint32 words: command, argument, result, generation. The calling developer
// client owns this buffer. No game API is invoked on this worker-thread boundary.
extern "C" DWORD WINAPI Heroes5PluginInvoke(void* memory) {
    Guard guard;
    if (!runtime || !memory) { return static_cast<DWORD>(heroes5_sdk::RuntimeResult::NotLoaded); }
    auto* request = static_cast<uint32_t*>(memory);
    const auto status = runtime->Invoke(request[0], request[1], request[2]);
    request[3] = runtime->Generation();
    return static_cast<DWORD>(status);
}
// Synchronous window-message boundary on this process's visible window thread.
// This proves thread affinity, not safety of arbitrary engine calls in every scene.
// If the window hangs, the developer client times out and retains remote code and
// request memory. Do not force-unload the resident bridge after this hook is used.
namespace {
DWORD EnsureMainThreadHook(bool withConsole = true) {
    if (!hookThread) {
        dispatchMessage = RegisterWindowMessageW(L"Heroes5ModDevkit.PluginDispatch.v1");
        exitMessage = RegisterWindowMessageW(L"Heroes5ModDevkit.RequestExit.v1");
        consoleMessage = RegisterWindowMessageW(L"Heroes5ModDevkit.ConsoleCommand.v1");
        selectorMessage = RegisterWindowMessageW(L"Heroes5ModDevkit.BankSelector.v1");
        if (!dispatchMessage || !exitMessage || !consoleMessage || !selectorMessage) { return 100; }
        hookReady = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        hookStop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (!hookReady || !hookStop) {
            if (hookReady) { CloseHandle(hookReady); }
            if (hookStop) { CloseHandle(hookStop); }
            hookReady = nullptr; hookStop = nullptr; return 100;
        }
        hookThread = CreateThread(nullptr, 0, HookOwner, nullptr, 0, nullptr);
        if (!hookThread) {
            CloseHandle(hookReady); CloseHandle(hookStop);
            hookReady = nullptr; hookStop = nullptr; return 100;
        }
    }
    if (WaitForSingleObject(hookReady, 4000) != WAIT_OBJECT_0) { return 101; }
    if (!mainHook || !windowInputHook || !IsWindow(mainWindow)) { return 101; }
    wchar_t consolePath[32768]{};
    if (withConsole && GetEnvironmentVariableW(L"XALKIT_CONSOLE_PATH", consolePath, 32768)) {
        ConsoleGuard consoleGuard;
        if (!consoleGuard.acquired) { return 111; }
        auto consoleModule = static_cast<HMODULE>(GetPropW(mainWindow, ActiveConsole));
        if (!consoleModule) {
            consoleModule = LoadLibraryExW(consolePath, nullptr,
                LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
            if (!consoleModule) { return 111; }
            if (!SetPropW(mainWindow, ActiveConsole, consoleModule)) { FreeLibrary(consoleModule); return 111; }
        }
        const auto attach = reinterpret_cast<DWORD (WINAPI*)(void*)>(GetProcAddress(consoleModule, "XalKitConsoleAttach"));
        const auto state = reinterpret_cast<DWORD (WINAPI*)(void*)>(GetProcAddress(consoleModule, "XalKitConsoleState"));
        HMODULE sdkModule = nullptr;
        if (!attach || !state || !GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, reinterpret_cast<LPCWSTR>(EnsureMainThreadHook), &sdkModule)) { return 111; }
        heroes5_sdk::ConsoleConnection connection;
        connection.gameWindow = mainWindow;
        connection.sdkModule = sdkModule;
        if (attach(&connection) != 0) { return 111; }
        const auto deadline = GetTickCount64() + 5000;
        while (state(nullptr) == 1 && GetTickCount64() < deadline) { Sleep(25); }
        if (state(nullptr) != 2) { return 111; }
    }
    return 0;
}
}
// Resource-only sessions use the SDK host without loading a native mod payload.
extern "C" DWORD WINAPI Heroes5PluginConnect(void* memory) {
    Guard guard;
    const auto mode = memory ? *static_cast<const uint32_t*>(memory) : 0u;
    if (mode > 2) { return ERROR_INVALID_PARAMETER; }
    if (mode == 2) { return runtime && runtime->Generation() == 0 && activePath.empty() ? 0 : 108; }
    if (mode == 1 && (hookThread || (runtime && runtime->Generation() != 0))) { return 108; }
    if (!runtime) {
        RuntimeLifetimeGuard lifetime(false);
        runtime = new (std::nothrow) heroes5_sdk::PluginRuntime;
        if (!runtime) { return ERROR_NOT_ENOUGH_MEMORY; }
    }
    if (mode == 1) { return 0; }
    const auto ready = EnsureMainThreadHook();
    if (ready != 0) { return ready; }
    uint32_t request[4]{UINT32_MAX - 1, 0, 0, 0};
    return SendToMain(request);
}
#pragma comment(linker, "/EXPORT:Heroes5PluginConnect=_Heroes5PluginConnect@4")
extern "C" DWORD WINAPI Heroes5PluginInvokeMain(void* memory) {
    Guard guard;
    if (!runtime || !memory) { return static_cast<DWORD>(heroes5_sdk::RuntimeResult::NotLoaded); }
    const auto ready = EnsureMainThreadHook();
    return ready != 0 ? ready : SendToMain(static_cast<uint32_t*>(memory));
}
extern "C" DWORD WINAPI Heroes5PluginRequestExit(void*) {
    Guard guard;
    const auto ready = EnsureMainThreadHook(false);
    if (ready != 0) { return ready; }
    uint32_t request[4]{};
    return SendToMain(request, true);
}
extern "C" DWORD WINAPI Heroes5PluginBankControlMain(void* memory) {
    Guard guard;
    if (!memory || !runtime || !hookThread || !mainHook || !IsWindow(mainWindow)) { return ERROR_NOT_READY; }
    return SendToMain(static_cast<uint32_t*>(memory), false, false, true);
}
#pragma comment(linker, "/EXPORT:Heroes5PluginBankControlMain=_Heroes5PluginBankControlMain@4")
extern "C" DWORD WINAPI Heroes5PluginConsoleCommand(void* memory) {
    Guard guard;
    if (!memory || !h5::hooks::ObserverMemory(reinterpret_cast<uintptr_t>(memory),
        sizeof(h5::ConsoleCommandRequest), PAGE_READWRITE)) { return ERROR_INVALID_PARAMETER; }
    const auto& command = *static_cast<const h5::ConsoleCommandRequest*>(memory);
    if (command.size != sizeof(command) || command.version != 1 || !command.text[0] ||
        wcsnlen_s(command.text, 4096) == 4096) { return ERROR_INVALID_PARAMETER; }
    // A borrowed command must never restart a stopped or replaced core.
    if (!hookThread || !mainHook || !IsWindow(mainWindow)) { return ERROR_NOT_READY; }
    uint32_t request[4]{0, static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&command)), 0, 0};
    return SendToMain(request, false, true);
}
#pragma comment(linker, "/EXPORT:Heroes5PluginConsoleCommand=_Heroes5PluginConsoleCommand@4")
extern "C" DWORD WINAPI Heroes5PluginGameEvent(void* memory) {
    Guard guard;
    if (!memory || !h5::hooks::ObserverMemory(reinterpret_cast<uintptr_t>(memory),
        sizeof(h5::ConsoleCommandRequest),PAGE_READWRITE)) { return ERROR_INVALID_PARAMETER; }
    if (!hookThread || !mainHook || !IsWindow(mainWindow)) { return ERROR_NOT_READY; }
    uint32_t request[4]{1,static_cast<uint32_t>(reinterpret_cast<uintptr_t>(memory)),0,0};
    return SendToMain(request,false,true);
}
#pragma comment(linker, "/EXPORT:Heroes5PluginGameEvent=_Heroes5PluginGameEvent@4")
extern "C" DWORD WINAPI Heroes5PluginGameScript(void* memory) {
    Guard guard;
    if (!memory || !h5::hooks::ObserverMemory(reinterpret_cast<uintptr_t>(memory),
        sizeof(h5::ConsoleCommandRequest),PAGE_READWRITE)) { return ERROR_INVALID_PARAMETER; }
    if (!hookThread || !mainHook || !IsWindow(mainWindow)) { return ERROR_NOT_READY; }
    uint32_t request[4]{2,static_cast<uint32_t>(reinterpret_cast<uintptr_t>(memory)),0,0};
    return SendToMain(request,false,true);
}
#pragma comment(linker, "/EXPORT:Heroes5PluginGameScript=_Heroes5PluginGameScript@4")
extern "C" DWORD WINAPI Heroes5PluginReplaceBank(void* memory) {
    Guard guard;
    if (!memory) { return ERROR_INVALID_PARAMETER; }
    auto& replacement = *static_cast<heroes5_sdk::BankReplaceRequest*>(memory);
    if (replacement.size != sizeof(replacement) || replacement.version != 1 || !replacement.path[0]
        || wcsnlen_s(replacement.path, 4096) == 4096) { return ERROR_INVALID_PARAMETER; }
    replacement.applied = 0; replacement.moduleReleased = 0;
    if (!hookThread || !mainHook || !IsWindow(mainWindow)) { return ERROR_NOT_READY; }
    HMODULE module = nullptr;
    HANDLE file = INVALID_HANDLE_VALUE;
    DWORD result = ERROR_INVALID_DATA;
    try {
        const auto path = std::filesystem::canonical(replacement.path);
        wchar_t executable[32768]{};
        const DWORD length = GetModuleFileNameW(nullptr, executable, 32768);
        if (!length || length >= 32768) { return ERROR_BAD_PATHNAME; }
        const auto gameBin = std::filesystem::canonical(executable).parent_path();
        const auto allowed = gameBin / L"Heroes5Mods/BankUpdates";
        if (std::filesystem::canonical(allowed) != allowed || path.parent_path() != allowed) { return ERROR_ACCESS_DENIED; }
        file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
        if (file == INVALID_HANDLE_VALUE) { return GetLastError(); }
        if (path.filename().string() != h5::Sha256(file) + ".dll") { result = ERROR_INVALID_DATA; }
        else {
            module = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
            using Query = const heroes5_sdk::BankSelectorPayload* (__cdecl*)();
            const auto query = module ? reinterpret_cast<Query>(GetProcAddress(module, "Heroes5BankSelectorQuery")) : nullptr;
            const auto* payload = query ? query() : nullptr;
            using heroes5_sdk::SelectorModuleBytes;
            const bool valid = SelectorModuleBytes(module, payload, sizeof(heroes5_sdk::BankSelectorPayload))
                && payload->size == sizeof(*payload) && payload->version == 1 && payload->codeBytes && payload->codeBytes <= 4096
                && payload->dataBytes && payload->dataBytes <= 65536 && payload->dataOffsetCount <= 16384
                && payload->codeFixupCount <= 1024 && payload->dataFixupCount <= 1024
                && SelectorModuleBytes(module, payload->code, payload->codeBytes)
                && SelectorModuleBytes(module, payload->initialData, payload->dataBytes)
                && SelectorModuleBytes(module, payload->dataOffsets, payload->dataOffsetCount * sizeof(uint32_t))
                && SelectorModuleBytes(module, payload->codeFixups, payload->codeFixupCount * sizeof(heroes5_sdk::SelectorFixup))
                && SelectorModuleBytes(module, payload->dataFixups, payload->dataFixupCount * sizeof(heroes5_sdk::SelectorFixup))
                && SelectorModuleBytes(module, payload->packageSha256, 65);
            if (valid && payload->packageSha256[64] == '\0' && strspn(payload->packageSha256, "0123456789abcdef") == 64
                && h5::Sha256(gameBin.parent_path() / L"UserMODs/workshop-army-reference.h5u") == payload->packageSha256) {
                using Control = DWORD (WINAPI*)(void*);
                const auto loader = GetModuleHandleW((gameBin / L"dinput8.dll").c_str());
                const auto control = loader ? reinterpret_cast<Control>(GetProcAddress(loader, "Heroes5BankSelectorControl")) : nullptr;
                if (control) {
                    result=heroes5_sdk::ApplySelectorPayload(control,*payload);
                    replacement.applied=result==ERROR_SUCCESS;
                } else { result = ERROR_NOT_SUPPORTED; }
            }
        }
    } catch (const std::exception&) { result = ERROR_INVALID_DATA; }
    if (module) {
        if (FreeLibrary(module)) { replacement.moduleReleased = 1; }
        else { result = ERROR_BUSY; }
    }
    if (file != INVALID_HANDLE_VALUE) { CloseHandle(file); }
    return result;
}
#pragma comment(linker, "/EXPORT:Heroes5PluginReplaceBank=_Heroes5PluginReplaceBank@4")
extern "C" DWORD WINAPI Heroes5PluginReplaceConsole(void* memory) {
    Guard guard;
    if (!memory) { return ERROR_INVALID_PARAMETER; }
    const auto ready = EnsureMainThreadHook(false);
    if (ready) { return ready; }
    ConsoleGuard consoleGuard;
    if (!consoleGuard.acquired) { return ERROR_BUSY; }
    wchar_t initialPath[32768]{};
    if (!GetEnvironmentVariableW(L"XALKIT_CONSOLE_PATH", initialPath, 32768)) { return ERROR_NOT_SUPPORTED; }
    HMODULE candidate = nullptr;
    HANDLE candidateFile = INVALID_HANDLE_VALUE;
    try {
        const auto path = std::filesystem::canonical(static_cast<const wchar_t*>(memory));
        const auto root = std::filesystem::canonical(initialPath).parent_path().parent_path();
        if (path.filename() != L"XalKitConsole.dll" || path.parent_path().parent_path() != root) {
            return ERROR_ACCESS_DENIED;
        }
        candidateFile = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
        if (candidateFile == INVALID_HANDLE_VALUE) { return GetLastError(); }
        const auto expectedDirectory = "generation-" + h5::Sha256(candidateFile).substr(0, 16);
        if (path.parent_path().filename().string() != expectedDirectory) {
            CloseHandle(candidateFile); return ERROR_INVALID_DATA;
        }
        candidate = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        CloseHandle(candidateFile); candidateFile = INVALID_HANDLE_VALUE;
        if (!candidate) { return GetLastError(); }
        using Entry = DWORD (WINAPI*)(void*);
        const auto attach = reinterpret_cast<Entry>(GetProcAddress(candidate, "XalKitConsoleAttach"));
        const auto state = reinterpret_cast<Entry>(GetProcAddress(candidate, "XalKitConsoleState"));
        const auto detach = reinterpret_cast<Entry>(GetProcAddress(candidate, "XalKitConsoleDetach"));
        const auto abi = reinterpret_cast<Entry>(GetProcAddress(candidate, "XalKitConsoleAbi"));
        const auto previous = static_cast<HMODULE>(GetPropW(mainWindow, ActiveConsole));
        if (!attach || !state || !detach || !abi || abi(nullptr) != 1 || !previous) {
            FreeLibrary(candidate); return ERROR_BAD_FORMAT;
        }
        if (candidate == previous) { FreeLibrary(candidate); return 0; }
        const auto oldDetach = reinterpret_cast<Entry>(GetProcAddress(previous, "XalKitConsoleDetach"));
        const auto oldAttach = reinterpret_cast<Entry>(GetProcAddress(previous, "XalKitConsoleAttach"));
        const auto oldState = reinterpret_cast<Entry>(GetProcAddress(previous, "XalKitConsoleState"));
        if (!oldDetach || !oldAttach || !oldState) { FreeLibrary(candidate); return ERROR_NOT_SUPPORTED; }
        const auto stopped = oldDetach(nullptr);
        if (stopped) { FreeLibrary(candidate); return stopped; }
        heroes5_sdk::ConsoleConnection connection;
        connection.gameWindow = mainWindow;
        GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                          reinterpret_cast<LPCWSTR>(Heroes5PluginReplaceConsole), &connection.sdkModule);
        auto activated = attach(&connection);
        const auto deadline = GetTickCount64() + 5000;
        while (!activated && state(nullptr) == 1 && GetTickCount64() < deadline) { Sleep(25); }
        if (!activated && state(nullptr) == 2 && SetPropW(mainWindow, ActiveConsole, candidate)) {
            FreeLibrary(previous); return 0;
        }
        activated = activated ? activated : ERROR_DLL_INIT_FAILED;
        if (detach(nullptr) != 0) {
            // Candidate teardown is uncertain: retain both images and prohibit
            // another old UI from starting through another core generation.
            SetPropW(mainWindow, ActiveConsole, candidate);
            return ERROR_TIMEOUT;
        }
        FreeLibrary(candidate); candidate = nullptr;
        const auto rollback = oldAttach(&connection);
        const auto rollbackDeadline = GetTickCount64() + 5000;
        while (!rollback && oldState(nullptr) == 1 && GetTickCount64() < rollbackDeadline) { Sleep(25); }
        return rollback || oldState(nullptr) != 2 ? ERROR_RECOVERY_FAILURE : activated;
    } catch (...) {
        if (candidateFile != INVALID_HANDLE_VALUE) { CloseHandle(candidateFile); }
        // Unexpected failure can leave a live thread; retain a loaded candidate.
        return ERROR_INVALID_DATA;
    }
}
#pragma comment(linker, "/EXPORT:Heroes5PluginReplaceConsole=_Heroes5PluginReplaceConsole@4")
namespace {
DWORD StopRuntime(heroes5_sdk::PluginSnapshot* snapshot) {
    if (heroes5_sdk::PluginRuntime::CallingPayloadOnCurrentThread() ||
        (GetCurrentThreadId() == windowThread && nativeDispatchActive)) { return ERROR_BUSY; }
    Guard guard;
    PayloadTransitionGuard transition;
    const bool requiresFinalization=!snapshot && runtime && runtime->HasEngineFinalizer();
    if (!hookThread && (nativeHookSettings.count || nativeInterceptionSettings.count || requiresFinalization)) { return 104; }
    if (hookThread) {
        if (requiresFinalization && !mainHook) { return 104; }
        if (mainHook && (requiresFinalization || eventHook || resultWindow || engineSettings[0] || cameraInputSlot || nativeHookSettings.count || nativeInterceptionSettings.count)) {
            uint32_t request[4]{UINT32_MAX, snapshot ? 0u : 1u, 0, 0};
            if (SendToMain(request) != 0) { return 104; }
        }
        SetEvent(hookStop);
        if (WaitForSingleObject(hookThread, 4000) != WAIT_OBJECT_0) { return 103; }
        DWORD result = 103;
        if (!GetExitCodeThread(hookThread, &result) || result != 0) { return 103; }
        CloseHandle(hookThread); CloseHandle(hookReady); CloseHandle(hookStop);
        hookThread = nullptr; hookReady = nullptr; hookStop = nullptr;
    }
    if (snapshot) {
        if (!runtime) { return static_cast<DWORD>(heroes5_sdk::RuntimeResult::NotLoaded); }
        const auto result = runtime->ExportState(*snapshot);
        if (result == heroes5_sdk::RuntimeResult::NotLoaded && runtime->Generation() == 0) {
            *snapshot = heroes5_sdk::PluginSnapshot{};
        } else if (result != heroes5_sdk::RuntimeResult::Ok) { return static_cast<DWORD>(result); }
    }
    {
        RuntimeLifetimeGuard lifetime(false);
        delete runtime;
        runtime = nullptr;
    }
    activePath.clear();
    return 0;
}
}
extern "C" DWORD WINAPI Heroes5PluginStop(void*) { return StopRuntime(nullptr); }
// Legacy controllers allocate the smaller snapshot v1. Refuse without touching
// their buffer or retiring active code. Versioned exports fix the wire size.
extern "C" DWORD WINAPI Heroes5PluginSuspend(void*) {
    return static_cast<DWORD>(heroes5_sdk::RuntimeResult::IncompatibleState);
}
extern "C" DWORD WINAPI Heroes5PluginRestore(void*) {
    return static_cast<DWORD>(heroes5_sdk::RuntimeResult::IncompatibleState);
}
#pragma comment(linker, "/EXPORT:Heroes5PluginSuspend=_Heroes5PluginSuspend@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginRestore=_Heroes5PluginRestore@4")
extern "C" DWORD WINAPI Heroes5PluginSuspendV2(void* memory) {
    if (!memory) { return static_cast<DWORD>(heroes5_sdk::RuntimeResult::IncompatibleState); }
    return StopRuntime(static_cast<heroes5_sdk::PluginSnapshot*>(memory));
}
extern "C" DWORD WINAPI Heroes5PluginRestoreV2(void* memory) {
    if (heroes5_sdk::PluginRuntime::CallingPayloadOnCurrentThread() ||
        (GetCurrentThreadId() == windowThread && nativeDispatchActive)) { return ERROR_BUSY; }
    Guard guard;
    if (!runtime || !memory) { return static_cast<DWORD>(heroes5_sdk::RuntimeResult::NotLoaded); }
    // Restore before enabling callbacks; otherwise counters/game events could
    // be overwritten by an older snapshot while they are executing.
    if (mainHook || eventHook || engineSettings[0] || nativeHookSettings.count || nativeInterceptionSettings.count) { return 108; }
#ifdef H5_CORE_REJECT_RESTORE
    return static_cast<DWORD>(heroes5_sdk::RuntimeResult::IncompatibleState);
#endif
#ifdef H5_CORE_REJECT_SLOT
    if (uiSlot == H5_CORE_REJECT_SLOT) { return static_cast<DWORD>(heroes5_sdk::RuntimeResult::IncompatibleState); }
#endif
    if (runtime->Generation() == 0 && activePath.empty()) {
        const auto& snapshot = *static_cast<const heroes5_sdk::PluginSnapshot*>(memory);
        const heroes5_sdk::PluginSnapshot empty;
        return snapshot.magic == empty.magic && snapshot.version == empty.version &&
            snapshot.schema == 0 && snapshot.bytes == 0 && snapshot.generation == 0 &&
            snapshot.residentSchema == 0 && snapshot.residentBytes == 0 &&
            std::all_of(snapshot.data.begin(), snapshot.data.end(), [](unsigned char value) { return value == 0; })
            ? 0 : static_cast<DWORD>(heroes5_sdk::RuntimeResult::IncompatibleState);
    }
    return static_cast<DWORD>(runtime->ImportState(*static_cast<heroes5_sdk::PluginSnapshot*>(memory)));
}
extern "C" DWORD WINAPI Heroes5PluginPostEvent(void* memory) {
    Guard guard;
    if (!memory || !mainWindow || !eventSettings[0]) { return 105; }
    const auto argument = *static_cast<uint32_t*>(memory);
    return PostMessageW(mainWindow, eventSettings[0], argument, reinterpret_cast<LPARAM>(&eventSettings)) ? 0 : 105;
}
extern "C" DWORD WINAPI Heroes5PluginSetSlot(void* memory) {
    Guard guard;
    if (!memory || resultWindow || nativeHookSettings.count || nativeInterceptionSettings.count || *static_cast<uint32_t*>(memory) >= 64) { return 107; }
    uiSlot = *static_cast<uint32_t*>(memory);
    return 0;
}
extern "C" DWORD WINAPI Heroes5PluginCoreVersion(void*) { return H5_CORE_VERSION; }
extern "C" DWORD WINAPI Heroes5PluginReadDiagnostics(void* memory) {
    if (!memory) { return 110; }
    auto& request = *static_cast<heroes5_sdk::DiagnosticReadRequest*>(memory);
    if (request.size != sizeof(request) || request.version != 1 || request.reserved != 0) { return 110; }
    return heroes5_sdk::Diagnostics().Read(request.after, request.minimum, request.snapshot) ? 0 : 110;
}
#if H5_CORE_VERSION >= 2
#ifndef H5_CORE_FEATURE_RESULT
#define H5_CORE_FEATURE_RESULT 42
#endif
extern "C" DWORD WINAPI Heroes5PluginNewFeature(void*) { return H5_CORE_FEATURE_RESULT; }
#pragma comment(linker, "/EXPORT:Heroes5PluginNewFeature=_Heroes5PluginNewFeature@4")
#endif
// No initialization or teardown in DllMain. The developer client calls Stop
// before unloading; a resident game bridge will remain loaded until shutdown.
#pragma comment(linker, "/EXPORT:Heroes5PluginReplace=_Heroes5PluginReplace@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginInvoke=_Heroes5PluginInvoke@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginInvokeMain=_Heroes5PluginInvokeMain@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginStop=_Heroes5PluginStop@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginPostEvent=_Heroes5PluginPostEvent@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginSetSlot=_Heroes5PluginSetSlot@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginSuspendV2=_Heroes5PluginSuspendV2@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginRestoreV2=_Heroes5PluginRestoreV2@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginCoreVersion=_Heroes5PluginCoreVersion@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginReadDiagnostics=_Heroes5PluginReadDiagnostics@4")
#pragma comment(linker, "/EXPORT:Heroes5PluginRequestExit=_Heroes5PluginRequestExit@4")

#ifdef HEROES5_PLUGIN_RELEASE
extern "C" __declspec(dllexport) volatile LONG Heroes5PluginReleaseStatus = 0;
namespace {
DWORD WINAPI StartReleasedPlugin(void*) {
    HMODULE owner = nullptr;
    wchar_t filename[32768]{};
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
        GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(StartReleasedPlugin), &owner) ||
        !GetModuleFileNameW(owner, filename, 32768)) {
        InterlockedExchange(&Heroes5PluginReleaseStatus, -1); return 1;
    }
    // The release DLL contains both the stable bridge and the same payload source.
    // Loading itself retains a reference owned by PluginRuntime; no compiler/client.
    const auto loaded = Heroes5PluginReplace(filename);
    uint32_t request[4]{};
    const auto ready = loaded == 0 ? Heroes5PluginInvokeMain(request) : loaded;
    InterlockedExchange(&Heroes5PluginReleaseStatus, ready == 0 ? 2 : -static_cast<LONG>(ready + 1));
    return ready;
}
}
extern "C" DWORD __cdecl Heroes5PluginInstall() {
    if (InterlockedCompareExchange(&Heroes5PluginReleaseStatus, 1, 0) != 0) { return 1; }
    const auto thread = CreateThread(nullptr, 0, StartReleasedPlugin, nullptr, 0, nullptr);
    if (!thread) { InterlockedExchange(&Heroes5PluginReleaseStatus, -1); return 0; }
    CloseHandle(thread);
    return 1; // Accepted; status2 separately proves completed startup.
}
#pragma comment(linker, "/EXPORT:Heroes5PluginInstall=_Heroes5PluginInstall")
#endif
