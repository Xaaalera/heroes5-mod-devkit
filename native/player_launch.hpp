#pragma once
#include "../game-api/include/h5/build.hpp"

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

using h5::Sha256;
using h5::VerifyGame;

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
