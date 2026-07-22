-- Portable BizHawk <-> MCP file bridge.
local source = debug.getinfo(1, "S").source or ""
local home_dir = os.getenv("EMULATOR_MCP_HOME")

if not home_dir or home_dir == "" then
  local script_path = string.sub(source, 1, 1) == "@" and string.sub(source, 2) or source
  if script_path == "" or script_path == "main" then
    error("EMULATOR_MCP_HOME is required when BizHawk does not expose the Lua script path")
  end

  local script_dir = string.match(script_path, "^(.*)[/\\]")
  if not script_dir then
    error("Unable to determine the bridge home; set EMULATOR_MCP_HOME")
  end
  home_dir = string.match(script_dir, "^(.*)[/\\][^/\\]+$") or "."
end

while #home_dir > 3 and (string.sub(home_dir, -1) == "/" or string.sub(home_dir, -1) == "\\") do
  home_dir = string.sub(home_dir, 1, -2)
end

local SYSTEM_TARGETS = {
  GB = "gb", GBC = "gb", SGB = "gb",
  GEN = "md",
  NES = "nes",
  SNES = "snes"
}
local TARGET = SYSTEM_TARGETS[emu.getsystemid()]
if not TARGET then
  error("Unsupported system " .. tostring(emu.getsystemid()) .. "; expected GB, GEN, NES, or SNES")
end

local BRIDGE_DIR = home_dir .. "/runtime/" .. TARGET

local CMD_FILE = BRIDGE_DIR .. "/command.txt"
local RESP_FILE = BRIDGE_DIR .. "/response.txt"
local LOG_FILE = BRIDGE_DIR .. "/bridge.log"
local BRIDGE_VERSION = "1.1.0"
local BRIDGE_CAPABILITIES = {
  "status", "step", "tap", "hold", "release",
  "screenshot", "save_state", "load_state", "reset", "memory_read"
}
if TARGET == "gb" then table.insert(BRIDGE_CAPABILITIES, "gb_gpu_snapshot") end

local VALID_BUTTONS = {
  A = { "P1 A", "A" },
  B = { "P1 B", "B" },
  C = { "P1 C", "C" },
  X = { "P1 X", "X" },
  Y = { "P1 Y", "Y" },
  Z = { "P1 Z", "Z" },
  L = { "P1 L", "L" },
  R = { "P1 R", "R" },
  MODE = { "P1 Mode", "Mode" },
  START = { "P1 Start", "Start" },
  SELECT = { "P1 Select", "Select" },
  UP = { "P1 Up", "Up" },
  DOWN = { "P1 Down", "Down" },
  LEFT = { "P1 Left", "Left" },
  RIGHT = { "P1 Right", "Right" }
}

local function log(msg)
  local f = io.open(LOG_FILE, "a")
  if f then
    f:write(os.date("%Y-%m-%d %H:%M:%S") .. " " .. msg .. "\n")
    f:close()
  end
end

local function read_all(path)
  local f = io.open(path, "r")
  if not f then return nil end
  local s = f:read("*a")
  f:close()
  return s
end

local function atomic_write(path, content)
  local tmp = path .. ".tmp"
  local f = io.open(tmp, "w")
  if not f then return false end
  f:write(content)
  f:close()
  os.remove(path)
  os.rename(tmp, path)
  return true
end

local function split(s, sep)
  local parts = {}
  for part in string.gmatch(s, "([^" .. sep .. "]+)") do
    table.insert(parts, part)
  end
  return parts
end

local function dump_table(t)
  if not t then return "" end

  local s = ""
  for k, v in pairs(t) do
    s = s .. tostring(k) .. "=" .. tostring(v) .. ","
  end
  return s
end

local function join_table(t, sep)
  sep = sep or ","
  local s = ""
  for i, v in ipairs(t) do
    if i > 1 then
      s = s .. sep
    end
    s = s .. tostring(v)
  end
  return s
end

local function json_escape(s)
  s = tostring(s or "")
  s = string.gsub(s, "\\", "\\\\")
  s = string.gsub(s, "\"", "\\\"")
  s = string.gsub(s, "\n", "\\n")
  s = string.gsub(s, "\r", "\\r")
  s = string.gsub(s, "\t", "\\t")
  return s
end

local function json_string(s)
  return "\"" .. json_escape(s) .. "\""
end

local function memory_domains()
  local domains = memory.getmemorydomainlist()
  if not domains then
    return {}
  end
  return domains
