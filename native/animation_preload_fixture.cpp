#include <windows.h>
// Test-only import dependency. Never installed or included in a player archive.
extern "C" __declspec(dllexport) void* GrannyInt32Type = reinterpret_cast<void*>(1);
