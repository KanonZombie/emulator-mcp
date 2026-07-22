from __future__ import annotations

import json
import os
import re
import shutil
import sys
import sysconfig
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP
from PIL import Image, ImageChops, ImageDraw, ImageFont

from gb_gpu_renderer import render_snapshot

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.0"
VALID_TARGETS = {"gb", "md", "nes", "snes"}


def get_home() -> Path:
    configured = os.environ.get("EMULATOR_MCP_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if (ROOT / "bizhawk" / "bridge.lua").is_file():
        return ROOT
    return (Path.cwd() / ".emulator-mcp").resolve()


HOME = get_home()
PAIR_RUNS_DIR = HOME / "runtime" / "pairs"

mcp = FastMCP("bizhawk-emulator")


@dataclass(frozen=True)
class TargetContext:
    target: str
    runtime_dir: Path
    command_file: Path
    response_file: Path
    screenshots_dir: Path
    states_dir: Path
    runs_dir: Path
    gpu_snapshots_dir: Path


def get_target_context(target: str = "gb") -> TargetContext:
    target = (target or "gb").lower().strip()
    if target not in VALID_TARGETS:
        raise ValueError(f"Unknown emulator target: {target}")

    env_key = f"BIZHAWK_{target.upper()}_BRIDGE_DIR"
    default_dir = HOME / "runtime" / target
    fallback = os.environ.get("BIZHAWK_BRIDGE_DIR") if target == "gb" else None
    runtime_dir = Path(os.environ.get(env_key) or fallback or default_dir).expanduser().resolve()

    return TargetContext(
        target=target,
        runtime_dir=runtime_dir,
        command_file=runtime_dir / "command.txt",
        response_file=runtime_dir / "response.txt",
        screenshots_dir=runtime_dir / "screenshots",
        states_dir=runtime_dir / "states",
        runs_dir=runtime_dir / "runs",
        gpu_snapshots_dir=runtime_dir / "gpu_snapshots",
    )


def parse_response(line: str) -> Dict[str, str]:
    parts = line.strip().split("|")
    data: Dict[str, str] = {
        "id": parts[0] if len(parts) > 0 else "",
        "status": parts[1] if len(parts) > 1 else "error",
    }

    for part in parts[2:]:
        key, _, value = part.partition("=")
        if key:
            data[key] = value

    return data


def send_command(target: str, *parts: object, timeout_sec: float = 10.0) -> Dict[str, str]:
    context = get_target_context(target)
    context.runtime_dir.mkdir(parents=True, exist_ok=True)
    context.screenshots_dir.mkdir(parents=True, exist_ok=True)
    context.states_dir.mkdir(parents=True, exist_ok=True)

    command_id = uuid.uuid4().hex
    command = "|".join([command_id, *[str(p) for p in parts]])

    try:
        context.response_file.unlink(missing_ok=True)
    except TypeError:
        if context.response_file.exists():
            context.response_file.unlink()

    tmp_file = context.command_file.with_suffix(".tmp")
    tmp_file.write_text(command, encoding="utf-8")
    os.replace(tmp_file, context.command_file)

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if context.response_file.exists():
            line = context.response_file.read_text(encoding="utf-8", errors="replace")
            data = parse_response(line)

            if data.get("id") == command_id:
                try:
                    context.response_file.unlink()
                except OSError:
                    pass

                if data.get("status") != "ok":
                    raise RuntimeError(f"BizHawk bridge error: {data}")

                data["target"] = context.target
                return data

        time.sleep(0.02)

    raise TimeoutError(
        f"No response from BizHawk bridge after {timeout_sec}s. "
        f"Check that bridge.lua is running and runtime dir is correct: {context.runtime_dir}"
    )


def safe_name(name: str) -> str:
    name = name.strip() or "shot"
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)


def state_path_from_name(target: str, name: str) -> Path:
    context = get_target_context(target)
    filename = safe_name(name)
    if not filename.lower().endswith(".state"):
        filename = f"{filename}.state"

    path = (context.states_dir / filename).resolve()
    state_root = context.states_dir.resolve()

    if state_root not in path.parents and path != state_root:
        raise ValueError(f"State name escapes state directory: {name}")

    return path


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def resolve_relative_path(base_file: Path, path: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_file.parent / candidate).resolve()


def infer_screenshot_name(path: str) -> str:
    stem = Path(path).stem
    return re.sub(r"_\d+$", "", stem)


def normalize_screenshots(screenshots: List[Any], fallback_target: str) -> List[Dict[str, Any]]:
    normalized = []
    for screenshot in screenshots:
        if isinstance(screenshot, dict):
            path = str(screenshot.get("path", ""))
            normalized.append({
                "name": str(screenshot.get("name") or infer_screenshot_name(path)),
                "path": path,
                "target": str(screenshot.get("target") or fallback_target),
                "frame": screenshot.get("frame"),
            })
        else:
            path = str(screenshot)
            normalized.append({
                "name": infer_screenshot_name(path),
                "path": path,
                "target": fallback_target,
                "frame": None,
            })
    return normalized


def viewport_from_config(config: Dict[str, Any], default: Dict[str, int] | None = None) -> Dict[str, int] | None:
    viewport = config.get("viewport")
    if viewport is None:
        return default
    if not isinstance(viewport, dict):
        raise ValueError("viewport must be an object with x, y, w, h")
    return {
        "x": int(viewport["x"]),
        "y": int(viewport["y"]),
        "w": int(viewport["w"]),
        "h": int(viewport["h"]),
    }


def canonical_size_from_config(config: Dict[str, Any], default: tuple[int, int]) -> tuple[int, int]:
    value = config.get("canonical_size", list(default))
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("canonical_size must be [width, height]")
    return int(value[0]), int(value[1])


def crop_to_canonical(
    source_path: str | None,
    viewport: Dict[str, int] | None,
    canonical_size: tuple[int, int],
    output_path: Path,
) -> tuple[str | None, str | None]:
    if not source_path:
        return None, "missing screenshot"

    image_path = Path(source_path)
    if not image_path.exists():
        return None, f"screenshot not found: {source_path}"

    image = Image.open(image_path).convert("RGB")
    if viewport is None:
        viewport = {"x": 0, "y": 0, "w": image.width, "h": image.height}

    x = viewport["x"]
    y = viewport["y"]
    w = viewport["w"]
    h = viewport["h"]
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > image.width or y + h > image.height:
        return None, (
            f"invalid viewport x={x} y={y} w={w} h={h} "
            f"for image {image.width}x{image.height}: {source_path}"
        )

    crop = image.crop((x, y, x + w, y + h))
    if crop.size != canonical_size:
        crop = crop.resize(canonical_size, Image.Resampling.NEAREST)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(output_path)
    return str(output_path), None


