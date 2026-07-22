# Decisions

- Use `pyproject.toml` as the single dependency declaration; `requirements.txt` delegates to the local project for compatibility.
- Prefer `EMULATOR_MCP_HOME`; retain target-specific bridge-directory variables for compatibility.
- Document direct checkout use as a development path, while keeping `init` plus `.emulator-mcp` as the isolated deployment path.
- Treat `EMULATOR_MCP_HOME` as authoritative in the Lua bridge because BizHawk's Lua Console may expose `debug.getinfo(...).source` only as `main`; reject ambiguous path resolution instead of timing out silently.
- Keep deployment as package installation plus `emulator-mcp init`; do not add an installer or background service.
- Validate packaging in CI by running the installed CLI, `init`, `pip check`, unit tests, and a wheel build.
- Document CLI commands, MCP tools, legacy aliases, and scenario operations from source signatures instead of maintaining a separate command registry.
- Expose memory inspection as one bounded `read_memory` range command with an explicit domain and uppercase hexadecimal response.
- Normalize Mega Drive `M68K BUS` addresses from sign-extended 32-bit form to the core's 24-bit bus before dispatch.
- Keep path assertions aligned with production's canonicalization so Windows 8.3 aliases do not create false failures.
