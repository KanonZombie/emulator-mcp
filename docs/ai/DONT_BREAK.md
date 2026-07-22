# Do Not Break

- Keep the Python server and Lua bridge on the same target-specific runtime directory.
- Do not infer the bridge home from a relative or `main` Lua chunk name; use `EMULATOR_MCP_HOME` to avoid silently writing runtime files under BizHawk's working directory.
- Preserve the atomic command/response file protocol; no socket or BizHawk plugin is required.
- Keep `emulator-mcp init` idempotent and refresh the copied bridge on package upgrades.
- Do not add ROMs, savestates, screenshots, logs, runtime command files, virtual environments, or build output to version control.
- Preserve the legacy `bizhawk_*` Game Boy-compatible tool aliases.
- Keep command-reference tables aligned with the actual CLI branches and `@mcp.tool` signatures.
- Keep memory reads bounded to 64 KiB and validate the requested domain before reading.
- Normalize sign-extended 68000 addresses to 24 bits only for the `M68K BUS` domain.
