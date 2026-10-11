#pragma once
#include <filesystem>
#include <fstream>
#include <set>
#include <nlohmann/json.hpp>

namespace heroes5_sdk {
inline nlohmann::json DiscoverModuleCatalog(const std::filesystem::path& workspace,const char* language) {
    namespace fs=std::filesystem;
    std::set<fs::path> roots{workspace/"plugins",workspace/"mods"};
    for (const auto& root : {workspace/"plugins",workspace/"mods"}) {
        std::error_code error;
        for (const auto& entry : fs::directory_iterator(root,error)) {
            const auto canonical=fs::weakly_canonical(entry.path(),error);
            if (!error && (canonical.parent_path().filename()=="mods" || canonical.parent_path().filename()=="plugins")) {
                roots.insert(canonical.parent_path());
            }
        }
    }
    nlohmann::json catalog=nlohmann::json::object();
    std::set<fs::path> seen;
    for (const auto& root : roots) {
        std::error_code error;
        for (const auto& entry : fs::directory_iterator(root,error)) {
            if (catalog.size()>=64) { break; }
            const auto source=fs::weakly_canonical(entry.path(),error);
            if (error || !entry.is_directory(error) || !seen.insert(source).second) { continue; }
            auto marker=source/"xalkit.json";
            if (!fs::is_regular_file(marker,error)) { marker=source/"mod.json"; }
            std::ifstream stream(marker);
            if (!stream) { continue; }
            const auto metadata=nlohmann::json::parse(stream,nullptr,false);
            if (!metadata.is_object()) { continue; }
            const auto id=source.filename().string();
            std::string name=id;
            const auto names=metadata.find("display_name");
            if (names!=metadata.end()) {
                if (names->is_string()) { name=names->get<std::string>(); }
                else if (names->is_object()) {
                    const auto translated=names->find(language);
                    if (translated!=names->end() && translated->is_string()) { name=translated->get<std::string>(); }
                }
            }
            const auto category=metadata.find("category");
            catalog[id]={{"name",id},{"display_name",name},{"state","available"},
                {"category",category!=metadata.end() && category->is_string() ? category->get<std::string>() : "mod"}};
        }
    }
    return catalog;
}
inline nlohmann::json MergeModuleCatalog(nlohmann::json catalog,const nlohmann::json& runtime) {
    for (const auto& entry : runtime) {
        const auto id=entry.value("name",std::string{});
        if (id.empty()) { continue; }
        if (!catalog.contains(id)) { catalog[id]={{"name",id},{"display_name",id},{"category","mod"}}; }
        catalog[id]["state"]=entry.value("state","unknown");
    }
    auto result=nlohmann::json::array();
    for (const auto& entry : catalog.items()) { result.push_back(entry.value()); }
    return result;
}
}
