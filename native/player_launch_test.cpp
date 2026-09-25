#include "player_launch.hpp"
#include <iostream>

int main() {
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
        std::cout << "Native launch boundaries passed; no game or dialogs opened.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
