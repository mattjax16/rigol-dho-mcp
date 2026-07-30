# Rigol DHO800/DHO900 MCP Server

An MCP (Model Context Protocol) server for controlling and reading Rigol DHO800/DHO900 series oscilloscopes over LAN, built on the SCPI command set from the official programming guide. It talks directly to the scope's raw SCPI socket on port 5555, so there's no VISA install to deal with.

> **Note:** This server is currently pinned to `mcp<2.0.0`. The official MCP Python SDK's v2.0 release renames `FastMCP` to `MCPServer` and moves it out of `mcp.server.fastmcp`, which breaks this server's imports as written. The pin in `pyproject.toml` keeps deploys working until `server.py` is migrated to the v2 API.

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

This exposes the MCP endpoint at `http://<docker-host>:8000/mcp` (streamable HTTP). 

#### Using Docker Compose

Alternatively, you can use `docker-compose.yml`:

```bash
# Copy the example environment file and edit it with your scope's IP address:
cp .env.example .env
# Edit .env to set your scope's IP address under RIGOL_HOST

# Then start the service:
docker compose up -d

# View logs:
docker compose logs -f

# Stop the service:
docker compose down
```

### stdio inside Docker

```bash
docker run -i --rm \
  -e RIGOL_HOST=192.168.1.100 \
  -e MCP_TRANSPORT=stdio \
  rigol-dho-mcp
```

> The container needs to be able to reach the scope's IP. On Linux the default bridge network usually works fine; if your scope only sits on the host's LAN segment and bridge routing doesn't reach it, add `--network host`. For docker-compose, you can uncomment the `network_mode: "host"` line in `docker-compose.yml`.

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

## Security & Network Configuration

### HTTP Endpoint Access Restrictions

When running with **HTTP transport** (Docker or `MCP_TRANSPORT=streamable-http`), the MCP server exposes an endpoint at `http://<host>:8000/mcp`.

> ⚠️ **Important:** This endpoint has **no authentication mechanism**. Anyone who can reach this port can control your oscilloscope and read waveform data/screenshots.

Because of that, `compose.yml` publishes the port on **loopback only** (`MCP_BIND_ADDRESS=127.0.0.1`) by default. The intended deployment is behind a reverse proxy that adds authentication.

- To expose it on your LAN anyway, set `MCP_BIND_ADDRESS=0.0.0.0` in `.env` — and be aware that this makes the scope controllable by anyone who can reach the port.
- To put it behind Traefik, uncomment the labels block in `compose.yml` and drop the `ports:` block.

> ⚠️ **Authentik/SSO note:** this is a pure-API service. MCP clients can't complete an interactive browser login, so forward-auth SSO middleware (`authentik_domain@file`) will break every client. Use a non-interactive credential — a `basicauth` or bearer-token middleware — instead.

**CORS is not access control.** `MCP_ALLOWED_ORIGINS` constrains *browsers* only; `curl`, a script, or any non-browser MCP client is unaffected by it regardless of how it's set.

### DNS Rebinding Protection

The HTTP transport includes DNS rebinding protection by default (`MCP_ENABLE_DNS_REBINDING_PROTECTION=1`). This validates `Origin` and `Host` headers on incoming requests to prevent malicious websites from accessing your MCP server through a browser. It is a useful control, but it is **not** authentication — a direct client that sets an allowed `Host` header passes it trivially.

Both allowlists default to **empty**, which rejects everything:

- **Allowed Hosts:** set `MCP_ALLOWED_HOSTS` to the exact `host:port` you connect to (e.g. `localhost:8000`, `scope.home.lab:8000`). **With protection enabled and this unset, every request is rejected with `421 Misdirected Request`** — if the server appears to reject all traffic, this is why.
- **Allowed Origins:** set `MCP_ALLOWED_ORIGINS` only if a browser-based client needs access (e.g. `http://localhost:6274` for MCP Inspector).

The container healthcheck endpoint `/health` is intentionally exempt from these checks and reports HTTP liveness only — it doesn't probe the scope and returns no information about it.

> ⚠️ **Warning:** Setting `MCP_ENABLE_DNS_REBINDING_PROTECTION=0` or using `MCP_ALLOWED_ORIGINS=*` disables this protection entirely and should only be done on trusted, isolated networks for local testing.

### SCPI Input Handling

Every tool parameter that gets interpolated into a SCPI command is an enumerated type or a bounded number, and the SCPI client rejects any command containing control characters or non-ASCII. This is what keeps `RIGOL_ENABLE_SCPI_RAW=0` meaningful: SCPI is newline-delimited, so without both checks an embedded newline in a parameter would reach the scope as a second, arbitrary command.

If you add a tool, **do not** interpolate a free-form `str` into a command string — give the parameter a `Literal` type.

### Raw SCPI Escape Hatch Risks

The `scpi_command` tool is **opt-in** via the `RIGOL_ENABLE_SCPI_RAW=1` environment variable. When enabled, it accepts arbitrary SCPI commands from the MCP client.

> ⚠️ **Warning:** Arbitrary SCPI can leave the scope in any state or perform destructive actions (e.g., `*RST` to reset the scope, changing critical settings). Ensure your MCP client has appropriate access controls and that you understand the risks before enabling this feature.

| Variable | Default | Meaning |
|---|---|---|
| `RIGOL_HOST` | — (required) | Scope IP address or hostname |
| `RIGOL_PORT` | `5555` | SCPI socket port |
| `RIGOL_TIMEOUT` | `10` | I/O timeout, seconds |
| `MCP_TRANSPORT` | `stdio` (local) / `streamable-http` (Docker) | Transport |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | HTTP bind address/port *inside* the container |
| `MCP_BIND_ADDRESS` | `127.0.0.1` | Host interface compose publishes the port on. `0.0.0.0` exposes the unauthenticated endpoint to the LAN |
| `MCP_ENABLE_DNS_REBINDING_PROTECTION` | `1` | Validate `Origin`/`Host` headers on the HTTP transport |
| `MCP_ALLOWED_HOSTS` | — (empty) | Allowed `Host` values. **Required** when protection is on, or all requests get `421` |
| `MCP_ALLOWED_ORIGINS` | — (empty) | Allowed browser origins; also configures CORS |
| `MCP_MEM_LIMIT` | `2g` | Container memory ceiling (deep-memory reads need ~2 GB) |
| `RIGOL_ENABLE_SCPI_RAW` | `0` | Set to `1` to expose `scpi_command` (off by default) |

## Notes

- Deep-memory reads (`get_waveform` with `mode="memory"`) need the scope in the STOP state, so call `run_control("stop")` first. Data comes back in chunks and gets decimated to `max_points` before returning.
- Waveform samples are scaled to volts using the preamble: `V = (raw − YORigin − YREFerence) × YINCrement`.
- A measurement value near `9.9e37` just means it's invalid for the current signal. `get_measurement` flags this for you.
- `scpi_command` is opt-in (set `RIGOL_ENABLE_SCPI_RAW=1`). It checks `:SYSTem:ERRor?` after write-only commands, so a typo in raw SCPI shows up right away instead of failing silently.

## Known issue: pending MCP v2 migration

The official MCP Python SDK's v2.0 release (stable as of late July 2026) replaces `FastMCP` with `MCPServer` and relocates it out of `mcp.server.fastmcp`. `server.py` still imports the old path (`from mcp.server.fastmcp import FastMCP, Image`), so an unpinned install pulls v2 and crashes on startup with `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. The `mcp<2.0.0` pin in `pyproject.toml` avoids this for now. Migrating `server.py` to the v2 API is planned but not yet done.
