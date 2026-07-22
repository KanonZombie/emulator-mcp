# Project State

Updated: 2026-07-22

- The project is a Windows Python package exposing BizHawk through an MCP stdio server.
- `server/mcp_server.py` owns MCP tools, the CLI, scenario execution, and the file protocol.
- `bizhawk/bridge.lua` is copied into a project home by `emulator-mcp init`.
- `EMULATOR_MCP_HOME` is the preferred shared home for Python and BizHawk.
- `bizhawk/bridge.lua` uses `EMULATOR_MCP_HOME` first because BizHawk's Lua Console reports the loaded chunk as `source = main`; it only derives a home from an absolute script source when available.
- Runtime output belongs under `.emulator-mcp/runtime/` and is ignored by Git.
- A source checkout can also be used directly by setting `EMULATOR_MCP_HOME` and launching BizHawk with the repository bridge; that layout writes to `runtime/<target>/`.
- Unit tests run without BizHawk; the smoke runner is opt-in and needs a local emulator and ROM.
- README now contains the complete CLI, MCP tool, legacy alias, and scenario operation reference.
- The README direct-checkout example uses generic Windows paths and keeps the repository root as `EMULATOR_MCP_HOME`.
- Bridge/server version is `1.1.0`; `read_memory` reads up to 64 KiB from an exact BizHawk memory domain and returns uppercase hexadecimal bytes.
- Mega Drive `M68K BUS` reads normalize sign-extended addresses such as `0xFFFFE75E` to the 24-bit bus address `0xFFE75E`.
