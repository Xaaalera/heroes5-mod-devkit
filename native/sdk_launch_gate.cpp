#include "player_launch.hpp"
#include "../game-api/include/h5/image_placement.hpp"
#include "../game-api/include/h5/graphics_lifetime.hpp"
#include <iostream>
#include <chrono>
#include "graphics_build_identity.hpp"

int wmain(int count, wchar_t** arguments) {
    PROCESS_INFORMATION process{};
    bool detached = false;
    bool pending = false;
    DEBUG_EVENT event{};
    bool animationVerified = false;
    HANDLE graphicsFile = nullptr;
    const void* graphicsBase = nullptr;
    uintptr_t systemBase = 0;
    uintptr_t systemEnd = 0;
    try {
        const bool background = count == 5 && std::wstring(arguments[4]) == L"--background";
        if ((count != 4 && !background) || std::wstring(arguments[1]) != L"--game" ||
            std::wstring(arguments[3]) != L"--prepare-stdin") {
            throw std::runtime_error("Expected --game executable --prepare-stdin");
        }
        const auto executable = std::filesystem::canonical(arguments[2]);
        universe_player::RequireGameClosed();
        h5::VerifyGame(executable, xkit::GraphicsFacadeSha256);
        const bool facadeChain = h5::Sha256(executable.parent_path() / L"d3d9.dll") == xkit::GraphicsFacadeSha256;
        const auto granny = std::filesystem::canonical(executable.parent_path() / L"granny2.dll");
        const std::string expectedHash = "b40c1636c298ba0af1bfda8cc6db38c73924a2f85397976e40cf2fc2365aae00";
        if (h5::Sha256(granny) != expectedHash) { throw std::runtime_error("Unsupported animation dependency"); }
        constexpr uintptr_t expectedBase = 0x50000000;
        SIZE_T size = 0;
        InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
        std::vector<unsigned char> attributes(size);
        if (!InitializeProcThreadAttributeList(reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributes.data()), 1, 0, &size)) { throw std::runtime_error("Cannot initialize startup attributes"); }
        DWORD64 policy = PROCESS_CREATION_MITIGATION_POLICY_FORCE_RELOCATE_IMAGES_ALWAYS_OFF |
                         PROCESS_CREATION_MITIGATION_POLICY_BOTTOM_UP_ASLR_ALWAYS_OFF;
        STARTUPINFOEXW startup{};
        startup.StartupInfo.cb = sizeof(startup);
        if (background) {
            startup.StartupInfo.dwFlags = STARTF_USESHOWWINDOW;
            startup.StartupInfo.wShowWindow = SW_SHOWNOACTIVATE;
        }
        startup.lpAttributeList = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributes.data());
        const bool updated = UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY,
            &policy, sizeof(policy), nullptr, nullptr);
        const bool created = updated && CreateProcessW(executable.c_str(), nullptr, nullptr, nullptr, FALSE,
            DEBUG_ONLY_THIS_PROCESS | CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT, nullptr,
            executable.parent_path().c_str(), &startup.StartupInfo, &process);
        const auto createError = GetLastError();
        DeleteProcThreadAttributeList(startup.lpAttributeList);
        if (!created) { throw std::runtime_error("Owned create failed: " + std::to_string(createError)); }
        if (!DebugSetProcessKillOnExit(TRUE)) { throw std::runtime_error("Cannot retain owned debugger-exit cleanup"); }
        FILETIME ownedCreation{}, ownedExit{}, ownedKernel{}, ownedUser{};
        if (!GetProcessTimes(process.hProcess, &ownedCreation, &ownedExit, &ownedKernel, &ownedUser)) { throw std::runtime_error("Cannot retain owned creation"); }
        const uint64_t creation = (uint64_t(ownedCreation.dwHighDateTime) << 32) | ownedCreation.dwLowDateTime;
        std::cout << "PREPARED " << process.dwProcessId << ' ' << process.dwThreadId << ' ' << creation << std::endl;
        std::string command;
        if (!std::getline(std::cin, command) || command != "resume") {
            throw std::runtime_error("Owned launch cancelled before resume");
        }
        if (ResumeThread(process.hThread) == DWORD(-1)) { throw std::runtime_error("Cannot resume owned loader"); }
        const auto deadline = GetTickCount64() + 15000;
        while (GetTickCount64() < deadline) {
            if (!WaitForDebugEvent(&event, 500)) {
                if (GetLastError() == ERROR_SEM_TIMEOUT) { continue; }
                throw std::runtime_error("Debug event wait failed");
            }
            pending = true;
            if (event.dwProcessId != process.dwProcessId) { throw std::runtime_error("Unexpected debug process"); }
            bool accepted = false;
            if (event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT && event.u.CreateProcessInfo.hFile) {
                CloseHandle(event.u.CreateProcessInfo.hFile);
            }
            if (event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT && event.u.LoadDll.hFile) {
                std::array<wchar_t, 32768> filename{};
                const auto length = GetFinalPathNameByHandleW(event.u.LoadDll.hFile, filename.data(), DWORD(filename.size()), FILE_NAME_NORMALIZED);
                if (length && length < filename.size()) {
                    std::wstring normalized(filename.data());
                    if (normalized.starts_with(L"\\\\?\\")) { normalized.erase(0, 4); }
                    const auto path = std::filesystem::canonical(normalized);
                    if (_wcsicmp(path.filename().c_str(), L"ntdll.dll") == 0) {
                        systemBase = reinterpret_cast<uintptr_t>(event.u.LoadDll.lpBaseOfDll);
                        IMAGE_DOS_HEADER dos{}; IMAGE_NT_HEADERS32 header{}; SIZE_T bytes = 0;
                        if (!ReadProcessMemory(process.hProcess, reinterpret_cast<void*>(systemBase), &dos, sizeof(dos), &bytes) ||
                            bytes != sizeof(dos) || dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew <= 0 || dos.e_lfanew > 65536 ||
                            !ReadProcessMemory(process.hProcess, reinterpret_cast<void*>(systemBase + dos.e_lfanew), &header, sizeof(header), &bytes) ||
                            bytes != sizeof(header) || header.Signature != IMAGE_NT_SIGNATURE || header.FileHeader.Machine != IMAGE_FILE_MACHINE_I386 ||
                            header.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC || !header.OptionalHeader.SizeOfImage ||
                            header.OptionalHeader.SizeOfImage > 67108864 || systemBase > UINTPTR_MAX - header.OptionalHeader.SizeOfImage) {
                            throw std::runtime_error("System startup image invalid");
                        }
                        systemEnd = systemBase + header.OptionalHeader.SizeOfImage;
                    }
                    if (path == executable.parent_path() / L"d3d9.dll") {
                        const auto expectedGraphics = facadeChain ? xkit::GraphicsFacadeSha256 : h5::UniverseGraphicsSha256;
                        if (h5::Sha256(event.u.LoadDll.hFile) != expectedGraphics) {
                            throw std::runtime_error("Loaded graphics identity mismatch");
                        }
                        if (graphicsFile) { CloseHandle(graphicsFile); graphicsFile = nullptr; }
                        if (!DuplicateHandle(GetCurrentProcess(), event.u.LoadDll.hFile, GetCurrentProcess(),
                            &graphicsFile, 0, FALSE, DUPLICATE_SAME_ACCESS)) { throw std::runtime_error("Cannot retain graphics image identity"); }
                        graphicsBase = event.u.LoadDll.lpBaseOfDll;
                    }
                    if (path == granny) {
                        const h5::ImagePlacementContract contract{granny, expectedHash, expectedBase,
                            0x86000, 0x55080, 32};
                        h5::VerifyOwnedImagePlacement(process.dwProcessId, creation, executable,
                            event.u.LoadDll.hFile, event.u.LoadDll.lpBaseOfDll, contract, xkit::GraphicsFacadeSha256);
                        animationVerified = true;
                    }
                }
            }
            if (event.dwDebugEventCode == UNLOAD_DLL_DEBUG_EVENT && event.u.UnloadDll.lpBaseOfDll == graphicsBase) {
                if (graphicsFile) { CloseHandle(graphicsFile); graphicsFile = nullptr; }
                graphicsBase = nullptr;
            }
            if (event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT && event.dwThreadId == process.dwThreadId &&
                event.u.Exception.ExceptionRecord.ExceptionCode == EXCEPTION_BREAKPOINT) {
                if (!systemBase) { throw std::runtime_error("System startup image identity unavailable"); }
                const auto breakpointAddress = reinterpret_cast<uintptr_t>(event.u.Exception.ExceptionRecord.ExceptionAddress);
                if (breakpointAddress >= systemBase && breakpointAddress < systemEnd) {
                    if (!event.u.Exception.dwFirstChance || !animationVerified || !graphicsFile || !graphicsBase) {
                        throw std::runtime_error("Startup dependencies unverified at system boundary");
                    }
                    MEMORY_BASIC_INFORMATION breakpointMemory{};
                    if (VirtualQueryEx(process.hProcess, reinterpret_cast<void*>(breakpointAddress), &breakpointMemory, sizeof(breakpointMemory)) != sizeof(breakpointMemory) ||
                        breakpointMemory.AllocationBase != reinterpret_cast<void*>(systemBase) || breakpointMemory.Type != MEM_IMAGE ||
                        breakpointMemory.State != MEM_COMMIT || (breakpointMemory.Protect & PAGE_GUARD) ||
                        !(breakpointMemory.Protect & (PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY))) {
                        throw std::runtime_error("System startup breakpoint mapping invalid");
                    }
                    if (!facadeChain) {
                        h5::RepairOwnedGraphicsProxyLifetime(process.dwProcessId, creation, executable, graphicsFile, graphicsBase);
                    }
                    accepted = true;
                } else {
                    throw std::runtime_error("Unexpected breakpoint before verified startup");
                }
            }
            if (event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT && event.u.LoadDll.hFile) {
                CloseHandle(event.u.LoadDll.hFile);
                event.u.LoadDll.hFile = nullptr;
            }
            if (event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT) { throw std::runtime_error("Child exited before dependency verification"); }
            const auto continuation = event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT &&
                event.u.Exception.ExceptionRecord.ExceptionCode != EXCEPTION_BREAKPOINT ? DBG_EXCEPTION_NOT_HANDLED : DBG_CONTINUE;
            if (!ContinueDebugEvent(event.dwProcessId, event.dwThreadId, continuation)) { throw std::runtime_error("Cannot continue owned event"); }
            pending = false;
            if (accepted) {
                if (!DebugActiveProcessStop(process.dwProcessId)) { throw std::runtime_error("Cannot detach verified owned child"); }
                detached = true;
                if (graphicsFile) { CloseHandle(graphicsFile); graphicsFile = nullptr; }
                std::cout << "VERIFIED before game entry " << process.dwProcessId << ' ' << creation << std::endl;
                CloseHandle(process.hThread); CloseHandle(process.hProcess);
                return 0;
            }
        }
        throw std::runtime_error("Dependency verification timed out");
    } catch (const std::exception& failure) {
        std::cerr << failure.what() << std::endl;
        if (graphicsFile) { CloseHandle(graphicsFile); graphicsFile = nullptr; }
        if (pending && event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT && event.u.LoadDll.hFile) {
            CloseHandle(event.u.LoadDll.hFile);
            event.u.LoadDll.hFile = nullptr;
        }
        if (process.hProcess && !detached) {
            const bool terminationRequested = TerminateProcess(process.hProcess, 1) != FALSE;
            if (!terminationRequested) {
                std::cerr << "OWNED_TERMINATE_REQUEST_FAILED " << GetLastError() << std::endl;
            }
            const bool exitEventPending = pending && event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT;
            if (!terminationRequested && !exitEventPending && WaitForSingleObject(process.hProcess, 0) != WAIT_OBJECT_0) {
                // Never resume potentially partial repair code. The debugger's
                // verified kill-on-exit policy handles this still-stopped child.
                std::cerr << "OWNED_CHILD_HELD_FOR_DEBUGGER_EXIT" << std::endl;
                if (process.hThread) { CloseHandle(process.hThread); }
                CloseHandle(process.hProcess);
                return 1;
            }
            bool continuationConfirmed = true;
            if (pending) {
                continuationConfirmed = ContinueDebugEvent(event.dwProcessId, event.dwThreadId, DBG_CONTINUE) != 0;
                if (!continuationConfirmed) { std::cerr << "OWNED_EVENT_CONTINUE_FAILED " << GetLastError() << std::endl; }
            }
            const auto deadline = GetTickCount64() + 5000;
            while (continuationConfirmed && GetTickCount64() < deadline) {
                if (!WaitForDebugEvent(&event, 100)) {
                    if (GetLastError() == ERROR_SEM_TIMEOUT) { continue; }
                    std::cerr << "OWNED_EVENT_DRAIN_FAILED " << GetLastError() << std::endl;
                    break;
                }
                if (event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT && event.u.LoadDll.hFile) { CloseHandle(event.u.LoadDll.hFile); }
                if (event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT && event.u.CreateProcessInfo.hFile) {
                    CloseHandle(event.u.CreateProcessInfo.hFile);
                }
                const bool exited = event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT;
                if (!ContinueDebugEvent(event.dwProcessId, event.dwThreadId, DBG_CONTINUE)) {
                    std::cerr << "OWNED_EVENT_CONTINUE_FAILED " << GetLastError() << std::endl;
                    break;
                }
                if (exited) { break; }
            }
            if (WaitForSingleObject(process.hProcess, 1000) == WAIT_OBJECT_0) { std::cerr << "OWNED_CHILD_TERMINATED" << std::endl; }
            else { std::cerr << "OWNED_CHILD_TERMINATION_UNCONFIRMED" << std::endl; }
        }
        if (process.hThread) { CloseHandle(process.hThread); }
        if (process.hProcess) { CloseHandle(process.hProcess); }
        return 1;
    }
}