end

local function find_domain_exact(name, domains)
  for _, domain in ipairs(domains) do
    if domain == name then
      return domain
    end
  end
  return nil
end

local function write_binary_file(path, bytes)
  local f = io.open(path, "wb")
  if not f then return false end
  f:write(bytes)
  f:close()
  return true
end

local function write_text_file(path, text)
  local f = io.open(path, "w")
  if not f then return false end
  f:write(text)
  f:close()
  return true
end

local function write_domains_json(path, domains)
  local lines = { "{\n  \"domains\": [" }
  for i, domain in ipairs(domains) do
    local suffix = ","
    if i == #domains then suffix = "" end
    table.insert(lines, "\n    " .. json_string(domain) .. suffix)
  end
  table.insert(lines, "\n  ]\n}\n")
  return write_text_file(path, table.concat(lines, ""))
end

local function write_regs_json(path, regs)
  local lines = {
    "{\n",
    "  \"frame\": " .. tostring(regs.frame) .. ",\n",
    "  \"system\": " .. json_string(regs.system) .. ",\n",
    "  \"lcdc\": " .. tostring(regs.lcdc) .. ",\n",
    "  \"stat\": " .. tostring(regs.stat) .. ",\n",
    "  \"scy\": " .. tostring(regs.scy) .. ",\n",
    "  \"scx\": " .. tostring(regs.scx) .. ",\n",
    "  \"ly\": " .. tostring(regs.ly) .. ",\n",
    "  \"lyc\": " .. tostring(regs.lyc) .. ",\n",
    "  \"bgp\": " .. tostring(regs.bgp) .. ",\n",
    "  \"obp0\": " .. tostring(regs.obp0) .. ",\n",
    "  \"obp1\": " .. tostring(regs.obp1) .. ",\n",
    "  \"wy\": " .. tostring(regs.wy) .. ",\n",
    "  \"wx\": " .. tostring(regs.wx) .. "\n",
    "}\n"
  }
  return write_text_file(path, table.concat(lines, ""))
end

local read_u8_checked

local function read_domain_binary(addr, length, domain)
  if memory.read_bytes_as_binary_string then
    local ok, bytes = pcall(memory.read_bytes_as_binary_string, addr, length, domain)
    if not ok then
      return nil, "memory_read_bytes_failed:" .. tostring(bytes)
    end
    if not bytes then
      return nil, "memory_read_bytes_returned_nil"
    end
    return bytes, nil
  end

  local chunks = {}
  local chunk = {}
  local chunk_size = 0

  for i = 0, length - 1 do
    local value, err = read_u8_checked(addr + i, domain)
    if value == nil then
      return nil, "memory_read_u8_fallback_failed_at_" .. tostring(i) .. ":" .. tostring(err)
    end

    chunk_size = chunk_size + 1
    chunk[chunk_size] = string.char(value)

    if chunk_size >= 512 then
      table.insert(chunks, table.concat(chunk, ""))
      chunk = {}
      chunk_size = 0
    end
  end

  if chunk_size > 0 then
    table.insert(chunks, table.concat(chunk, ""))
  end

  return table.concat(chunks, ""), nil
end

local function bytes_to_hex(bytes)
  local chunks = {}
  for i = 1, #bytes do
    chunks[i] = string.format("%02X", string.byte(bytes, i))
  end
  return table.concat(chunks, "")
end

function read_u8_checked(addr, domain)
  local ok, value = pcall(memory.read_u8, addr, domain)
  if not ok then
    return nil, "memory_read_u8_failed:" .. tostring(value)
  end
  if value == nil then
    return nil, "memory_read_u8_returned_nil"
  end
  return value, nil
end

local function respond(id, status, fields)
  local line = id .. "|" .. status
  fields = fields or {}
  for k, v in pairs(fields) do
    line = line .. "|" .. k .. "=" .. tostring(v)
  end
  atomic_write(RESP_FILE, line)
end

local function normalize_button(button)
  button = string.upper(button or "")
  return VALID_BUTTONS[button]
end

local function table_has_any_key(t)
  if not t then return false end
  for _, _ in pairs(t) do
    return true
  end
  return false
end

local function get_available_buttons()
  local global = joypad.get()
  if table_has_any_key(global) then
    return global, "joypad.get"
  end

  local p1 = joypad.get(1)
  if table_has_any_key(p1) then
    return p1, "joypad.get(1)"
  end

  return {}, "empty"
