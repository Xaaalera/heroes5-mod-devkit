#include <winsock2.h>
#include <windows.h>
#include <iostream>
#include <cstring>
#include <array>
#include <string>
#include <stdexcept>

void Require(bool valid, const char* message) {
    if (!valid) { throw std::runtime_error(message); }
}
template<typename Function> Function Resolve(HMODULE library, const char* name) {
    const auto function = reinterpret_cast<Function>(GetProcAddress(library, name));
    Require(function != nullptr, name); return function;
}
int wmain(int count, wchar_t** arguments) {
    SOCKET receiver = INVALID_SOCKET, sender = INVALID_SOCKET;
    HMODULE library = nullptr;
    bool initialized = false;
    try {
        Require(count == 3, "Expected explicit proxy and test dependency paths");
        Require(GetModuleHandleW(L"granny2.dll") == nullptr, "Dependency was loaded before proxy");
        std::wstring fixturePath(arguments[2]);
        fixturePath.resize(fixturePath.find_last_of(L"\\/"));
        const auto searchDirectory = AddDllDirectory(fixturePath.c_str());
        Require(searchDirectory != nullptr, "Test dependency directory unavailable");
        library = LoadLibraryExW(arguments[1], nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
        RemoveDllDirectory(searchDirectory);
        Require(library != nullptr, "Proxy load failed");
        Require(GetModuleHandleW(L"granny2.dll") != nullptr, "Early dependency absent");
        Require(Resolve<DWORD (__cdecl*)()>(library, "Heroes5AnimationDependencyReady")() == 1, "Dependency anchor mismatch");
        std::array<wchar_t, MAX_PATH> systemDirectory{};
        Require(GetSystemDirectoryW(systemDirectory.data(), DWORD(systemDirectory.size())) != 0, "System path unavailable");
        const auto systemPath = std::wstring(systemDirectory.data()) + L"\\wsock32.dll";
        const auto original = LoadLibraryExW(systemPath.c_str(), nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
        Require(original != nullptr, "Original system sockets unavailable");
        const auto image = reinterpret_cast<const BYTE*>(original);
        const auto dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(image);
        const auto header = reinterpret_cast<const IMAGE_NT_HEADERS32*>(image + dos->e_lfanew);
        const auto exports = reinterpret_cast<const IMAGE_EXPORT_DIRECTORY*>(image + header->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT].VirtualAddress);
        const auto names = reinterpret_cast<const DWORD*>(image + exports->AddressOfNames);
        const auto ordinals = reinterpret_cast<const WORD*>(image + exports->AddressOfNameOrdinals);
        for (DWORD index = 0; index < exports->NumberOfNames; ++index) {
            const auto name = reinterpret_cast<const char*>(image + names[index]);
            const auto ordinal = static_cast<WORD>(exports->Base + ordinals[index]);
            const auto byName = GetProcAddress(library, name);
            Require(byName != nullptr && byName == GetProcAddress(library, MAKEINTRESOURCEA(ordinal)), "Export name/ordinal mismatch");
            if (std::strcmp(name, "recv") && std::strcmp(name, "recvfrom") &&
                std::strcmp(name, "getsockopt") && std::strcmp(name, "setsockopt")) {
                Require(byName == GetProcAddress(original, name), "Forwarded system target mismatch");
            }
        }
        const auto startup = Resolve<decltype(&WSAStartup)>(library, "WSAStartup");
        WSADATA data{};
        Require(startup(MAKEWORD(1, 1), &data) == 0, "Legacy socket initialization failed");
        initialized = true;
        const auto createSocket = Resolve<decltype(&socket)>(library, "socket");
        const auto bindSocket = Resolve<decltype(&bind)>(library, "bind");
        const auto getName = Resolve<decltype(&getsockname)>(library, "getsockname");
        const auto getOption = Resolve<decltype(&getsockopt)>(library, "getsockopt");
        const auto setOption = Resolve<decltype(&setsockopt)>(library, "setsockopt");
        const auto sendData = Resolve<decltype(&sendto)>(library, "sendto");
        const auto receiveData = Resolve<decltype(&recvfrom)>(library, "recvfrom");
        receiver = createSocket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        sender = createSocket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        Require(receiver != INVALID_SOCKET && sender != INVALID_SOCKET, "Socket creation failed");
        int socketType = 0, optionBytes = sizeof(socketType);
        Require(getOption(receiver, SOL_SOCKET, SO_TYPE, reinterpret_cast<char*>(&socketType), &optionBytes) == 0 &&
            socketType == SOCK_DGRAM, "Legacy getsockopt mismatch");
        DWORD timeout = 2000;
        Require(setOption(receiver, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout)) == 0,
            "Legacy setsockopt failed");
        sockaddr_in endpoint{};
        endpoint.sin_family = AF_INET; endpoint.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        Require(bindSocket(receiver, reinterpret_cast<sockaddr*>(&endpoint), sizeof(endpoint)) == 0, "Loopback bind failed");
        int addressBytes = sizeof(endpoint);
        Require(getName(receiver, reinterpret_cast<sockaddr*>(&endpoint), &addressBytes) == 0, "Bound address unavailable");
        const char payload[] = "sdk-local-check";
        Require(sendData(sender, payload, sizeof(payload), 0, reinterpret_cast<sockaddr*>(&endpoint), sizeof(endpoint)) == sizeof(payload),
            "Loopback send failed");
        char buffer[64]{};
        Require(receiveData(receiver, buffer, sizeof(buffer), 0, nullptr, nullptr) == sizeof(payload) &&
            std::memcmp(buffer, payload, sizeof(payload)) == 0, "Legacy receive mismatch");
        const auto closeSocket = Resolve<decltype(&closesocket)>(library, "closesocket");
        closeSocket(sender); sender = INVALID_SOCKET;
        closeSocket(receiver); receiver = INVALID_SOCKET;
        Resolve<decltype(&WSACleanup)>(library, "WSACleanup")(); initialized = false;
        std::cout << "PASS early dependency and legacy UDP loopback forwarding\n";
        FreeLibrary(library); return 0;
    } catch (const std::exception& error) {
        if (sender != INVALID_SOCKET) { closesocket(sender); }
        if (receiver != INVALID_SOCKET) { closesocket(receiver); }
        if (initialized) { WSACleanup(); }
        if (library) { FreeLibrary(library); }
        std::cerr << error.what() << '\n'; return 1;
    }
}