def build_diff_for_checkpoint(checkpoint: Dict[str, Any], diffs_dir: Path) -> str | None:
    if checkpoint.get("status") != "matched":
        return None

    gb_path = ((checkpoint.get("gb") or {}).get("canonical"))
    md_path = ((checkpoint.get("md") or {}).get("canonical"))
    if not gb_path or not md_path:
        return "missing canonical crop"

    gb_image_path = Path(gb_path)
    md_image_path = Path(md_path)
    if not gb_image_path.exists() or not md_image_path.exists():
        return "canonical crop file not found"

    gb_image = Image.open(gb_image_path).convert("RGB")
    md_image = Image.open(md_image_path).convert("RGB")
    if gb_image.size != md_image.size:
        return f"canonical size mismatch: gb={gb_image.size} md={md_image.size}"

    diff = ImageChops.difference(gb_image, md_image)
    total_pixels = diff.width * diff.height
    channel_count = len(diff.getbands())
    diff_bytes = diff.tobytes()
    different_pixels = sum(
        1
        for offset in range(0, len(diff_bytes), channel_count)
        if any(diff_bytes[offset + channel] != 0 for channel in range(channel_count))
    )
    sum_abs_diff = sum(diff_bytes)
    max_abs_diff = max(diff_bytes, default=0)
    mean_abs_diff = sum_abs_diff / (total_pixels * channel_count) if total_pixels else 0.0

    diff_path = diffs_dir / f"diff_{safe_name(str(checkpoint.get('name', 'checkpoint')))}.png"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff.save(diff_path)

    checkpoint["diff"] = {
        "path": str(diff_path),
        "different_pixels": different_pixels,
        "total_pixels": total_pixels,
        "different_ratio": different_pixels / total_pixels if total_pixels else 0.0,
        "mean_abs_diff": mean_abs_diff,
        "max_abs_diff": max_abs_diff,
    }
    return None


def build_pair_diffs(checkpoints: List[Dict[str, Any]], diffs_dir: Path) -> tuple[int, List[str]]:
    errors = []
    generated = 0
    for checkpoint in checkpoints:
        error = build_diff_for_checkpoint(checkpoint, diffs_dir)
        if error:
            checkpoint["diff_error"] = error
            errors.append(f"{checkpoint.get('name', 'checkpoint')}: {error}")
        elif checkpoint.get("diff", {}).get("path"):
            generated += 1
    return generated, errors


def build_checkpoints(
    gb_screenshots: List[Any],
    md_screenshots: List[Any],
    canonical_dir: Path | None = None,
    viewports: Dict[str, Dict[str, int] | None] | None = None,
    canonical_sizes: Dict[str, tuple[int, int]] | None = None,
) -> tuple[List[Dict[str, Any]], List[str]]:
    gb_normalized = normalize_screenshots(gb_screenshots, "gb")
    md_normalized = normalize_screenshots(md_screenshots, "md")
    gb_by_name = {screenshot["name"]: screenshot for screenshot in gb_normalized}
    md_by_name = {screenshot["name"]: screenshot for screenshot in md_normalized}
    viewports = viewports or {"gb": None, "md": None}
    canonical_sizes = canonical_sizes or {"gb": (160, 144), "md": (160, 144)}
    names = list(gb_by_name.keys())
    for name in md_by_name:
        if name not in gb_by_name:
            names.append(name)

    checkpoints = []
    errors = []
    for name in names:
        gb_item = gb_by_name.get(name)
        md_item = md_by_name.get(name)
        if gb_item and md_item:
            status = "matched"
        elif gb_item:
            status = "missing_md"
        else:
            status = "missing_gb"

        gb_canonical = None
        md_canonical = None
        gb_error = None
        md_error = None

        if canonical_dir and gb_item:
            gb_canonical, gb_error = crop_to_canonical(
                gb_item.get("path"),
                viewports.get("gb"),
                canonical_sizes.get("gb", (160, 144)),
                canonical_dir / f"gb_{safe_name(name)}.png",
            )
        if canonical_dir and md_item:
            md_canonical, md_error = crop_to_canonical(
                md_item.get("path"),
                viewports.get("md"),
                canonical_sizes.get("md", (160, 144)),
                canonical_dir / f"md_{safe_name(name)}.png",
            )

        if gb_error:
            status = "invalid_gb_viewport" if gb_item else status
            errors.append(f"{name} gb: {gb_error}")
        if md_error:
            status = "invalid_md_viewport" if md_item else status
            errors.append(f"{name} md: {md_error}")

        checkpoints.append({
            "name": name,
            "gb_screenshot": gb_item.get("path") if gb_item else None,
            "md_screenshot": md_item.get("path") if md_item else None,
            "gb": {
                "full": gb_item.get("path") if gb_item else None,
                "canonical": gb_canonical,
                "screenshot": gb_item,
                "error": gb_error,
            },
            "md": {
                "full": md_item.get("path") if md_item else None,
                "canonical": md_canonical,
                "screenshot": md_item,
                "error": md_error,
            },
            "status": status,
        })

    return checkpoints, errors


