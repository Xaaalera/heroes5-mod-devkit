#pragma once
#include <winsock2.h>
#include <windows.h>
#include <nlohmann/json.hpp>
#include <string>
#include <stdexcept>

namespace heroes5_sdk {
class ConsoleClient {
    unsigned short port_ = 0;
    std::string token_;
    mutable std::string pendingCheckpoint_;
public:
    ConsoleClient() {
        char port[16]{}, token[128]{};
        if (!GetEnvironmentVariableA("XALKIT_CONSOLE_PORT", port, sizeof(port)) ||
            !GetEnvironmentVariableA("XALKIT_CONSOLE_TOKEN", token, sizeof(token))) {
            throw std::runtime_error("console_broker_missing");
        }
        const auto number = std::stoul(port);
        if (!number || number > 65535) { throw std::runtime_error("console_broker_port_invalid"); }
        port_ = static_cast<unsigned short>(number); token_ = token;
        WSADATA data{};
        if (WSAStartup(MAKEWORD(2, 2), &data) != 0) { throw std::runtime_error("console_socket_start_failed"); }
    }
    ~ConsoleClient() { WSACleanup(); }
    ConsoleClient(const ConsoleClient&) = delete;
    nlohmann::json RestoreUiState() const {
        auto response = Request({{"kind", "ui_state_get"}});
        if (!response.value("ok", false)) { throw std::runtime_error("console_restore_rejected"); }
        const auto size = response.at("size").get<size_t>();
        if (size > 32 * 1024 * 1024) { throw std::runtime_error("console_restore_exceeds_limit"); }
        std::string encoded = response.at("text");
        while (encoded.size() < size) {
            response = Request({{"kind", "ui_state_get"}, {"offset", encoded.size()}, {"version", response.at("version")}});
            const auto part = response.at("text").get<std::string>();
            if (!response.value("ok", false) || part.empty()) { throw std::runtime_error("console_restore_incomplete"); }
            encoded += part;
        }
        return encoded.empty() ? nlohmann::json() : nlohmann::json::parse(encoded);
    }
    void SaveUiState(const nlohmann::json& snapshot) const {
        // ASCII JSON can be split without breaking UTF8 code points; each
        // request remains below the existing transport byte limit.
        const auto encoded = snapshot.dump(-1, ' ', true);
        if (!pendingCheckpoint_.empty()) {
            if (!Request({{"kind", "ui_state_abort"}, {"id", pendingCheckpoint_}}).value("ok", false)) {
                throw std::runtime_error("console_checkpoint_abort_unconfirmed");
            }
            pendingCheckpoint_.clear();
        }
        static volatile LONG sequence = 0;
        std::string id = "state-" + std::to_string(GetCurrentProcessId()) + "-" +
            std::to_string(GetTickCount64()) + "-" + std::to_string(InterlockedIncrement(&sequence));
        pendingCheckpoint_ = id;
        try {
            const auto begun = Request({{"kind", "ui_state_begin"}, {"id", id}});
            if (!begun.value("ok", false)) { throw std::runtime_error("console_checkpoint_rejected"); }
            // Older brokers return a server-generated ID; retain compatibility
            // once that response arrives. Current brokers preserve the known ID.
            id = begun.at("id").get<std::string>();
            pendingCheckpoint_ = id;
            for (size_t offset = 0, index = 0; offset < encoded.size(); offset += 16000, ++index) {
                const auto saved = Request({{"kind", "ui_state_part"}, {"id", id}, {"index", index},
                                            {"text", encoded.substr(offset, 16000)}});
                if (!saved.value("ok", false)) { throw std::runtime_error("console_checkpoint_part_rejected"); }
            }
            if (!Request({{"kind", "ui_state_commit"}, {"id", id}}).value("ok", false)) {
                throw std::runtime_error("console_checkpoint_commit_rejected");
            }
            pendingCheckpoint_.clear();
        } catch (...) {
            try {
                if (Request({{"kind", "ui_state_abort"}, {"id", id}}).value("ok", false)) { pendingCheckpoint_.clear(); }
            } catch (...) { }
            throw;
        }
    }
    nlohmann::json Request(nlohmann::json request) const {
        request["token"] = token_;
        const auto connection = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (connection == INVALID_SOCKET) { throw std::runtime_error("console_socket_failed"); }
        struct Close { SOCKET value; ~Close() { closesocket(value); } } close{connection};
        DWORD timeout = 800;
        setsockopt(connection, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));
        setsockopt(connection, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));
        sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(port_);
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (connect(connection, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR) {
            throw std::runtime_error("console_broker_disconnected");
        }
        const auto message = request.dump() + "\n";
        for (size_t offset = 0; offset < message.size();) {
            const auto sent = send(connection, message.data() + offset, static_cast<int>(message.size() - offset), 0);
            if (sent <= 0) { throw std::runtime_error("console_send_unconfirmed"); }
            offset += sent;
        }
        std::string received;
        char bytes[4096];
        while (received.size() <= 65536) {
            const auto count = recv(connection, bytes, sizeof(bytes), 0);
            if (count <= 0) { throw std::runtime_error("console_response_unconfirmed"); }
            received.append(bytes, count);
            const auto newline = received.find('\n');
            if (newline != std::string::npos) {
                if (newline > 65536) { break; }
                return nlohmann::json::parse(received.begin(), received.begin() + newline);
            }
        }
        throw std::runtime_error("console_response_exceeds_limit");
    }
};
}
