from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont

RGBA = Tuple[int, int, int, int]
DMG_SHADES: List[RGBA] = [
    (255, 255, 255, 255),
    (170, 170, 170, 255),
    (85, 85, 85, 255),
    (0, 0, 0, 255),
]


def decode_dmg_palette(reg: int, transparent_zero: bool = False) -> List[RGBA]:
    palette = []
    for index in range(4):
        shade_index = (int(reg) >> (index * 2)) & 0x03
        color = DMG_SHADES[shade_index]
        if transparent_zero and index == 0:
            color = (color[0], color[1], color[2], 0)
        palette.append(color)
    return palette


def _check_tile_bounds(vram: bytes, tile_offset: int) -> None:
    if tile_offset < 0 or tile_offset + 16 > len(vram):
        raise ValueError(f"tile offset out of VRAM bounds: {tile_offset:#06x}")


def decode_tile(
    vram: bytes,
    tile_offset: int,
    palette: List[RGBA],
    hflip: bool = False,
    vflip: bool = False,
) -> Image.Image:
    _check_tile_bounds(vram, tile_offset)
    image = Image.new("RGBA", (8, 8))
    pixels = image.load()

    for y in range(8):
        low = vram[tile_offset + y * 2]
        high = vram[tile_offset + y * 2 + 1]
        dst_y = 7 - y if vflip else y
        for x in range(8):
            bit = 7 - x
            color_id = ((low >> bit) & 0x01) | (((high >> bit) & 0x01) << 1)
            dst_x = 7 - x if hflip else x
            pixels[dst_x, dst_y] = palette[color_id]

    return image


def tile_offset_for_bg(tile_id: int, lcdc: int) -> int:
    tile_id = int(tile_id) & 0xFF
    if int(lcdc) & 0x10:
        offset = tile_id * 16
    else:
        signed = tile_id if tile_id < 0x80 else tile_id - 0x100
        offset = 0x1000 + signed * 16
    if offset < 0 or offset + 16 > 0x2000:
        raise ValueError(f"BG tile offset out of range: tile_id={tile_id:#04x} offset={offset:#06x}")
    return offset


def tile_offset_for_obj(tile_id: int) -> int:
    offset = (int(tile_id) & 0xFF) * 16
    if offset < 0 or offset + 16 > 0x2000:
        raise ValueError(f"OBJ tile offset out of range: tile_id={tile_id:#04x} offset={offset:#06x}")
    return offset


