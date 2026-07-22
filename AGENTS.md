# Repository Guidelines

## Project Structure & Module Organization

- `server/mcp_server.py` contains the MCP tools, CLI entry point, scenario runner, and file-based BizHawk protocol.
- `server/gb_gpu_renderer.py` renders Game Boy diagnostic snapshots.
- `bizhawk/bridge.lua` is the portable script loaded by BizHawk.
- `scenarios/{gb,md,nes,snes}/` stores target-specific JSON flows; `scenarios/pairs/` stores GB/Mega Drive comparisons.
- `server/tests/` contains unit tests and the opt-in BizHawk smoke runner.
- `runtime/`, `build/`, `dist/`, virtual environments, and `.emulator-mcp/` are generated and must remain untracked.

## Build, Test, and Development Commands

Run these from the repository root on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m unittest discover -s server\tests -v
.\.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
```

The installation exposes `emulator-mcp`; the test command needs no emulator; the wheel command verifies packaging. For release-level bridge checks, run `.\.venv\Scripts\python.exe server\tests\bizhawk_smoke.py --bizhawk <EmuHawk.exe>` with BizHawk installed.

## Coding Style & Naming Conventions

Use Python 3.10+ syntax, four-space indentation, type hints on public functions, `snake_case` for functions and files, `PascalCase` for classes, and uppercase constants. Group imports as standard library, third party, then local modules. Match the existing straightforward Lua style in `bridge.lua`. Name scenarios with lowercase snake case and IDs such as `nes.title_start`. No formatter or linter is configured; keep diffs focused and follow surrounding code.

## Testing Guidelines

Tests use `unittest`. Put files under `server/tests/` as `test_*.py`, methods as `test_*`, and isolate filesystem or environment changes with temporary directories and mocks. Add one focused regression test for non-trivial behavior. No coverage threshold is configured. Changes to the bridge or target handling should also pass the relevant BizHawk smoke target.

## Commit & Pull Request Guidelines

This repository has no commit history yet, so no local convention is established. Use short, imperative Conventional Commit subjects, for example `fix(md): release held buttons after tap`. Pull requests should explain behavior and risk, link any issue, list exact test commands and results, and include screenshots or smoke-test output when rendering or emulator behavior changes. Never commit ROMs, savestates, screenshots, or runtime command files.
