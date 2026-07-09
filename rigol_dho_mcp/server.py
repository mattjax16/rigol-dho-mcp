"""MCP server for Rigol DHO800/DHO900 series oscilloscopes.

Exposes the scope's SCPI command set (per the DHO800/900 Programming
Guide) as MCP tools: run control, channel/timebase/trigger/acquisition
setup, automatic measurements, scaled waveform capture, screenshots,
and a raw SCPI escape hatch.

Configuration (environment variables):
    RIGOL_HOST      IP address or hostname of the scope (required)
    RIGOL_PORT      SCPI socket port (default 5555)
    RIGOL_TIMEOUT   I/O timeout in seconds (default 10)
    MCP_TRANSPORT   "stdio" (default) or "streamable-http"
    MCP_HOST        Bind address for HTTP transport (default 0.0.0.0)
    MCP_PORT        Port for HTTP transport (default 8000)
"""

from __future__ import annotations

import math
import os
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP, Image
from pydantic import Field

from .scpi import ScpiClient, ScpiError

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

RIGOL_HOST = os.environ.get("RIGOL_HOST", "")
RIGOL_PORT = int(os.environ.get("RIGOL_PORT", "5555"))
RIGOL_TIMEOUT = float(os.environ.get("RIGOL_TIMEOUT", "10"))

mcp = FastMCP(
    "rigol-dho800",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8000")),
)

_client: ScpiClient | None = None


def scope() -> ScpiClient:
    global _client
    if _client is None:
        if not RIGOL_HOST:
            raise ScpiError(
                "RIGOL_HOST is not set. Set it to the oscilloscope's IP address "
                "(shown on the scope under Utility > IO)."
            )
        _client = ScpiClient(RIGOL_HOST, RIGOL_PORT, RIGOL_TIMEOUT)
    return _client


Channel = Literal["CHAN1", "CHAN2", "CHAN3", "CHAN4"]

MEASUREMENT_ITEMS = [
    "VMAX", "VMIN", "VPP", "VTOP", "VBASe", "VAMP", "VAVG", "VRMS",
    "OVERshoot", "PREShoot", "MARea", "MPARea", "PERiod", "FREQuency",
    "RTIMe", "FTIMe", "PWIDth", "NWIDth", "PDUTy", "NDUTy", "TVMAX",
    "TVMIN", "PSLewrate", "NSLewrate", "VUPPer", "VMID", "VLOWer",
    "VARiance", "PVRMS", "PPULses", "NPULses", "PEDGes", "NEDGes",
]

# ---------------------------------------------------------------------------
# Identification & status
# ---------------------------------------------------------------------------


@mcp.tool()
def identify() -> str:
    """Query the instrument identity (*IDN?) to verify the connection.

    Returns manufacturer, model, serial number, and firmware version.
    Use this first to confirm the scope is reachable.
    """
    return scope().query("*IDN?")


@mcp.tool()
def get_status() -> dict:
    """Get an overview of the scope's current state: trigger status,
    sample rate, memory depth, timebase, and per-channel settings."""
    s = scope()
    status: dict = {
        "trigger_status": s.query(":TRIGger:STATus?"),
        "sample_rate_Sa_per_s": s.query(":ACQuire:SRATe?"),
        "memory_depth": s.query(":ACQuire:MDEPth?"),
        "acquisition_type": s.query(":ACQuire:TYPE?"),
        "timebase_scale_s_per_div": s.query(":TIMebase:MAIN:SCALe?"),
        "timebase_offset_s": s.query(":TIMebase:MAIN:OFFSet?"),
        "trigger": {
            "mode": s.query(":TRIGger:MODE?"),
            "sweep": s.query(":TRIGger:SWEep?"),
        },
        "channels": {},
    }
    for ch in (1, 2, 3, 4):
        try:
            enabled = s.query(f":CHANnel{ch}:DISPlay?")
        except ScpiError:
            break
        info = {"enabled": enabled.strip() == "1"}
        if info["enabled"]:
            info.update(
                scale_V_per_div=s.query(f":CHANnel{ch}:SCALe?"),
                offset_V=s.query(f":CHANnel{ch}:OFFSet?"),
                coupling=s.query(f":CHANnel{ch}:COUPling?"),
                probe_ratio=s.query(f":CHANnel{ch}:PROBe?"),
                bandwidth_limit=s.query(f":CHANnel{ch}:BWLimit?"),
            )
        status["channels"][f"CH{ch}"] = info
    return status