def render_tilesheet(vram: bytes, bgp: int, output_path: str | Path) -> str:
    palette = decode_dmg_palette(bgp)
    image = Image.new("RGBA", (128, 192), palette[0])
    for tile_id in range(384):
        tile = decode_tile(vram, tile_id * 16, palette)
        x = (tile_id % 16) * 8
        y = (tile_id // 16) * 8
        image.paste(tile, (x, y))
    return _save(image.convert("RGB"), output_path)


def render_tilemap(vram: bytes, map_offset: int, lcdc: int, bgp: int, output_path: str | Path) -> str:
    if map_offset < 0 or map_offset + 0x400 > len(vram):
        raise ValueError(f"tilemap offset out of VRAM bounds: {map_offset:#06x}")

    palette = decode_dmg_palette(bgp)
    image = Image.new("RGBA", (256, 256), palette[0])
    for row in range(32):
        for col in range(32):
            tile_id = vram[map_offset + row * 32 + col]
            tile = decode_tile(vram, tile_offset_for_bg(tile_id, lcdc), palette)
            image.paste(tile, (col * 8, row * 8))
    return _save(image.convert("RGB"), output_path)


def render_bg_maps(vram: bytes, regs: Dict[str, Any], output_dir: str | Path) -> Dict[str, str]:
    output = Path(output_dir)
    lcdc = int(regs.get("lcdc", 0))
    bgp = int(regs.get("bgp", 0xE4))
    files = {
        "bg_map_9800": render_tilemap(vram, 0x1800, lcdc, bgp, output / "bg_map_9800.png"),
        "bg_map_9c00": render_tilemap(vram, 0x1C00, lcdc, bgp, output / "bg_map_9c00.png"),
    }

    active_bg_offset = 0x1C00 if (lcdc & 0x08) else 0x1800
    active_window_offset = 0x1C00 if (lcdc & 0x40) else 0x1800
    files["bg_map_active"] = render_tilemap(vram, active_bg_offset, lcdc, bgp, output / "bg_map_active.png")
    files["window_map_active"] = render_tilemap(
        vram,
        active_window_offset,
        lcdc,
        bgp,
        output / "window_map_active.png",
    )
    return files


def _obj_palette(regs: Dict[str, Any], attrs: int) -> List[RGBA]:
    palette_reg = int(regs.get("obp1" if (attrs & 0x10) else "obp0", 0xE4))
    return decode_dmg_palette(palette_reg, transparent_zero=True)


def _render_sprite(vram: bytes, tile_id: int, attrs: int, height: int, palette: List[RGBA]) -> Image.Image:
    if height == 16:
        top = decode_tile(vram, tile_offset_for_obj(tile_id & 0xFE), palette)
        bottom = decode_tile(vram, tile_offset_for_obj(tile_id | 0x01), palette)
        sprite = Image.new("RGBA", (8, 16), (0, 0, 0, 0))
        sprite.paste(top, (0, 0))
        sprite.paste(bottom, (0, 8))
    else:
        sprite = decode_tile(vram, tile_offset_for_obj(tile_id), palette)

    if attrs & 0x20:
        sprite = sprite.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if attrs & 0x40:
        sprite = sprite.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return sprite


def _font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", 10)
    except OSError:
        return ImageFont.load_default()


def render_oam_sheet(vram: bytes, oam: bytes, regs: Dict[str, Any], output_path: str | Path) -> str:
    if len(oam) < 0xA0:
        raise ValueError(f"OAM too short: {len(oam)} bytes")

    sprite_height = 16 if (int(regs.get("lcdc", 0)) & 0x04) else 8
    cell_w = 128
    cell_h = 72
    image = Image.new("RGB", (cell_w * 10, cell_h * 4), (240, 240, 240))
    draw = ImageDraw.Draw(image)
    font = _font()

    for index in range(40):
        base = index * 4
        y_raw, x_raw, tile_id, attrs = oam[base], oam[base + 1], oam[base + 2], oam[base + 3]
        x = x_raw - 8
        y = y_raw - 16
        col = index % 10
        row = index // 10
        cell_x = col * cell_w
        cell_y = row * cell_h
        draw.rectangle((cell_x, cell_y, cell_x + cell_w - 1, cell_y + cell_h - 1), outline=(190, 190, 190))

        sprite = _render_sprite(vram, tile_id, attrs, sprite_height, _obj_palette(regs, attrs))
        preview = sprite.resize((sprite.width * 3, sprite.height * 3), Image.Resampling.NEAREST)
        image.paste(preview.convert("RGB"), (cell_x + 6, cell_y + 6), preview)

        palette_name = "OBP1" if (attrs & 0x10) else "OBP0"
        flags = ("x" if attrs & 0x20 else "-") + ("y" if attrs & 0x40 else "-")
        lines = [
            f"#{index:02d} x={x} y={y}",
            f"tile={tile_id:02X} attr={attrs:02X}",
            f"{palette_name} flip={flags}",
        ]
        for line_index, text in enumerate(lines):
            draw.text((cell_x + 38, cell_y + 8 + line_index * 12), text, fill=(20, 20, 20), font=font)

    return _save(image, output_path)


def _paste_clipped(base: Image.Image, sprite: Image.Image, x: int, y: int) -> None:
    left = max(0, x)
    top = max(0, y)
    right = min(base.width, x + sprite.width)
    bottom = min(base.height, y + sprite.height)
    if right <= left or bottom <= top:
        return
    crop = sprite.crop((left - x, top - y, right - x, bottom - y))
    base.paste(crop, (left, top), crop)


def render_obj_positions(vram: bytes, oam: bytes, regs: Dict[str, Any], output_path: str | Path) -> str:
    if len(oam) < 0xA0:
        raise ValueError(f"OAM too short: {len(oam)} bytes")

    image = Image.new("RGBA", (160, 144), (232, 236, 224, 255))
    draw = ImageDraw.Draw(image)
    for x in range(0, 160, 8):
        draw.line((x, 0, x, 143), fill=(210, 214, 204, 255))
    for y in range(0, 144, 8):
        draw.line((0, y, 159, y), fill=(210, 214, 204, 255))

    sprite_height = 16 if (int(regs.get("lcdc", 0)) & 0x04) else 8
    for index in range(40):
        base = index * 4
        y = oam[base] - 16
        x = oam[base + 1] - 8
        tile_id = oam[base + 2]
        attrs = oam[base + 3]
        sprite = _render_sprite(vram, tile_id, attrs, sprite_height, _obj_palette(regs, attrs))
        _paste_clipped(image, sprite, x, y)
    return _save(image.convert("RGB"), output_path)


def render_viewport_bg(vram: bytes, regs: Dict[str, Any], output_path: str | Path) -> str:
    lcdc = int(regs.get("lcdc", 0))
    bgp = int(regs.get("bgp", 0xE4))
    scx = int(regs.get("scx", 0))
    scy = int(regs.get("scy", 0))
    map_offset = 0x1C00 if (lcdc & 0x08) else 0x1800
    palette = decode_dmg_palette(bgp)

    tilemap = Image.new("RGBA", (256, 256), palette[0])
    for row in range(32):
        for col in range(32):
            tile_id = vram[map_offset + row * 32 + col]
            tile = decode_tile(vram, tile_offset_for_bg(tile_id, lcdc), palette)
            tilemap.paste(tile, (col * 8, row * 8))

    viewport = Image.new("RGBA", (160, 144), palette[0])
    src = tilemap.load()
    dst = viewport.load()
    for y in range(144):
        for x in range(160):
            dst[x, y] = src[(scx + x) & 0xFF, (scy + y) & 0xFF]

    return _save(viewport.convert("RGB"), output_path)


def render_register_metadata(regs: Dict[str, Any], output_path: str | Path) -> str:
    lcdc = int(regs.get("lcdc", 0))
    stat = int(regs.get("stat", 0))
    decoded = {
        "frame": regs.get("frame"),
        "system": regs.get("system"),
        "lcdc": {
            "value": lcdc,
            "lcd_enable": bool(lcdc & 0x80),
            "window_tile_map_area": "9C00" if (lcdc & 0x40) else "9800",
            "window_enable": bool(lcdc & 0x20),
            "bg_window_tile_data_area": "8000_unsigned" if (lcdc & 0x10) else "9000_signed",
            "bg_tile_map_area": "9C00" if (lcdc & 0x08) else "9800",
            "obj_size": "8x16" if (lcdc & 0x04) else "8x8",
            "obj_enable": bool(lcdc & 0x02),
            "bg_window_enable": bool(lcdc & 0x01),
        },
        "stat": {
            "value": stat,
            "lyc_interrupt": bool(stat & 0x40),
            "mode_2_interrupt": bool(stat & 0x20),
            "mode_1_interrupt": bool(stat & 0x10),
            "mode_0_interrupt": bool(stat & 0x08),
            "lyc_equals_ly": bool(stat & 0x04),
            "mode": stat & 0x03,
        },
        "scroll": {"scx": int(regs.get("scx", 0)), "scy": int(regs.get("scy", 0))},
        "window": {"wx": int(regs.get("wx", 0)), "wy": int(regs.get("wy", 0))},
        "ly": int(regs.get("ly", 0)),
        "lyc": int(regs.get("lyc", 0)),
        "palettes": {
            "bgp": _palette_as_lists(decode_dmg_palette(int(regs.get("bgp", 0xE4)))),
            "obp0": _palette_as_lists(decode_dmg_palette(int(regs.get("obp0", 0xE4),), True)),
            "obp1": _palette_as_lists(decode_dmg_palette(int(regs.get("obp1", 0xE4),), True)),
        },
        "notes": [
            "DMG renderer only; CGB attributes and palettes are not implemented.",
            "Window overlay is not composited in viewport_bg in this version.",
            "Sprite priority and 10 sprites per scanline limit are approximate/not enforced.",
        ],
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(decoded, indent=2), encoding="utf-8")
    return str(output)


def render_snapshot(snapshot_dir: str | Path, output_dir: str | Path | None = None) -> Dict[str, Any]:
    snapshot = Path(snapshot_dir)
    output = Path(output_dir) if output_dir else snapshot / "rendered"
    output.mkdir(parents=True, exist_ok=True)

    vram = (snapshot / "vram.bin").read_bytes()
    oam = (snapshot / "oam.bin").read_bytes()
    regs = json.loads((snapshot / "regs.json").read_text(encoding="utf-8"))
    if len(vram) != 0x2000:
        raise ValueError(f"VRAM dump must be 0x2000 bytes, got {len(vram)}")
    if len(oam) != 0x00A0:
        raise ValueError(f"OAM dump must be 0x00A0 bytes, got {len(oam)}")

    files: Dict[str, str] = {}
    files["tilesheet"] = render_tilesheet(vram, int(regs.get("bgp", 0xE4)), output / "tilesheet.png")
    files.update(render_bg_maps(vram, regs, output))
    files["oam_sheet"] = render_oam_sheet(vram, oam, regs, output / "oam_sheet.png")
    files["obj_positions"] = render_obj_positions(vram, oam, regs, output / "obj_positions.png")
    files["viewport_bg"] = render_viewport_bg(vram, regs, output / "viewport_bg.png")
    files["regs_decoded"] = render_register_metadata(regs, output / "regs_decoded.json")

    return {
        "files": files,
        "notes": [
            "DMG renderer only",
            "CGB attributes/palettes not implemented",
            "Sprite priority is approximate",
            "10 sprites per scanline limit not enforced",
            "Window overlay is not composited in viewport_bg in this version",
        ],
    }


def _palette_as_lists(palette: List[RGBA]) -> List[List[int]]:
    return [[r, g, b, a] for r, g, b, a in palette]


def _save(image: Image.Image, output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return str(output)
