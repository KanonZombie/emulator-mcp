<div align="center">

# BizHawk Emulator MCP

Deterministic BizHawk control over MCP for Game Boy, Mega Drive/Genesis, NES, and SNES.

[![CI](https://img.shields.io/github/actions/workflow/status/KanonZombie/emulator-mcp/ci.yml?branch=master&style=for-the-badge&label=CI)](https://github.com/KanonZombie/emulator-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/KanonZombie/emulator-mcp?style=for-the-badge)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/KanonZombie/emulator-mcp?style=for-the-badge)](https://github.com/KanonZombie/emulator-mcp/stargazers)

</div>

## What is this?

This project exposes BizHawk as an MCP server. It sends input, frame-step,
screenshot, reset, and savestate commands through a portable Lua bridge that
exchanges atomic files with Python. JSON scenarios make emulator checks
repeatable without requiring a BizHawk plugin or a socket service.

The package does not include BizHawk or ROMs. Use legally obtained ROMs and
install BizHawk separately.

## Quick start

The supported deployment is a normal Python installation plus one project home
shared by the MCP client and BizHawk.

```powershell
git clone https://github.com/KanonZombie/emulator-mcp.git
Set-Location emulator-mcp

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install .
& .\.venv\Scripts\emulator-mcp.exe init .emulator-mcp
& .\.venv\Scripts\emulator-mcp.exe --version
```

`init` creates the portable bridge and empty runtime directories:

```text
.emulator-mcp/
  bizhawk/bridge.lua
  runtime/gb/
  runtime/md/
  runtime/nes/
  runtime/snes/
```

The command is safe to run again after upgrading the package; it refreshes the
bridge and keeps existing runtime files.

### Start BizHawk

1. Open a legally obtained ROM in BizHawk.
2. Open `Tools > Lua Console`.
3. Load `.emulator-mcp/bizhawk/bridge.lua`.
4. Leave the Lua script running. BizHawk stays paused between commands.

The bridge detects the loaded core and selects its runtime automatically:

| BizHawk core | MCP target | Buttons |
| --- | --- | --- |
| Game Boy / Game Boy Color | `gb` | A, B, Start, Select, D-pad |
| Genesis / Mega Drive | `md` | A, B, C, X, Y, Z, Mode, Start, D-pad |
| NES | `nes` | A, B, Start, Select, D-pad |
| SNES | `snes` | A, B, X, Y, L, R, Start, Select, D-pad |

Run one BizHawk instance per active target. Each instance must load the bridge
from the same `.emulator-mcp` directory configured for the MCP client.

### Run directly from a checkout

For local development, you can use the repository as the project home without
running `init`. In PowerShell, set the environment variable before launching
BizHawk:

```powershell
$env:EMULATOR_MCP_HOME = "<emulator-mcp-path>"

& "<bizhawk-path>\EmuHawk.exe" `
  "--lua=<emulator-mcp-path>\bizhawk\bridge.lua" `
  "<rom-file-path>"
```

This starts BizHawk with the Game Boy ROM and the repository bridge. Runtime
files are written under `runtime/gb/`, which is ignored by Git. The variable
only applies to the current PowerShell session; configure the same
`EMULATOR_MCP_HOME` in the MCP client if it is launched separately.

### Configure an MCP client

Use absolute paths. Replace both placeholders in this example with the paths
on the machine that runs the client:

```json
{
  "mcpServers": {
    "bizhawk": {
      "command": "C:\\path\\to\\emulator-mcp\\.venv\\Scripts\\emulator-mcp.exe",
      "env": {
        "EMULATOR_MCP_HOME": "C:\\path\\to\\emulator-mcp\\.emulator-mcp"
      }
    }
  }
}
```

`EMULATOR_MCP_HOME` is the authoritative project home. The Python server and
the Lua bridge must point to the same directory. If the command is installed
with `pipx`, use `emulator-mcp` as the command and keep the same environment
variable.

### Verify the connection

These commands use the same implementation as the MCP tools and require the
bridge to be running in BizHawk:

```powershell
& .\.venv\Scripts\emulator-mcp.exe --target gb --test bridge-info
& .\.venv\Scripts\emulator-mcp.exe --target gb --test status
& .\.venv\Scripts\emulator-mcp.exe --target nes --test buttons
& .\.venv\Scripts\emulator-mcp.exe --target snes --test tap START 8 2
& .\.venv\Scripts\emulator-mcp.exe --target snes --test screenshot title
```

`bridge-info` should report bridge version `1.0.0`. A timeout normally means
that the Lua script is not running or `EMULATOR_MCP_HOME` points to a different
directory.

## CLI command reference

With no arguments, the installed executable starts the stdio MCP server. With
`--test`, it runs one diagnostic command and prints its result.

### Process commands

| Command | Arguments | Description |
| --- | --- | --- |
| `emulator-mcp` | none | Start the MCP server over stdio. |
| `emulator-mcp --help` | none | Show short help; `-h` is also accepted. |
| `emulator-mcp --version` | none | Print the package version. |
| `emulator-mcp init` | `[directory]` | Copy `bridge.lua` and create runtime directories. Defaults to `.emulator-mcp`. |
| `emulator-mcp --target TARGET --test COMMAND` | see below | Run a diagnostic against `gb`, `md`, `nes`, or `snes`; target defaults to `gb`. |

`init` is idempotent: it refreshes the copied bridge without removing runtime
files. Use this prefix for the diagnostic commands:

```powershell
& .\.venv\Scripts\emulator-mcp.exe --target gb --test COMMAND [ARGUMENTS]
```

### Emulator test commands

| Command | Arguments and defaults | Description |
| --- | --- | --- |
| `status` | none | Return system, frame, and screen size. |
| `bridge-info` | none | Return bridge version, detected system, and capabilities. |
| `buttons` | none | Return BizHawk button names and current states. |
| `step` | `[frames=1]` | Advance neutral input; frames are clamped to 1--600. |
| `tap` | `<button> [hold_frames=8] [release_frames=2]` | Tap a button; hold is clamped to 1--120 and release to 1--600. |
| `hold` | `<button> [frames=1]` | Hold a button; frames are clamped to 1--120. |
| `release` | `[frames=2]` | Release all buttons; frames are clamped to 1--600. |
| `reset` | `[settle_frames=60]` | Reboot and settle with neutral input; frames are clamped to 1--600. |
| `press` | `<button> [frames=1]` | Legacy alias for `tap` with two release frames. |
| `save-state` | `[name=state]` | Save a state under the selected target runtime. |
| `load-state` | `[name=state]` | Load a state from the selected target runtime. |
| `create-baseline` | `[name=baseline]` | Alias for `save-state`. |
| `load-baseline` | `[name=baseline]` | Alias for `load-state`. |
| `screenshot` | `[name=shot]` | Save a screenshot under the selected target runtime. |
| `gb-gpu-snapshot` | `[name=snapshot] [--no-render]` | Capture Game Boy DMG GPU data; target must be `gb`. |
| `run-scenario` | `<path>` | Run a JSON scenario against the selected target. |
| `run-pair-scenario` | `<path>` | Run the GB and Mega Drive entries in a pair scenario. |
| `build-contact-sheet` | `<pair_summary_path>` | Rebuild a pair contact sheet from an existing summary. |
| `build-pair-diffs` | `<pair_summary_path>` | Rebuild crops, diffs, and the pair contact sheet. |

`gb-gpu-snapshot --no-render` skips diagnostic PNG panels. Pair commands use
the pair file's GB/MD entries; the target option is not needed.

```powershell
& .\.venv\Scripts\emulator-mcp.exe --target md --test status
& .\.venv\Scripts\emulator-mcp.exe --target gb --test tap A 8 2
& .\.venv\Scripts\emulator-mcp.exe --target gb --test save-state title_screen
& .\.venv\Scripts\emulator-mcp.exe --test run-pair-scenario scenarios/pairs/title_start.json
```

## MCP tools

Generic tools accept `target="gb"`, `"md"`, `"nes"`, or `"snes"`:

| Tool | Parameters | Description |
| --- | --- | --- |
| `emulator_status` | `target="gb"` | Return system, frame, and screen size. |
| `emulator_bridge_info` | `target="gb"` | Return bridge version and capabilities. |
| `emulator_buttons` | `target="gb"` | Return BizHawk button names and states. |
| `emulator_step` | `target="gb", frames=1` | Advance neutral input; frames 1--600. |
| `emulator_tap` | `target="gb", button="", hold_frames=8, release_frames=2` | Tap one button; hold 1--120 and release 1--600. |
| `emulator_hold` | `target="gb", button="", frames=1` | Hold one button; frames 1--120. |
| `emulator_release` | `target="gb", frames=2` | Release all buttons; frames 1--600. |
| `emulator_reset` | `target="gb", settle_frames=60` | Reboot and settle; frames 1--600. |
| `emulator_screenshot` | `target="gb", name="shot"` | Save a screenshot and return its path. |
| `emulator_save_state` | `target="gb", name="state"` | Save a named state. |
| `emulator_load_state` | `target="gb", name="state"` | Load a named state. |
| `emulator_create_baseline` | `target="gb", name="baseline"` | Alias for saving a named state. |
| `emulator_load_baseline` | `target="gb", name="baseline"` | Alias for loading a named state. |
| `emulator_run_scenario` | `path, target="gb"` | Run a JSON scenario against a target. |

Additional tools cover Game Boy GPU snapshots and GB/Mega Drive pair scenarios:

| Tool | Parameters | Description |
| --- | --- | --- |
| `emulator_gb_gpu_snapshot` | `target="gb", render=True, include_raw=True, output_dir=None, name="snapshot"` | Capture DMG GPU data and optionally render PNG panels. Only target `gb`. |
| `gb_gpu_snapshot` | `render=True, include_raw=True, output_dir=None` | Legacy short alias for the Game Boy snapshot tool. |
| `emulator_run_pair_scenario` | `path` | Run the GB and Mega Drive scenarios in a pair definition. |
| `emulator_build_pair_contact_sheet` | `pair_summary_path` | Rebuild a pair contact sheet. |
| `emulator_build_pair_diffs` | `pair_summary_path` | Rebuild crops, visual diffs, and the contact sheet. |

The original Game Boy-only MCP names remain available as legacy aliases:

| Tool | Parameters | Description |
| --- | --- | --- |
| `bizhawk_status` | none | Alias for `emulator_status(target="gb")`. |
| `bizhawk_bridge_info` | none | Alias for `emulator_bridge_info(target="gb")`. |
| `bizhawk_step` | `frames=1` | Alias for `emulator_step(target="gb")`. |
| `bizhawk_tap` | `button, hold_frames=8, release_frames=2` | Alias for `emulator_tap(target="gb")`. |
| `bizhawk_hold` | `button, frames` | Hold a Game Boy button; frames 1--120. |
| `bizhawk_release` | `frames=2` | Alias for `emulator_release(target="gb")`. |
| `bizhawk_reset` | `settle_frames=60` | Alias for `emulator_reset(target="gb")`. |
| `bizhawk_press` | `button, frames=1` | Legacy tap alias with two release frames. |
| `bizhawk_screenshot` | `name="shot"` | Alias for `emulator_screenshot(target="gb")`. |
| `bizhawk_save_state` | `name` | Save a named Game Boy state. |
| `bizhawk_load_state` | `name` | Load a named Game Boy state. |
| `bizhawk_create_baseline` | `name` | Save a named Game Boy baseline. |
| `bizhawk_load_baseline` | `name` | Load a named Game Boy baseline. |
| `bizhawk_run_sequence` | `operations` | Run a list of scenario operations against Game Boy. |
| `bizhawk_run_scenario` | `path` | Run a JSON scenario against Game Boy. |

### Scenario operations

The `steps` array accepts one operation object per sequence:

| Operation | Fields | Description |
| --- | --- | --- |
| `tap` | `tap, hold_frames=8, release_frames=2` | Tap a button. |
| `press` | `press, frames=8, release_frames=2` | Legacy tap spelling. |
| `hold` | `hold, frames=1` | Hold a button. |
| `release` | `release` | Release all buttons for that many frames. |
| `step` | `step` | Advance neutral frames. |
| `screenshot` | `screenshot` | Capture a named screenshot in the run directory. |
| `gb_gpu_snapshot` | `gb_gpu_snapshot` | Capture a named Game Boy GPU snapshot. |
| `gpu_snapshot` | string or `{name, render, include_raw}` | Capture a GPU snapshot with options. |

Scenarios can use `start.mode` values `none`, `reset`, or `load_state`, plus
the legacy top-level `load_state` and optional `save_state_after` fields.

### Input semantics

- `tap` holds one button and adds neutral frames before/after it.
- `hold` holds one button without an automatic final release.
- `release` releases every known button for the requested number of frames.
- `step` advances with neutral input.
- `reset` reboots the core and advances neutral settling frames.

Frame counts are bounded by both the server and the bridge. Button names are
case-insensitive.

## Scenarios

Scenarios are plain JSON. Paths are resolved relative to the scenario file for
pair scenarios and relative to the current directory for direct CLI use.

```json
{
  "id": "nes.title_start",
  "description": "Boot and press Start",
  "start": { "mode": "reset", "settle_frames": 60 },
  "steps": [
    { "step": 300 },
    { "screenshot": "title" },
    { "tap": "START", "hold_frames": 8, "release_frames": 2 },
    { "step": 60 },
    { "screenshot": "after_start" }
  ],
  "save_state_after": "after_start"
}
```

Run a scenario with:

```powershell
& .\.venv\Scripts\emulator-mcp.exe --target nes --test run-scenario scenarios/nes/title_start.json
```

Supported starts are `none`, `reset`, and `load_state`. Run artifacts are
written below `.emulator-mcp/runtime/<target>/runs/` and are intentionally
ignored by Git.

## Configuration

| Variable | Purpose |
| --- | --- |
| `EMULATOR_MCP_HOME` | Absolute project home shared by Python and BizHawk. |
| `BIZHAWK_GB_BRIDGE_DIR` | Optional Game Boy runtime directory override. |
| `BIZHAWK_MD_BRIDGE_DIR` | Optional Mega Drive runtime directory override. |
| `BIZHAWK_NES_BRIDGE_DIR` | Optional NES runtime directory override. |
| `BIZHAWK_SNES_BRIDGE_DIR` | Optional SNES runtime directory override. |
| `BIZHAWK_BRIDGE_DIR` | Legacy Game Boy runtime override. |

Prefer `EMULATOR_MCP_HOME`; the target-specific variables exist for
compatibility and isolated runtime layouts. Empty overrides are ignored.

## Project structure

```text
.github/
  workflows/
bizhawk/
docs/
  ai/
scenarios/
  gb/
  md/
  nes/
  pairs/
  snes/
server/
  tests/
AGENTS.md
LICENSE
README.md
pyproject.toml
requirements.txt
```

Generated directories are deliberately absent from the tree: `.venv/`,
`build/`, `dist/`, `runtime/`, and `.emulator-mcp/` stay local.

## Development and release checks

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install .
& .\.venv\Scripts\python.exe -m unittest discover -s server/tests -v
& .\.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
```

The unit tests do not require an emulator. For a BizHawk release smoke test,
provide the executable and local ROMs explicitly:

```powershell
& .\.venv\Scripts\python.exe server/tests/bizhawk_smoke.py `
  --bizhawk "C:\\path\\to\\BizHawk\\EmuHawk.exe" `
  --rom gb="C:\\path\\to\\roms\\game.gb" gb
```

Never commit ROMs, savestates, screenshots, logs, command/response files,
virtual environments, or generated build output. Check the result before
sharing changes:

```powershell
git status --short --ignored
```

## Troubleshooting

- **Timeout:** reload and run `.emulator-mcp/bizhawk/bridge.lua`, then verify
  that the MCP client's `EMULATOR_MCP_HOME` is the same absolute directory.
- **Old or unknown command:** run `emulator-mcp init .emulator-mcp` again and
  reload the copied Lua script in BizHawk.
- **Unsupported system:** open a GB/GBC, Genesis, NES, or SNES ROM before
  loading the bridge.
- **Wrong buttons:** call `emulator_buttons`; BizHawk exposes the core's exact
  names.
- **Missing state:** states are target-specific and live under that target's
  runtime directory.

## Documentation

| Resource | Description |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Repository layout, commands, style, and contribution rules. |
| [pyproject.toml](pyproject.toml) | Package metadata, dependencies, and CLI entry point. |
| [bizhawk/bridge.lua](bizhawk/bridge.lua) | Portable BizHawk file bridge. |
| [server/mcp_server.py](server/mcp_server.py) | MCP tools, CLI, scenarios, and runtime protocol. |
| [server/tests](server/tests) | Unit tests and optional BizHawk smoke runner. |
| [scenarios](scenarios) | Target-specific and paired JSON flows. |

## Contributing

Keep changes focused, add one regression test for non-trivial behavior, run the
unit tests and wheel build, and keep emulator artifacts outside version
control. Pull requests should include the exact commands run and any relevant
BizHawk smoke-test output.

<a href="https://github.com/KanonZombie/emulator-mcp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=KanonZombie/emulator-mcp" />
</a>

## License

MIT. See [LICENSE](LICENSE).

---

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=KanonZombie/emulator-mcp&type=Date)](https://star-history.com/#KanonZombie/emulator-mcp&Date)

</div>
