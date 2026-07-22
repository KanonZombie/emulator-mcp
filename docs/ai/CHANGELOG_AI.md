# AI Changelog

## 2026-07-22

- Added a focused regression test for normalized and empty runtime overrides.
- Improved package metadata, CI packaging gates, and repository artifact exclusions.
- Reworked the README around installation, MCP configuration, BizHawk startup, scenarios, and release checks.
- Added complete CLI, MCP tool, legacy alias, and scenario operation reference tables to the README.
- Added a direct-checkout PowerShell launch example with explicit `EMULATOR_MCP_HOME`, BizHawk, bridge, and ROM paths.
- Replaced machine-specific direct-checkout paths in the README with generic Windows examples.
- Fixed Lua bridge home resolution for BizHawk Lua Console sessions by honoring `EMULATOR_MCP_HOME` and removing the PowerShell path guess.
- Pending: run the optional BizHawk smoke matrix on a machine with BizHawk and legal local ROMs.
