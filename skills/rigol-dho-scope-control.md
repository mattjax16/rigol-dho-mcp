---
name: rigol-dho-scope-control
description: Control and read a Rigol DHO800/DHO900 oscilloscope through the rigol-dho-mcp server — identify the scope, run/stop/single acquisition, configure channels/timebase/trigger/acquisition, pull automatic measurements, cursors, waveform data, and screenshots. Use this whenever the user wants to take a measurement, capture a waveform, configure the scope, or debug a circuit using the Rigol DHO800/900 connected via rigol-dho-mcp — not just when they say "oscilloscope" explicitly.
license: MIT
compatibility: ">=0.8.0"
metadata:
  author: matt
  version: 1.0.0
allowed-tools:
  - identify
  - get_status
  - run_control
  - configure_channel
  - configure_timebase
  - configure_trigger_edge
  - configure_acquisition
  - get_measurement
  - get_waveform
  - get_screenshot
  - configure_cursors
  - get_cursor_values
  - measure_between
  - scpi_command
is_default: false
category: engineering
---

# Rigol DHO800/900 Scope Control (rigol-dho-mcp)

Drives a Rigol DHO800/DHO900 oscilloscope over LAN via `rigol-dho-mcp`, which talks directly to the scope's raw SCPI socket on port 5555 — no VISA layer involved.

## Tool reference

| Tool | Purpose |
|---|---|
| `identify` | `*IDN?` — verify connectivity, get model/serial/firmware |
| `get_status` | Trigger state, sample rate, memory depth, timebase, per-channel settings |
| `run_control` | `run` / `stop` / `single` / `autoset` / `clear` / `force_trigger` |
| `configure_channel` | Enable, V/div, offset, coupling (AC/DC/GND), probe ratio, BW limit, invert |
| `configure_timebase` | Main timebase scale and offset |
| `configure_trigger_edge` | Edge trigger source, slope, level, sweep mode |
| `configure_acquisition` | Memory depth, acquisition type (Normal/Average/Peak/Ultra), average count |
| `get_measurement` | Automatic measurements (VPP, VRMS, FREQuency, RTIMe, etc.) on any channel |
| `get_waveform` | Scaled voltage/time data, screen or deep memory, with stats |
| `get_screenshot` | PNG of the scope display |
| `configure_cursors` | Cursor mode (OFF/MANual/TRACk), type, source, positions |
| `get_cursor_values` | Cursor positions and delta/frequency readouts |
| `measure_between` | Delay or phase between two channels (RRDelay, FFPHase, etc.) |
| `scpi_command` | Raw SCPI escape hatch — **opt-in only**, see below |

## Standard workflow

1. **`identify`** first in any new session to confirm you're talking to the right instrument before touching settings.
2. **`get_status`** to see current channel/trigger/timebase state before changing anything — don't assume defaults.
3. Configure in this order when setting up a new capture: `configure_channel` → `configure_timebase` → `configure_trigger_edge` → `configure_acquisition`.
4. Use `run_control("single")` for one-shot captures on transient signals, `run_control("run")` for continuous, `run_control("autoset")` only when the user explicitly wants the scope to guess scale/trigger — it overwrites channel and trigger config.
5. Pull results with `get_measurement`, `get_waveform`, `get_cursor_values`, or `measure_between` as appropriate — don't call `get_waveform` in memory mode without stopping first (see below).

## Critical constraints

- **Deep-memory waveform reads require STOP state.** Before calling `get_waveform` with `mode="memory"`, call `run_control("stop")` first, or the read will fail or return garbage. Screen-mode reads don't have this restriction.
- **`scpi_command` is opt-in** (server only exposes it when `RIGOL_ENABLE_SCPI_RAW=1` is set on the deployment). Don't assume it's available — check `get_status` or attempt a benign query first, and fall back to the dedicated tools if it errors out. Reach for it only when a dedicated tool doesn't cover what's needed (e.g. an obscure SCPI command from the programming guide).
- **Invalid measurements read as `~9.9e37`.** `get_measurement` flags this, but if you're inspecting raw values yourself (e.g. via `scpi_command`), treat anything near that magnitude as "invalid for current signal," not a real reading.
- **Waveform scaling**: samples come back pre-scaled to volts (`V = (raw − YORigin − YREFerence) × YINCrement`) — don't re-apply scaling math on top of what `get_waveform` returns.
- **`configure_acquisition` with type=Average** changes how `get_measurement` and `get_waveform` behave (averaged vs. single-shot) — mention this to the user if they're comparing measurements across a session where acquisition type changed mid-way.

## Common request patterns

- *"What's the peak-to-peak voltage on channel 1?"* → `get_measurement` on CH1, VPP.
- *"Capture the waveform and show me a screenshot"* → `get_waveform` (screen mode is usually sufficient) + `get_screenshot`.
- *"Set up a trigger on the rising edge of channel 2 at 1.5V"* → `configure_trigger_edge` with source=CH2, slope=positive, level=1.5.
- *"Measure the phase difference between channel 1 and channel 3"* → `measure_between`.
- *"Grab the full memory depth waveform"* → `run_control("stop")` then `get_waveform` with `mode="memory"`.

## Safety / good practice

- Don't call `run_control("clear")` or `configure_acquisition` mid-capture without confirming the user is done with the current trace — both discard data.
- `configure_channel` changes (V/div, offset, coupling) invalidate in-flight trigger levels set relative to the old scale — re-check `configure_trigger_edge` levels after a channel rescale if precision matters.
- If `identify` fails or times out, the likely causes are: scope not on the same network segment as the MCP server/container, wrong `RIGOL_HOST`, or the scope's LAN interface disabled under `Utility > IO` — report the specific failure rather than retrying blindly.
