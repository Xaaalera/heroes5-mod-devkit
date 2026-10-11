#include "module_catalog.hpp"
#include <windows.h>
#include <iostream>
#include <stdexcept>

int main(int count,char** arguments) {
    namespace fs=std::filesystem;
    if (count==2) {
        std::cout<<heroes5_sdk::DiscoverModuleCatalog(arguments[1],"ru").dump(2)<<'\n';return 0;
    }
    const auto root=fs::temp_directory_path()/("xkit-module-catalog-"+std::to_string(GetCurrentProcessId())+"-"+std::to_string(GetTickCount64()));
    if (!fs::create_directory(root)) { return 1; }
    try {
        for (const auto& name : {"plugins/predictor","plugins/proof","mods/reference","mods/broken"}) {
            fs::create_directories(root/name);
        }
        std::ofstream(root/"plugins/predictor/xalkit.json")<<R"({"kind":"native","display_name":{"en":"Predictor","ru":"Predictor RU"}})";
        std::ofstream(root/"plugins/proof/xalkit.json")<<R"({"kind":"native","category":"sdk-test"})";
        std::ofstream(root/"mods/reference/mod.json")<<R"({"id":"reference","display_name":{"en":"Reference","ru":"Reference RU"}})";
        std::ofstream(root/"mods/broken/mod.json")<<"invalid-json";
        const auto catalog=heroes5_sdk::DiscoverModuleCatalog(root,"en");
        if (catalog.size()!=3 || catalog["reference"]["state"]!="available" ||
            catalog["reference"]["display_name"]!="Reference" || catalog["proof"]["category"]!="sdk-test") {
            throw std::runtime_error("Available resource/native mods or test category missing");
        }
        if (heroes5_sdk::DiscoverModuleCatalog(root,"ru")["predictor"]["display_name"]!="Predictor RU") {
            throw std::runtime_error("Module display-name localization lost");
        }
        const auto merged=heroes5_sdk::MergeModuleCatalog(catalog,nlohmann::json::array({
            {{"name","predictor"},{"state","active"}},{{"name","external"},{"state","failed"}}}));
        bool active=false,available=false,external=false;
        for (const auto& entry : merged) {
            active=active || (entry["name"]=="predictor" && entry["state"]=="active" && entry["display_name"]=="Predictor");
            available=available || (entry["name"]=="reference" && entry["state"]=="available");
            external=external || (entry["name"]=="external" && entry["state"]=="failed");
        }
        if (!active || !available || !external) { throw std::runtime_error("Catalog confused available and active modules"); }
        fs::remove_all(root);
        std::cout<<"PASS: module inventory, localization, development category and lifecycle merge\n";return 0;
    } catch (const std::exception& error) { fs::remove_all(root);std::cerr<<error.what()<<'\n';return 1; }
}
