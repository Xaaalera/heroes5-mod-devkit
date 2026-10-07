#include "player_launch.hpp"
#include "graphics_build_identity.hpp"
#include "plugin_runtime.hpp"
#include "../game-api/include/h5/console.hpp"
#include "diagnostic_bus.hpp"
#include "../game-api/include/h5/process.hpp"
#include <tlhelp32.h>
#include <cstring>
#include <iostream>
#include <sstream>

namespace {
void Require(bool value, const char* reason) {
    if (!value) { throw std::runtime_error(reason); }
}
uintptr_t RemoteModule(DWORD pid, const std::wstring& name,
    const std::filesystem::path* expectedPath = nullptr) {
    HANDLE snapshot = INVALID_HANDLE_VALUE;
    for (unsigned attempt = 0; attempt < 20; ++attempt) {
        snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
        if (snapshot != INVALID_HANDLE_VALUE) { break; }
        Require(GetLastError() == ERROR_BAD_LENGTH, "module_snapshot_failed");
        Sleep(25);
    }
    Require(snapshot != INVALID_HANDLE_VALUE, "module_snapshot_failed");
    MODULEENTRY32W entry{}; entry.dwSize = sizeof(entry);
    uintptr_t found = 0;
    if (Module32FirstW(snapshot, &entry)) {
        do {
            if (_wcsicmp(entry.szModule, name.c_str()) == 0) {
                if (expectedPath) {
                    std::error_code error;
                    if (!std::filesystem::equivalent(entry.szExePath, *expectedPath, error) || error) { continue; }
                }
                found = reinterpret_cast<uintptr_t>(entry.modBaseAddr); break;
            }
        } while (Module32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    Require(found != 0, "remote_module_missing");
    return found;
}
uintptr_t RemoteApi(DWORD pid, const char* name) {
    const auto address = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), name);
    Require(address != nullptr, "system_api_missing");
    HMODULE owner = nullptr;
    Require(GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
        GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, reinterpret_cast<LPCWSTR>(address), &owner),
        "system_api_owner_missing");
    wchar_t path[32768]{};
    Require(GetModuleFileNameW(owner, path, 32768) != 0, "system_api_path_missing");
    return RemoteModule(pid, std::filesystem::path(path).filename().wstring()) +
        reinterpret_cast<uintptr_t>(address) - reinterpret_cast<uintptr_t>(owner);
}
DWORD Call(HANDLE process, uintptr_t address, void* argument, const char* operation = "bridge_call",
    DWORD waitMilliseconds = 10000) {
    if (!process) { return reinterpret_cast<DWORD (WINAPI*)(void*)>(address)(argument); }
    const auto started = GetTickCount64();
    const bool tracedDispatch = std::strcmp(operation, "invoke_main") == 0 || std::strcmp(operation, "connect_sdk") == 0;
    if (tracedDispatch) {
        std::cerr << "{\"event\":\"remote_call_started\",\"operation\":\"" << operation << "\",\"pid\":"
                  << GetProcessId(process) << "}\n" << std::flush;
    }
    const auto thread = CreateRemoteThread(process, nullptr, 0,
        reinterpret_cast<LPTHREAD_START_ROUTINE>(address), argument, 0, nullptr);
    Require(thread != nullptr, "remote_call_failed");
    DWORD result = 0;
    const auto waitResult = WaitForSingleObject(thread, waitMilliseconds);
    const auto waitError = waitResult == WAIT_FAILED ? GetLastError() : 0;
    const auto finished = waitResult == WAIT_OBJECT_0;
    const auto obtained = finished && GetExitCodeThread(thread, &result);
    if (!obtained) {
        FILETIME created{}, exited{}, kernel{}, user{};
        const bool hasCreation = GetThreadTimes(thread, &created, &exited, &kernel, &user) != 0;
        const uint64_t creation = (uint64_t(created.dwHighDateTime) << 32) | created.dwLowDateTime;
        std::cerr << "{\"event\":\"remote_call_unconfirmed\",\"operation\":\"" << operation
                  << "\",\"pid\":" << GetProcessId(process) << ",\"thread_id\":" << GetThreadId(thread)
                  << ",\"thread_created\":";
        if (hasCreation) { std::cerr << '"' << creation << '"'; }
        else { std::cerr << "null"; }
        std::cerr << ",\"wait_result\":" << waitResult << ",\"wait_error\":" << waitError
                  << ",\"keep_remote_memory\":true}\n" << std::flush;
    }
    CloseHandle(thread);
    Require(obtained, "remote_call_unconfirmed_keep_code_loaded");
    if (tracedDispatch) {
        std::cerr << "{\"event\":\"remote_call_completed\",\"operation\":\"" << operation << "\",\"pid\":"
                  << GetProcessId(process) << ",\"result\":" << result
                  << ",\"duration_seconds\":" << (GetTickCount64() - started) / 1000.0
                  << "}\n" << std::flush;
    }
    return result;
}
std::wstring Wide(const std::string& text) {
    const auto size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.c_str(), -1, nullptr, 0);
    Require(size > 0, "invalid_utf8_path");
    std::wstring output(size, L'\0');
    Require(MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.c_str(), -1, output.data(), size) != 0,
        "invalid_utf8_path");
    output.resize(size - 1); return output;
}
template <typename Snapshot>
std::string EncodeSnapshot(const Snapshot& snapshot) {
    const auto* bytes = reinterpret_cast<const unsigned char*>(&snapshot);
    const char* digits = "0123456789abcdef";
    std::string output;
    output.reserve(sizeof(snapshot) * 2);
    for (size_t index = 0; index < sizeof(snapshot); ++index) {
        output += digits[bytes[index] >> 4];
        output += digits[bytes[index] & 15];
    }
    return output;
}
heroes5_sdk::PluginSnapshot DecodeSnapshot(const std::string& text) {
    heroes5_sdk::PluginSnapshot snapshot;
    Require(text.size() == sizeof(snapshot) * 2, "snapshot_size_mismatch");
    auto* bytes = reinterpret_cast<unsigned char*>(&snapshot);
    const auto nibble = [](char value) -> unsigned {
        if (value >= '0' && value <= '9') { return value - '0'; }
        if (value >= 'a' && value <= 'f') { return value - 'a' + 10; }
        throw std::runtime_error("snapshot_encoding_invalid");
    };
    for (size_t index = 0; index < sizeof(snapshot); ++index) {
        bytes[index] = static_cast<unsigned char>((nibble(text[index * 2]) << 4) | nibble(text[index * 2 + 1]));
    }
    return snapshot;
}
}