# ---------------------------------------------------------------------------
# Run control
# ---------------------------------------------------------------------------


@mcp.tool()
def run_control(
    action: Literal["run", "stop", "single", "autoset", "clear", "force_trigger"],
) -> str:
    """Control acquisition state.

    - run: start continuous acquisition (:RUN)
    - stop: stop acquisition (:STOP) — required before reading deep memory
    - single: arm a single-shot acquisition (:SINGle)
    - autoset: auto-configure vertical/horizontal/trigger for the applied signal
    - clear: clear all waveforms on screen (:CLEar)
    - force_trigger: force a trigger event (:TFORce)
    """
    cmd = {
        "run": ":RUN",
        "stop": ":STOP",
        "single": ":SINGle",
        "autoset": ":AUToset",
        "clear": ":CLEar",
        "force_trigger": ":TFORce",
    }[action]
    scope().write(cmd)
    return f"Sent {cmd}. Trigger status: {scope().query(':TRIGger:STATus?')}"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@mcp.tool()
def configure_channel(
    channel: Annotated[int, Field(ge=1, le=4, description="Channel number 1-4")],
    enabled: bool | None = None,
    scale: Annotated[float | None, Field(description="Vertical scale in V/div")] = None,
    offset: Annotated[float | None, Field(description="Vertical offset in V")] = None,
    coupling: Literal["AC", "DC", "GND"] | None = None,
    probe_ratio: Annotated[float | None, Field(description="Probe attenuation, e.g. 1, 10, 100")] = None,
    bandwidth_limit: Literal["OFF", "20M"] | None = None,
    invert: bool | None = None,
) -> dict:
    """Configure an analog channel. Only the parameters you pass are changed;
    the tool returns the channel's resulting settings."""
    s = scope()
    p = f":CHANnel{channel}"
    if enabled is not None:
        s.write(f"{p}:DISPlay {'ON' if enabled else 'OFF'}")
    if scale is not None:
        s.write(f"{p}:SCALe {scale}")
    if offset is not None:
        s.write(f"{p}:OFFSet {offset}")
    if coupling is not None:
        s.write(f"{p}:COUPling {coupling}")
    if probe_ratio is not None:
        s.write(f"{p}:PROBe {probe_ratio}")
    if bandwidth_limit is not None:
        s.write(f"{p}:BWLimit {bandwidth_limit}")
    if invert is not None:
        s.write(f"{p}:INVert {'ON' if invert else 'OFF'}")
    return {
        "channel": channel,
        "enabled": s.query(f"{p}:DISPlay?") == "1",
        "scale_V_per_div": s.query(f"{p}:SCALe?"),
        "offset_V": s.query(f"{p}:OFFSet?"),
        "coupling": s.query(f"{p}:COUPling?"),
        "probe_ratio": s.query(f"{p}:PROBe?"),
        "bandwidth_limit": s.query(f"{p}:BWLimit?"),
    }


@mcp.tool()
def configure_timebase(
    scale: Annotated[float | None, Field(description="Main timebase in s/div, e.g. 0.0002 for 200 µs/div")] = None,
    offset: Annotated[float | None, Field(description="Horizontal offset in seconds")] = None,
) -> dict:
    """Set the main horizontal timebase scale and/or offset."""
    s = scope()
    if scale is not None:
        s.write(":TIMebase:MODE MAIN")
        s.write(f":TIMebase:MAIN:SCALe {scale}")
    if offset is not None:
        s.write(f":TIMebase:MAIN:OFFSet {offset}")
    return {
        "scale_s_per_div": s.query(":TIMebase:MAIN:SCALe?"),
        "offset_s": s.query(":TIMebase:MAIN:OFFSet?"),
    }


