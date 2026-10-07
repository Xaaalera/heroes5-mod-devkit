// Explicit diagnostic fixture only; never loaded into or launched as the game.
#include <windows.h>
#include <iostream>
#include <string>

int main() {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    std::cout << "ready\n" << std::flush;
    std::string command;
    if (std::getline(std::cin, command) && command == "crash") {
        RaiseException(0xe0424242, EXCEPTION_NONCONTINUABLE, 0, nullptr);
        return 2;
    }
    return 0;
}
