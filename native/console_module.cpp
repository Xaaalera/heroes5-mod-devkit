#include "console_client.hpp"
#include "graphics_build_identity.hpp"
#include "console_module.hpp"
#include "diagnostic_bus.hpp"
#include "../game-api/include/h5/build.hpp"
#include <algorithm>
#include <charconv>
#include <memory>
#include <mutex>
#include <cmath>
#include <array>
#include <string>
#include <vector>
#include <filesystem>
#include <fstream>
#include <imgui.h>
#include <imgui_impl_win32.h>
#include <imgui_impl_dx9.h>
namespace ImGui { inline bool IsKeyPressedMap(ImGuiKey key, bool repeat = true) { return IsKeyPressed(key, repeat); } }
#include <imterm/terminal_helpers.hpp>
#include "TextEditor.h"
#include <d3d9.h>

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);
extern "C" __declspec(dllexport) volatile LONG XalKitConsoleStatus = 0;
#ifndef XALKIT_CONSOLE_STATE_VERSION
#define XALKIT_CONSOLE_STATE_VERSION 1
#endif
namespace {
HMODULE module = nullptr;
HWND gameWindow = nullptr, frameWindow = nullptr, buttonHost = nullptr;
HANDLE sdkProcess = nullptr;
HANDLE consoleThread = nullptr, consoleStop = nullptr;
std::mutex consoleLifecycle;
heroes5_sdk::ConsoleStopGate stopGate;
IDirect3D9* graphics = nullptr;
IDirect3DDevice9* device = nullptr;
HMODULE graphicsLibrary = nullptr;
D3DPRESENT_PARAMETERS presentation{};
bool stopping = false, resizing = false;
bool gameInputDisabled = false;
unsigned cursorHideAdjustments = 0;
constexpr wchar_t FrameClass[] = L"XalKit.Console.Frame.v1";
constexpr wchar_t ButtonClass[] = L"XalKit.Console.Button.v1";

std::wstring Label(unsigned id) {
    wchar_t text[256]{};
    const int size = LoadStringW(module, id, text, 256);
    return std::wstring(text, size > 0 ? size : 0);
}
std::string Utf8(const std::wstring& value) {
    const auto size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    std::string result(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), size, nullptr, nullptr);
    return result;
}
void PlaceButton() {
    RECT bounds{};
    if (GetClientRect(gameWindow, &bounds) && buttonHost) {
        SetWindowPos(buttonHost, HWND_TOP, (std::max)(8L, bounds.right - 100), 64, 92, 30,
                     SWP_NOACTIVATE | SWP_SHOWWINDOW);
    }
}
LRESULT CALLBACK ButtonProcedure(HWND window, UINT message, WPARAM word, LPARAM value) {
    if (message == WM_COMMAND && HIWORD(word) == BN_CLICKED) {
        const bool hiding = IsWindowVisible(frameWindow);
        ShowWindow(frameWindow, hiding ? SW_HIDE : SW_SHOWNORMAL);
        if (hiding) { SetFocus(gameWindow); }
        return 0;
    }
    return DefWindowProcW(window, message, word, value);
}
LRESULT CALLBACK FrameProcedure(HWND window, UINT message, WPARAM word, LPARAM value) {
    if (message == WM_GETMINMAXINFO) {
        auto* limits = reinterpret_cast<MINMAXINFO*>(value);
        limits->ptMinTrackSize = POINT{480, 300};
        return 0;
    }
    if (message == WM_MOUSEACTIVATE) {
        return MA_ACTIVATE;
    }
    if (message == WM_LBUTTONDOWN) { SetFocus(window); }
    if (message == WM_KILLFOCUS && reinterpret_cast<HWND>(word) == gameWindow && IsWindowVisible(window)) {
        // The game may restore its main HWND while the user edits this child.
        // Defer SetFocus until the focus-change notification has completed.
        PostMessageW(window, WM_APP + 48, 0, 0);
    }
    if (message == WM_APP + 48) {
        POINT pointer{};
        const auto foreground = GetForegroundWindow();
        if (!stopping && IsWindowVisible(window) && GetAncestor(foreground, GA_ROOT) == gameWindow && GetCursorPos(&pointer)) {
            const auto hovered = WindowFromPoint(pointer);
            if (hovered == window || IsChild(window, hovered)) { SetFocus(window); }
        }
        return 0;
    }
    if (message == WM_MOUSEWHEEL || message == WM_MOUSEHWHEEL) {
        RECT bounds{};
        POINT wheelPoint{static_cast<short>(LOWORD(value)), static_cast<short>(HIWORD(value))};
        if (!IsWindowVisible(window) || !GetWindowRect(window, &bounds) || !PtInRect(&bounds, wheelPoint)) {
            PostMessageW(gameWindow, message, word, value);
            return 0;
        }
    }
    if (ImGui::GetCurrentContext()) {
        const auto handled = ImGui_ImplWin32_WndProcHandler(window, message, word, value);
        if (message == WM_MOUSEWHEEL || message == WM_MOUSEHWHEEL) {
            const auto count = reinterpret_cast<uintptr_t>(GetPropW(window, L"XalKit.WheelEvents"));
            SetPropW(window, L"XalKit.WheelEvents", reinterpret_cast<HANDLE>(count + 1));
            return 0;
        }
        if (handled) { return 1; }
    }
    if (message == WM_CLOSE) { ShowWindow(window, SW_HIDE); SetFocus(gameWindow); return 0; }
    if (message == WM_SIZE && word != SIZE_MINIMIZED) {
        presentation.BackBufferWidth = LOWORD(value);
        presentation.BackBufferHeight = HIWORD(value);
        resizing = true;
    }
    if (message == WM_APP + 47) { stopping = true; return 0; }
    return DefWindowProcW(window, message, word, value);
}
struct CommandSession {
    heroes5_sdk::ConsoleClient* client = nullptr;
    const nlohmann::json* commands = nullptr;
    std::vector<std::string> pending;
    std::vector<std::string> acknowledgements;
    nlohmann::json lastResult;
    std::vector<std::string> output;
    std::vector<std::string> history;
    bool followOutput = false;
    bool forceOutputScroll = false;
    void Append(std::string text) {
        output.push_back(std::move(text));
        if (output.size() > 512) { output.erase(output.begin()); }
        followOutput = true;
    }
    void ShowHelp(const std::string& selected = "") {
        if (!commands) { return; }
        if (selected.empty() || selected == "help") { Append("help [command] — " + Utf8(Label(105))); }
        if (selected.empty() || selected == "clear") { Append("clear — " + Utf8(Label(108))); }
        bool found = selected.empty() || selected == "help" || selected == "clear";
        for (const auto& command : *commands) {
            const auto name = command.at("name").get<std::string>();
            if (!selected.empty() && selected != name) { continue; }
            found = true;
            Append(name + " — " + command.at("description").get<std::string>());
            auto usage = command.value("usage", name);
            if (!command.contains("usage")) {
                for (const auto& parameter : command.at("parameters")) {
                    if (parameter.value("required", false)) { usage += " <" + parameter.at("name").get<std::string>() + ">"; }
                }
            }
            Append("  " + usage);
            for (const auto& parameter : command.at("parameters")) {
                const auto help = parameter.value("help", "");
                if (!help.empty()) { Append("  " + parameter.at("name").get<std::string>() + ": " + help); }
            }
            if (command.contains("examples")) {
                for (const auto& example : command.at("examples")) { Append("  " + example.get<std::string>()); }
            }
        }
        if (!found) { Append(Utf8(Label(119))); }
    }
};
class LocalCommands : public ImTerm::basic_terminal_helper<LocalCommands, CommandSession> {
    std::string clearDescription_ = Utf8(Label(108));
    std::string helpDescription_ = Utf8(Label(105));
    nlohmann::json catalog_;
    CommandSession& session_;
public:
    std::optional<ImTerm::message> format(std::string text, ImTerm::message::type type) {
        if (type == ImTerm::message::type::user_input) {
            session_.history.push_back(text);
            if (session_.history.size() > 512) { session_.history.erase(session_.history.begin()); }
            text = "> " + text;
        }
        session_.Append(std::move(text));
        return std::nullopt;
    }
    int is_space(std::string_view text) const {
        return text.front() == ' ' || text.front() == '\t' || text.front() == '\r' || text.front() == '\n';
    }
    explicit LocalCommands(nlohmann::json catalog, CommandSession& session) : catalog_(std::move(catalog)), session_(session) {
        session_.commands = &catalog_;
        add_command_({"clear", clearDescription_, [](argument_type& args) { args.val.output.clear(); args.term.clear(); }, nullptr});
        add_command_({"help", helpDescription_, [](argument_type& args) {
            args.val.ShowHelp(args.command_line.size() > 1 ? args.command_line[1] : "");
        }, nullptr});
        for (const auto& command : catalog_) {
            add_command_({command.at("name").get_ref<const std::string&>(),
                command.at("description").get_ref<const std::string&>(), [](argument_type& args) {
                    static uint64_t requestCounter = 0;
                    const auto id = "ui-" + std::to_string(GetTickCount64()) + "-" + std::to_string(++requestCounter);
                    args.val.pending.push_back(id);
                    try {
                        const auto response = args.val.client->Request({{"kind", "run"}, {"id", id}, {"arguments", args.command_line}});
                        if (!response.value("ok", false)) {
                            args.val.Append(response.at("error").at("message").get<std::string>());
                            args.val.pending.pop_back();
                        }
                    } catch (...) { args.val.Append(Utf8(Label(110))); }
                }, [](argument_type& args) {
                    try {
                        const auto response = args.val.client->Request({{"kind", "complete"}, {"arguments", args.command_line}});
                        if (response.value("ok", false)) { return response.at("candidates").get<std::vector<std::string>>(); }
                    } catch (...) { }
                    return std::vector<std::string>{};
                }});
        }
    }
};
bool CreateGraphics() {
    wchar_t directory[MAX_PATH]{};
    if (!GetSystemDirectoryW(directory, MAX_PATH)) { return false; }
    const auto path = std::filesystem::path(directory) / L"d3d9.dll";
    graphicsLibrary = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!graphicsLibrary) { return false; }
    const auto create = reinterpret_cast<IDirect3D9* (WINAPI*)(UINT)>(GetProcAddress(graphicsLibrary, "Direct3DCreate9"));
    graphics = create ? create(D3D_SDK_VERSION) : nullptr;
    if (!graphics) { return false; }
    presentation.Windowed = TRUE;
    presentation.SwapEffect = D3DSWAPEFFECT_DISCARD;
    presentation.BackBufferFormat = D3DFMT_UNKNOWN;
    presentation.PresentationInterval = D3DPRESENT_INTERVAL_ONE;
    presentation.hDeviceWindow = frameWindow;
    return SUCCEEDED(graphics->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, frameWindow,
        D3DCREATE_SOFTWARE_VERTEXPROCESSING, &presentation, &device));
}
bool SaveConsoleCapture(const std::string& path) {
    IDirect3DSurface9* backBuffer = nullptr;
    IDirect3DSurface9* snapshot = nullptr;
    bool saved = false;
    if (SUCCEEDED(device->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &backBuffer))) {
        D3DSURFACE_DESC description{};
        if (SUCCEEDED(backBuffer->GetDesc(&description)) && description.Width <= 4096 && description.Height <= 4096 &&
            (description.Format == D3DFMT_X8R8G8B8 || description.Format == D3DFMT_A8R8G8B8) &&
            SUCCEEDED(device->CreateOffscreenPlainSurface(description.Width, description.Height, description.Format,
                D3DPOOL_SYSTEMMEM, &snapshot, nullptr)) && SUCCEEDED(device->GetRenderTargetData(backBuffer, snapshot))) {
            D3DLOCKED_RECT pixels{};
            if (SUCCEEDED(snapshot->LockRect(&pixels, nullptr, D3DLOCK_READONLY))) {
                BITMAPINFOHEADER information{};
                information.biSize = sizeof(information);
                information.biWidth = description.Width;
                information.biHeight = -static_cast<LONG>(description.Height);
                information.biPlanes = 1; information.biBitCount = 32;
                information.biSizeImage = description.Width * description.Height * 4;
                BITMAPFILEHEADER header{};
                header.bfType = 0x4d42;
                header.bfOffBits = sizeof(header) + sizeof(information);
                header.bfSize = header.bfOffBits + information.biSizeImage;
                std::ofstream output(std::filesystem::u8path(path), std::ios::binary);
                output.write(reinterpret_cast<const char*>(&header), sizeof(header));
                output.write(reinterpret_cast<const char*>(&information), sizeof(information));
                for (UINT row = 0; row < description.Height; ++row) {
                    output.write(static_cast<const char*>(pixels.pBits) + row * pixels.Pitch, description.Width * 4);
                }
                saved = output.good();
                snapshot->UnlockRect();
            }
        }
    }
    if (snapshot) { snapshot->Release(); }
    if (backBuffer) { backBuffer->Release(); }
    return saved;
}
DWORD WINAPI ConsoleThread(void*) {
    bool windowBackend = false, graphicsBackend = false;
    try {
        wchar_t language[8]{};
        GetEnvironmentVariableW(L"XALKIT_LANG", language, 8);
        SetThreadUILanguage(wcscmp(language, L"ru") == 0 ? MAKELANGID(LANG_RUSSIAN, SUBLANG_DEFAULT) :
            MAKELANGID(LANG_ENGLISH, SUBLANG_DEFAULT));
        WNDCLASSW frameClass{};
        frameClass.hInstance = module;
        frameClass.lpfnWndProc = FrameProcedure;
        frameClass.lpszClassName = FrameClass;
        frameClass.hCursor = LoadCursorW(nullptr, IDC_ARROW);
        if (!RegisterClassW(&frameClass)) { throw std::runtime_error("console_class_failed"); }
        auto buttonClass = frameClass;
        buttonClass.lpfnWndProc = ButtonProcedure;
        buttonClass.lpszClassName = ButtonClass;
        if (!RegisterClassW(&buttonClass)) { throw std::runtime_error("console_button_class_failed"); }
        RECT gameBounds{};
        GetWindowRect(gameWindow, &gameBounds);
        SetWindowLongPtrW(gameWindow, GWL_STYLE, GetWindowLongPtrW(gameWindow, GWL_STYLE) | WS_CLIPCHILDREN);
        frameWindow = CreateWindowExW(0, FrameClass, L"xkit", WS_CHILD | WS_THICKFRAME | WS_CLIPSIBLINGS,
            120, 90, 740, 460, gameWindow, nullptr, module, nullptr);
        buttonHost = CreateWindowExW(0, ButtonClass, L"xkit", WS_CHILD | WS_VISIBLE,
            0, 64, 92, 30, gameWindow, nullptr, module, nullptr);
        if (!frameWindow || !buttonHost || !CreateWindowExW(0, L"BUTTON", Label(100).c_str(),
            WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON, 0, 0, 92, 30, buttonHost,
            reinterpret_cast<HMENU>(1), module, nullptr)) { throw std::runtime_error("console_window_failed"); }
        SetPropW(gameWindow, FrameClass, frameWindow);
        if (!CreateGraphics()) { throw std::runtime_error("console_device_failed"); }
        ImGui::CreateContext();
        ImGui::GetIO().ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
        ImGui::GetIO().IniFilename = nullptr;
        // The game can hide the Windows cursor. Draw the console cursor using
        // ImGui so its visibility does not depend on the game's cursor counter.
        ImGui::GetIO().MouseDrawCursor = true;
        ImGui::StyleColorsDark();
        wchar_t windowsDirectory[MAX_PATH]{};
        GetWindowsDirectoryW(windowsDirectory, MAX_PATH);
        const auto font = std::filesystem::path(windowsDirectory) / L"Fonts" / L"consola.ttf";
        ImGui::GetIO().Fonts->AddFontFromFileTTF(Utf8(font.wstring()).c_str(), 17.0f, nullptr,
                                               ImGui::GetIO().Fonts->GetGlyphRangesCyrillic());
        windowBackend = ImGui_ImplWin32_Init(frameWindow);
        graphicsBackend = ImGui_ImplDX9_Init(device);
        if (!windowBackend || !graphicsBackend) { throw std::runtime_error("console_backend_failed"); }
        const auto logs = Utf8(Label(101)), commands = Utf8(Label(102)), modules = Utf8(Label(103));
        const auto empty = Utf8(Label(104)), pendingCommands = Utf8(Label(106)), pendingModules = Utf8(Label(107));
        const auto searchHint = Utf8(Label(109));
        heroes5_sdk::ConsoleClient client;
        const auto catalog = client.Request({{"kind", "catalog"}});
        if (!catalog.value("ok", false) || catalog.value("version", 0) != 1 ||
            catalog.at("owner").at("pid").get<DWORD>() != GetCurrentProcessId()) {
            throw std::runtime_error("console_broker_session_mismatch");
        }
        CommandSession commandState{&client};
        const bool acknowledgeResults = catalog.value("delivery_acknowledgements", false);
        auto helper = std::make_shared<LocalCommands>(catalog.at("commands"), commandState);
        ImTerm::terminal<LocalCommands> terminal(commandState, commands.c_str(), 600, 180, helper);
        terminal.set_flags(ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings);
        terminal.disallow_x_resize(); terminal.disallow_y_resize();
        TextEditor editor;
        editor.SetImGuiChildIgnored(true);
        editor.SetLanguageDefinition(TextEditor::LanguageDefinition::Lua());
        nlohmann::json completions = nlohmann::json::array();
        nlohmann::json completionLabels = nlohmann::json::array();
        std::string completionText, completionDescription;
        bool completionPending = false;
        bool focusCompletion = false;
        uint64_t nextCompletionRefresh = 0;
        int historyIndex = -1;
        std::string historyDraft;
        bool editorFocused = false, focusInput = true;
        ImVec2 inputPosition{};
        uintptr_t validationScenario = 0;
        int scenarioPhase = 0;
        uint64_t scenarioReportAt = 0;
        uint64_t cursor = 0;
        std::vector<heroes5_sdk::DiagnosticRecord> records;
        ImGuiTextFilter filter;
        int minimum = 1;
        int selectedTab = 0, restoreTab = -1;
        std::array<ImVec2, 2> scrollPositions{};
        std::array<bool, 2> restoreScroll{false, false}, followScroll{true, true};
        uint64_t nextPoll = 0;
        uint64_t nextHeartbeat = GetTickCount64() + 1000;
        uint64_t eventCursor = 0;
        bool logContentChanged = false;
        char validationFlag[8]{};
        const bool validateInput = GetEnvironmentVariableA("XALKIT_CONSOLE_VALIDATE_INPUT", validationFlag, 8) &&
                                   std::strcmp(validationFlag, "1") == 0;
        int validationPhase = 0;
        unsigned validationFrames = 0;
        uint64_t nextValidationReport = 0;
        ImVec2 commandTabCenter{}, runButtonCenter{};
        bool commandTabKnown = false, runButtonKnown = false, commandsSelected = false, commandSubmitted = false;
        POINT originalCursor{}; GetCursorPos(&originalCursor);
        const auto originalForeground = GetForegroundWindow();
        if (validateInput) {
            ImGui::GetIO().ConfigFlags |= ImGuiConfigFlags_NoMouseCursorChange;
            ImGui::GetIO().ConfigDebugIgnoreFocusLoss = true;
            ImGui::GetIO().ConfigInputTrickleEventQueue = false;
            ImGui::GetIO().AddFocusEvent(true);
            ShowWindow(frameWindow, SW_SHOWNOACTIVATE);
        }
        nlohmann::json mirrored = nlohmann::json::array();
        nlohmann::json moduleStates = nlohmann::json::array();
        const auto restored = client.RestoreUiState();
        if (!restored.is_null()) {
            if (restored.at("version") != XALKIT_CONSOLE_STATE_VERSION || restored.at("owner") != catalog.at("owner")) {
                throw std::runtime_error("console_restore_owner_changed");
            }
            commandState.pending = restored.at("pending").get<std::vector<std::string>>();
            commandState.acknowledgements = restored.value("acknowledgements", std::vector<std::string>{});
            if (!acknowledgeResults) { commandState.acknowledgements.clear(); }
            commandState.output = restored.at("output").get<std::vector<std::string>>();
            commandState.history = restored.at("history").get<std::vector<std::string>>();
            commandState.lastResult = restored.at("last_result");
            editor.SetText(restored.at("editor").get<std::string>());
            minimum = restored.at("minimum").get<int>();
            selectedTab = restored.value("selected_tab", 0);
            restoreTab = selectedTab;
            if (restored.contains("scroll_positions")) {
                const auto& positions = restored.at("scroll_positions");
                if (!positions.is_array() || positions.size() != 2) { throw std::runtime_error("console_restore_scroll_invalid"); }
                for (size_t index = 0; index < 2; ++index) {
                    const auto& position = positions.at(index);
                    if (!position.is_array() || position.size() != 2) { throw std::runtime_error("console_restore_scroll_invalid"); }
                    scrollPositions[index] = ImVec2(position.at(0).get<float>(), position.at(1).get<float>());
                    if (!std::isfinite(scrollPositions[index].x) || !std::isfinite(scrollPositions[index].y) ||
                        scrollPositions[index].x < 0 || scrollPositions[index].y < 0 ||
                        scrollPositions[index].x > 10000000 || scrollPositions[index].y > 10000000) {
                        throw std::runtime_error("console_restore_scroll_invalid");
                    }
                    followScroll[index] = restored.at("follow_scroll").at(index).get<bool>();
                    restoreScroll[index] = true;
                }
            }
            const auto search = restored.at("filter").get<std::string>();
            if (search.size() >= sizeof(filter.InputBuf) || minimum < 0 || minimum > 3 || selectedTab < 0 || selectedTab > 2 ||
                commandState.pending.size() > 96 || commandState.acknowledgements.size() > 96 ||
                commandState.output.size() > 512 || commandState.history.size() > 512) {
                throw std::runtime_error("console_restore_shape_invalid");
            }
            std::memcpy(filter.InputBuf, search.c_str(), search.size() + 1); filter.Build();
            mirrored = restored.at("mirrored");
            eventCursor = restored.at("event_cursor").get<uint64_t>();
            if (restored.contains("window_rect") && !restored.at("window_rect").is_null()) {
                const auto& saved = restored.at("window_rect");
                if (!saved.is_array() || saved.size() != 4) { throw std::runtime_error("console_restore_geometry_invalid"); }
                RECT bounds{saved.at(0).get<LONG>(), saved.at(1).get<LONG>(), saved.at(2).get<LONG>(), saved.at(3).get<LONG>()};
                const LONG width = bounds.right - bounds.left, height = bounds.bottom - bounds.top;
                if (width < 480 || height < 300 || width > 16384 || height > 16384) {
                    throw std::runtime_error("console_restore_geometry_invalid");
                }
                MONITORINFO monitor{sizeof(MONITORINFO)};
                if (!GetMonitorInfoW(MonitorFromRect(&bounds, MONITOR_DEFAULTTONEAREST), &monitor)) {
                    throw std::runtime_error("console_restore_monitor_failed");
                }
                const auto restoredWidth = (std::min)(width, monitor.rcWork.right - monitor.rcWork.left);
                const auto restoredHeight = (std::min)(height, monitor.rcWork.bottom - monitor.rcWork.top);
                const auto left = (std::clamp)(bounds.left, monitor.rcWork.left, monitor.rcWork.right - restoredWidth);
                const auto top = (std::clamp)(bounds.top, monitor.rcWork.top, monitor.rcWork.bottom - restoredHeight);
                POINT position{left, top}; ScreenToClient(gameWindow, &position);
                RECT clientBounds{}; GetClientRect(gameWindow, &clientBounds);
                position.x = (std::clamp)(position.x, 0L, (std::max)(0L, clientBounds.right - restoredWidth));
                position.y = (std::clamp)(position.y, 0L, (std::max)(0L, clientBounds.bottom - restoredHeight));
                if (!SetWindowPos(frameWindow, nullptr, position.x, position.y, restoredWidth, restoredHeight, SWP_NOACTIVATE | SWP_NOZORDER)) {
                    throw std::runtime_error("console_restore_position_failed");
                }
            }
            ShowWindow(frameWindow, restored.at("visible").get<bool>() ? SW_SHOWNOACTIVATE : SW_HIDE);
            validationPhase = 9;
            ImGui::GetIO().ConfigFlags &= ~ImGuiConfigFlags_NoMouseCursorChange;
            ImGui::GetIO().ConfigDebugIgnoreFocusLoss = false;
            ImGui::GetIO().ConfigInputTrickleEventQueue = true;
            if (validateInput) {
                client.Request({{"kind", "validation_result"}, {"result", {
                    {"restored", true}, {"editor", editor.GetText()}, {"history", commandState.history},
                    {"selected_tab", selectedTab},
                    {"output", commandState.output}, {"pending", commandState.pending},
                    {"owner", catalog.at("owner")}, {"last_result", commandState.lastResult},
                    {"source_watch_probe", "console-source-edit-20261005-v3"}}}});
            }
        }
        InterlockedExchange(&XalKitConsoleStatus, 2);
        while (!stopping && IsWindow(gameWindow) && WaitForSingleObject(sdkProcess, 0) == WAIT_TIMEOUT) {
            MSG message{};
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message); DispatchMessageW(&message);
            }
            if (WaitForSingleObject(consoleStop, 0) == WAIT_OBJECT_0) {
                try {
                    auto editorText = editor.GetText();
                    if (!editorText.empty() && editorText.back() == '\n') { editorText.pop_back(); }
                    RECT bounds{};
                    nlohmann::json windowRect;
                    if (!IsIconic(frameWindow) && GetWindowRect(frameWindow, &bounds)) {
                        windowRect = {bounds.left, bounds.top, bounds.right, bounds.bottom};
                    }
                    client.SaveUiState({{"version", XALKIT_CONSOLE_STATE_VERSION}, {"owner", catalog.at("owner")},
                        {"window_rect", windowRect},
                        {"editor", editorText}, {"history", commandState.history}, {"output", commandState.output},
                        {"pending", commandState.pending}, {"last_result", commandState.lastResult},
                        {"acknowledgements", commandState.acknowledgements},
                        {"minimum", minimum}, {"filter", filter.InputBuf}, {"mirrored", mirrored},
                        {"selected_tab", selectedTab},
                        {"scroll_positions", {{scrollPositions[0].x, scrollPositions[0].y}, {scrollPositions[1].x, scrollPositions[1].y}}},
                        {"follow_scroll", followScroll},
                        {"event_cursor", eventCursor}, {"visible", IsWindowVisible(frameWindow) != FALSE}});
                    if (stopGate.Commit()) { break; }
                } catch (...) {
                    // Keep the old UI alive when a checkpoint is unconfirmed.
                    stopGate.Cancel();
                    ResetEvent(consoleStop);
                    commandState.Append(Utf8(Label(110)));
                }
            }
            PlaceButton();
            if (IsWindowVisible(frameWindow)) {
                SetWindowPos(frameWindow, HWND_TOP, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE);
            }
            const bool consoleOwnsInput = IsWindowVisible(frameWindow) &&
                (GetForegroundWindow() == frameWindow || IsChild(frameWindow, GetForegroundWindow()));
            POINT pointer{};
            const bool pointerKnown = GetCursorPos(&pointer) != FALSE;
            const auto hoveredWindow = pointerKnown ? WindowFromPoint(pointer) : nullptr;
            const bool consoleOwnsCursor = IsWindowVisible(frameWindow) &&
                (consoleOwnsInput || hoveredWindow == frameWindow || IsChild(frameWindow, hoveredWindow));
            if (consoleOwnsCursor) {
                // Keep the hardware cursor hidden while ImGui draws the console
                // cursor. SetCursor alone cannot control ShowCursor's counter.
                auto visibility = ShowCursor(FALSE);
                if (visibility < -1) { ShowCursor(TRUE); }
                else {
                    ++cursorHideAdjustments;
                    for (unsigned attempt = 0; visibility >= 0 && attempt < 32; ++attempt) {
                        visibility = ShowCursor(FALSE); ++cursorHideAdjustments;
                    }
                }
                SetCursor(nullptr);
            } else {
                while (cursorHideAdjustments) { ShowCursor(TRUE); --cursorHideAdjustments; }
            }
            if (gameInputDisabled) {
                EnableWindow(gameWindow, TRUE);
                gameInputDisabled = false;
            }
            if (GetTickCount64() >= nextHeartbeat) {
                nextHeartbeat = GetTickCount64() + 1000;
                try {
                    const auto response = client.Request({{"kind", "events"}, {"after", eventCursor}});
                    if (!response.value("ok", false)) { stopping = true; }
                    else {
                        moduleStates = response.at("modules");
                        for (const auto& event : response.at("events")) {
                            eventCursor = event.at("sequence").get<uint64_t>();
                            mirrored.push_back(event);
                            logContentChanged = true;
                        }
                        if (mirrored.size() > 256) { mirrored.erase(mirrored.begin(), mirrored.end() - 256); }
                    }
                } catch (...) { stopping = true; }
                if (stopping) { break; }
            }
            if (GetTickCount64() >= nextPoll && !commandState.acknowledgements.empty()) {
                nextPoll = GetTickCount64() + 250;
                try {
                    const auto response = client.Request({{"kind", "ack"}, {"id", commandState.acknowledgements.front()}});
                    if (response.value("ok", false)) { commandState.acknowledgements.erase(commandState.acknowledgements.begin()); }
                } catch (...) { /* Delivery acknowledgement can retry; commands never replay. */ }
            }
            // Diagnostic scenario 8 holds an unread real result across a DLL swap.
            // Ordinary sessions never defer delivery through this branch.
            if (!(validateInput && validationScenario == 8) && GetTickCount64() >= nextPoll && !commandState.pending.empty()) {
                nextPoll = GetTickCount64() + 250;
                const auto id = commandState.pending.front();
                try {
                    const auto response = client.Request({{"kind", "poll"}, {"id", id}});
                    if (response.value("status", "") != "pending") {
                        if (response.value("ok", false)) {
                            commandState.lastResult = response.at("result");
                            if (response.contains("display_lines")) {
                                for (const auto& line : response.at("display_lines")) { commandState.Append(line.get<std::string>()); }
                            } else { commandState.Append(commandState.lastResult.dump(2)); }
                        }
                        else {
                            commandState.lastResult = response.at("error");
                            commandState.Append(commandState.lastResult.at("message").get<std::string>());
                            const auto hint = response.value("hint", "");
                            if (!hint.empty()) { commandState.Append(hint); }
                        }
                        const auto status = response.value("status", "");
                        if (acknowledgeResults && (status == "completed" || status == "failed")) {
                            commandState.acknowledgements.push_back(id);
                        }
                        commandState.pending.erase(commandState.pending.begin());
                    }
                } catch (...) { /* Keep the pending ID on a transport failure; poll again. */ }
            }
            if (!IsWindowVisible(frameWindow)) { Sleep(40); continue; }
            if (resizing || device->TestCooperativeLevel() == D3DERR_DEVICENOTRESET) {
                ImGui_ImplDX9_InvalidateDeviceObjects();
                const auto resetState = device->Reset(&presentation);
                SetPropW(frameWindow, L"XalKit.Graphics.Reset", reinterpret_cast<HANDLE>(static_cast<uintptr_t>(resetState) + 1));
                if (FAILED(resetState)) { Sleep(40); continue; }
                ImGui_ImplDX9_CreateDeviceObjects(); resizing = false;
            }
            const auto graphicsState = device->TestCooperativeLevel();
            SetPropW(frameWindow, L"XalKit.Graphics.State", reinterpret_cast<HANDLE>(static_cast<uintptr_t>(graphicsState) + 1));
            if (graphicsState == D3DERR_DEVICELOST) { Sleep(40); continue; }
            heroes5_sdk::DiagnosticSnapshot snapshot;
            if (heroes5_sdk::Diagnostics().Read(cursor, heroes5_sdk::LogLevel::Debug, snapshot)) {
                for (uint32_t index = 0; index < snapshot.count; ++index) {
                    records.push_back(snapshot.records[index]); cursor = snapshot.records[index].sequence;
                    logContentChanged = true;
                }
                if (records.size() > 1024) { records.erase(records.begin(), records.end() - 1024); }
            }
            ImGui_ImplDX9_NewFrame(); ImGui_ImplWin32_NewFrame();
            if (consoleOwnsCursor && pointerKnown) {
                POINT localPointer = pointer;
                ScreenToClient(frameWindow, &localPointer);
                ImGui::GetIO().AddMousePosEvent(static_cast<float>(localPointer.x), static_cast<float>(localPointer.y));
            }
            if (validateInput && ++validationFrames > 5 && validationPhase < 6) {
                auto& input = ImGui::GetIO();
                if (validationPhase < 3 && commandTabKnown) {
                    input.AddMousePosEvent(commandTabCenter.x, commandTabCenter.y);
                    if (validationPhase == 1) { input.AddMouseButtonEvent(0, true); }
                    if (validationPhase == 2) { input.AddMouseButtonEvent(0, false); }
                    ++validationPhase;
                } else if (validationPhase >= 3 && runButtonKnown) {
                    if (validationPhase == 4) { input.AddKeyEvent(ImGuiMod_Ctrl, true); input.AddKeyEvent(ImGuiKey_Enter, true); }
                    if (validationPhase == 5) { input.AddKeyEvent(ImGuiKey_Enter, false); input.AddKeyEvent(ImGuiMod_Ctrl, false); }
                    ++validationPhase;
                }
            }
            if (validateInput && validationPhase >= 9) {
                const auto requested = reinterpret_cast<uintptr_t>(GetPropW(frameWindow, L"XalKit.Validation.Scenario"));
                if (requested >= 1 && requested <= 8) {
                    RemovePropW(frameWindow, L"XalKit.Validation.Scenario");
                    validationScenario = requested; scenarioPhase = 1;
                    scenarioReportAt = GetTickCount64() + 1500;
                    const char* scenarios[]{"army ", "army Brem ", "help army", "trace", "army Brem ARCHER", "draft"};
                    if (requested <= 6) { editor.SetText(scenarios[requested - 1]); editor.MoveEnd(); }
                    if (requested == 8) { editor.SetText("heroes"); editor.MoveEnd(); }
                    focusInput = true;
                    restoreTab = 1;
                } else if (scenarioPhase == 1 && editorFocused) {
                    if (validationScenario <= 2 || validationScenario == 5) { ImGui::GetIO().AddKeyEvent(ImGuiKey_Tab, true); }
                    else if (validationScenario == 6) { ImGui::GetIO().AddKeyEvent(ImGuiKey_UpArrow, true); }
                    else if (validationScenario == 7) { ImGui::GetIO().AddKeyEvent(ImGuiKey_DownArrow, true); }
                    else { ImGui::GetIO().AddKeyEvent(ImGuiMod_Ctrl, true); ImGui::GetIO().AddKeyEvent(ImGuiKey_Enter, true); }
                    scenarioPhase = 2;
                } else if (scenarioPhase == 2) {
                    ImGui::GetIO().AddKeyEvent(ImGuiKey_Tab, false);
                    ImGui::GetIO().AddKeyEvent(ImGuiKey_Enter, false);
                    ImGui::GetIO().AddKeyEvent(ImGuiMod_Ctrl, false);
                    ImGui::GetIO().AddKeyEvent(ImGuiKey_UpArrow, false);
                    ImGui::GetIO().AddKeyEvent(ImGuiKey_DownArrow, false);
                    scenarioPhase = 3;
                } else if (scenarioPhase == 4) {
                    ImGui::GetIO().AddKeyEvent(ImGuiKey_Enter, false);
                    scenarioPhase = 5;
                }
            }
            ImGui::NewFrame();
            const auto diagnosticMouseY = (std::clamp)(ImGui::GetIO().MousePos.y, -1.0f, 100000.0f);
            SetPropW(frameWindow, L"XalKit.MouseY", reinterpret_cast<HANDLE>(static_cast<intptr_t>(diagnosticMouseY) + 1));
            SetPropW(frameWindow, L"XalKit.WheelUnits", reinterpret_cast<HANDLE>(static_cast<intptr_t>(ImGui::GetIO().MouseWheel * 120) + 65536));
            ImGui::SetNextWindowPos(ImVec2(0, 0));
            ImGui::SetNextWindowSize(ImGui::GetIO().DisplaySize);
            ImGui::Begin("xkit", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings);
            if (ImGui::Button(Utf8(Label(116)).c_str())) { ShowWindow(frameWindow, SW_HIDE); SetFocus(gameWindow); }
            ImGui::SameLine(); ImGui::TextUnformatted("xkit");
            bool commandTabSelected = false;
            if (ImGui::BeginTabBar("SDK")) {
                if (ImGui::BeginTabItem(logs.c_str(), nullptr,
                    (validateInput && validationPhase == 7) || restoreTab == 0 ? ImGuiTabItemFlags_SetSelected : 0)) {
                    selectedTab = 0;
                    ImGui::Combo("##level", &minimum, "DEBUG\0INFO\0WARNING\0ERROR\0");
                    ImGui::SetNextItemWidth(-1);
                    if (ImGui::InputTextWithHint("##filter", searchHint.c_str(), filter.InputBuf, IM_ARRAYSIZE(filter.InputBuf))) {
                        filter.Build();
                    }
                    ImGui::BeginChild("system", ImVec2(0, 0), ImGuiChildFlags_Borders, ImGuiWindowFlags_HorizontalScrollbar);
                    const bool restoring = restoreScroll[0];
                    if (restoring) { ImGui::SetScrollX(scrollPositions[0].x); ImGui::SetScrollY(scrollPositions[0].y); restoreScroll[0] = false; }
                    else { followScroll[0] = ImGui::GetScrollY() >= ImGui::GetScrollMaxY(); }
                    if (records.empty() && mirrored.empty()) { ImGui::TextUnformatted(empty.c_str()); }
                    for (const auto& record : records) {
                        if (static_cast<uint32_t>(record.level) > 3 ||
                            !std::memchr(record.module, 0, sizeof(record.module)) ||
                            !std::memchr(record.message, 0, sizeof(record.message))) { continue; }
                        if (static_cast<int>(record.level) < minimum) { continue; }
                        const char* names[] = {"DEBUG", "INFO", "WARNING", "ERROR"};
                        const auto line = "[" + std::string(names[static_cast<int>(record.level)]) + "] " + record.module + ": " + record.message;
                        if (!filter.PassFilter(line.c_str())) { continue; }
                        const auto color = record.level == heroes5_sdk::LogLevel::Error ? ImVec4(1, .45f, .45f, 1) :
                            record.level == heroes5_sdk::LogLevel::Warning ? ImVec4(1, .8f, .35f, 1) : ImVec4(.85f, .9f, .95f, 1);
                        ImGui::PushStyleColor(ImGuiCol_Text, color); ImGui::TextUnformatted(line.c_str()); ImGui::PopStyleColor();
                    }
                    for (const auto& event : mirrored) {
                        const auto level = event.value("level", "info");
                        const int rank = level == "error" ? 3 : level == "warning" ? 2 : level == "debug" ? 0 : 1;
                        if (rank < minimum) { continue; }
                        const auto line = "[" + level + "] " + event.value("source", "SDK") + ": " + event.value("message", "");
                        const auto result = event.value("result_summary", "");
                        const auto searchable = line + "\n" + result;
                        if (!filter.PassFilter(searchable.c_str())) { continue; }
                        ImGui::TextUnformatted(line.c_str());
                        if (!result.empty()) { ImGui::TextWrapped("%s", result.c_str()); }
                    }
                    if (followScroll[0] && (logContentChanged || restoring)) { ImGui::SetScrollHereY(1); }
                    logContentChanged = false;
                    if (!restoring) { scrollPositions[0] = ImVec2(ImGui::GetScrollX(), ImGui::GetScrollY()); }
                    SetPropW(frameWindow, L"XalKit.LogScroll", reinterpret_cast<HANDLE>(static_cast<uintptr_t>(ImGui::GetScrollY()) + 1));
                    ImGui::EndChild(); ImGui::EndTabItem();
                }
                commandTabSelected = ImGui::BeginTabItem(commands.c_str(), nullptr,
                    (validateInput && validationPhase == 8) || restoreTab == 1 ? ImGuiTabItemFlags_SetSelected : 0);
                const auto tabMinimum = ImGui::GetItemRectMin(), tabMaximum = ImGui::GetItemRectMax();
                commandTabCenter = ImVec2((tabMinimum.x + tabMaximum.x) / 2, (tabMinimum.y + tabMaximum.y) / 2);
                commandTabKnown = true;
                if (commandTabSelected) {
                    selectedTab = 1;
                    commandsSelected = true;
                    if (validateInput && validationPhase == 3) { editor.SetText("heroes"); }
                    const bool focused = editorFocused && !ImGui::IsPopupOpen("command-completion") &&
                        !ImGui::IsPopupOpen("command-history");
                    const bool submitShortcut = focused && ImGui::GetIO().KeyCtrl &&
                        (ImGui::IsKeyPressed(ImGuiKey_Enter, false) || ImGui::IsKeyPressed(ImGuiKey_KeypadEnter, false));
                    const bool completionShortcut = focused && !ImGui::GetIO().KeyCtrl && !ImGui::GetIO().KeyAlt &&
                        ImGui::IsKeyPressed(ImGuiKey_Tab, false) && !ImGui::IsPopupOpen("command-completion");
                    editor.SetHandleKeyboardInputs(focused && !submitShortcut && !completionShortcut);
                    if (ImGui::Button(Utf8(Label(111)).c_str()) || submitShortcut) {
                        commandSubmitted = true;
                        auto commandText = editor.GetText();
                        const auto lastCharacter = commandText.find_last_not_of(" \t\r\n");
                        commandText.erase(lastCharacter == std::string::npos ? 0 : lastCharacter + 1);
                        if (!commandText.empty()) {
                            if (!terminal.execute(commandText)) { commandState.Append(Utf8(Label(112))); }
                            else { editor.SetText(""); historyIndex = -1; commandState.forceOutputScroll = true; focusInput = true; }
                        }
                    }
                    const auto minimum = ImGui::GetItemRectMin(), maximum = ImGui::GetItemRectMax();
                    runButtonCenter = ImVec2((minimum.x + maximum.x) / 2, (minimum.y + maximum.y) / 2);
                    runButtonKnown = true;
                    ImGui::SameLine();
                    if (ImGui::Button(Utf8(Label(113)).c_str()) || completionShortcut) {
                        auto text = editor.GetText();
                        // TextEditor appends a newline even for a single-line command.
                        if (!text.empty() && text.back() == '\n') { text.pop_back(); }
                        completionText = text;
                        try {
                            const auto response = client.Request({{"kind", "complete"}, {"text", text}});
                            completions = response.value("ok", false) ? response.at("replacements") : nlohmann::json::array();
                            completionLabels = response.value("candidates", nlohmann::json::array());
                            completionPending = response.value("pending", false);
                            completionDescription = response.value("description", "");
                            nextCompletionRefresh = GetTickCount64() + 250;
                            focusCompletion = true;
                            ImGui::OpenPopup("command-completion");
                        } catch (...) { commandState.Append(Utf8(Label(110))); }
                    }
                    const auto popupWidth = (std::min)(480.0f, ImGui::GetIO().DisplaySize.x - 16);
                    const auto popupHeight = (std::max)(80.0f, inputPosition.y - ImGui::GetWindowPos().y - 8);
                    ImGui::SetNextWindowPos(inputPosition, ImGuiCond_Always, ImVec2(0, 1));
                    ImGui::SetNextWindowSizeConstraints(ImVec2(popupWidth, 0), ImVec2(popupWidth, popupHeight));
                    if (ImGui::BeginPopup("command-completion")) {
                        if (completionPending && GetTickCount64() >= nextCompletionRefresh) {
                            nextCompletionRefresh = GetTickCount64() + 250;
                            try {
                                const auto response = client.Request({{"kind", "complete"}, {"text", completionText}});
                                if (response.value("ok", false)) {
                                    if (completions != response.at("replacements")) { focusCompletion = true; }
                                    completions = response.at("replacements");
                                    completionLabels = response.value("candidates", nlohmann::json::array());
                                    completionPending = response.value("pending", false);
                                    completionDescription = response.value("description", "");
                                }
                            } catch (...) { completionPending = false; }
                        }
                        if (!completionDescription.empty()) { ImGui::TextWrapped("%s", completionDescription.c_str()); }
                        if (completions.empty()) { ImGui::TextUnformatted(Utf8(Label(completionPending ? 118 : 115)).c_str()); }
                        size_t completionIndex = 0;
                        for (const auto& completion : completions) {
                            const auto text = completion.get<std::string>();
                            const auto label = completionIndex < completionLabels.size() ? completionLabels[completionIndex].get<std::string>() : text;
                            if (completionIndex == 0 && focusCompletion) {
                                ImGui::SetKeyboardFocusHere();
                                ImGui::SetNavCursorVisible(true);
                                focusCompletion = false;
                            }
                            if (ImGui::Selectable(label.c_str())) {
                                editor.SetText(text + " "); editor.MoveEnd(); focusInput = true;
                                editor.SetHandleKeyboardInputs(false);
                            }
                            if (validateInput && scenarioPhase == 3 && validationScenario == 5 &&
                                !completionPending && completionIndex == 0 && ImGui::IsItemFocused()) {
                                ImGui::GetIO().AddKeyEvent(ImGuiKey_Enter, true);
                                scenarioPhase = 4;
                            }
                            if (completionIndex++ == 0) { ImGui::SetItemDefaultFocus(); }
                        }
                        ImGui::EndPopup();
                    }
                    ImGui::SameLine();
                    if (ImGui::Button(Utf8(Label(114)).c_str())) { ImGui::OpenPopup("command-history"); }
                    if (ImGui::BeginPopup("command-history")) {
                        const auto& history = commandState.history;
                        if (history.empty()) { ImGui::TextUnformatted(empty.c_str()); }
                        for (size_t index = history.size(); index > 0; --index) {
                            ImGui::PushID(static_cast<int>(index));
                            if (ImGui::Selectable(history[index - 1].c_str())) {
                                editor.SetText(history[index - 1]); editor.MoveEnd(); focusInput = true;
                                editor.SetHandleKeyboardInputs(false);
                            }
                            ImGui::PopID();
                        }
                        ImGui::EndPopup();
                    }
                    const auto inputHeight = ImGui::GetTextLineHeightWithSpacing() * 3;
                    const auto hintHeight = ImGui::GetTextLineHeightWithSpacing() * 2;
                    const auto outputHeight = (std::max)(40.0f, ImGui::GetContentRegionAvail().y - inputHeight - hintHeight - 12);
                    ImGui::BeginChild("command-output", ImVec2(0, outputHeight), ImGuiChildFlags_Borders,
                                      ImGuiWindowFlags_HorizontalScrollbar);
                    const bool restoring = restoreScroll[1];
                    if (restoring) { ImGui::SetScrollX(scrollPositions[1].x); ImGui::SetScrollY(scrollPositions[1].y); restoreScroll[1] = false; }
                    else { followScroll[1] = ImGui::GetScrollY() >= ImGui::GetScrollMaxY(); }
                    for (const auto& text : commandState.output) { ImGui::TextUnformatted(text.c_str()); }
                    if (commandState.forceOutputScroll || (commandState.followOutput && followScroll[1])) { ImGui::SetScrollHereY(1); }
                    commandState.forceOutputScroll = false;
                    commandState.followOutput = false;
                    if (!restoring) { scrollPositions[1] = ImVec2(ImGui::GetScrollX(), ImGui::GetScrollY()); }
                    ImGui::EndChild();
                    const bool historyUp = focused && ImGui::IsKeyPressed(ImGuiKey_UpArrow, false) &&
                        !ImGui::IsPopupOpen("command-completion") && !ImGui::IsPopupOpen("command-history");
                    const bool historyDown = focused && ImGui::IsKeyPressed(ImGuiKey_DownArrow, false) &&
                        !ImGui::IsPopupOpen("command-completion") && !ImGui::IsPopupOpen("command-history");
                    if ((historyUp || historyDown) && !commandState.history.empty()) {
                        if (historyIndex < 0) { historyDraft = editor.GetText(); historyIndex = static_cast<int>(commandState.history.size()); }
                        historyIndex = (std::clamp)(historyIndex + (historyUp ? -1 : 1), 0, static_cast<int>(commandState.history.size()));
                        editor.SetText(historyIndex == static_cast<int>(commandState.history.size()) ? historyDraft : commandState.history[historyIndex]);
                        editor.SetHandleKeyboardInputs(false);
                    }
                    ImGui::BeginChild("command-input", ImVec2(0, inputHeight), ImGuiChildFlags_Borders,
                                      ImGuiWindowFlags_HorizontalScrollbar);
                    inputPosition = ImGui::GetWindowPos();
                    if (focusInput) { ImGui::SetWindowFocus(); focusInput = false; }
                    editor.Render("##editor", ImVec2(-1, inputHeight));
                    editorFocused = ImGui::IsWindowFocused();
                    ImGui::EndChild();
                    ImGui::TextWrapped("%s", Utf8(Label(117)).c_str());
                    ImGui::EndTabItem();
                }
                if (ImGui::BeginTabItem(modules.c_str(), nullptr, restoreTab == 2 ? ImGuiTabItemFlags_SetSelected : 0)) {
                    selectedTab = 2;
                    if (moduleStates.empty()) { ImGui::TextUnformatted(empty.c_str()); }
                    for (const auto& entry : moduleStates) {
                        const char* states[]{"loading", "active", "stopped", "failed", "unconfirmed"};
                        const auto state = entry.value("state", "");
                        const auto selected = std::find(std::begin(states), std::end(states), state);
                        const auto label = 120 + static_cast<unsigned>(std::distance(std::begin(states), selected));
                        const auto line = entry.at("name").get<std::string>() + " - " + Utf8(Label(label));
                        ImGui::TextUnformatted(line.c_str());
                    }
                    ImGui::EndTabItem();
                }
                ImGui::EndTabBar();
                restoreTab = -1;
            }
            ImGui::End(); ImGui::Render();
            if (validateInput && GetTickCount64() >= nextValidationReport && validationPhase < 9) {
                nextValidationReport = GetTickCount64() + 1000;
                client.Request({{"kind", "validation_result"}, {"result", {
                    {"phase", validationPhase}, {"frames", validationFrames},
                    {"commands_tab_selected", commandsSelected}, {"command_submitted", commandSubmitted},
                    {"tab_center", {commandTabCenter.x, commandTabCenter.y}},
                    {"run_center", {runButtonCenter.x, runButtonCenter.y}},
                    {"result", commandState.lastResult}}}});
            }
            if (validateInput && validationPhase == 8 && commandTabSelected) {
                POINT currentCursor{}; GetCursorPos(&currentCursor);
                client.Request({{"kind", "validation_result"}, {"result", {
                    {"commands_tab_selected", commandsSelected}, {"command_submitted", commandSubmitted},
                    {"result", commandState.lastResult}, {"text_input_method", "programmatic editor fill"},
                    {"returned_to_commands", true}, {"retained_output_entries", commandState.output.size()},
                    {"mouse_input_method", "public ImGui IO events on UI thread"},
                    {"execution_input_method", "Ctrl+Enter through public ImGui IO"},
                    {"cursor_unchanged", currentCursor.x == originalCursor.x && currentCursor.y == originalCursor.y},
                    {"foreground_unchanged", GetForegroundWindow() == originalForeground}}}});
                validationPhase = 9;
                ImGui::GetIO().ConfigFlags &= ~ImGuiConfigFlags_NoMouseCursorChange;
                ImGui::GetIO().ConfigDebugIgnoreFocusLoss = false;
                ImGui::GetIO().ConfigInputTrickleEventQueue = true;
            } else if (validateInput && validationPhase == 7) {
                validationPhase = 8;
            } else if (validateInput && validationPhase == 6 && !commandState.lastResult.is_null()) {
                validationPhase = 7;
            }
            device->Clear(0, nullptr, D3DCLEAR_TARGET, D3DCOLOR_XRGB(20, 23, 29), 1, 0);
            if (SUCCEEDED(device->BeginScene())) { ImGui_ImplDX9_RenderDrawData(ImGui::GetDrawData()); device->EndScene(); }
            if (validateInput && (validationScenario == 5 ? scenarioPhase == 5 : scenarioPhase == 3) && GetTickCount64() >= scenarioReportAt &&
                !completionPending && (validationScenario == 8 || commandState.pending.empty())) {
                client.Request({{"kind", "validation_result"}, {"result", {
                    {"scenario", validationScenario}, {"method", "public ImGui keyboard events"},
                    {"editor", editor.GetText()}, {"candidates", completionLabels},
                    {"pending", commandState.pending},
                    {"output", commandState.output}, {"result", commandState.lastResult}}}});
                SetPropW(frameWindow, L"XalKit.Capture.Request", reinterpret_cast<HANDLE>(1));
                scenarioPhase = 0;
            }
            if (validateInput && GetPropW(frameWindow, L"XalKit.Capture.Request")) {
                RemovePropW(frameWindow, L"XalKit.Capture.Request");
                auto path = catalog.value("validation_capture", "");
                if (path.empty()) {
                    wchar_t workspace[32768]{};
                    const auto length = GetEnvironmentVariableW(L"H5_WORKSPACE", workspace, 32768);
                    if (length && length < 32768) { path = Utf8((std::filesystem::path(workspace) / L".local/xalkit/console-input-capture.bmp").wstring()); }
                }
                if (!path.empty()) { SetPropW(frameWindow, L"XalKit.Capture.Result", reinterpret_cast<HANDLE>(SaveConsoleCapture(path) ? 1 : 2)); }
            }
            const auto presentState = device->Present(nullptr, nullptr, nullptr, nullptr);
            SetPropW(frameWindow, L"XalKit.Graphics.Present", reinterpret_cast<HANDLE>(static_cast<uintptr_t>(presentState) + 1));
            Sleep(consoleOwnsCursor ? 33 : 100);
        }
    } catch (const std::exception& error) {
        heroes5_sdk::Diagnostics().Write(heroes5_sdk::LogLevel::Error, "xkit.console", error.what());
        InterlockedExchange(&XalKitConsoleStatus, -1);
    } catch (...) {
        heroes5_sdk::Diagnostics().Write(heroes5_sdk::LogLevel::Error, "xkit.console", "Console worker failed with an unknown exception");
        InterlockedExchange(&XalKitConsoleStatus, -1);
    }
    // Publish disconnection before HWND/backend teardown; cached DLLs must not
    // claim readiness after their UI has gone.
    InterlockedCompareExchange(&XalKitConsoleStatus, -2, 2);
    if (graphicsBackend) { ImGui_ImplDX9_Shutdown(); }
    if (windowBackend) { ImGui_ImplWin32_Shutdown(); }
    if (ImGui::GetCurrentContext()) { ImGui::DestroyContext(); }
    if (device) { device->Release(); device = nullptr; }
    if (graphics) { graphics->Release(); graphics = nullptr; }
    if (graphicsLibrary) { FreeLibrary(graphicsLibrary); graphicsLibrary = nullptr; }
    if (gameInputDisabled && IsWindow(gameWindow)) { EnableWindow(gameWindow, TRUE); }
    while (cursorHideAdjustments) { ShowCursor(TRUE); --cursorHideAdjustments; }
    gameInputDisabled = false;
    if (gameWindow) { RemovePropW(gameWindow, FrameClass); }
    if (buttonHost) { DestroyWindow(buttonHost); buttonHost = nullptr; }
    if (frameWindow) { DestroyWindow(frameWindow); frameWindow = nullptr; }
    UnregisterClassW(ButtonClass, module);
    UnregisterClassW(FrameClass, module);
    if (sdkProcess) { CloseHandle(sdkProcess); sdkProcess = nullptr; }
    FreeLibraryAndExitThread(module, 0);
}
}
extern "C" __declspec(dllexport) DWORD WINAPI XalKitConsoleAttach(void* memory) {
    std::lock_guard<std::mutex> lifecycle(consoleLifecycle);
    if (!memory) { return 1; }
    const auto& connection = *static_cast<const heroes5_sdk::ConsoleConnection*>(memory);
    DWORD windowOwner = 0;
    GetWindowThreadProcessId(connection.gameWindow, &windowOwner);
    if (connection.size != sizeof(connection) || connection.version != 1 || windowOwner != GetCurrentProcessId() ||
        !connection.sdkModule || !GetProcAddress(connection.sdkModule, "Heroes5PluginCoreVersion") ||
        !GetProcAddress(connection.sdkModule, "Heroes5PluginReadDiagnostics")) { return 1; }
    wchar_t hostId[32]{}, hostCreated[32]{};
    if (!GetEnvironmentVariableW(L"XALKIT_CONSOLE_HOST_PID", hostId, 32) ||
        !GetEnvironmentVariableW(L"XALKIT_CONSOLE_HOST_CREATED", hostCreated, 32)) { return 1; }
    const auto previous = InterlockedCompareExchange(&XalKitConsoleStatus, 1, 0);
    if (previous != 0) { return previous > 0 ? 0 : 1; }
    try {
        wchar_t executable[32768]{};
        if (!GetModuleFileNameW(nullptr, executable, 32768)) { throw std::runtime_error("game_path_missing"); }
        h5::VerifyGame(executable, xkit::GraphicsFacadeSha256);
        sdkProcess = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, std::stoul(hostId));
        FILETIME created{}, exited{}, kernel{}, user{};
        if (!sdkProcess || !GetProcessTimes(sdkProcess, &created, &exited, &kernel, &user) ||
            ((uint64_t(created.dwHighDateTime) << 32) | created.dwLowDateTime) != std::stoull(hostCreated)) {
            throw std::runtime_error("sdk_host_changed");
        }
        gameWindow = connection.gameWindow;
        consoleStop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (!consoleStop) { throw std::runtime_error("console_stop_failed"); }
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
            reinterpret_cast<LPCWSTR>(XalKitConsoleAttach), &module)) { throw std::runtime_error("console_reference_failed"); }
        consoleThread = CreateThread(nullptr, 0, ConsoleThread, nullptr, 0, nullptr);
        if (!consoleThread) { FreeLibrary(module); throw std::runtime_error("console_thread_failed"); }
        return 0;
    } catch (...) {
        // Keep common handle cleanup below while preserving the actual cause.
        try { throw; }
        catch (const std::exception& error) {
            heroes5_sdk::Diagnostics().Write(heroes5_sdk::LogLevel::Error, "xkit.console.attach", error.what());
        } catch (...) {
            heroes5_sdk::Diagnostics().Write(heroes5_sdk::LogLevel::Error, "xkit.console.attach", "Console attachment failed with an unknown exception");
        }
        if (sdkProcess) { CloseHandle(sdkProcess); sdkProcess = nullptr; }
        if (consoleStop) { CloseHandle(consoleStop); consoleStop = nullptr; }
        InterlockedExchange(&XalKitConsoleStatus, -1); return 1;
    }
}
#pragma comment(linker, "/EXPORT:XalKitConsoleAttach=_XalKitConsoleAttach@4")
extern "C" __declspec(dllexport) DWORD WINAPI XalKitConsoleState(void*) {
    return static_cast<DWORD>(InterlockedCompareExchange(&XalKitConsoleStatus, 0, 0));
}
#pragma comment(linker, "/EXPORT:XalKitConsoleState=_XalKitConsoleState@4")
extern "C" __declspec(dllexport) DWORD WINAPI XalKitConsoleAbi(void*) { return 1; }
#pragma comment(linker, "/EXPORT:XalKitConsoleAbi=_XalKitConsoleAbi@4")
extern "C" __declspec(dllexport) DWORD WINAPI XalKitConsoleDetach(void*) {
    std::lock_guard<std::mutex> lifecycle(consoleLifecycle);
    if (consoleThread) {
        if (stopGate.Request() && !SetEvent(consoleStop)) {
            stopGate.Cancel(); return GetLastError();
        }
        // A timeout retains the DLL/thread handles; unloading is not confirmed.
        const auto result = WaitForSingleObject(consoleThread, 8000);
        if (result != WAIT_OBJECT_0) {
            if (stopGate.Cancel()) { ResetEvent(consoleStop); }
            return result == WAIT_TIMEOUT ? ERROR_TIMEOUT : GetLastError();
        }
        CloseHandle(consoleThread); consoleThread = nullptr;
    }
    if (consoleStop) { CloseHandle(consoleStop); consoleStop = nullptr; }
    stopping = false; resizing = false;
    stopGate.Reset();
    InterlockedExchange(&XalKitConsoleStatus, 0);
    return 0;
}
#pragma comment(linker, "/EXPORT:XalKitConsoleDetach=_XalKitConsoleDetach@4")