int wmain(int count, wchar_t** arguments) {
    HANDLE process = nullptr;
    try {
        Require(count == 3 || count == 6, "expected_local_bridge_or_owned_pid_creation_exe_bridge");
        const bool remote = count == 6;
        const bool diagnosticReader = remote && std::wstring(arguments[1]) == L"--owned-read";
        const bool consoleController = remote && std::wstring(arguments[1]) == L"--owned-console";
        const bool gameCommandController = remote && std::wstring(arguments[1]) == L"--owned-command";
        DWORD pid = 0;
        if (remote) {
            Require(diagnosticReader || consoleController || gameCommandController ||
                std::wstring(arguments[1]) == L"--owned", "owned_mode_required");
            pid = std::stoul(arguments[2]);
            process = h5::OpenOwnedProcess(pid, std::stoull(arguments[3]), arguments[4],
                PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION |
                PROCESS_VM_READ | PROCESS_VM_WRITE | SYNCHRONIZE, xkit::GraphicsFacadeSha256);
        } else { Require(std::wstring(arguments[1]) == L"--local", "local_mode_required"); }
        const auto bridgePath = std::filesystem::absolute(arguments[remote ? 5 : 2]);
        const auto local = LoadLibraryExW(bridgePath.c_str(), nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        Require(local != nullptr, "bridge_load_failed");
        for (const auto* name : {"Heroes5PluginReplace", "Heroes5PluginInvoke", "Heroes5PluginStop",
            "Heroes5PluginInvokeMain", "Heroes5PluginPostEvent", "Heroes5PluginSetSlot",
            "Heroes5PluginSuspend", "Heroes5PluginRestore", "Heroes5PluginCoreVersion"}) {
            Require(GetProcAddress(local, name) != nullptr, "bridge_export_missing");
        }
        uintptr_t base = reinterpret_cast<uintptr_t>(local);
        void* buffer = nullptr;
        if (remote) {
            buffer = VirtualAllocEx(process, nullptr, 65536, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
            Require(buffer != nullptr, "request_buffer_failed");
            const auto path = bridgePath.wstring(); SIZE_T written = 0;
            Require(WriteProcessMemory(process, buffer, path.c_str(), (path.size()+1)*2, &written), "path_write_failed");
            // An observed cold loader call exceeded the ordinary invocation budget.
            // Allow this one loader call more time without replaying it.
            base = diagnosticReader || consoleController || gameCommandController ?
                RemoteModule(pid, bridgePath.filename().wstring(), &bridgePath) :
                Call(process, RemoteApi(pid, "LoadLibraryW"), buffer, "load_bridge", 30000);
            Require(base != 0, "remote_bridge_load_failed");
            if (gameCommandController) {
                // Hold a module reference through dispatch. An uncertain call retains it.
                const auto held = Call(process, RemoteApi(pid, "LoadLibraryW"), buffer, "hold_command_core", 30000);
                Require(held == base, "command_core_changed_before_lease");
            }
        }
        const auto resolve = [&](const char* name) {
            const auto address = GetProcAddress(local, name);
            Require(address != nullptr, "bridge_export_missing");
            return base + reinterpret_cast<uintptr_t>(address) - reinterpret_cast<uintptr_t>(local);
        };
        const auto replace = resolve("Heroes5PluginReplace"), invoke = resolve("Heroes5PluginInvoke");
        const auto stop = resolve("Heroes5PluginStop");
        const auto invokeMain = resolve("Heroes5PluginInvokeMain");
        const auto postEvent = resolve("Heroes5PluginPostEvent");
        const auto setSlot = resolve("Heroes5PluginSetSlot");
        const auto suspend = resolve("Heroes5PluginSuspend");
        const auto restore = resolve("Heroes5PluginRestore");
        const auto coreVersion = resolve("Heroes5PluginCoreVersion");
        const auto connectExport = GetProcAddress(local, "Heroes5PluginConnect");
        const auto connectSdk = connectExport ? base + (reinterpret_cast<uintptr_t>(connectExport) -
            reinterpret_cast<uintptr_t>(local)) : 0;
        const auto consoleAddress = GetProcAddress(local, "Heroes5PluginReplaceConsole");
        const auto replaceConsole = consoleAddress ? base + reinterpret_cast<uintptr_t>(consoleAddress) - reinterpret_cast<uintptr_t>(local) : 0;
        const auto diagnosticsExport = GetProcAddress(local, "Heroes5PluginReadDiagnostics");
        const auto readDiagnostics = diagnosticsExport ? base + reinterpret_cast<uintptr_t>(diagnosticsExport) -
            reinterpret_cast<uintptr_t>(local) : 0;
        const auto exitExport = GetProcAddress(local, "Heroes5PluginRequestExit");
        const auto requestExit = exitExport ? base + reinterpret_cast<uintptr_t>(exitExport) -
            reinterpret_cast<uintptr_t>(local) : 0;
        const auto consoleExport = GetProcAddress(local, "Heroes5PluginConsoleCommand");
        const auto dispatchConsole = consoleExport ? base + reinterpret_cast<uintptr_t>(consoleExport) -
            reinterpret_cast<uintptr_t>(local) : 0;
        const auto featureAddress = GetProcAddress(local, "Heroes5PluginNewFeature");
        const auto feature = featureAddress ? base + reinterpret_cast<uintptr_t>(featureAddress) -
            reinterpret_cast<uintptr_t>(local) : 0;
        bool mainUsed = false;
        bool exitConfirmed = false;
        DWORD gameExitCode = STILL_ACTIVE;
        std::cout << "{\"ready\":true}\n" << std::flush;
        std::string line;
        while (std::getline(std::cin, line)) {
            if (line == "quit") { break; }
            Require(!diagnosticReader || line.rfind("trace ", 0) == 0, "diagnostic_reader_command_rejected");
            Require(!consoleController || line.rfind("console-replace ", 0) == 0, "console_controller_command_rejected");
            Require(!gameCommandController || line.rfind("console ", 0) == 0, "game_command_controller_command_rejected");
            DWORD status = 0; uint32_t request[4]{};
            std::string stateSnapshot;
            std::string diagnosticSnapshot;
            if (line.rfind("console ", 0) == 0) {
                mainUsed = true;
                const auto text = Wide(line.substr(8));
                Require(dispatchConsole && !text.empty() && text.size() < 4096 &&
                    text.find(L'\0') == std::wstring::npos, "invalid_console_command");
                h5::ConsoleCommandRequest command;
                std::copy(text.begin(), text.end(), command.text);
                if (remote) {
                    SIZE_T written = 0;
                    Require(WriteProcessMemory(process, buffer, &command, sizeof(command), &written) &&
                        written == sizeof(command), "console_command_write_failed");
                }
                status = Call(process, dispatchConsole, remote ? buffer : &command, "dispatch_console");
            } else if (line == "stop") {
                status = Call(process, stop, nullptr, "stop_payload");
            } else if (line == "connect" || line == "connect-prepare" || line == "connect-check") {
                mainUsed = true;
                uint32_t mode = line == "connect-check" ? 2 : (line == "connect-prepare" ? 1 : 0);
                if (remote) {
                    SIZE_T written = 0;
                    Require(WriteProcessMemory(process, buffer, &mode, sizeof(mode), &written) && written == sizeof(mode),
                        "connect_request_write_failed");
                }
                status = connectSdk ? Call(process, connectSdk, remote ? buffer : &mode, "connect_sdk") : 109;
            } else if (line == "exit") {
                mainUsed = true;
                status = requestExit ? Call(process, requestExit, nullptr, "request_exit") : 109;
                if (remote && status == 0) {
                    Require(WaitForSingleObject(process, 10000) == WAIT_OBJECT_0, "owned_game_exit_unconfirmed");
                    Require(GetExitCodeProcess(process, &gameExitCode) != 0, "owned_game_exit_code_failed");
                    exitConfirmed = true;
                }
            } else if (line.rfind("trace ", 0) == 0) {
                heroes5_sdk::DiagnosticReadRequest diagnostics;
                uint32_t minimum = 0;
                std::string trailing;
                std::istringstream input(line.substr(6));
                Require(static_cast<bool>(input >> diagnostics.after >> minimum) && minimum <= 3 &&
                        !(input >> trailing), "invalid_diagnostic_query");
                diagnostics.minimum = static_cast<heroes5_sdk::LogLevel>(minimum);
                if (!readDiagnostics) { status = 109; }
                else {
                    if (remote) {
                        SIZE_T written = 0;
                        Require(WriteProcessMemory(process, buffer, &diagnostics, sizeof(diagnostics), &written) &&
                                written == sizeof(diagnostics), "diagnostics_write_failed");
                    }
                    status = Call(process, readDiagnostics, remote ? buffer : &diagnostics, "read_diagnostics");
                    if (remote && status == 0) {
                        SIZE_T read = 0;
                        Require(ReadProcessMemory(process, buffer, &diagnostics, sizeof(diagnostics), &read) &&
                                read == sizeof(diagnostics), "diagnostics_read_failed");
                    }
                    if (status == 0) { diagnosticSnapshot = EncodeSnapshot(diagnostics); }
                }
            } else if (line == "core" || line == "feature") {
                const auto address = line == "core" ? coreVersion : feature;
                if (address) { request[2] = Call(process, address, nullptr, line == "core" ? "core_version" : "core_feature"); }
                else { status = 109; }
            } else if (line == "suspend" || line.rfind("restore ", 0) == 0) {
                auto snapshot = line == "suspend" ? heroes5_sdk::PluginSnapshot{} : DecodeSnapshot(line.substr(8));
                if (remote) {
                    SIZE_T written = 0;
                    Require(WriteProcessMemory(process, buffer, &snapshot, sizeof(snapshot), &written) && written == sizeof(snapshot),
                        "snapshot_write_failed");
                }
                status = Call(process, line == "suspend" ? suspend : restore, remote ? buffer : &snapshot,
                              line == "suspend" ? "suspend_payload" : "restore_payload");
                if (remote) {
                    SIZE_T read = 0;
                    Require(ReadProcessMemory(process, buffer, &snapshot, sizeof(snapshot), &read) && read == sizeof(snapshot),
                        "snapshot_read_failed");
                }
                if (line == "suspend" && status == 0) { stateSnapshot = EncodeSnapshot(snapshot); }
                request[3] = snapshot.generation;
            } else if (line.rfind("reload ", 0) == 0 || line.rfind("console-replace ", 0) == 0) {
                const bool consoleReplacement = line.rfind("console-replace ", 0) == 0;
                Require(!consoleReplacement || replaceConsole, "console_replace_export_missing");
                const auto path = std::filesystem::absolute(Wide(line.substr(consoleReplacement ? 16 : 7))).wstring();
                Require((path.size()+1)*2 <= 65536, "path_too_long");
                if (remote) {
                    SIZE_T written = 0;
                    Require(WriteProcessMemory(process, buffer, path.c_str(), (path.size()+1)*2, &written), "path_write_failed");
                }
                status = Call(process, consoleReplacement ? replaceConsole : replace,
                              remote ? buffer : const_cast<wchar_t*>(path.c_str()), "replacement", consoleReplacement ? 45000 : 10000);
            } else {
                std::istringstream input(line); std::string command;
                Require(static_cast<bool>(input >> command >> request[0] >> request[1]) &&
                    (command == "invoke" || command == "main" || command == "event" || command == "slot"),
                    "invalid_client_command");
                mainUsed = mainUsed || command == "main";
                if (remote) {
                    SIZE_T written = 0;
                    Require(WriteProcessMemory(process, buffer, request, sizeof(request), &written), "request_write_failed");
                }
                status = Call(process, command == "slot" ? setSlot : (command == "event" ? postEvent :
                    (command == "main" ? invokeMain : invoke)), remote ? buffer : request,
                    command == "main" ? "invoke_main" : (command == "slot" ? "set_slot" :
                        (command == "event" ? "post_event" : "invoke_payload")));
                if (remote) {
                    SIZE_T read = 0;
                    Require(ReadProcessMemory(process, buffer, request, sizeof(request), &read) && read == sizeof(request),
                        "response_read_failed");
                }
            }
            std::cout << "{\"status\":" << status << ",\"result\":" << request[2]
                << ",\"generation\":" << request[3];
            if (line.rfind("console ", 0) == 0) {
                std::cout << ",\"dispatch_returned\":" << (status == 0 ? "true" : "false");
            }
            if (!stateSnapshot.empty()) { std::cout << ",\"snapshot\":\"" << stateSnapshot << "\""; }
            if (!diagnosticSnapshot.empty()) { std::cout << ",\"diagnostics\":\"" << diagnosticSnapshot << "\""; }
            if (exitConfirmed) { std::cout << ",\"game_exited\":true,\"exit_code\":" << gameExitCode; }
            std::cout << "}\n" << std::flush;
            if (exitConfirmed) { break; }
        }
        if (remote && !exitConfirmed && WaitForSingleObject(process, 0) == WAIT_OBJECT_0) {
            Require(GetExitCodeProcess(process, &gameExitCode) != 0, "owned_game_exit_code_failed");
            exitConfirmed = true;
        }
        if (!exitConfirmed && !diagnosticReader && !consoleController && !gameCommandController) {
            Require(Call(process, stop, nullptr) == 0, "bridge_stop_unconfirmed_keep_loaded");
        }
        if (remote && !exitConfirmed) {
            if (gameCommandController) {
                Require(Call(process, RemoteApi(pid, "FreeLibrary"), reinterpret_cast<void*>(base),
                    "release_command_core") != 0, "command_core_release_unconfirmed");
            }
            // A Windows hook callback can still be returning after unhook. Keep
            // the bridge image resident until game exit if main dispatch was used.
            Require(diagnosticReader || consoleController || gameCommandController || mainUsed ||
                Call(process, RemoteApi(pid, "FreeLibrary"), reinterpret_cast<void*>(base)) != 0,
                "bridge_unload_failed");
            VirtualFreeEx(process, buffer, 0, MEM_RELEASE);
        }
        FreeLibrary(local);
        if (process) { CloseHandle(process); }
        return 0;
    } catch (const std::exception& error) {
        // On an unconfirmed call, keep remote code/buffers resident.
        if (process) { CloseHandle(process); }
        std::cerr << error.what() << '\n'; return 1;
    }
}
