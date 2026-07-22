from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_server  # noqa: E402
from bizhawk_smoke import ROM_BUILDERS  # noqa: E402


class McpCoreTests(unittest.TestCase):
    def test_all_supported_targets_have_isolated_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"EMULATOR_MCP_HOME": tmp}, clear=False):
                with patch.object(mcp_server, "HOME", Path(tmp)):
                    for target in ("gb", "md", "nes", "snes"):
                        context = mcp_server.get_target_context(target)
                        self.assertEqual(context.runtime_dir, Path(tmp) / "runtime" / target)

    def test_unknown_target_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown emulator target"):
            mcp_server.get_target_context("n64")

    def test_runtime_override_is_resolved_and_empty_values_use_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            override = home / "custom-md-runtime"
            with patch.dict(
                os.environ,
                {
                    "EMULATOR_MCP_HOME": str(home),
                    "BIZHAWK_MD_BRIDGE_DIR": str(override),
                    "BIZHAWK_NES_BRIDGE_DIR": "",
                },
                clear=False,
            ):
                with patch.object(mcp_server, "HOME", home):
                    self.assertEqual(mcp_server.get_target_context("md").runtime_dir, override.resolve())
                    self.assertEqual(
                        mcp_server.get_target_context("nes").runtime_dir,
                        (home / "runtime" / "nes").resolve(),
                    )

    def test_init_project_copies_portable_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = mcp_server.init_project(str(Path(tmp) / ".emulator-mcp"))
            bridge = Path(result["bridge"])
            source = bridge.read_text(encoding="utf-8")

            self.assertTrue(bridge.is_file())
            self.assertEqual(result["targets"], ["gb", "md", "nes", "snes"])
            self.assertIn('NES = "nes"', source)
            self.assertIn('SNES = "snes"', source)
            self.assertIn('cmd == "read_memory"', source)
            self.assertIn('return nil, "button_not_available"', source)
            self.assertNotIn("E:\\desarrollo", source)
            for target in result["targets"]:
                self.assertTrue((Path(result["home"]) / "runtime" / target).is_dir())

    def test_memory_read_normalizes_hex_address_and_dispatches(self) -> None:
        self.assertEqual(mcp_server.normalize_memory_read("010", 1, "System Bus")[0], 10)
        self.assertEqual(
            mcp_server.normalize_memory_read("0xFFFFE75E", 1, "M68K BUS")[0],
            0xFFE75E,
        )
        with patch.object(mcp_server, "require_bridge_capabilities") as require_capabilities:
            with patch.object(mcp_server, "send_command", return_value={"status": "ok"}) as send_command:
                result = mcp_server.emulator_read_memory(
                    target="gb",
                    address="0xC000",
                    length=4,
                    domain="System Bus",
                )

        self.assertEqual(result, {"status": "ok"})
        require_capabilities.assert_called_once_with("gb", ["memory_read"])
        send_command.assert_called_once_with(
            "gb",
            "read_memory",
            0xC000,
            4,
            "System Bus",
            timeout_sec=10,
        )

    def test_memory_read_rejects_invalid_ranges(self) -> None:
        with self.assertRaises(ValueError):
            mcp_server.normalize_memory_read(-1, 1, "System Bus")
        with self.assertRaises(ValueError):
            mcp_server.normalize_memory_read("0xC000", 0, "System Bus")
        with self.assertRaises(ValueError):
            mcp_server.normalize_memory_read("0xC000", 1, "System|Bus")

    def test_smoke_rom_builders_create_expected_formats(self) -> None:
        expected = {
            "gb": (0x8000, 0x100, b"\xC3\x50\x01\x00"),
            "md": (0x8000, 0, b"\x00\xFF\x00\x00"),
            "nes": (0x6010, 0, b"NES\x1A"),
            "snes": (0x8000, 0, b"\x78\x80\xFE\xEA"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for target, (filename, builder, _) in ROM_BUILDERS.items():
                path = Path(tmp) / filename
                builder(path)
                size, offset, prefix = expected[target]
                self.assertEqual(path.stat().st_size, size)
                self.assertEqual(path.read_bytes()[offset:offset + 4], prefix)


if __name__ == "__main__":
    unittest.main()
