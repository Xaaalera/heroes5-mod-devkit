# Included after the selected SDK core project defines heroes5_plugin_bridge.
# SDK_PLUGIN_SOURCES and SDK_INCLUDE_DIRECTORY are provided by the release tool.
get_target_property(core_sources heroes5_plugin_bridge SOURCES)
get_target_property(core_directory heroes5_plugin_bridge SOURCE_DIR)
set(player_sources)
foreach(source IN LISTS core_sources)
    if(IS_ABSOLUTE "${source}" OR source MATCHES "^\\$<")
        list(APPEND player_sources "${source}")
    else()
        list(APPEND player_sources "${core_directory}/${source}")
    endif()
endforeach()
add_library(heroes5_plugin_release SHARED ${player_sources} ${SDK_PLUGIN_SOURCES})
foreach(property COMPILE_DEFINITIONS COMPILE_OPTIONS COMPILE_FEATURES INCLUDE_DIRECTORIES
        LINK_OPTIONS LINK_LIBRARIES CXX_STANDARD CXX_STANDARD_REQUIRED CXX_EXTENSIONS
        MSVC_RUNTIME_LIBRARY POSITION_INDEPENDENT_CODE)
    get_target_property(value heroes5_plugin_bridge ${property})
    if(NOT value MATCHES "-NOTFOUND$")
        set_property(TARGET heroes5_plugin_release PROPERTY ${property} "${value}")
    endif()
endforeach()
target_compile_definitions(heroes5_plugin_release PRIVATE HEROES5_PLUGIN_RELEASE)
target_include_directories(heroes5_plugin_release PRIVATE "${SDK_INCLUDE_DIRECTORY}"
    "${SDK_INCLUDE_DIRECTORY}/../game-api/include")
target_link_libraries(heroes5_plugin_release PRIVATE user32 bcrypt)
