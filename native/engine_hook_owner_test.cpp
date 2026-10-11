#include "engine_hook_owner.hpp"
#include "engine_hook_protocol.hpp"
#include <iostream>
#include <stdexcept>
#include <thread>
void Require(bool condition, const char* message) {
    if (!condition) { throw std::runtime_error(message); }
}
uint32_t interceptionMode = 1, interceptionCalls = 0, interceptedArgument = 0;
__declspec(code_seg(".xkitrw")) __declspec(naked) uint32_t AlreadyWritableImageTarget() {
    __asm {
        mov eax, 42
        nop
        nop
        ret
    }
}
uint32_t __cdecl Intercept(heroes5_sdk::EngineCall* call) {
    ++interceptionCalls;
    __asm { fninit }
    interceptedArgument = *reinterpret_cast<const uint32_t*>(call->frame.savedEsp + 8);
    if (interceptionMode == 2) {
        call->frame.eax = 100;
        call->action = heroes5_sdk::EngineCallAction::Branch;
        return 1;
    }
    call->frame.ebp = 0xdeadbeef;
    call->frame.eax = 123;
    call->result = 42;
    call->action = interceptionMode == 4 ? static_cast<heroes5_sdk::EngineCallAction>(99) :
        heroes5_sdk::EngineCallAction::ReturnEax;
    return interceptionMode == 3 ? 0u : 1u;
}