end

local function build_input(button)
  local candidates = normalize_button(button)
  if not candidates then
    return nil, "unknown_button"
  end

  local available, source = get_available_buttons()
  local input = {}
  local matched = 0

  for _, name in ipairs(candidates) do
    if available[name] ~= nil then
      input[name] = true
      matched = matched + 1
    end
  end

  if matched == 0 then
    if table_has_any_key(available) then
      return nil, "button_not_available"
    end
    -- Some cores expose no useful table until input is set once.
    local fallback = candidates[1]
    input[fallback] = true
    log("button fallback: " .. tostring(button) .. " -> " .. tostring(fallback) .. " source=" .. tostring(source))
  else
    log("button matched: " .. tostring(button) .. " source=" .. tostring(source) .. " input=" .. dump_table(input))
  end

  return input, "ok"
end

local function build_release_input()
  local input = {}
  local available = get_available_buttons()

  if table_has_any_key(available) then
    for name, _ in pairs(available) do
      input[name] = false
    end
    return input
  end

  for _, candidates in pairs(VALID_BUTTONS) do
    for _, name in ipairs(candidates) do
      input[name] = false
    end
  end

  return input
end

local function advance_one_frame(input)
  local start_frame = emu.framecount()
  local guard = 0

  client.unpause()

  while emu.framecount() == start_frame do
    if input then
      joypad.set(input)
      -- gui.drawText(5, 5, "INPUT " .. dump_table(input), "white", "black")
    end

    emu.yield()

    guard = guard + 1
    if guard > 300 then
      client.pause()
      return false, "frame_did_not_advance"
    end
  end

  client.pause()
  return true, "ok"
end

local function clamp_frames(frames, default_frames, max_frames)
  frames = tonumber(frames) or default_frames
  if frames < 1 then frames = 1 end
  if frames > max_frames then frames = max_frames end
  return frames
end

local function release_frames(frames)
  frames = clamp_frames(frames, 2, 600)
  local input = build_release_input()

  log("release frames=" .. tostring(frames) .. " input=" .. dump_table(input))

  for i = 1, frames do
    local ok, err = advance_one_frame(input)
    if not ok then
      return false, err
    end
  end

  return true, "ok"
end

local function step_frames(frames)
  return release_frames(clamp_frames(frames, 1, 600))
end

local function hold_button(button, frames)
  local input, err = build_input(button)
  if not input then
    return false, err
  end

  frames = clamp_frames(frames, 1, 120)

  log("hold button=" .. tostring(button) .. " frames=" .. tostring(frames) .. " input=" .. dump_table(input))

  for i = 1, frames do
    local ok, step_err = advance_one_frame(input)
    if not ok then
      return false, step_err
    end
  end

  return true, "ok"
end

local function tap_button(button, hold_frames, release_frames_count)
  hold_frames = clamp_frames(hold_frames, 8, 120)
  release_frames_count = clamp_frames(release_frames_count, 2, 600)

  log("tap button=" .. tostring(button) .. " hold_frames=" .. tostring(hold_frames) ..
      " release_frames=" .. tostring(release_frames_count))

  local ok, err = release_frames(release_frames_count)
  if not ok then
    return false, err
  end

  ok, err = hold_button(button, hold_frames)
  if not ok then
    return false, err
  end

  ok, err = release_frames(release_frames_count)
  if not ok then
    return false, err
  end

  return true, "ok"
end

local function reset_core(settle_frames)
  settle_frames = clamp_frames(settle_frames, 60, 600)
  local input = build_release_input()

  log("reset settle_frames=" .. tostring(settle_frames) .. " input=" .. dump_table(input))
  joypad.set(input)
  client.reboot_core()

  for i = 1, settle_frames do
    local ok, err = advance_one_frame(input)
    if not ok then
      return false, err
    end
  end

  client.pause()
  return true, "ok"
end

