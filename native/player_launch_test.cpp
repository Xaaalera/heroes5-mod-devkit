#include "player_launch.hpp"
#include <iostream>
#define DIRECTINPUT_VERSION 0x0800
#include <dinput.h>

int wmain(int count, wchar_t* arguments[]) {
    std::filesystem::path directory;
    try {
        std::array<wchar_t, MAX_PATH> temporary{};
        if (!GetTempPathW(static_cast<DWORD>(temporary.size()), temporary.data())) { return 1; }
        directory = std::filesystem::path(temporary.data()) / (L"h5-native-check-" + std::to_wstring(GetCurrentProcessId()));
        if (!std::filesystem::create_directory(directory)) { return 1; }
        const auto sample = directory / L"hash-input.txt";
        { std::ofstream output(sample, std::ios::binary); output << "abc"; }
        if (universe_player::Sha256(sample) != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") {
            throw std::runtime_error("SHA-256 known vector mismatch");
        }
        bool rejected = false;
        try { universe_player::Sha256(directory / L"missing"); }
        catch (const std::runtime_error&) { rejected = true; }
        if (!rejected) { throw std::runtime_error("Missing file accepted"); }
        rejected = false;
        try { universe_player::VerifyGame(sample); }
        catch (const std::runtime_error&) { rejected = true; }
        if (!rejected) { throw std::runtime_error("Wrong executable name accepted"); }
        const auto executable = directory / L"H5_Game.exe";
        { std::ofstream output(executable, std::ios::binary); output << "not a game"; }
        rejected = false;
        try { universe_player::VerifyGame(executable); }
        catch (const std::runtime_error&) { rejected = true; }
        if (!rejected) { throw std::runtime_error("Unsupported executable accepted"); }
        if (universe_player::Sha256(sample) != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") {
            throw std::runtime_error("Validation changed a file");
        }
        if (!std::filesystem::is_directory(universe_player::LauncherDirectory())) {
            throw std::runtime_error("Invalid launcher directory");
        }
        std::filesystem::remove(executable);
        std::filesystem::remove(sample);
        std::filesystem::remove(directory);
        if (count == 2) {
            const auto library = LoadLibraryW(arguments[1]);
            if (!library) { throw std::runtime_error("Cannot load the input forwarder."); }
            using CreateInput = HRESULT (WINAPI*)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);
            const auto createInput = reinterpret_cast<CreateInput>(GetProcAddress(library, "DirectInput8Create"));
            const auto state = reinterpret_cast<const LONG*>(GetProcAddress(library, "Heroes5ModsStatus"));
            IDirectInput8W* input = nullptr;
            if (!createInput || !state || FAILED(createInput(GetModuleHandleW(nullptr), DIRECTINPUT_VERSION,
                IID_IDirectInput8W, reinterpret_cast<void**>(&input), nullptr)) || !input) {
                throw std::runtime_error("Forwarded DirectInput factory failed.");
            }
            input->Release();
            if (*state != 0) { throw std::runtime_error("Mods initialized inside a non-game test host."); }
            FreeLibrary(library);
        }
        std::cout << "Native launch boundaries passed; no game or dialogs opened.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
