#include "plugin_runtime.hpp"
#include <iostream>
#include <stdexcept>
#include <thread>

void Require(bool value, const char* reason) {
    if (!value) { throw std::runtime_error(reason); }
}
int wmain(int count, wchar_t** arguments) {
    try {
        Require(count == 5, "Expected v1 v2 bad-abi bad-state payloads");
        using heroes5_sdk::RuntimeResult;
        heroes5_sdk::PluginRuntime runtime;
        uint32_t output = 123;
        Require(runtime.Invoke(0, 0, output) == RuntimeResult::NotLoaded, "Unloaded call accepted");
        Require(runtime.Replace(arguments[1]) == RuntimeResult::Ok, "Initial load failed");
        Require(runtime.Invoke(0, 0, output) == RuntimeResult::Ok && output == 1, "Wrong initial code");
        Require(runtime.Invoke(1, 7, output) == RuntimeResult::Ok && output == 7, "Initial state missing");
        Require(runtime.Invoke(2, 0, output) == RuntimeResult::CallRejected, "New command existed in v1");
        Require(runtime.Replace(arguments[3]) == RuntimeResult::IncompatibleAbi, "Bad ABI accepted");
        Require(runtime.Replace(arguments[4]) == RuntimeResult::IncompatibleState, "Bad state accepted");
        Require(runtime.Generation() == 1, "Rejected payload changed generation");
        Require(runtime.Replace(std::filesystem::path(arguments[1]).parent_path() / L"absent-payload.dll")
            == RuntimeResult::LoadFailed, "Missing payload accepted");
        Require(runtime.Invoke(0, 0, output) == RuntimeResult::Ok && output == 1, "Rejected load replaced working code");
        for (unsigned iteration = 0; iteration < 10; ++iteration) {
            Require(runtime.Replace(arguments[2]) == RuntimeResult::Ok, "Version two reload failed");
            Require(runtime.Invoke(0, 0, output) == RuntimeResult::Ok && output == 2, "Changed code missing");
            Require(runtime.Invoke(2, 0, output) == RuntimeResult::Ok && output == 42, "New function missing");
            Require(runtime.Invoke(3, 0, output) == RuntimeResult::CallRejected && output == 42,
                "Rejected call changed result");
            Require(runtime.Invoke(1, 0, output) == RuntimeResult::Ok && output == 7,
                "State lost on reload or rejected call");
            Require(runtime.Replace(arguments[1]) == RuntimeResult::Ok, "Version one reload failed");
        }
        Require(runtime.Generation() == 21, "Repeated reload count wrong");
        HANDLE events[] = {CreateEventW(nullptr, TRUE, FALSE, nullptr), CreateEventW(nullptr, TRUE, FALSE, nullptr)};
        HANDLE replacing = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        HANDLE replaced = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        Require(events[0] && events[1] && replacing && replaced, "Cannot allocate concurrency test events");
        RuntimeResult callStatus{}, replaceStatus{}; uint32_t callValue = 0;
        std::thread caller([&] { callStatus = runtime.Invoke(6, reinterpret_cast<uint32_t>(events), callValue); });
        const bool entered = WaitForSingleObject(events[0], 2000) == WAIT_OBJECT_0;
        std::thread reloader([&] {
            SetEvent(replacing); replaceStatus = runtime.Replace(arguments[2]); SetEvent(replaced);
        });
        const bool attempted = WaitForSingleObject(replacing, 2000) == WAIT_OBJECT_0;
        const bool waited = WaitForSingleObject(replaced, 100) == WAIT_TIMEOUT;
        SetEvent(events[1]); caller.join(); reloader.join();
        CloseHandle(events[0]); CloseHandle(events[1]); CloseHandle(replacing); CloseHandle(replaced);
        Require(entered && attempted && waited, "Reload did not wait for active payload call");
        Require(callStatus == RuntimeResult::Ok && callValue == 1 && replaceStatus == RuntimeResult::Ok,
            "Active old call or replacement failed");
        Require(runtime.Invoke(0, 0, output) == RuntimeResult::Ok && output == 2, "New code missing after quiescence");
        heroes5_sdk::PluginSnapshot snapshot;
        Require(runtime.ExportState(snapshot) == RuntimeResult::Ok, "State export failed");
        heroes5_sdk::PluginRuntime replacement;
        Require(replacement.Replace(arguments[2]) == RuntimeResult::Ok, "Replacement core payload load failed");
        Require(replacement.ImportState(snapshot) == RuntimeResult::Ok, "Cross-core state handoff failed");
        Require(replacement.Invoke(1, 0, output) == RuntimeResult::Ok && output == 7, "Cross-core state changed");
        Require(replacement.Generation() == snapshot.generation + 1, "Core handoff reused generation");
        auto incompatible = snapshot; incompatible.schema = 999;
        Require(replacement.ImportState(incompatible) == RuntimeResult::IncompatibleState, "Wrong schema snapshot accepted");
        incompatible = snapshot; incompatible.version = 999;
        Require(replacement.ImportState(incompatible) == RuntimeResult::IncompatibleState, "Wrong transport version accepted");
        Require(replacement.Invoke(1, 0, output) == RuntimeResult::Ok && output == 7, "Rejected restore changed state");
        std::cout << "PASS: changed/new code, preserved state, ABI/state rejection, call rollback, 20 reloads\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
