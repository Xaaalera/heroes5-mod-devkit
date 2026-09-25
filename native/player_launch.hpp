#pragma once

#include <windows.h>
#include <commdlg.h>
#include <bcrypt.h>
#include <tlhelp32.h>
#include <array>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>

// Windows-only player launch support. No Python runtime or game-file writes.
namespace universe_player {

inline std::filesystem::path LauncherDirectory() {
    std::array<wchar_t, 32768> path{};
    const auto length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (length == 0 || length >= path.size()) {
        throw std::runtime_error("Cannot locate the launcher.");
    }
    return std::filesystem::path(path.data()).parent_path();
}

inline std::filesystem::path SelectGame() {
    const auto directory = LauncherDirectory();
    for (const auto& candidate : {directory / L"bin/H5_Game.exe", directory.parent_path() / L"bin/H5_Game.exe"}) {
        if (std::filesystem::is_regular_file(candidate)) {
            return std::filesystem::canonical(candidate);
        }
    }
    std::array<wchar_t, 32768> filename{};
    OPENFILENAMEW dialog{};
    dialog.lStructSize = sizeof(dialog);
    dialog.lpstrFilter = L"Heroes V Universe (H5_Game.exe)\0H5_Game.exe\0\0";
    dialog.lpstrFile = filename.data();
    dialog.nMaxFile = static_cast<DWORD>(filename.size());
    dialog.lpstrTitle = L"Select Heroes V Universe / bin / H5_Game.exe";
    dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
    if (!GetOpenFileNameW(&dialog)) {
        if (CommDlgExtendedError() != 0) {
            throw std::runtime_error("Cannot open the game selection dialog.");
        }
        return {}; // Cancel means no launch and no writes.
    }
    return std::filesystem::canonical(filename.data());
}

inline std::string Sha256(const std::filesystem::path& path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) { throw std::runtime_error("Cannot read file: " + path.filename().string()); }
    BCRYPT_ALG_HANDLE algorithm{};
    BCRYPT_HASH_HANDLE hash{};
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) != 0) {
        throw std::runtime_error("Cannot initialize SHA-256.");
    }
    if (BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) != 0) {
        BCryptCloseAlgorithmProvider(algorithm, 0);
        throw std::runtime_error("Cannot create SHA-256 hash.");
    }
    std::array<unsigned char, 65536> buffer{};
    bool valid = true;
    while (file) {
        file.read(reinterpret_cast<char*>(buffer.data()), buffer.size());
        if (file.gcount() && BCryptHashData(hash, buffer.data(), static_cast<ULONG>(file.gcount()), 0) != 0) {
            valid = false;
            break;
        }
    }
    std::array<unsigned char, 32> digest{};
    valid = valid && file.eof() && BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) == 0;
    BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    if (!valid) { throw std::runtime_error("Failed to hash file."); }
    std::string result;
    for (const auto value : digest) {
        result += "0123456789abcdef"[value >> 4];
        result += "0123456789abcdef"[value & 15];
    }
    return result;
}

inline void VerifyGame(const std::filesystem::path& executable) {
    if (_wcsicmp(executable.filename().c_str(), L"H5_Game.exe") != 0) {
        throw std::runtime_error("Select bin/H5_Game.exe from the supported Universe installation.");
    }
    const std::pair<const wchar_t*, const char*> binaries[] = {
        {L"H5_Game.exe", "88c9dc6107b9bced0649924a86360f1c56397ee00de0413f6f2b08f865ed5519"},
        {L"uni.dll", "aa5211151d9e9a8c135e180ff8832908d128ccae08a5145162bcdae4946c18ee"},
        {L"um.dll", "1956c00b371d22a3e1a644394ff3e7159b6ec36d660d5ffa36628fcf63fd0fc6"},
        {L"d3d9.dll", "5eb152357f99d53397b764384d5cf9a0f6aece733ced30a34186ac57fb15be25"},
    };
    for (const auto& [name, expected] : binaries) {
        if (Sha256(executable.parent_path() / name) != expected) {
            throw std::runtime_error("Unsupported Universe build. No game files were changed.");
        }
    }
}

inline void RequireGameClosed() {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) { throw std::runtime_error("Cannot check running games."); }
    PROCESSENTRY32W entry{sizeof(entry)};
    bool running = false;
    if (!Process32FirstW(snapshot, &entry)) {
        CloseHandle(snapshot);
        throw std::runtime_error("Cannot enumerate running processes.");
    }
    do {
        if (_wcsicmp(entry.szExeFile, L"H5_Game.exe") == 0 || _wcsicmp(entry.szExeFile, L"H5_MapEditor.exe") == 0) {
            running = true;
        }
    } while (Process32NextW(snapshot, &entry));
    CloseHandle(snapshot);
    if (running) { throw std::runtime_error("Close Heroes V and its map editor before launching this mod."); }
}

} // namespace universe_player