def load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def draw_text_centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: ImageFont.ImageFont) -> None:
    text_box = draw.textbbox((0, 0), text, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    x = box[0] + ((box[2] - box[0] - text_width) // 2)
    y = box[1] + ((box[3] - box[1] - text_height) // 2)
    draw.text((x, y), text, fill=(40, 40, 40), font=font)


def thumbnail_image(path: str, max_width: int, max_height: int) -> Image.Image | None:
    if not path:
        return None
    image_path = Path(path)
    if not image_path.exists():
        return None

    image = Image.open(image_path).convert("RGB")
    image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    return image


def canonical_preview(path: str | None, scale: int = 2) -> Image.Image | None:
    if not path:
        return None
    image_path = Path(path)
    if not image_path.exists():
        return None
    image = Image.open(image_path).convert("RGB")
    return image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)


def build_pair_contact_sheet(pair_summary: Dict[str, Any], output_path: Path) -> str:
    checkpoints = pair_summary.get("checkpoints", [])
    full_width = 220
    full_height = 180
    canonical_width = 320
    canonical_height = 288
    diff_width = 320
    diff_height = 288
    label_width = 230
    padding = 16
    header_height = 62
    row_height = canonical_height + padding
    width = label_width + (full_width * 2) + (canonical_width * 2) + diff_width + (padding * 7)
    height = header_height + max(1, len(checkpoints)) * row_height + padding

    image = Image.new("RGB", (width, height), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    title_font = load_font(22)
    label_font = load_font(16)
    small_font = load_font(14)

    draw.text((padding, padding), pair_summary.get("pair_id", "pair"), fill=(20, 20, 20), font=title_font)
    gb_full_x = label_width + padding * 2
    md_full_x = gb_full_x + full_width + padding
    gb_canonical_x = md_full_x + full_width + padding
    md_canonical_x = gb_canonical_x + canonical_width + padding
    diff_x = md_canonical_x + canonical_width + padding
    columns = (
        ("GB full", gb_full_x, full_width, full_height, "gb", "full"),
        ("MD full", md_full_x, full_width, full_height, "md", "full"),
        ("GB canonical", gb_canonical_x, canonical_width, canonical_height, "gb", "canonical"),
        ("MD canonical", md_canonical_x, canonical_width, canonical_height, "md", "canonical"),
        ("Diff", diff_x, diff_width, diff_height, "diff", "path"),
    )
    for title, x, column_width, _, _, _ in columns:
        draw_text_centered(draw, (x, padding, x + column_width, header_height), title, title_font)

    if not checkpoints:
        draw_text_centered(draw, (0, header_height, width, height), "no checkpoints", title_font)
    for row, checkpoint in enumerate(checkpoints):
        y = header_height + row * row_height
        draw.line((padding, y, width - padding, y), fill=(220, 220, 220))
        draw.text((padding, y + padding), str(checkpoint.get("name", "")), fill=(20, 20, 20), font=label_font)
        draw.text((padding, y + padding + 24), str(checkpoint.get("status", "")), fill=(90, 90, 90), font=small_font)

        for _, x, column_width, column_height, target_key, image_key in columns:
            box = (x, y + padding // 2, x + column_width, y + padding // 2 + column_height)
            draw.rectangle(box, fill=(232, 232, 232), outline=(190, 190, 190))
            target_info = checkpoint.get(target_key) or {}
            error = checkpoint.get("diff_error") if target_key == "diff" else target_info.get("error")
            if image_key == "canonical" or target_key == "diff":
                preview = canonical_preview(target_info.get(image_key))
            else:
                preview = thumbnail_image(target_info.get(image_key), column_width, column_height)
            if preview:
                paste_x = x + (column_width - preview.width) // 2
                paste_y = y + padding // 2 + (column_height - preview.height) // 2
                image.paste(preview, (paste_x, paste_y))
            else:
                if target_key == "diff":
                    label = "diff unavailable"
                else:
                    label = "invalid viewport" if error else "missing"
                draw_text_centered(draw, box, label, title_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return str(output_path)


def old_bridge_error(missing: str) -> str:
    return (
        "BizHawk is running an old bridge.lua. Reload the Lua script. "
        f"Missing capability: {missing}"
    )


def missing_capability_hint(required: List[str]) -> str:
    for capability in ("reset", "save_state", "load_state", "tap", "hold", "release"):
        if capability in required:
            return capability
    return "bridge_info"


@mcp.tool()
def emulator_status(target: str = "gb") -> dict:
    """Return emulator status for a target: system, frame, screen size."""
    return send_command(target, "status", timeout_sec=3)


@mcp.tool()
def emulator_bridge_info(target: str = "gb") -> dict:
    """Return bridge.lua version and advertised capabilities for a target."""
    try:
        return send_command(target, "bridge_info", timeout_sec=3)
    except RuntimeError as exc:
        if "unknown_command" in str(exc):
            raise RuntimeError(old_bridge_error("bridge_info")) from exc
        raise


def require_bridge_capabilities(target: str, required: List[str]) -> dict:
    try:
        info = emulator_bridge_info(target)
    except RuntimeError as exc:
        if "Missing capability:" in str(exc):
            raise RuntimeError(old_bridge_error(missing_capability_hint(required))) from exc
        raise RuntimeError(old_bridge_error(missing_capability_hint(required))) from exc

    capabilities = {
        capability.strip()
        for capability in info.get("capabilities", "").split(",")
        if capability.strip()
    }

    for capability in required:
        if capability not in capabilities:
            raise RuntimeError(old_bridge_error(capability))

    return info


@mcp.tool()
def emulator_step(target: str = "gb", frames: int = 1) -> dict:
    """Advance the emulator by N neutral/released frames. Max 600 per call."""
    frames = max(1, min(int(frames), 600))
    return send_command(target, "step", frames, timeout_sec=15)


@mcp.tool()
def emulator_tap(target: str = "gb", button: str = "", hold_frames: int = 8, release_frames: int = 2) -> dict:
    """Tap one button with neutral frames before and after."""
    button = button.upper().strip()
    hold_frames = max(1, min(int(hold_frames), 120))
    release_frames = max(1, min(int(release_frames), 600))
    return send_command(target, "tap", button, hold_frames, release_frames, timeout_sec=15)


@mcp.tool()
def emulator_hold(target: str = "gb", button: str = "", frames: int = 1) -> dict:
    """Hold one button for N frames without releasing afterward."""
    button = button.upper().strip()
    frames = max(1, min(int(frames), 120))
    return send_command(target, "hold", button, frames, timeout_sec=10)


@mcp.tool()
def emulator_release(target: str = "gb", frames: int = 2) -> dict:
    """Release all known buttons for N frames."""
    frames = max(1, min(int(frames), 600))
    return send_command(target, "release", frames, timeout_sec=15)


@mcp.tool()
def emulator_reset(target: str = "gb", settle_frames: int = 60) -> dict:
    """Reboot the emulator core and settle with neutral input for N frames."""
    settle_frames = max(1, min(int(settle_frames), 600))
    return send_command(target, "reset", settle_frames, timeout_sec=30)


@mcp.tool()
def emulator_screenshot(target: str = "gb", name: str = "shot") -> dict:
    """Save a screenshot for a target and return its path."""
    context = get_target_context(target)
    filename = f"{safe_name(name)}_{int(time.time())}.png"
    path = context.screenshots_dir / filename
    return send_command(target, "screenshot", path.as_posix(), timeout_sec=5)


def emulator_screenshot_to_dir(target: str, name: str, screenshot_dir: Path) -> dict:
    filename = f"{safe_name(name)}_{int(time.time())}.png"
    path = screenshot_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return send_command(target, "screenshot", path.as_posix(), timeout_sec=5)


@mcp.tool()
def emulator_save_state(target: str = "gb", name: str = "state") -> dict:
    """Save a BizHawk state under the target runtime states directory."""
    path = state_path_from_name(target, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return send_command(target, "save_state", path.as_posix(), timeout_sec=10)


@mcp.tool()
def emulator_load_state(target: str = "gb", name: str = "state") -> dict:
    """Load a BizHawk state from the target runtime states directory."""
    path = state_path_from_name(target, name)
    if not path.exists():
        raise RuntimeError(f"State not found: {path}")
    return send_command(target, "load_state", path.as_posix(), timeout_sec=10)


@mcp.tool()
def emulator_create_baseline(target: str = "gb", name: str = "baseline") -> dict:
    """Alias for saving a named baseline state under runtime/<target>/states."""
    return emulator_save_state(target=target, name=name)


@mcp.tool()
def emulator_load_baseline(target: str = "gb", name: str = "baseline") -> dict:
    """Alias for loading a named baseline state from runtime/<target>/states."""
    return emulator_load_state(target=target, name=name)


@mcp.tool()
def emulator_buttons(target: str = "gb") -> dict:
    """Return current BizHawk joypad button names/states."""
    return send_command(target, "buttons", timeout_sec=3)


@mcp.tool()
def emulator_gb_gpu_snapshot(
    target: str = "gb",
    render: bool = True,
    include_raw: bool = True,
    output_dir: str | None = None,
    name: str = "snapshot",
) -> dict:
    """Dump raw GB DMG GPU data and optionally render diagnostic PNG panels."""
    target = (target or "gb").lower().strip()
    if target != "gb":
        raise RuntimeError("GB GPU snapshots are only supported for target gb")

    info = require_bridge_capabilities(target, ["gb_gpu_snapshot"])
    system = str(info.get("system", ""))
    if system and system.upper() not in ("GB", "GBC"):
        raise RuntimeError(f"GB GPU snapshots require a GB system, got: {system}")

    context = get_target_context(target)
    if output_dir:
        snapshot_dir = Path(output_dir).expanduser()
        if not snapshot_dir.is_absolute():
            snapshot_dir = (Path.cwd() / snapshot_dir).resolve()
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        snapshot_dir = context.gpu_snapshots_dir / f"{safe_name(name)}_{run_id}"

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    lua_result = send_command(target, "gb_gpu_snapshot", snapshot_dir.as_posix(), timeout_sec=10)
    regs_path = Path(lua_result["regs_path"])
    registers = json.loads(regs_path.read_text(encoding="utf-8"))

    files = {}
    if include_raw:
        files.update({
            "raw_vram": lua_result.get("vram_path"),
            "raw_oam": lua_result.get("oam_path"),
            "raw_regs": lua_result.get("regs_path"),
            "domains": lua_result.get("domains_path"),
            "frame": lua_result.get("frame_path"),
        })

    notes = [
        "DMG renderer only",
        "CGB attributes/palettes not implemented",
        "Sprite priority is approximate",
        "10 sprites per scanline limit not enforced",
        "Window overlay is not composited in viewport_bg in this version",
    ]
    if render:
        rendered = render_snapshot(snapshot_dir, snapshot_dir / "rendered")
        files.update(rendered.get("files", {}))
        notes = rendered.get("notes", notes)

    return {
        "ok": True,
        "target": target,
        "system": lua_result.get("system") or registers.get("system"),
        "frame": lua_result.get("frame") or registers.get("frame"),
        "snapshot_dir": str(snapshot_dir),
        "registers": registers,
        "files": files,
        "notes": notes,
    }


@mcp.tool()
def gb_gpu_snapshot(render: bool = True, include_raw: bool = True, output_dir: str | None = None) -> dict:
    """Legacy-short alias for emulator_gb_gpu_snapshot(target='gb')."""
    return emulator_gb_gpu_snapshot(target="gb", render=render, include_raw=include_raw, output_dir=output_dir)


def run_operation(target: str, op: Dict[str, Any], screenshot_dir: Path | None = None) -> dict:
    if "tap" in op:
        return emulator_tap(
            target=target,
            button=str(op["tap"]),
            hold_frames=int(op.get("hold_frames", 8)),
            release_frames=int(op.get("release_frames", 2)),
        )
    if "press" in op:
        return emulator_tap(
            target=target,
            button=str(op["press"]),
            hold_frames=int(op.get("frames", 8)),
            release_frames=int(op.get("release_frames", 2)),
        )
    if "hold" in op:
        return emulator_hold(target=target, button=str(op["hold"]), frames=int(op.get("frames", 1)))
    if "release" in op:
        return emulator_release(target=target, frames=int(op["release"]))
    if "step" in op:
        return emulator_step(target=target, frames=int(op["step"]))
    if "screenshot" in op:
        if screenshot_dir is not None:
            return emulator_screenshot_to_dir(target, str(op["screenshot"]), screenshot_dir)
        return emulator_screenshot(target=target, name=str(op["screenshot"]))
    if "gb_gpu_snapshot" in op:
        return emulator_gb_gpu_snapshot(target=target, name=str(op["gb_gpu_snapshot"]))
    if "gpu_snapshot" in op:
        config = op["gpu_snapshot"]
        if isinstance(config, dict):
            return emulator_gb_gpu_snapshot(
                target=target,
                name=str(config.get("name", "snapshot")),
                render=bool(config.get("render", True)),
                include_raw=bool(config.get("include_raw", True)),
            )
        return emulator_gb_gpu_snapshot(target=target, name=str(config))

    raise ValueError(f"Unknown sequence operation: {op}")


def scenario_start_config(scenario: Dict[str, Any]) -> Dict[str, Any]:
    start = scenario.get("start")
    if start is None:
        if scenario.get("load_state"):
            return {"mode": "load_state", "state": str(scenario["load_state"]), "legacy": True}
        return {"mode": "none"}

    if not isinstance(start, dict):
        raise ValueError("scenario start must be an object")

    mode = str(start.get("mode", "none")).lower().strip()
    if mode in ("", "none"):
        return {"mode": "none"}
    if mode == "reset":
        return {
            "mode": "reset",
            "settle_frames": max(1, min(int(start.get("settle_frames", 60)), 600)),
        }
    if mode == "load_state":
        state = str(start.get("state", "")).strip()
        if not state:
            raise ValueError("scenario start load_state requires state")
        return {"mode": "load_state", "state": state}

    raise ValueError(f"unsupported scenario start.mode: {mode}")


def run_scenario_start(target: str, start: Dict[str, Any]) -> tuple[dict | None, str | None]:
    mode = start.get("mode", "none")
    if mode == "none":
        return None, None
    if mode == "reset":
        result = emulator_reset(target=target, settle_frames=int(start.get("settle_frames", 60)))
        return result, result.get("frame")
    if mode == "load_state":
        result = emulator_load_state(target=target, name=str(start["state"]))
        return result, result.get("frame")

    raise ValueError(f"unsupported scenario start.mode: {mode}")


def scenario_required_capabilities(scenario: Dict[str, Any]) -> List[str]:
    required = {"status", "step", "screenshot"}
    start = scenario_start_config(scenario)
    if start.get("mode") == "reset":
        required.add("reset")
    if start.get("mode") == "load_state":
        required.add("load_state")

    if scenario.get("load_state"):
        required.add("load_state")
    if scenario.get("save_state_after"):
        required.add("save_state")

    steps = scenario.get("steps")
    if isinstance(steps, list):
        for op in steps:
            if not isinstance(op, dict):
                continue
            for capability in ("tap", "hold", "release", "step", "screenshot"):
                if capability in op:
                    required.add(capability)
            if "press" in op:
                required.add("tap")
            if "gb_gpu_snapshot" in op or "gpu_snapshot" in op:
                required.add("gb_gpu_snapshot")

    return sorted(required)


def scenario_uses_gpu_snapshot(scenario: Dict[str, Any]) -> bool:
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        return False
    return any(
        isinstance(op, dict) and ("gb_gpu_snapshot" in op or "gpu_snapshot" in op)
        for op in steps
    )


@mcp.tool()
def emulator_run_scenario(path: str, target: str = "gb") -> dict:
    """Run a JSON scenario file against a target."""
    context = get_target_context(target)
    scenario_path = Path(path).expanduser()
    if not scenario_path.is_absolute():
        scenario_path = (Path.cwd() / scenario_path).resolve()

    screenshots = []
    gpu_snapshots = []
    steps_executed = 0
    final_frame = None
    scenario_id = scenario_path.stem
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    started_at = datetime.now(timezone.utc).isoformat()
    loaded_state = None
    saved_state_after = None
    start_config = {"mode": "none"}
    start_result = None
    initial_frame_after_start = None
    run_dir = context.runs_dir / safe_name(scenario_id) / run_id
    run_screenshot_dir = run_dir / "screenshots"
    summary_path = run_dir / "summary.json"

    def finish(summary: dict) -> dict:
        summary.setdefault("start", start_config)
        summary.setdefault("start_result", start_result)
        summary.setdefault("initial_frame_after_start", initial_frame_after_start)
        summary.setdefault("gpu_snapshots", gpu_snapshots)
        write_json(summary_path, summary)
        return summary

    try:
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        scenario_id = str(scenario.get("id", scenario_path.stem))
        run_dir = context.runs_dir / safe_name(scenario_id) / run_id
        run_screenshot_dir = run_dir / "screenshots"
        summary_path = run_dir / "summary.json"
        steps = scenario.get("steps")
        start_config = scenario_start_config(scenario)

        if not isinstance(steps, list):
            return finish({
                "ok": False,
                "target": context.target,
                "scenario": scenario_id,
                "run_id": run_id,
                "started_at": started_at,
                "steps_executed": 0,
                "screenshots": [],
                "gpu_snapshots": [],
                "final_frame": None,
                "error": "scenario steps must be a list",
                "path": str(scenario_path),
                "summary_path": str(summary_path),
            })

        if context.target != "gb" and scenario_uses_gpu_snapshot(scenario):
            return finish({
                "ok": False,
                "target": context.target,
                "scenario": scenario_id,
                "run_id": run_id,
                "started_at": started_at,
                "steps_executed": 0,
                "screenshots": [],
                "gpu_snapshots": [],
                "final_frame": None,
                "error": "GB GPU snapshots are only supported for target gb",
                "path": str(scenario_path),
                "summary_path": str(summary_path),
            })

        require_bridge_capabilities(target, scenario_required_capabilities(scenario))

        if start_config.get("mode") == "load_state":
            loaded_state = str(start_config["state"])

        start_result, initial_frame_after_start = run_scenario_start(target, start_config)
        final_frame = initial_frame_after_start or final_frame

        for index, op in enumerate(steps):
            if not isinstance(op, dict):
                return finish({
                    "ok": False,
                    "target": context.target,
                    "scenario": scenario_id,
                    "run_id": run_id,
                    "started_at": started_at,
                    "steps_executed": steps_executed,
                    "screenshots": screenshots,
                    "gpu_snapshots": gpu_snapshots,
                    "loaded_state": loaded_state,
                    "final_frame": final_frame,
                    "error": "scenario step must be an object",
                    "failed_step_index": index,
                    "failed_step": op,
                    "path": str(scenario_path),
                    "summary_path": str(summary_path),
                })

            result = run_operation(target, op, screenshot_dir=run_screenshot_dir)
            steps_executed += 1
            final_frame = result.get("frame", final_frame)

            if "path" in result:
                screenshots.append({
                    "name": str(op.get("screenshot", infer_screenshot_name(result["path"]))),
                    "path": result["path"],
                    "target": context.target,
                    "frame": result.get("frame"),
                })
            if result.get("snapshot_dir") and result.get("files"):
                snapshot_name = str(op.get("gb_gpu_snapshot", "snapshot"))
                if "gpu_snapshot" in op:
                    config = op["gpu_snapshot"]
                    snapshot_name = str(config.get("name", "snapshot")) if isinstance(config, dict) else str(config)
                gpu_snapshots.append({
                    "name": snapshot_name,
                    "frame": result.get("frame"),
                    "snapshot_dir": result.get("snapshot_dir"),
                    "files": result.get("files", {}),
                    "notes": result.get("notes", []),
                })

        if scenario.get("save_state_after"):
            saved_state_after = str(scenario["save_state_after"])
            result = emulator_save_state(target=target, name=saved_state_after)
            final_frame = result.get("frame", final_frame)

    except FileNotFoundError:
        return finish({
            "ok": False,
            "target": context.target,
            "scenario": scenario_id or scenario_path.stem,
            "run_id": run_id,
            "started_at": started_at,
            "steps_executed": steps_executed,
            "screenshots": screenshots,
            "gpu_snapshots": gpu_snapshots,
            "loaded_state": loaded_state,
            "saved_state_after": saved_state_after,
            "final_frame": final_frame,
            "error": f"scenario file not found: {scenario_path}",
            "path": str(scenario_path),
            "summary_path": str(summary_path),
        })
    except json.JSONDecodeError as exc:
        return finish({
            "ok": False,
            "target": context.target,
            "scenario": scenario_id or scenario_path.stem,
            "run_id": run_id,
            "started_at": started_at,
            "steps_executed": steps_executed,
            "screenshots": screenshots,
            "gpu_snapshots": gpu_snapshots,
            "loaded_state": loaded_state,
            "saved_state_after": saved_state_after,
            "final_frame": final_frame,
            "error": f"invalid scenario JSON: {exc}",
            "path": str(scenario_path),
            "summary_path": str(summary_path),
        })
    except Exception as exc:
        return finish({
            "ok": False,
            "target": context.target,
            "scenario": scenario_id or scenario_path.stem,
            "run_id": run_id,
            "started_at": started_at,
            "steps_executed": steps_executed,
            "screenshots": screenshots,
            "gpu_snapshots": gpu_snapshots,
            "loaded_state": loaded_state,
            "saved_state_after": saved_state_after,
            "final_frame": final_frame,
            "error": str(exc),
            "path": str(scenario_path),
            "summary_path": str(summary_path),
        })

    summary = {
        "ok": True,
        "target": context.target,
        "scenario": scenario_id,
        "run_id": run_id,
        "started_at": started_at,
        "steps_executed": steps_executed,
        "screenshots": screenshots,
        "gpu_snapshots": gpu_snapshots,
        "loaded_state": loaded_state,
        "saved_state_after": saved_state_after,
        "final_frame": final_frame,
        "path": str(scenario_path),
        "summary_path": str(summary_path),
    }

    return finish(summary)


@mcp.tool()
def emulator_run_pair_scenario(path: str) -> dict:
    """Run paired GB and MD scenarios and collect their outputs without visual diffing."""
    pair_path = Path(path).expanduser()
    if not pair_path.is_absolute():
        pair_path = (Path.cwd() / pair_path).resolve()

    pair_id = pair_path.stem
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    started_at = datetime.now(timezone.utc).isoformat()
    pair_run_dir = PAIR_RUNS_DIR / safe_name(pair_id) / run_id
    pair_summary_path = pair_run_dir / "pair_summary.json"
    gb_result = None
    md_result = None
    pair_viewports = {"gb": None, "md": None}
    pair_canonical_sizes = {"gb": (160, 144), "md": (160, 144)}

    def finish(summary: dict) -> dict:
        gb_screenshots = normalize_screenshots(summary.get("gb_screenshots", []), "gb")
        md_screenshots = normalize_screenshots(summary.get("md_screenshots", []), "md")
        summary["gb_screenshots"] = gb_screenshots
        summary["md_screenshots"] = md_screenshots
        gb_summary = summary.get("gb_result") or {}
        md_summary = summary.get("md_result") or {}
        summary["gb"] = summary.get("gb") or {
            "summary_path": summary.get("gb_summary_path"),
            "start": gb_summary.get("start"),
            "start_result": gb_summary.get("start_result"),
            "initial_frame_after_start": gb_summary.get("initial_frame_after_start"),
        }
        summary["md"] = summary.get("md") or {
            "summary_path": summary.get("md_summary_path"),
            "start": md_summary.get("start"),
            "start_result": md_summary.get("start_result"),
            "initial_frame_after_start": md_summary.get("initial_frame_after_start"),
        }
        summary["viewports"] = summary.get("viewports") or pair_viewports
        summary["canonical_size"] = summary.get("canonical_size") or {
            "gb": list(pair_canonical_sizes["gb"]),
            "md": list(pair_canonical_sizes["md"]),
        }
        checkpoints, checkpoint_errors = build_checkpoints(
            gb_screenshots,
            md_screenshots,
            canonical_dir=pair_summary_path.parent / "canonical",
            viewports=pair_viewports,
            canonical_sizes=pair_canonical_sizes,
        )
        summary["checkpoints"] = summary.get("checkpoints") or checkpoints
        errors = list(summary.get("errors") or [])
        errors.extend(checkpoint_errors)
        diffs_dir = pair_summary_path.parent / "diffs"
        summary["diffs_dir"] = str(diffs_dir)
        summary["diff_metrics_version"] = "basic_absdiff_v1"
        diffs_generated, diff_errors = build_pair_diffs(summary["checkpoints"], diffs_dir)
        summary["diffs_generated"] = diffs_generated
        errors.extend(diff_errors)
        summary["errors"] = errors
        if checkpoint_errors and summary.get("ok"):
            summary["ok"] = False
            summary["error"] = summary.get("error") or "pair completed with viewport/contact sheet errors"
        contact_sheet_path = pair_summary_path.parent / "contact_sheet.png"
        try:
            summary["contact_sheet_path"] = build_pair_contact_sheet(summary, contact_sheet_path)
        except Exception as exc:
            summary["contact_sheet_path"] = None
            summary["contact_sheet_error"] = str(exc)
        write_json(pair_summary_path, summary)
        return summary

    try:
        pair = json.loads(pair_path.read_text(encoding="utf-8"))
        pair_id = str(pair.get("id", pair_path.stem))
        pair_run_dir = PAIR_RUNS_DIR / safe_name(pair_id) / run_id
        pair_summary_path = pair_run_dir / "pair_summary.json"

        gb_entry = pair.get("gb")
        md_entry = pair.get("md")
        if not isinstance(gb_entry, dict) or "scenario" not in gb_entry:
            raise ValueError("pair scenario must include gb.scenario")
        if not isinstance(md_entry, dict) or "scenario" not in md_entry:
            raise ValueError("pair scenario must include md.scenario")

        pair_viewports = {
            "gb": viewport_from_config(gb_entry, {"x": 0, "y": 0, "w": 160, "h": 144}),
            "md": viewport_from_config(md_entry, None),
        }
        pair_canonical_sizes = {
            "gb": canonical_size_from_config(gb_entry, (160, 144)),
            "md": canonical_size_from_config(md_entry, (160, 144)),
        }

        gb_scenario_path = resolve_relative_path(pair_path, str(gb_entry["scenario"]))
        md_scenario_path = resolve_relative_path(pair_path, str(md_entry["scenario"]))

        gb_result = emulator_run_scenario(str(gb_scenario_path), target="gb")
        if not gb_result.get("ok"):
            return finish({
                "ok": False,
                "pair_id": pair_id,
                "run_id": run_id,
                "started_at": started_at,
                "gb_summary_path": gb_result.get("summary_path"),
                "md_summary_path": None,
                "gb_screenshots": gb_result.get("screenshots", []),
                "md_screenshots": [],
                "gb_result": gb_result,
                "md_result": None,
                "viewports": pair_viewports,
                "canonical_size": {
                    "gb": list(pair_canonical_sizes["gb"]),
                    "md": list(pair_canonical_sizes["md"]),
                },
                "errors": [],
                "error": f"GB scenario failed; MD scenario was not executed: {gb_result.get('error', 'unknown error')}",
                "path": str(pair_path),
            })

        md_result = emulator_run_scenario(str(md_scenario_path), target="md")

    except FileNotFoundError:
        return finish({
            "ok": False,
            "pair_id": pair_id,
            "run_id": run_id,
            "started_at": started_at,
            "gb_summary_path": gb_result.get("summary_path") if gb_result else None,
            "md_summary_path": md_result.get("summary_path") if md_result else None,
            "gb_screenshots": gb_result.get("screenshots", []) if gb_result else [],
            "md_screenshots": md_result.get("screenshots", []) if md_result else [],
            "gb_result": gb_result,
            "md_result": md_result,
            "viewports": pair_viewports,
            "canonical_size": {
                "gb": list(pair_canonical_sizes["gb"]),
                "md": list(pair_canonical_sizes["md"]),
            },
            "errors": [],
            "error": f"pair scenario file not found: {pair_path}",
            "path": str(pair_path),
        })
    except json.JSONDecodeError as exc:
        return finish({
            "ok": False,
            "pair_id": pair_id,
            "run_id": run_id,
            "started_at": started_at,
            "gb_summary_path": gb_result.get("summary_path") if gb_result else None,
            "md_summary_path": md_result.get("summary_path") if md_result else None,
            "gb_screenshots": gb_result.get("screenshots", []) if gb_result else [],
            "md_screenshots": md_result.get("screenshots", []) if md_result else [],
            "gb_result": gb_result,
            "md_result": md_result,
            "viewports": pair_viewports,
            "canonical_size": {
                "gb": list(pair_canonical_sizes["gb"]),
                "md": list(pair_canonical_sizes["md"]),
            },
            "errors": [],
            "error": f"invalid pair scenario JSON: {exc}",
            "path": str(pair_path),
        })
    except Exception as exc:
        return finish({
            "ok": False,
            "pair_id": pair_id,
            "run_id": run_id,
            "started_at": started_at,
            "gb_summary_path": gb_result.get("summary_path") if gb_result else None,
            "md_summary_path": md_result.get("summary_path") if md_result else None,
            "gb_screenshots": gb_result.get("screenshots", []) if gb_result else [],
            "md_screenshots": md_result.get("screenshots", []) if md_result else [],
            "gb_result": gb_result,
            "md_result": md_result,
            "viewports": pair_viewports,
            "canonical_size": {
                "gb": list(pair_canonical_sizes["gb"]),
                "md": list(pair_canonical_sizes["md"]),
            },
            "errors": [],
            "error": str(exc),
            "path": str(pair_path),
        })

    ok = bool(gb_result and gb_result.get("ok") and md_result and md_result.get("ok"))
    error = None
    if not ok:
        if gb_result and not gb_result.get("ok"):
            error = f"GB scenario failed: {gb_result.get('error', 'unknown error')}"
        elif md_result and not md_result.get("ok"):
            error = f"MD scenario failed: {md_result.get('error', 'unknown error')}"
        else:
            error = "pair scenario failed"

    return finish({
        "ok": ok,
        "pair_id": pair_id,
        "run_id": run_id,
        "started_at": started_at,
        "gb_summary_path": gb_result.get("summary_path") if gb_result else None,
        "md_summary_path": md_result.get("summary_path") if md_result else None,
        "gb_screenshots": gb_result.get("screenshots", []) if gb_result else [],
        "md_screenshots": md_result.get("screenshots", []) if md_result else [],
        "gb_result": gb_result,
        "md_result": md_result,
        "viewports": pair_viewports,
        "canonical_size": {
            "gb": list(pair_canonical_sizes["gb"]),
            "md": list(pair_canonical_sizes["md"]),
        },
        "errors": [],
        "error": error,
        "path": str(pair_path),
    })


@mcp.tool()
def emulator_build_pair_contact_sheet(pair_summary_path: str) -> dict:
    """Rebuild a pair contact sheet from an existing pair_summary.json."""
    summary_path = Path(pair_summary_path).expanduser()
    if not summary_path.is_absolute():
        summary_path = (Path.cwd() / summary_path).resolve()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    output_path = summary_path.parent / "contact_sheet.png"
    summary["contact_sheet_path"] = build_pair_contact_sheet(summary, output_path)
    write_json(summary_path, summary)
    return {
        "ok": True,
        "pair_summary_path": str(summary_path),
        "contact_sheet_path": summary["contact_sheet_path"],
    }


@mcp.tool()
def emulator_build_pair_diffs(pair_summary_path: str) -> dict:
    """Rebuild canonical crop diffs and the contact sheet from an existing pair_summary.json."""
    summary_path = Path(pair_summary_path).expanduser()
    if not summary_path.is_absolute():
        summary_path = (Path.cwd() / summary_path).resolve()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    diffs_dir = summary_path.parent / "diffs"
    generated, diff_errors = build_pair_diffs(summary.get("checkpoints", []), diffs_dir)
    errors = list(summary.get("errors") or [])
    errors.extend(diff_errors)
    summary["errors"] = errors
    summary["diffs_dir"] = str(diffs_dir)
    summary["diffs_generated"] = generated
    summary["diff_metrics_version"] = "basic_absdiff_v1"
    summary["contact_sheet_path"] = build_pair_contact_sheet(summary, summary_path.parent / "contact_sheet.png")
    write_json(summary_path, summary)
    return {
        "ok": True,
        "pair_summary_path": str(summary_path),
        "diffs_dir": summary["diffs_dir"],
        "diffs_generated": generated,
        "diff_errors": diff_errors,
        "contact_sheet_path": summary["contact_sheet_path"],
    }


@mcp.tool()
def bizhawk_status() -> dict:
    """GB-compatible wrapper for emulator_status."""
    return emulator_status("gb")


@mcp.tool()
def bizhawk_bridge_info() -> dict:
    """GB-compatible wrapper for emulator_bridge_info."""
    return emulator_bridge_info("gb")


@mcp.tool()
def bizhawk_step(frames: int = 1) -> dict:
    """GB-compatible wrapper for emulator_step."""
    return emulator_step("gb", frames)


@mcp.tool()
def bizhawk_tap(button: str, hold_frames: int = 8, release_frames: int = 2) -> dict:
    """GB-compatible wrapper for emulator_tap."""
    return emulator_tap(target="gb", button=button, hold_frames=hold_frames, release_frames=release_frames)


@mcp.tool()
def bizhawk_hold(button: str, frames: int) -> dict:
    """GB-compatible wrapper for emulator_hold."""
    return emulator_hold(target="gb", button=button, frames=frames)


@mcp.tool()
def bizhawk_release(frames: int = 2) -> dict:
    """GB-compatible wrapper for emulator_release."""
    return emulator_release("gb", frames)


@mcp.tool()
def bizhawk_reset(settle_frames: int = 60) -> dict:
    """GB-compatible wrapper for emulator_reset."""
    return emulator_reset(target="gb", settle_frames=settle_frames)


@mcp.tool()
def bizhawk_press(button: str, frames: int = 1) -> dict:
    """Legacy GB alias for tap(button, hold_frames=frames, release_frames=2)."""
    return emulator_tap(target="gb", button=button, hold_frames=frames, release_frames=2)


@mcp.tool()
def bizhawk_screenshot(name: str = "shot") -> dict:
    """GB-compatible wrapper for emulator_screenshot."""
    return emulator_screenshot(target="gb", name=name)


@mcp.tool()
def bizhawk_save_state(name: str) -> dict:
    """GB-compatible wrapper for emulator_save_state."""
    return emulator_save_state(target="gb", name=name)


@mcp.tool()
def bizhawk_load_state(name: str) -> dict:
    """GB-compatible wrapper for emulator_load_state."""
    return emulator_load_state(target="gb", name=name)


@mcp.tool()
def bizhawk_create_baseline(name: str) -> dict:
    """GB-compatible alias for saving a named baseline state."""
    return emulator_create_baseline(target="gb", name=name)


@mcp.tool()
def bizhawk_load_baseline(name: str) -> dict:
    """GB-compatible alias for loading a named baseline state."""
    return emulator_load_baseline(target="gb", name=name)


@mcp.tool()
def bizhawk_run_sequence(operations: List[dict]) -> dict:
    """GB-compatible sequence runner."""
    results = []

    for op in operations:
        results.append(run_operation("gb", op))

    return {"status": "ok", "target": "gb", "results": results}


@mcp.tool()
def bizhawk_run_scenario(path: str) -> dict:
    """GB-compatible wrapper for emulator_run_scenario."""
    return emulator_run_scenario(path, target="gb")


def bizhawk_buttons() -> dict:
    """GB-compatible button name diagnostic."""
    return emulator_buttons("gb")


def bridge_source_path() -> Path:
    candidates = (
        ROOT / "bizhawk" / "bridge.lua",
        Path(sysconfig.get_path("data")) / "share" / "emulator-mcp" / "bridge.lua",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("bridge.lua was not included in this installation")


def init_project(destination: str = ".emulator-mcp") -> dict:
    home = Path(destination).expanduser().resolve()
    bridge = home / "bizhawk" / "bridge.lua"
    bridge.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bridge_source_path(), bridge)
    for target in sorted(VALID_TARGETS):
        (home / "runtime" / target).mkdir(parents=True, exist_ok=True)

    return {
        "status": "ok",
        "home": str(home),
        "bridge": str(bridge),
        "targets": sorted(VALID_TARGETS),
    }


def parse_cli_args(argv: List[str]) -> tuple[str, List[str]]:
    target = "gb"
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--target":
            if i + 1 >= len(argv):
                raise SystemExit("Usage: emulator-mcp --target <gb|md|nes|snes> --test <command>")
            target = argv[i + 1].lower().strip()
            i += 2
            continue
        args.append(argv[i])
        i += 1

    get_target_context(target)
    return target, args


def cli_test(argv: List[str]) -> None:
    target, args = parse_cli_args(argv)

    if "--test" not in args:
        raise SystemExit("Usage: emulator-mcp [--target gb|md|nes|snes] --test <command>")

    test_index = args.index("--test")
    if len(args) <= test_index + 1:
        print("Usage:")
        print("  emulator-mcp --target gb --test status")
        print("  emulator-mcp --target gb --test bridge-info")
        print("  emulator-mcp --target gb --test step 60")
        print("  emulator-mcp --target gb --test tap A 8")
        print("  emulator-mcp --target gb --test hold RIGHT 16")
        print("  emulator-mcp --target gb --test release 2")
        print("  emulator-mcp --target gb --test reset")
        print("  emulator-mcp --target gb --test buttons")
        print("  emulator-mcp --target gb --test press START 10")
        print("  emulator-mcp --target gb --test save-state title_screen")
        print("  emulator-mcp --target gb --test load-state title_screen")
        print("  emulator-mcp --target gb --test create-baseline title_screen")
        print("  emulator-mcp --target gb --test load-baseline title_screen")
        print("  emulator-mcp --target gb --test screenshot title")
        print("  emulator-mcp --target gb --test gb-gpu-snapshot")
        print("  emulator-mcp --target gb --test gb-gpu-snapshot --no-render")
        print("  emulator-mcp --target gb --test run-scenario scenarios\\gb\\title_start.json")
        print("  emulator-mcp --target md --test run-scenario scenarios\\md\\title_start.json")
        print("  emulator-mcp --target nes --test run-scenario scenarios\\nes\\title_start.json")
        print("  emulator-mcp --target snes --test run-scenario scenarios\\snes\\title_start.json")
        print("  emulator-mcp --test run-pair-scenario scenarios\\pairs\\title_start.json")
        print("  emulator-mcp --test build-contact-sheet <pair_summary_path>")
        print("  emulator-mcp --test build-pair-diffs <pair_summary_path>")
        raise SystemExit(1)

    cmd = args[test_index + 1]
    cmd_args = args[test_index + 2:]

    if cmd == "status":
        print(emulator_status(target))
    elif cmd == "bridge-info":
        print(emulator_bridge_info(target))
    elif cmd == "step":
        frames = int(cmd_args[0]) if len(cmd_args) > 0 else 1
        print(emulator_step(target, frames))
    elif cmd == "tap":
        button = cmd_args[0]
        hold_frames = int(cmd_args[1]) if len(cmd_args) > 1 else 8
        release_frames = int(cmd_args[2]) if len(cmd_args) > 2 else 2
        print(emulator_tap(target=target, button=button, hold_frames=hold_frames, release_frames=release_frames))
    elif cmd == "hold":
        button = cmd_args[0]
        frames = int(cmd_args[1]) if len(cmd_args) > 1 else 1
        print(emulator_hold(target=target, button=button, frames=frames))
    elif cmd == "release":
        frames = int(cmd_args[0]) if len(cmd_args) > 0 else 2
        print(emulator_release(target, frames))
    elif cmd == "reset":
        settle_frames = int(cmd_args[0]) if len(cmd_args) > 0 else 60
        print(emulator_reset(target=target, settle_frames=settle_frames))
    elif cmd == "press":
        button = cmd_args[0]
        frames = int(cmd_args[1]) if len(cmd_args) > 1 else 1
        print(emulator_tap(target=target, button=button, hold_frames=frames, release_frames=2))
    elif cmd == "save-state":
        name = cmd_args[0] if len(cmd_args) > 0 else "state"
        print(emulator_save_state(target=target, name=name))
    elif cmd == "load-state":
        name = cmd_args[0] if len(cmd_args) > 0 else "state"
        print(emulator_load_state(target=target, name=name))
    elif cmd == "create-baseline":
        name = cmd_args[0] if len(cmd_args) > 0 else "baseline"
        print(emulator_create_baseline(target=target, name=name))
    elif cmd == "load-baseline":
        name = cmd_args[0] if len(cmd_args) > 0 else "baseline"
        print(emulator_load_baseline(target=target, name=name))
    elif cmd == "screenshot":
        name = cmd_args[0] if len(cmd_args) > 0 else "shot"
        print(emulator_screenshot(target=target, name=name))
    elif cmd == "gb-gpu-snapshot":
        render = "--no-render" not in cmd_args
        names = [arg for arg in cmd_args if not arg.startswith("--")]
        name = names[0] if names else "snapshot"
        print(emulator_gb_gpu_snapshot(target=target, render=render, include_raw=True, name=name))
    elif cmd == "run-scenario":
        if len(cmd_args) < 1:
            raise SystemExit("Usage: python mcp_server.py --test run-scenario <path>")
        print(emulator_run_scenario(cmd_args[0], target=target))
    elif cmd == "run-pair-scenario":
        if len(cmd_args) < 1:
            raise SystemExit("Usage: python mcp_server.py --test run-pair-scenario <path>")
        print(emulator_run_pair_scenario(cmd_args[0]))
    elif cmd == "build-contact-sheet":
        if len(cmd_args) < 1:
            raise SystemExit("Usage: python mcp_server.py --test build-contact-sheet <pair_summary_path>")
        print(emulator_build_pair_contact_sheet(cmd_args[0]))
    elif cmd == "build-pair-diffs":
        if len(cmd_args) < 1:
            raise SystemExit("Usage: python mcp_server.py --test build-pair-diffs <pair_summary_path>")
        print(emulator_build_pair_diffs(cmd_args[0]))
    elif cmd == "buttons":
        print(emulator_buttons(target))
    else:
        raise SystemExit(f"Unknown test command: {cmd}")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print("BizHawk Emulator MCP")
        print("  emulator-mcp                         Start the stdio MCP server")
        print("  emulator-mcp init [directory]        Create or update project files")
        print("  emulator-mcp --target <target> --test <command>")
        print("  emulator-mcp --version")
    elif args and args[0] == "--version":
        print(VERSION)
    elif args and args[0] == "init":
        destination = args[1] if len(args) > 1 else ".emulator-mcp"
        print(json.dumps(init_project(destination), indent=2))
    elif "--test" in args:
        cli_test(args)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