local function gb_gpu_snapshot(snapshot_dir)
  if not snapshot_dir or snapshot_dir == "" then
    return false, "missing_snapshot_dir", nil
  end

  local domains = memory_domains()
  local domains_path = snapshot_dir .. "/domains.json"
  write_domains_json(domains_path, domains)

  local vram_domain = find_domain_exact("VRAM", domains)
  local oam_domain = find_domain_exact("OAM", domains)
  local bus_domain = find_domain_exact("System Bus", domains)

  if not vram_domain then
    return false, "missing_memory_domain_VRAM", { domains_path = domains_path }
  end
  if not oam_domain then
    return false, "missing_memory_domain_OAM", { domains_path = domains_path }
  end
  if not bus_domain then
    return false, "missing_memory_domain_System_Bus", { domains_path = domains_path }
  end

  local vram_path = snapshot_dir .. "/vram.bin"
  local oam_path = snapshot_dir .. "/oam.bin"
  local regs_path = snapshot_dir .. "/regs.json"
  local frame_path = snapshot_dir .. "/frame.txt"

  local vram, read_err = read_domain_binary(0x0000, 0x2000, vram_domain)
  if not vram then
    return false, "read_vram_failed:" .. tostring(read_err), { domains_path = domains_path }
  end

  local oam
  oam, read_err = read_domain_binary(0x0000, 0x00A0, oam_domain)
  if not oam then
    return false, "read_oam_failed:" .. tostring(read_err), { domains_path = domains_path }
  end

  if not write_binary_file(vram_path, vram) then
    return false, "write_vram_failed", { domains_path = domains_path }
  end
  if not write_binary_file(oam_path, oam) then
    return false, "write_oam_failed", { domains_path = domains_path }
  end

  local regs = {
    frame = emu.framecount(),
    system = emu.getsystemid()
  }

  local reg_addresses = {
    lcdc = 0xFF40,
    stat = 0xFF41,
    scy = 0xFF42,
    scx = 0xFF43,
    ly = 0xFF44,
    lyc = 0xFF45,
    bgp = 0xFF47,
    obp0 = 0xFF48,
    obp1 = 0xFF49,
    wy = 0xFF4A,
    wx = 0xFF4B
  }

  for name, addr in pairs(reg_addresses) do
    local value
    value, read_err = read_u8_checked(addr, bus_domain)
    if value == nil then
      return false, "read_register_" .. name .. "_failed:" .. tostring(read_err), { domains_path = domains_path }
    end
    regs[name] = value
  end

  if not write_regs_json(regs_path, regs) then
    return false, "write_regs_failed", { domains_path = domains_path }
  end
  write_text_file(frame_path, tostring(regs.frame) .. "\n")

  return true, "ok", {
    frame = regs.frame,
    system = regs.system,
    snapshot_dir = snapshot_dir,
    vram_path = vram_path,
    oam_path = oam_path,
    regs_path = regs_path,
    domains_path = domains_path,
    frame_path = frame_path
  }
end

