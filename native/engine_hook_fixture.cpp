#include "../game-api/include/h5/hooks.hpp"
extern "C" void __cdecl XkitFixtureObserver(const h5::hooks::X86ObserverFrame* frame) {
    auto* count = *reinterpret_cast<uint32_t**>(frame->savedEsp + 8);
    *count += FIXTURE_INCREMENT;
}
#pragma comment(linker, "/EXPORT:XkitFixtureObserver=_XkitFixtureObserver")
extern "C" __declspec(naked) uintptr_t __cdecl XkitFixtureImageTarget(uint32_t*) {
    __asm {
        mov eax, [esp + 4]
        add eax, 5
        ret
    }
}
#pragma comment(linker, "/EXPORT:XkitFixtureImageTarget=_XkitFixtureImageTarget")
