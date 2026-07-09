"""Local CLI for testing SCPI commands against a Rigol DHO800/900 scope
without going through an MCP client.

This talks directly to ScpiClient (the same client server.py uses), so it's
useful for verifying a command works, checking exact response formatting, or
debugging the connection before wiring something up as an MCP tool.

Usage:
    # One-shot: send one or more commands and exit
    rigol-dho-cli --host 192.168.1.50 "*IDN?"
    rigol-dho-cli ":CHANnel1:SCALe?" ":CHANnel1:SCALe 0.5"

    # Interactive REPL (omit commands)
    rigol-dho-cli --host 192.168.1.50
    scpi> *IDN?
    RIGOL TECHNOLOGIES,DHO814,...
    scpi> :RUN
    OK
    scpi> :DISPlay:DATA? PNG
    binary response (34521 bytes) -> saved to shot_00001.png

Connection settings can also come from the same env vars server.py uses:
RIGOL_HOST, RIGOL_PORT, RIGOL_TIMEOUT — so `.env` / whatever you already use
for the MCP server works here unchanged.

Binary handling: any command whose response doesn't look like plain ASCII
(currently: known binary-producing queries, or a response that fails to
decode/looks like a TMC block) is written to a file in the current directory
instead of being dumped to the terminal.
"""

from __future__ import annotations

import argparse
import os
import sys

from .scpi import ScpiClient, ScpiError

# Commands whose response is a binary TMC block, not ASCII text.
_BINARY_HINTS = (":DISP:DATA", ":DISPLAY:DATA", ":WAV:DATA", ":WAVEFORM:DATA")


def _looks_binary(command: str) -> bool:
    upper = command.strip().upper()
    return any(hint in upper for hint in _BINARY_HINTS)


def _save_binary(payload: bytes, prefix: str = "capture") -> str:
    ext = "png" if payload[:8] == b"\x89PNG\r\n\x1a\n" else "bin"
    i = 1
    while True:
        path = f"{prefix}_{i:05d}.{ext}"
        if not os.path.exists(path):
            break
        i += 1
    with open(path, "wb") as f:
        f.write(payload)
    return path


def _run_one(client: ScpiClient, command: str) -> str:
    """Send a single command, returning a human-readable result string."""
    command = command.strip()
    if not command:
        return ""
    is_query = command.endswith("?")
    if _looks_binary(command):
        payload = client.query_binary(command, timeout=20.0)
        path = _save_binary(payload)
        return f"binary response ({len(payload)} bytes) -> saved to {path}"
    if is_query:
        return client.query(command)
    client.write(command)
    err = client.query(":SYSTem:ERRor?")
    return f"OK (system error queue: {err})"


def _build_client(args: argparse.Namespace) -> ScpiClient:
    host = args.host or os.environ.get("RIGOL_HOST", "")
    if not host:
        raise SystemExit(
            "No host given. Pass --host 192.168.1.x or set RIGOL_HOST."
        )
    port = args.port or int(os.environ.get("RIGOL_PORT", "5555"))
    timeout = args.timeout or float(os.environ.get("RIGOL_TIMEOUT", "10"))
    return ScpiClient(host, port, timeout)


def _repl(client: ScpiClient) -> None:
    print(f"Connected to {client.host}:{client.port}. Ctrl-D or 'quit' to exit.")
    try:
        while True:
            try:
                command = input("scpi> ")
            except EOFError:
                print()
                break
            if command.strip().lower() in ("quit", "exit"):
                break
            if not command.strip():
                continue
            try:
                result = _run_one(client, command)
            except ScpiError as e:
                print(f"error: {e}", file=sys.stderr)
                continue
            if result:
                print(result)
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="rigol-dho-cli",
        description="Send raw SCPI commands to a Rigol DHO800/900 scope for local testing.",
    )
    parser.add_argument("--host", help="Scope IP/hostname (default: RIGOL_HOST env var)")
    parser.add_argument("--port", type=int, help="SCPI port (default: RIGOL_PORT env var or 5555)")
    parser.add_argument("--timeout", type=float, help="I/O timeout in seconds (default: RIGOL_TIMEOUT env var or 10)")
    parser.add_argument(
        "commands",
        nargs="*",
        help="One or more SCPI commands to run and exit. Omit to start an interactive REPL.",
    )
    args = parser.parse_args()

    client = _build_client(args)

    if args.commands:
        try:
            for command in args.commands:
                try:
                    result = _run_one(client, command)
                except ScpiError as e:
                    print(f"error running '{command}': {e}", file=sys.stderr)
                    sys.exit(1)
                print(f"{command}\n  -> {result}" if result else command)
        finally:
            client.close()
        return

    _repl(client)


if __name__ == "__main__":
    main()