local function handle_command(line)
  line = line:gsub("%s+$", "")
  if line == "" then return end

  local parts = split(line, "|")
  local id = parts[1]
  local cmd = parts[2]

  if not id or not cmd then
    return
  end

  log("cmd=" .. line)
  if cmd == "buttons" then
    local buttons, source = get_available_buttons()

    respond(id, "ok", {
        frame = emu.framecount(),
        buttons = dump_table(buttons),
        source = source
    })
    return
  end

  if cmd == "status" then
    respond(id, "ok", {
      frame = emu.framecount(),
      system = emu.getsystemid(),
      width = client.screenwidth(),
      height = client.screenheight()
    })
    return
  end

  if cmd == "bridge_info" then
    respond(id, "ok", {
      frame = emu.framecount(),
      system = emu.getsystemid(),
      bridge_version = BRIDGE_VERSION,
      capabilities = join_table(BRIDGE_CAPABILITIES, ",")
    })
    return
  end

  if cmd == "read_memory" then
    local address = tonumber(parts[3])
    local length = tonumber(parts[4]) or 1
    local domain = parts[5] or "System Bus"
    local domains = memory_domains()

    if not address or math.floor(address) ~= address or address < 0 or address > 0xFFFFFFFF then
      respond(id, "error", { error = "invalid_memory_address" })
      return
    end
    if math.floor(length) ~= length or length < 1 or length > 0x10000 then
      respond(id, "error", { error = "invalid_memory_length", max_length = 0x10000 })
      return
    end
    if domain == "" or string.find(domain, "|", 1, true) then
      respond(id, "error", { error = "invalid_memory_domain" })
      return
    end
    if not find_domain_exact(domain, domains) then
      respond(id, "error", {
        error = "unknown_memory_domain",
        domain = domain,
        available_domains = join_table(domains, ",")
      })
      return
    end

    local bytes, read_err = read_domain_binary(address, length, domain)
    if not bytes then
      respond(id, "error", {
        error = "memory_read_failed",
        detail = read_err or "unknown_memory_read_error",
        address = address,
        length = length,
        domain = domain
      })
      return
    end

    respond(id, "ok", {
      frame = emu.framecount(),
      address = address,
      length = length,
      domain = domain,
      hex = bytes_to_hex(bytes)
    })
    return
  end

  if cmd == "reset" then
    local settle_frames = tonumber(parts[3]) or 60
    local ok, err = reset_core(settle_frames)

    if ok then
      respond(id, "ok", {
        frame = emu.framecount(),
        system = emu.getsystemid(),
        mode = "reset",
        settle_frames = settle_frames
      })
    else
      respond(id, "error", {
        error = err or "reset_failed",
        frame = emu.framecount(),
        system = emu.getsystemid()
      })
    end
    return
  end

  if cmd == "gb_gpu_snapshot" then
    if TARGET ~= "gb" then
      respond(id, "error", { error = "gb_gpu_snapshot_requires_gb", system = emu.getsystemid() })
      return
    end
    local snapshot_dir = parts[3]
    local ok, err, fields = gb_gpu_snapshot(snapshot_dir)

    if ok then
      respond(id, "ok", fields)
    else
      fields = fields or {}
      fields.error = err or "gb_gpu_snapshot_failed"
      fields.frame = emu.framecount()
      fields.system = emu.getsystemid()
      fields.snapshot_dir = snapshot_dir or ""
      respond(id, "error", fields)
    end
    return
  end

  if cmd == "step" then
    local frames = tonumber(parts[3]) or 1
    local ok, err = step_frames(frames)

    if ok then
        respond(id, "ok", {
        frame = emu.framecount(),
        mode = "step",
        frames = frames
        })
    else
        respond(id, "error", {
        error = err or "step_failed",
        frame = emu.framecount()
        })
    end

    return
  end

  if cmd == "tap" or cmd == "press" then
    local button = parts[3]
    local hold_frames = tonumber(parts[4]) or 8
    local release_frames_count = tonumber(parts[5]) or 2
    local ok, err = tap_button(button, hold_frames, release_frames_count)

    if ok then
      respond(id, "ok", {
        frame = emu.framecount(),
        mode = "tap",
        button = button,
        hold_frames = hold_frames,
        release_frames = release_frames_count
      })
    else
      respond(id, "error", {
        error = err or "tap_failed",
        button = button or ""
      })
    end
    return
  end

  if cmd == "hold" then
    local button = parts[3]
    local frames = tonumber(parts[4]) or 1
    local ok, err = hold_button(button, frames)

    if ok then
      respond(id, "ok", {
        frame = emu.framecount(),
        mode = "hold",
        button = button,
        frames = frames
      })
    else
      respond(id, "error", {
        error = err or "hold_failed",
        button = button or ""
      })
    end
    return
  end

  if cmd == "release" then
    local frames = tonumber(parts[3]) or 2
    local ok, err = release_frames(frames)

    if ok then
      respond(id, "ok", {
        frame = emu.framecount(),
        mode = "release",
        frames = frames
      })
    else
      respond(id, "error", {
        error = err or "release_failed",
        frame = emu.framecount()
      })
    end
    return
  end

  if cmd == "screenshot" then
    local path = parts[3]
    if not path or path == "" then
      respond(id, "error", { error = "missing_path" })
      return
    end

    client.screenshot(path)
    respond(id, "ok", {
      frame = emu.framecount(),
      path = path
    })
    return
  end

  if cmd == "save_state" then
    local path = parts[3]
    if not path or path == "" then
      respond(id, "error", { error = "missing_path" })
      return
    end

    log("save_state path=" .. tostring(path))
    savestate.save(path, true)
    respond(id, "ok", {
      frame = emu.framecount(),
      path = path
    })
    return
  end

  if cmd == "load_state" then
    local path = parts[3]
    if not path or path == "" then
      respond(id, "error", { error = "missing_path" })
      return
    end

    log("load_state path=" .. tostring(path))
    savestate.load(path, true)
    respond(id, "ok", {
      frame = emu.framecount(),
      path = path
    })
    return
  end

  respond(id, "error", { error = "unknown_command", cmd = cmd })
end

log("bridge started")
client.pause()

while true do
  local cmd = read_all(CMD_FILE)
  if cmd and cmd ~= "" then
    os.remove(CMD_FILE)
    handle_command(cmd)
  end

  -- No avanza el juego si no hay comandos.
  emu.yield()
end
