if(POLICY CMP0168)
    # Populate dependencies directly; avoid nested MSBuild tracking paths on Windows.
    cmake_policy(SET CMP0168 NEW)
endif()
include(FetchContent)
set(CMAKE_OBJECT_PATH_MAX 180)
FetchContent_Declare(xalkit_imgui
    URL https://codeload.github.com/ocornut/imgui/zip/ed73ef4e84c9488256c5710de6ff1ebc2c9a8496
    URL_HASH SHA256=a41bb163efce53e82c580a9e2f89b1396449922a90bbbefd704cd9f87b525007
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_Declare(xalkit_imterm
    URL https://codeload.github.com/Organic-Code/ImTerm/zip/c720144e5b03b068257309f9e5391af26b903c9e
    URL_HASH SHA256=1689f256c383e3f169ccdeab9ce223c92eb0abb3005a7a7d7b3d49e94d853e49
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_Declare(xalkit_editor
    URL https://codeload.github.com/BalazsJako/ImGuiColorTextEdit/zip/ca2f9f1462e3b60e56351bc466acda448c5ea50d
    URL_HASH SHA256=dae20d7e89b8293d2f00193f51a6cd6149fb0d75d042e6ba1243cf1b68aaa5c9
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(xalkit_imgui xalkit_imterm xalkit_editor)
set(JSON_BuildTests OFF CACHE BOOL "" FORCE)
set(JSON_Install OFF CACHE BOOL "" FORCE)
FetchContent_Declare(xalkit_json
    URL https://codeload.github.com/nlohmann/json/zip/55f93686c01528224f448c19128836e7df245f72
    URL_HASH SHA256=0746352e4e9532e7aeabbcbaa79079b6bc6008e9261e4d459c9486a9b48a172e
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(xalkit_json)
set(console_notices "Third-party software used by XalKit Console\nVersions are pinned in native/console_module.cmake.\n\n")
foreach(library IN ITEMS xalkit_imgui xalkit_imterm xalkit_editor xalkit_json)
    if(library STREQUAL "xalkit_imgui")
        set(license_name "LICENSE.txt")
        set(display_name "Dear ImGui")
    elseif(library STREQUAL "xalkit_json")
        set(license_name "LICENSE.MIT")
        set(display_name "nlohmann JSON")
    elseif(library STREQUAL "xalkit_editor")
        set(license_name "LICENSE")
        set(display_name "ImGuiColorTextEdit")
    else()
        set(license_name "LICENSE")
        set(display_name "ImTerm")
    endif()
    file(READ "${${library}_SOURCE_DIR}/${license_name}" license_text)
    string(APPEND console_notices "===== ${display_name} =====\n${license_text}\n\n")
endforeach()
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>/XalKitConsole.LICENSES.txt"
    CONTENT "${console_notices}")
find_package(Python3 REQUIRED COMPONENTS Interpreter)
enable_language(RC)
set(console_resources "${CMAKE_CURRENT_BINARY_DIR}/console_strings.rc")
add_custom_command(OUTPUT "${console_resources}"
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/native-ui-resources.py" "${console_resources}"
    DEPENDS ../scripts/native-ui-resources.py ../locale/en/LC_MESSAGES/xalkit.mo ../locale/ru/LC_MESSAGES/xalkit.mo
    VERBATIM)
add_library(xalkit_console SHARED console_module.cpp "${console_resources}"
    "${xalkit_imgui_SOURCE_DIR}/imgui.cpp" "${xalkit_imgui_SOURCE_DIR}/imgui_draw.cpp"
    "${xalkit_imgui_SOURCE_DIR}/imgui_tables.cpp" "${xalkit_imgui_SOURCE_DIR}/imgui_widgets.cpp"
    "${xalkit_imgui_SOURCE_DIR}/backends/imgui_impl_win32.cpp"
    "${xalkit_imgui_SOURCE_DIR}/backends/imgui_impl_dx9.cpp"
    "${xalkit_editor_SOURCE_DIR}/TextEditor.cpp")
set_target_properties(xalkit_console PROPERTIES OUTPUT_NAME XalKitConsole)
target_include_directories(xalkit_console PRIVATE "${xalkit_imgui_SOURCE_DIR}"
    "${xalkit_imgui_SOURCE_DIR}/backends" "${xalkit_imterm_SOURCE_DIR}/include" "${xalkit_editor_SOURCE_DIR}")
target_compile_definitions(xalkit_console PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN)
target_compile_options(xalkit_console PRIVATE /utf-8)
target_link_libraries(xalkit_console PRIVATE user32 gdi32 imm32 dwmapi bcrypt ws2_32 nlohmann_json::nlohmann_json)
add_executable(console_activation_test console_activation_test.cpp)
add_test(NAME console_requires_sdk COMMAND console_activation_test $<TARGET_FILE:xalkit_console>)
