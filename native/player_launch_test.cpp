#include "player_launch.hpp"
#include <iostream>
#define DIRECTINPUT_VERSION 0x0800
#include <dinput.h>
#include "selector_runtime.hpp"
#include <thread>
#include <MinHook.h>
#include <atomic>

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
        // Native library lifecycle on an isolated synthetic function. No game
        // site or loader-owned selector is modified by this fixture.
        auto* counterCode = static_cast<unsigned char*>(VirtualAlloc(nullptr, 4096,
            MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
        if (!counterCode) { throw std::runtime_error("Cannot allocate synthetic hook target."); }
        const unsigned char counterBytes[]{0x8b, 0x44, 0x24, 0x04, 0x83, 0xc0, 0x01, 0xc3};
        memcpy(counterCode, counterBytes, sizeof(counterBytes));
        DWORD protection = 0;
        if (!VirtualProtect(counterCode, 4096, PAGE_EXECUTE_READ, &protection) ||
            !FlushInstructionCache(GetCurrentProcess(), counterCode, sizeof(counterBytes))) {
            throw std::runtime_error("Cannot finalize synthetic hook target.");
        }
        using Counter = uint32_t (__cdecl*)(uint32_t);
        static Counter originalCounter = nullptr;
        const auto hookedCounter = +[](uint32_t value) -> uint32_t { return originalCounter(value) + 10; };
        void* originalCounterAddress = nullptr;
        if (MH_Initialize() != MH_OK || MH_CreateHook(counterCode, reinterpret_cast<void*>(hookedCounter),
                &originalCounterAddress) != MH_OK) { throw std::runtime_error("MinHook fixture creation failed."); }
        originalCounter = reinterpret_cast<Counter>(originalCounterAddress);
        const auto counter = reinterpret_cast<Counter>(counterCode);
        std::atomic<bool> stopCounterWorker{false};
        std::atomic<bool> invalidWorkerResult{false};
        std::atomic<uint32_t> workerCalls{0};
        std::thread counterWorker([&] {
            while (!stopCounterWorker.load()) {
                const auto value = counter(5);
                if (value != 6 && value != 16) { invalidWorkerResult = true; }
                ++workerCalls;
            }
        });
        bool lifecyclePassed = true;
        for (unsigned cycle = 0; cycle < 8; ++cycle) {
            if (counter(5) != 6 || MH_QueueEnableHook(counterCode) != MH_OK || MH_ApplyQueued() != MH_OK
                || counter(5) != 16 || MH_QueueDisableHook(counterCode) != MH_OK || MH_ApplyQueued() != MH_OK
                || counter(5) != 6 || memcmp(counterCode, counterBytes, sizeof(counterBytes)) != 0) {
                lifecyclePassed = false;
                break;
            }
        }
        stopCounterWorker = true;
        counterWorker.join();
        if (!lifecyclePassed || invalidWorkerResult || workerCalls == 0) {
            throw std::runtime_error("MinHook lifecycle/concurrent target execution failed.");
        }
        if (MH_RemoveHook(counterCode) != MH_OK || MH_Uninitialize() != MH_OK ||
            !VirtualFree(counterCode, 0, MEM_RELEASE)) { throw std::runtime_error("MinHook fixture retirement failed."); }
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
            using ControlSelector = DWORD (WINAPI*)(void*);
            const auto control = reinterpret_cast<ControlSelector>(GetProcAddress(library, "Heroes5BankSelectorControl"));
            if (!control) { throw std::runtime_error("Resident selector service export missing."); }
            heroes5_sdk::SelectorRequest request;
            if (control(&request) != ERROR_NOT_READY) { throw std::runtime_error("Selector initialized before request."); }
            std::array<unsigned char, 4> retainedData{42, 0, 0, 0};
            request.action = heroes5_sdk::SelectorAction::Initialize;
            request.bytes = retainedData.data(); request.byteCount = retainedData.size();
            if (control(&request) != 0) { throw std::runtime_error("Resident selector initialization failed."); }
            request.expectedGeneration = request.generation;
            retainedData[0] = 7; // The service must own a copy, not a caller buffer.
            std::array<unsigned char, 6> selectorCode{0xb8, 42, 0, 0, 0, 0xc3};
            request.action = heroes5_sdk::SelectorAction::Replace;
            request.bytes = selectorCode.data(); request.byteCount = selectorCode.size();
            if (control(&request) != 0) { throw std::runtime_error("Resident selector initial code failed."); }
            request.expectedGeneration = request.generation;
            const auto oldGeneration = request.generation;
            selectorCode[1] = 99;
            request.action = heroes5_sdk::SelectorAction::Invoke;
            if (control(&request) != 0 || request.root != 42) { throw std::runtime_error("Resident selector borrowed code."); }
            FreeLibrary(library);
            // The owner deliberately pins this startup component; losing a
            // consumer's DLL reference cannot destroy state used by later cores.
            const auto nextConsumer = LoadLibraryW(arguments[1]);
            if (nextConsumer != library || GetProcAddress(nextConsumer, "Heroes5BankSelectorControl")
                    != reinterpret_cast<FARPROC>(control)) { throw std::runtime_error("Resident owner did not survive consumer reload."); }
            request.action = heroes5_sdk::SelectorAction::Replace;
            if (control(&request) != 0) { throw std::runtime_error("Resident selector next code failed."); }
            const auto currentGeneration = request.generation;
            request.action = heroes5_sdk::SelectorAction::Stop;
            request.expectedGeneration = oldGeneration;
            if (control(&request) != ERROR_REVISION_MISMATCH || request.generation != currentGeneration) {
                throw std::runtime_error("Stale consumer stopped the replacement generation.");
            }
            request.action = heroes5_sdk::SelectorAction::Invoke;
            request.root = 555;
            if (control(&request) != ERROR_REVISION_MISMATCH || request.root != 555) {
                throw std::runtime_error("Stale consumer executed the current callback.");
            }
            request.action = heroes5_sdk::SelectorAction::Replace;
            selectorCode[1] = 123;
            if (control(&request) != ERROR_REVISION_MISMATCH || request.generation != currentGeneration) {
                throw std::runtime_error("Stale consumer replaced current code.");
            }
            request.expectedGeneration = currentGeneration;
            request.dataSchema[0] = 1;
            if (control(&request) != ERROR_INVALID_DATA || request.generation != currentGeneration) {
                throw std::runtime_error("Candidate changed the retained-state layout.");
            }
            request.dataSchema[0] = 0;
            const heroes5_sdk::SelectorFixup invalidFixup{99, 1};
            request.codeFixups = &invalidFixup; request.codeFixupCount = 1;
            if (control(&request) != ERROR_INVALID_DATA || request.generation != currentGeneration) {
                throw std::runtime_error("Rejected candidate changed the service generation.");
            }
            request.codeFixups = nullptr; request.codeFixupCount = 0;
            request.action = heroes5_sdk::SelectorAction::Invoke;
            if (control(&request) != 0 || request.root != 99) { throw std::runtime_error("Resident selector new code missing."); }
            auto foreignRequest = request;
            foreignRequest.root = 777;
            DWORD foreignResult = 0;
            std::thread foreignCaller([&] { foreignResult = control(&foreignRequest); });
            foreignCaller.join();
            if (foreignResult != ERROR_INVALID_THREAD_ID || foreignRequest.root != 777 ||
                    foreignRequest.generation != currentGeneration) {
                throw std::runtime_error("Resident selector accepted a foreign invocation thread.");
            }
            std::array<unsigned char, 4> restoredData{};
            request.action = heroes5_sdk::SelectorAction::ReadState;
            request.output = restoredData.data(); request.outputBytes = restoredData.size();
            if (control(&request) != 0 || restoredData[0] != 42) { throw std::runtime_error("Resident state did not survive consumer reload."); }
            request.action = heroes5_sdk::SelectorAction::Stop;
            if (control(&request) != 0) { throw std::runtime_error("Resident selector stop failed."); }
            request.expectedGeneration = request.generation;
            request.action = heroes5_sdk::SelectorAction::Invoke;
            if (control(&request) != ERROR_NOT_READY) { throw std::runtime_error("Stopped resident selector executed."); }
            request.action = heroes5_sdk::SelectorAction::BindWindow;
            if (control(&request) != ERROR_NOT_SUPPORTED || request.generation != request.expectedGeneration) {
                throw std::runtime_error("Non-game host acquired a game-window binding.");
            }
            request.action = heroes5_sdk::SelectorAction::AttachHook;
            if (control(&request) != ERROR_INVALID_THREAD_ID || request.generation != request.expectedGeneration) {
                throw std::runtime_error("Non-game host installed a game hook.");
            }
            request.action = heroes5_sdk::SelectorAction::DetachHook;
            if (control(&request) != ERROR_INVALID_THREAD_ID || request.generation != request.expectedGeneration) {
                throw std::runtime_error("Non-game host changed a game hook.");
            }
            if (*state != 0) { throw std::runtime_error("Selector fixture enabled actual game mods."); }
            FreeLibrary(nextConsumer);
        }
        std::cout << "Native launch boundaries passed; no game or dialogs opened.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