@mcp.tool()
def configure_trigger_edge(
    source: Annotated[str | None, Field(description="Trigger source: CHAN1-CHAN4, EXT, ACL (AC line), or D0-D15 (DHO900)")] = None,
    slope: Literal["POSitive", "NEGative", "RFALl"] | None = None,
    level: Annotated[float | None, Field(description="Trigger level in volts")] = None,
    sweep: Literal["AUTO", "NORMal", "SINGle"] | None = None,
) -> dict:
    """Configure edge triggering (source, slope, level, sweep mode).
    Sets trigger mode to EDGE, then applies only the parameters given."""
    s = scope()
    s.write(":TRIGger:MODE EDGE")
    if source is not None:
        s.write(f":TRIGger:EDGE:SOURce {source}")
    if slope is not None:
        s.write(f":TRIGger:EDGE:SLOPe {slope}")
    if level is not None:
        s.write(f":TRIGger:EDGE:LEVel {level}")
    if sweep is not None:
        s.write(f":TRIGger:SWEep {sweep}")
    return {
        "mode": s.query(":TRIGger:MODE?"),
        "source": s.query(":TRIGger:EDGE:SOURce?"),
        "slope": s.query(":TRIGger:EDGE:SLOPe?"),
        "level_V": s.query(":TRIGger:EDGE:LEVel?"),
        "sweep": s.query(":TRIGger:SWEep?"),
        "status": s.query(":TRIGger:STATus?"),
    }


@mcp.tool()
def configure_acquisition(
    memory_depth: Annotated[str | None, Field(description="AUTO, 1k, 10k, 100k, 1M, 5M, 10M, 25M, or 50M")] = None,
    acq_type: Literal["NORMal", "AVERages", "PEAK", "ULTRa"] | None = None,
    averages: Annotated[int | None, Field(description="Average count (power of 2, 2-65536); only for AVERages mode")] = None,
) -> dict:
    """Set acquisition memory depth, mode, and average count."""
    s = scope()
    if memory_depth is not None:
        s.write(f":ACQuire:MDEPth {memory_depth}")
    if acq_type is not None:
        s.write(f":ACQuire:TYPE {acq_type}")
    if averages is not None:
        s.write(f":ACQuire:AVERages {averages}")
    return {
        "memory_depth": s.query(":ACQuire:MDEPth?"),
        "type": s.query(":ACQuire:TYPE?"),
        "averages": s.query(":ACQuire:AVERages?"),
        "sample_rate_Sa_per_s": s.query(":ACQuire:SRATe?"),
    }


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


@mcp.tool()
def get_measurement(
    item: Annotated[str, Field(description=f"Measurement item, one of: {', '.join(MEASUREMENT_ITEMS)}")],
    channel: Annotated[int, Field(ge=1, le=4)] = 1,
) -> dict:
    """Perform an automatic measurement on a channel and return its value.

    Common items: VPP (peak-to-peak), VAVG, VRMS, VMAX, VMIN, FREQuency,
    PERiod, RTIMe (rise time), FTIMe (fall time), PDUTy (duty cycle).
    Values are in SI units (V, s, Hz). A value near 9.9e37 means the
    measurement is invalid for the current signal.
    """
    s = scope()
    src = f"CHANnel{channel}"
    s.write(f":MEASure:ITEM {item},{src}")
    value = s.query(f":MEASure:ITEM? {item},{src}")
    try:
        v = float(value)
        invalid = not math.isfinite(v) or abs(v) > 9e37
    except ValueError:
        v, invalid = value, False
    return {"item": item, "channel": channel, "value": v, "invalid": invalid}


# ---------------------------------------------------------------------------
# Waveform capture
# ---------------------------------------------------------------------------

_RAW_CHUNK = 250_000  # points per :WAV:DATA? read in RAW mode