int wmain(int count, wchar_t** arguments) {
    try {
        heroes5_sdk::EngineHookRequestV2 transport;
        Require(transport.base.version == 2 && transport.base.size == sizeof(transport) &&
            transport.expectedBytes == 0, "V2 transport defaults are incorrect");
        for (const auto value : transport.expected) { Require(value == 0, "V2 signature tail not cleared"); }
        Require(heroes5_sdk::EngineImageSite(reinterpret_cast<uintptr_t>(&wmain), 5),
            "Own executable code rejected by image admission");
        Require(!heroes5_sdk::EngineImageSite(reinterpret_cast<uintptr_t>(&transport), 5) &&
            !heroes5_sdk::EngineImageSite(0, 5) &&
            !heroes5_sdk::EngineImageSite(reinterpret_cast<uintptr_t>(&wmain), 4) &&
            !heroes5_sdk::EngineImageSite(reinterpret_cast<uintptr_t>(&wmain), 17),
            "Invalid image/signature range admitted");
        Require(count == 3, "Expected two callback DLLs");
        const auto window = CreateWindowExW(0, L"STATIC", L"Owned hook fixture", 0,
            0, 0, 1, 1, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
        Require(window != nullptr, "Owned fixture window unavailable");
        auto* owner = new heroes5_sdk::EngineHookOwner(window);
        auto* code = static_cast<unsigned char*>(VirtualAlloc(nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
        Require(code != nullptr, "Fixture code allocation failed");
        constexpr unsigned char original[]{0x8b, 0x44, 0x24, 0x04, 0x83, 0xc0, 0x05, 0xc3};
        memcpy(code, original, sizeof(original));
        DWORD protection = 0;
        Require(VirtualProtect(code, 4096, PAGE_EXECUTE_READ, &protection) &&
            FlushInstructionCache(GetCurrentProcess(), code, sizeof(original)), "Fixture code sealing failed");
        const auto site = reinterpret_cast<uintptr_t>(code);
        const auto function = reinterpret_cast<uintptr_t (__cdecl*)(uint32_t*)>(code);
        uint32_t calls = 0;
        const auto expectedResult = reinterpret_cast<uintptr_t>(&calls) + 5;
        auto wrong = std::array<unsigned char, 7>{};
        Require(!owner->Prepare(site, wrong), "Wrong signature accepted");
        auto* observers = owner->Prepare(site, std::span(original, 7));
        Require(observers && owner->Prepare(site, std::span(original, 7)) == observers, "Resident hook preparation failed");
        observers->codeDigest[0] ^= 1;
        Require(!owner->SetEnabled(site, true) && memcmp(code, original, sizeof(original)) == 0,
            "Damaged prepared seal enabled a game patch");
        observers->codeDigest[0] ^= 1;
        using Callback = void (__cdecl*)(const h5::hooks::X86ObserverFrame*);
        for (unsigned generation = 0; generation < 2; ++generation) {
            const auto module = LoadLibraryW(arguments[generation + 1]);
            Require(module != nullptr, "Callback DLL load failed");
            const auto callback = reinterpret_cast<Callback>(GetProcAddress(module, "XkitFixtureObserver"));
            auto* slot = h5::hooks::AddContextObserver(*observers, callback);
            Require(slot && owner->SetEnabled(site, true), "MinHook attach failed");
            Require(function(&calls) == expectedResult && calls == (generation ? 303u : 101u),
                "Callback generation or relocated original failed");
            bool foreignEnable = true;
            uintptr_t foreignResult = 0;
            std::thread worker([&] { foreignEnable = owner->SetEnabled(site, false); foreignResult = function(&calls); });
            worker.join();
            Require(!foreignEnable && foreignResult == expectedResult && calls == (generation ? 303u : 101u),
                "Foreign thread entered callback or changed hook ownership");
            LONG foreignSlot = 0;
            Require(!owner->Retire(site, &foreignSlot, callback), "Foreign slot ownership accepted");
            Require(owner->Retire(site, slot, callback), "Callback retirement failed");
            Require(FreeLibrary(module) && !GetModuleHandleW(arguments[generation + 1]), "Callback DLL remained loaded");
            Require(function(&calls) == expectedResult && calls == (generation ? 303u : 101u),
                "Retired callback executed or original continuation stopped");
        }
        Require(owner->SetEnabled(site, false) && memcmp(code, original, sizeof(original)) == 0,
            "Original site bytes not restored");
        Require(function(&calls) == expectedResult, "Disabled hook changed original result");
        heroes5_sdk::EngineCallPolicy policy{1,0,site+7};
        Require(!owner->SetInterceptor(site,{1,65,0},Intercept), "Invalid fixed stack cleanup accepted");
        Require(!owner->SetInterceptor(site,{1,0,site+4},Intercept), "Branch inside displaced instructions accepted");
        auto* interception = owner->SetInterceptor(site,policy,Intercept);
        Require(interception && !owner->CanObserve(site) && !owner->SetInterceptor(site,policy,Intercept),
            "Exclusive interceptor admission failed");
        Require(owner->SetEnabled(site,true), "Interceptor enable failed");
        const auto beforeIntercept = interceptionCalls;
        unsigned short originalControl=0,capturedControl=0x0c7f,afterControl=0;
        __asm { fnstcw originalControl }
        __asm { fninit }
        __asm { fldcw capturedControl }
        __asm { fld1 }
        __asm { fld1 }
        __asm { fld1 }
        __asm { fld1 }
        __asm { fld1 }
        __asm { fld1 }
        __asm { fld1 }
        __asm { fld1 }
        const auto interceptedResult=function(&calls);
        float restored[8]{};
        for (unsigned index=0;index<8;++index) {
            float value=0;
            __asm { fstp value }
            restored[index]=value;
        }
        __asm { fnstcw afterControl }
        __asm { fldcw originalControl }
        for (const auto value:restored) { Require(value==1.0f,"Interceptor corrupted full x87 caller stack"); }
        Require(afterControl==capturedControl,"Interceptor changed x87 caller control word");
        Require(interceptedResult==42 && interceptionCalls==beforeIntercept+1 &&
            interceptedArgument==reinterpret_cast<uint32_t>(&calls), "Return EAX or native argument frame failed");
        std::thread interceptedWorker([&] { Require(function(&calls)==expectedResult, "Worker intercepted instead of forwarded"); });
        interceptedWorker.join();
        Require(interceptionCalls==beforeIntercept+1, "Worker entered interceptor callback");
        interceptionMode=2;
        Require(function(&calls)==100, "Admitted native branch/register continuation failed");
        interceptionMode=3;
        Require(function(&calls)==expectedResult, "Refusal failed to restore original registers/continuation");
        interceptionMode=4;
        Require(function(&calls)==expectedResult, "Unsupported result changed native execution");
        DWORD inaccessibleProtection=0;
        Require(VirtualProtect(code,4096,PAGE_NOACCESS,&inaccessibleProtection), "No-access verification fixture failed");
        Require(!owner->SetEnabled(site,true) && !owner->Prepare(site,std::span(original,7)),
            "Inaccessible target was read/admitted during revalidation");
        Require(VirtualProtect(code,4096,PAGE_EXECUTE_READ,&inaccessibleProtection), "No-access fixture restore failed");
        Require(owner->RetireInterceptor(site,interception,Intercept) && owner->CanObserve(site), "Interceptor retirement failed");
        Require(function(&calls)==expectedResult && owner->SetEnabled(site,false), "Retired interceptor did not forward");

        DWORD stdcallProtection=0;
        Require(VirtualProtect(code,4096,PAGE_READWRITE,&stdcallProtection), "Stdcall fixture writable page failed");
        constexpr unsigned char stdcallBytes[]{0x8b,0x44,0x24,0x04,0x03,0x44,0x24,0x08,0xc2,0x08,0x00};
        memcpy(code+64,stdcallBytes,sizeof(stdcallBytes));
        Require(VirtualProtect(code,4096,PAGE_EXECUTE_READ,&stdcallProtection) &&
            FlushInstructionCache(GetCurrentProcess(),code+64,sizeof(stdcallBytes)), "Stdcall fixture sealing failed");
        const auto stdcallSite=reinterpret_cast<uintptr_t>(code+64);
        const auto stdcallFunction=reinterpret_cast<uint32_t (__stdcall*)(uint32_t,uint32_t)>(code+64);
        Require(owner->Prepare(stdcallSite,std::span(stdcallBytes,8)) && stdcallFunction(7,9)==16,
            "Stdcall original preparation failed");
        auto* stdcallSlot=owner->SetInterceptor(stdcallSite,{1,8,0},Intercept);
        Require(stdcallSlot && owner->SetEnabled(stdcallSite,true), "Stdcall interceptor attachment failed");
        interceptionMode=1;
        for (unsigned repeat=0;repeat<8;++repeat) { Require(stdcallFunction(7,9)==42,"Fixed callee stack cleanup failed"); }
        Require(owner->RetireInterceptor(stdcallSite,stdcallSlot,Intercept) && stdcallFunction(7,9)==16 &&
            owner->SetEnabled(stdcallSite,false), "Stdcall retirement/forwarding failed");
        constexpr unsigned char writableOriginal[]{0xb8,42,0,0,0,0x90,0x90};
        auto* writableHeap=static_cast<unsigned char*>(VirtualAlloc(nullptr,4096,MEM_COMMIT|MEM_RESERVE,PAGE_EXECUTE_READWRITE));
        Require(writableHeap!=nullptr,"Writable heap fixture allocation failed");
        memcpy(writableHeap,writableOriginal,sizeof(writableOriginal));
        Require(!owner->Prepare(reinterpret_cast<uintptr_t>(writableHeap),writableOriginal),
            "Unverified writable heap admitted as executable image");
        VirtualFree(writableHeap,0,MEM_RELEASE);
        const auto writableImageSite=reinterpret_cast<uintptr_t>(AlreadyWritableImageTarget);
        DWORD imageProtection=0;
        Require(VirtualProtect(reinterpret_cast<void*>(writableImageSite),sizeof(writableOriginal),
            PAGE_EXECUTE_READWRITE,&imageProtection),"Already-writable executable fixture preparation failed");
        // Touch the mapped image before subscribing, as an earlier game patch
        // does. Protection alone can remain copy-on-write until the first write.
        memcpy(reinterpret_cast<void*>(writableImageSite),writableOriginal,sizeof(writableOriginal));
        Require(FlushInstructionCache(GetCurrentProcess(),reinterpret_cast<void*>(writableImageSite),
            sizeof(writableOriginal)),"Writable executable fixture cache flush failed");
        MEMORY_BASIC_INFORMATION writableRegion{};
        Require(VirtualQuery(reinterpret_cast<void*>(writableImageSite),&writableRegion,sizeof(writableRegion)) &&
            writableRegion.Protect==PAGE_EXECUTE_READWRITE,"Writable executable fixture did not retain requested protection");
        Require(heroes5_sdk::EngineImageSite(writableImageSite,sizeof(writableOriginal)) &&
            owner->Prepare(writableImageSite,writableOriginal)!=nullptr,
            "Verified already-writable main executable rejected");
        Require(owner->SetEnabled(writableImageSite,true) && AlreadyWritableImageTarget()==42 &&
            owner->SetEnabled(writableImageSite,false),"Already-writable executable continuation/retirement failed");
        DWORD restoredProtection=0;
        Require(VirtualProtect(reinterpret_cast<void*>(writableImageSite),sizeof(writableOriginal),
            imageProtection,&restoredProtection),"Writable executable protection restore failed");
        const auto imageModule = LoadLibraryW(arguments[1]);
        const auto imageTarget = imageModule ? reinterpret_cast<uintptr_t (__cdecl*)(uint32_t*)>(
            GetProcAddress(imageModule, "XkitFixtureImageTarget")) : nullptr;
        Require(imageTarget != nullptr, "Image fixture target missing");
        const auto imageSite = reinterpret_cast<uintptr_t>(imageTarget);
        Require(!heroes5_sdk::EngineImageSite(imageSite, 7), "Foreign DLL code admitted as main-game image");
        Require(VirtualProtect(reinterpret_cast<void*>(imageSite), sizeof(original), PAGE_EXECUTE_WRITECOPY, &protection),
            "Image copy-on-write fixture preparation failed");
        Require(owner->Prepare(imageSite, std::span(original, 7)) != nullptr, "Copy-on-write hook prepare failed");
        Require(owner->SetEnabled(imageSite, true), "Copy-on-write hook enable failed");
        Require(imageTarget(&calls) == expectedResult, "Copy-on-write hook continuation failed");
        Require(owner->SetEnabled(imageSite, false), "Copy-on-write hook disable failed");
        MEMORY_BASIC_INFORMATION imageRegion{};
        Require(VirtualQuery(reinterpret_cast<void*>(imageSite), &imageRegion, sizeof(imageRegion)) &&
            imageRegion.Protect == PAGE_EXECUTE_READWRITE &&
            memcmp(reinterpret_cast<void*>(imageSite), original, sizeof(original)) == 0,
            "Private COW image transition or restored bytes were incorrect");
        Require(FreeLibrary(imageModule) != 0, "Image fixture cleanup failed");
        Require(DestroyWindow(window) != 0 && !owner->SetEnabled(site, true), "Destroyed window retained admission");
        // Backend/registry/code stay resident until this isolated process exits.
        std::cout << "PASS: real MinHook relocation, worker exclusion, two DLL callbacks and post-unload forwarding\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
