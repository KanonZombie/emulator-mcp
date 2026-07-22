"""Opt-in BizHawk smoke test using tiny, generated public-domain ROMs."""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path


GB_LOGO = bytes.fromhex(
    "CE ED 66 66 CC 0D 00 0B 03 73 00 83 00 0C 00 0D"
    "00 08 11 1F 88 89 00 0E DC CC 6E E6 DD DD D9 99"
    "BB BB 67 63 6E 0E EC CC DD DC 99 9F BB B9 33 3E"
)


def write_gb(path: Path) -> None:
    rom = bytearray(0x8000)
    rom[0x100:0x104] = b"\xC3\x50\x01\x00"
    rom[0x104:0x134] = GB_LOGO
    rom[0x134:0x143] = b"MCP SMOKE TEST\0"
    rom[0x143:0x150] = bytes(13)
    rom[0x150:0x153] = b"\xF3\x18\xFE"
    checksum = 0
    for value in rom[0x134:0x14D]:
        checksum = (checksum - value - 1) & 0xFF
    rom[0x14D] = checksum
    path.write_bytes(rom)


def write_md(path: Path) -> None:
    rom = bytearray(0x8000)
    struct.pack_into(">II", rom, 0, 0x00FF0000, 0x00000200)
    for offset in range(8, 0x100, 4):
        struct.pack_into(">I", rom, offset, 0x00000200)
    rom[0x100:0x110] = b"SEGA MEGA DRIVE "
    rom[0x120:0x150] = b"MCP SMOKE TEST".ljust(48)
    rom[0x150:0x180] = b"MCP SMOKE TEST".ljust(48)
    rom[0x180:0x18E] = b"GM 00000000-00"
    rom[0x1F0:0x200] = b"J               "
    struct.pack_into(">II", rom, 0x1A0, 0, len(rom) - 1)
    rom[0x200:0x206] = bytes.fromhex("46 FC 27 00 60 FE")
    path.write_bytes(rom)


def write_nes(path: Path) -> None:
    header = b"NES\x1A" + bytes((1, 1)) + bytes(10)
    prg = bytearray([0xEA] * 0x4000)
    prg[:8] = bytes.fromhex("78 D8 A2 FF 9A 4C 05 80")
    struct.pack_into("<HHH", prg, 0x3FFA, 0x8000, 0x8000, 0x8000)
    path.write_bytes(header + prg + bytes(0x2000))


def write_snes(path: Path) -> None:
    rom = bytearray([0xFF] * 0x8000)
    rom[:4] = bytes.fromhex("78 80 FE EA")
    rom[0x7FC0:0x7FD5] = b"MCP SMOKE TEST".ljust(21)
    rom[0x7FD5:0x7FDB] = bytes((0x20, 0, 5, 0, 1, 0x33))
    rom[0x7FDB] = 0
    for offset in range(0x7FE4, 0x8000, 2):
        struct.pack_into("<H", rom, offset, 0x8000)
    checksum = sum(rom) & 0xFFFF
    struct.pack_into("<HH", rom, 0x7FDC, checksum ^ 0xFFFF, checksum)
    path.write_bytes(rom)


ROM_BUILDERS = {
    "gb": ("smoke.gb", write_gb, {"GB", "GBC"}),
    "md": ("smoke.gen", write_md, {"GEN"}),
    "nes": ("smoke.nes", write_nes, {"NES"}),
    "snes": ("smoke.sfc", write_snes, {"SNES"}),
}


def wait_for_bridge(mcp_server, target: str, process: subprocess.Popen, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"BizHawk exited early with code {process.returncode}")
        try:
            return mcp_server.emulator_status(target)
        except (TimeoutError, RuntimeError) as exc:
            last_error = exc
    raise TimeoutError(f"Bridge did not start for {target}: {last_error}")


def run_target(mcp_server, executable: Path, bridge: Path, rom: Path, target: str) -> None:
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen(
        [str(executable), f"--lua={bridge}", str(rom)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=startup,
    )
    try:
        status = wait_for_bridge(mcp_server, target, process)
        if status.get("system") not in ROM_BUILDERS[target][2]:
            raise AssertionError(f"Unexpected system for {target}: {status}")
        info = mcp_server.emulator_bridge_info(target)
        if info.get("bridge_version") != mcp_server.VERSION:
            raise AssertionError(f"Unexpected bridge version for {target}: {info}")
        mcp_server.emulator_buttons(target)
        memory = mcp_server.emulator_read_memory(target=target, address=0, length=1)
        if len(memory.get("hex", "")) != 2:
            raise AssertionError(f"Unexpected memory read for {target}: {memory}")
        mcp_server.emulator_step(target, 2)
        mcp_server.emulator_tap(target, "A", 1, 1)
        screenshot = mcp_server.emulator_screenshot(target, "smoke")
        mcp_server.emulator_save_state(target, "smoke")
        mcp_server.emulator_load_state(target, "smoke")
        if not Path(screenshot["path"]).is_file():
            raise AssertionError(f"Screenshot was not created for {target}: {screenshot}")
        print(f"{target}: ok ({status['system']}, bridge {info['bridge_version']})")
    except Exception:
        log = mcp_server.get_target_context(target).runtime_dir / "bridge.log"
        print(log.read_text(encoding="utf-8", errors="replace") if log.is_file() else f"{target}: no bridge log created")
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bizhawk", required=True, type=Path)
    parser.add_argument("--rom", action="append", default=[], metavar="TARGET=PATH", help="use an existing local ROM")
    parser.add_argument("targets", nargs="*", choices=ROM_BUILDERS, default=list(ROM_BUILDERS))
    args = parser.parse_args()
    if not args.bizhawk.is_file():
        parser.error(f"BizHawk executable not found: {args.bizhawk}")

    overrides = {}
    for value in args.rom:
        target, separator, path = value.partition("=")
        if not separator or target not in ROM_BUILDERS:
            parser.error(f"Invalid --rom value: {value}; expected TARGET=PATH")
        overrides[target] = Path(path).expanduser().resolve()

    with tempfile.TemporaryDirectory(prefix="emulator-mcp-smoke-") as tmp:
        home = Path(tmp)
        os.environ["EMULATOR_MCP_HOME"] = str(home)
        import mcp_server

        bridge = Path(mcp_server.init_project(str(home))["bridge"])
        for target in args.targets:
            if target in overrides:
                rom = overrides[target]
                if not rom.is_file():
                    parser.error(f"ROM not found: {rom}")
            else:
                filename, builder, _ = ROM_BUILDERS[target]
                rom = home / filename
                builder(rom)
            run_target(mcp_server, args.bizhawk.resolve(), bridge, rom, target)


if __name__ == "__main__":
    main()