@mcp.tool()
def get_waveform(
    channel: Annotated[int, Field(ge=1, le=4)] = 1,
    mode: Annotated[
        Literal["screen", "memory"],
        Field(description="'screen' reads the ~1000 displayed points; 'memory' reads deep memory (scope must be STOPped)"),
    ] = "screen",
    max_points: Annotated[int, Field(ge=10, le=10000, description="Max points returned (data is decimated to fit)")] = 1000,
    include_data: Annotated[bool, Field(description="If false, return only statistics, no sample arrays")] = True,
) -> dict:
    """Capture waveform data from a channel, scaled to volts and seconds.

    Returns summary statistics (vmin/vmax/vpp/vavg/vrms) plus decimated
    time/voltage arrays. In 'memory' mode the scope must be stopped first
    (use run_control 'stop'); the full memory depth is read and decimated.
    """
    s = scope()
    s.write(f":WAVeform:SOURce CHANnel{channel}")
    s.write(f":WAVeform:MODE {'RAW' if mode == 'memory' else 'NORMal'}")
    s.write(":WAVeform:FORMat BYTE")

    # Preamble: format,type,points,count,xinc,xorig,xref,yinc,yorig,yref
    pre = s.query(":WAVeform:PREamble?").split(",")
    points = int(float(pre[2]))
    xinc, xorig = float(pre[4]), float(pre[5])
    yinc, yorig, yref = float(pre[7]), float(pre[8]), float(pre[9])

    raw = bytearray()
    if mode == "memory":
        start = 1
        while start <= points:
            stop = min(start + _RAW_CHUNK - 1, points)
            s.write(f":WAVeform:STARt {start}")
            s.write(f":WAVeform:STOP {stop}")
            raw.extend(s.query_binary(":WAVeform:DATA?", timeout=30.0))
            start = stop + 1
    else:
        raw.extend(s.query_binary(":WAVeform:DATA?", timeout=15.0))

    n = len(raw)
    if n == 0:
        return {"error": "No waveform data returned. Is the channel enabled and acquiring?"}

    # Scale: V = (raw - yorigin - yreference) * yincrement
    volts = [(b - yorig - yref) * yinc for b in raw]

    vmin, vmax = min(volts), max(volts)
    vavg = sum(volts) / n
    vrms = math.sqrt(sum(v * v for v in volts) / n)

    result: dict = {
        "channel": channel,
        "mode": mode,
        "points_captured": n,
        "dt_s": xinc,
        "t0_s": xorig,
        "sample_rate_Sa_per_s": (1.0 / xinc) if xinc else None,
        "stats": {
            "vmin_V": round(vmin, 6),
            "vmax_V": round(vmax, 6),
            "vpp_V": round(vmax - vmin, 6),
            "vavg_V": round(vavg, 6),
            "vrms_V": round(vrms, 6),
        },
    }

    if include_data:
        step = max(1, n // max_points)
        idx = range(0, n, step)
        result["decimation_factor"] = step
        result["points_returned"] = len(idx)
        result["time_s"] = [round(xorig + i * xinc, 12) for i in idx]
        result["voltage_V"] = [round(volts[i], 6) for i in idx]

    return result


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


@mcp.tool()
def get_screenshot() -> Image:
    """Capture the scope's current display as a PNG image.

    Useful for visually inspecting waveforms, menus, and measurement
    readouts exactly as shown on the instrument's screen.
    """
    data = scope().query_binary(":DISPlay:DATA? PNG", timeout=20.0)
    return Image(data=data, format="png")


# ---------------------------------------------------------------------------
# Raw SCPI escape hatch
# ---------------------------------------------------------------------------


@mcp.tool()
def scpi_command(
    command: Annotated[str, Field(description="Raw SCPI command, e.g. ':CHANnel1:SCALe 0.1' or ':ACQuire:SRATe?'")],
) -> str:
    """Send an arbitrary SCPI command from the DHO800/900 programming guide.

    Commands ending in '?' are treated as queries and their response is
    returned; others are write-only. Use for anything not covered by the
    dedicated tools (cursors, math, decoding, mask tests, DVM, etc.).
    """
    s = scope()
    if command.strip().endswith("?"):
        return s.query(command)
    s.write(command)
    # Surface any instrument error the command may have raised.
    err = s.query(":SYSTem:ERRor?")
    return f"OK (system error queue: {err})"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport not in ("stdio", "streamable-http", "sse"):
        raise SystemExit(f"Unknown MCP_TRANSPORT: {transport}")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
