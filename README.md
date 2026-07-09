# Rigol DHO800/DHO900 MCP Server

An MCP (Model Context Protocol) server for controlling and reading Rigol DHO800/DHO900 series oscilloscopes over LAN, built on the SCPI command set from the official programming guide. It talks directly to the scope's raw SCPI socket on port 5555, so there's no VISA install to deal with.

## Tools

| Tool | Purpose |
|---|---|
| `identify` | `*IDN?`, for verifying connectivity and grabbing model/serial/firmware |
| `get_status` | Trigger state, sample rate, memory depth, timebase, per-channel settings |
| `run_control` | run / stop / single / autoset / clear / force_trigger |
| `configure_channel` | Enable, V/div, offset, coupling, probe ratio, BW limit, invert |
| `configure_timebase` | Main timebase scale and offset |
| `configure_trigger_edge` | Edge trigger source, slope, level, sweep mode |
| `configure_acquisition` | Memory depth, acquisition type, averages |
| `get_measurement` | Automatic measurements (VPP, VRMS, FREQuency, RTIMe, etc.) |
| `get_waveform` | Scaled voltage/time data from screen or deep memory, with stats |
| `get_screenshot` | PNG of the scope's display |
| `scpi_command` | Raw SCPI escape hatch for anything else in the guide |
| `configure_cursors` | Set cursor mode (OFF/MANual/TRACk), type, source, and positions |
| `get_cursor_values` | Read cursor positions and delta/frequency readouts |
| `measure_between` | Delay or phase between two channels (RRDelay, FFPHase, etc.) |

## Scope setup

1. Connect the scope to your LAN and grab its IP address under `Utility > IO` on the scope.
2. That's really it. The raw SCPI socket on port 5555 is open by default.

## Run locally (stdio)

```bash
pip install .
RIGOL_HOST=192.168.1.100 rigol-dho-mcp
```

This starts the server on stdio, ready for any MCP client to spawn and talk to it directly.

## Testing SCPI commands locally (CLI)

`rigol-dho-cli` talks straight to the scope over the same SCPI client the MCP server uses — no MCP client required. Handy for checking a command works, or debugging the connection before wiring it up as a tool.

```bash
pip install .

# one-shot: run one or more commands and print the result, then exit
RIGOL_HOST=192.168.1.100 rigol-dho-cli "*IDN?" ":CHANnel1:SCALe?"

# interactive REPL: omit the commands
RIGOL_HOST=192.168.1.100 rigol-dho-cli
scpi> *IDN?
RIGOL TECHNOLOGIES,DHO814,...
scpi> :RUN
OK (system error queue: 0,"No error")
scpi> :DISPlay:DATA? PNG
binary response (34521 bytes) -> saved to capture_00001.png
scpi> quit
```

It reads the same `RIGOL_HOST` / `RIGOL_PORT` / `RIGOL_TIMEOUT` env vars as the server (or pass `--host` / `--port` / `--timeout` directly). Queries (commands ending in `?`) print the response; writes are followed by a `:SYSTem:ERRor?` check so a typo shows up immediately. Binary responses (screenshots, waveform data) are saved to a file in the current directory instead of being dumped to the terminal.

## Run with Docker

### HTTP transport (recommended for containers)

```bash
docker build -t rigol-dho-mcp .
docker run -d --name rigol-dho-mcp \
  -p 8000:8000 \
  -e RIGOL_HOST=192.168.1.100 \
  rigol-dho-mcp
```

This exposes the MCP endpoint at `http://<docker-host>:8000/mcp` (streamable HTTP). You can also just edit the IP in `docker-compose.yml` and run `docker compose up -d`.

### stdio inside Docker

```bash
docker run -i --rm \
  -e RIGOL_HOST=192.168.1.100 \
  -e MCP_TRANSPORT=stdio \
  rigol-dho-mcp
```

> The container needs to be able to reach the scope's IP. On Linux the default bridge network usually works fine; if your scope only sits on the host's LAN segment and bridge routing doesn't reach it, add `--network host`.

## Using it with an MCP client

This is a standard MCP server, so any client that speaks MCP over stdio or streamable HTTP can use it. The config shape is basically the same everywhere: point the client at the `rigol-dho-mcp` command (stdio) or the running HTTP endpoint, and pass `RIGOL_HOST`.

**stdio:**

```json
{
  "mcpServers": {
    "rigol-dho800": {
      "command": "rigol-dho-mcp",
      "env": { "RIGOL_HOST": "192.168.1.100" }
    }
  }
}
```

**Streamable HTTP** (pointing at the Dockerized server from above):

```json
{
  "mcpServers": {
    "rigol-dho800": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

If your client doesn't support remote MCP servers natively, use `mcp-remote` as a bridge instead:

```json
{
  "mcpServers": {
    "rigol-dho800": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:8000/mcp"]
    }
  }
}
```

Check your client's docs for exactly where this config goes; the values themselves don't change.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `RIGOL_HOST` | — (required) | Scope IP address or hostname |
| `RIGOL_PORT` | `5555` | SCPI socket port |
| `RIGOL_TIMEOUT` | `10` | I/O timeout, seconds |
| `MCP_TRANSPORT` | `stdio` (local) / `streamable-http` (Docker) | Transport |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | HTTP bind address/port |
| `RIGOL_ENABLE_SCPI_RAW` | `0` | Set to `1` to expose `scpi_command` (off by default) |

## Notes

- Deep-memory reads (`get_waveform` with `mode="memory"`) need the scope in the STOP state, so call `run_control("stop")` first. Data comes back in chunks and gets decimated to `max_points` before returning.
- Waveform samples are scaled to volts using the preamble: `V = (raw − YORigin − YREFerence) × YINCrement`.
- A measurement value near `9.9e37` just means it's invalid for the current signal. `get_measurement` flags this for you.
- `scpi_command` is opt-in (set `RIGOL_ENABLE_SCPI_RAW=1`). It checks `:SYSTem:ERRor?` after write-only commands, so a typo in raw SCPI shows up right away instead of failing silently.
