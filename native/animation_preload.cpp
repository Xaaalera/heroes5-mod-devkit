#include <winsock2.h>
#include <windows.h>
#include <array>
#include <string>

extern "C" __declspec(dllimport) void* GrannyInt32Type;
extern "C" __declspec(dllexport) DWORD __cdecl Heroes5AnimationDependencyReady() {
    return GrannyInt32Type != nullptr;
}

namespace {
INIT_ONCE socketsOnce = INIT_ONCE_STATIC_INIT;
HMODULE systemSockets = nullptr;
BOOL CALLBACK LoadSystemSockets(PINIT_ONCE, PVOID, PVOID*) {
    std::array<wchar_t, MAX_PATH> directory{};
    const auto length = GetSystemDirectoryW(directory.data(), static_cast<UINT>(directory.size()));
    if (!length || length >= directory.size()) { return FALSE; }
    const auto path = std::wstring(directory.data(), length) + L"\\wsock32.dll";
    systemSockets = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    return systemSockets != nullptr;
}
template<typename Function> Function SystemFunction(const char* name) {
    const auto previousError = GetLastError();
    if (!InitOnceExecuteOnce(&socketsOnce, LoadSystemSockets, nullptr, nullptr)) {
        WSASetLastError(WSASYSNOTREADY); return nullptr;
    }
    const auto function = reinterpret_cast<Function>(GetProcAddress(systemSockets, name));
    if (!function) { WSASetLastError(WSASYSNOTREADY); return nullptr; }
    SetLastError(previousError);
    return function;
}
}

extern "C" int WSAAPI ForwardRecv(SOCKET socket, char* buffer, int bytes, int flags) {
    const auto function = SystemFunction<decltype(&recv)>("recv");
    return function ? function(socket, buffer, bytes, flags) : SOCKET_ERROR;
}
extern "C" int WSAAPI ForwardRecvFrom(SOCKET socket, char* buffer, int bytes, int flags,
    sockaddr* address, int* addressBytes) {
    const auto function = SystemFunction<decltype(&recvfrom)>("recvfrom");
    return function ? function(socket, buffer, bytes, flags, address, addressBytes) : SOCKET_ERROR;
}
extern "C" int WSAAPI ForwardGetSockOpt(SOCKET socket, int level, int option, char* value, int* bytes) {
    const auto function = SystemFunction<decltype(&getsockopt)>("getsockopt");
    return function ? function(socket, level, option, value, bytes) : SOCKET_ERROR;
}
extern "C" int WSAAPI ForwardSetSockOpt(SOCKET socket, int level, int option, const char* value, int bytes) {
    const auto function = SystemFunction<decltype(&setsockopt)>("setsockopt");
    return function ? function(socket, level, option, value, bytes) : SOCKET_ERROR;
}
