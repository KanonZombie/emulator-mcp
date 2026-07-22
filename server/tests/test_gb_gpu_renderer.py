from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gb_gpu_renderer import (  # noqa: E402
    decode_dmg_palette,
    decode_tile,
    render_obj_positions,
    render_tilemap,
    render_tilesheet,
    render_viewport_bg,
    tile_offset_for_bg,
    tile_offset_for_obj,
)


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


class GbGpuRendererTests(unittest.TestCase):
    def test_decode_dmg_palette(self) -> None:
        palette = decode_dmg_palette(0xE4)
        self.assertEqual(palette[0], (255, 255, 255, 255))
        self.assertEqual(palette[1], (170, 170, 170, 255))
        self.assertEqual(palette[2], (85, 85, 85, 255))
        self.assertEqual(palette[3], (0, 0, 0, 255))

        transparent = decode_dmg_palette(0xE4, transparent_zero=True)
        self.assertEqual(transparent[0], (255, 255, 255, 0))
        self.assertEqual(transparent[1], (170, 170, 170, 255))

    def test_decode_tile_pixels(self) -> None:
        vram = bytearray(0x2000)
        vram[0] = 0b10000000
        vram[1] = 0
        vram[2] = 0
        vram[3] = 0b01000000
        vram[4] = 0b00100000
        vram[5] = 0b00100000

        palette = decode_dmg_palette(0xE4)
        tile = decode_tile(bytes(vram), 0, palette)
        self.assertEqual(tile.getpixel((0, 0)), palette[1])
        self.assertEqual(tile.getpixel((1, 0)), palette[0])
        self.assertEqual(tile.getpixel((1, 1)), palette[2])
        self.assertEqual(tile.getpixel((2, 2)), palette[3])

    def test_tile_offset_for_bg(self) -> None:
        self.assertEqual(tile_offset_for_bg(0x00, 0x10), 0x0000)
        self.assertEqual(tile_offset_for_bg(0x80, 0x10), 0x0800)
        self.assertEqual(tile_offset_for_bg(0x00, 0x00), 0x1000)
        self.assertEqual(tile_offset_for_bg(0x80, 0x00), 0x0800)
        self.assertEqual(tile_offset_for_bg(0xFF, 0x00), 0x0FF0)

    def test_tile_offset_for_obj(self) -> None:
        self.assertEqual(tile_offset_for_obj(0x00), 0x0000)
        self.assertEqual(tile_offset_for_obj(0x10), 0x0100)
        self.assertEqual(tile_offset_for_obj(0xFF), 0x0FF0)

    def test_render_tilesheet_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tilesheet.png"
            render_tilesheet(bytes(0x2000), 0xE4, path)
            self.assertEqual(image_size(path), (128, 192))

    def test_render_tilemap_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tilemap.png"
            render_tilemap(bytes(0x2000), 0x1800, 0x10, 0xE4, path)
            self.assertEqual(image_size(path), (256, 256))

    def test_render_obj_positions_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "obj_positions.png"
            render_obj_positions(bytes(0x2000), bytes(0xA0), {"lcdc": 0, "obp0": 0xE4, "obp1": 0xE4}, path)
            self.assertEqual(image_size(path), (160, 144))

    def test_render_viewport_bg_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "viewport_bg.png"
            render_viewport_bg(bytes(0x2000), {"lcdc": 0x10, "bgp": 0xE4, "scx": 0, "scy": 0}, path)
            self.assertEqual(image_size(path), (160, 144))


if __name__ == "__main__":
    unittest.main()
