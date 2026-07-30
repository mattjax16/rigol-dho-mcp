"""Minimal SCPI-over-TCP client for Rigol DHO800/DHO900 oscilloscopes.

The scope exposes a raw SCPI socket on port 5555 (LAN). Commands are
newline-terminated ASCII. Binary responses (screenshots, waveform data)
use the IEEE-488.2 / TMC definite-length block format:

    #<N><LLLL...><payload>\n

where N is the number of ASCII digits that follow, and those digits give
the payload length in bytes.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import NoReturn

logger = logging.getLogger(__name__)

# Caps on anything sized by the remote endpoint. A real DHO800/900 never
# approaches either, but the TMC header and the line reader are both driven by
# bytes the instrument chooses, so a malfunctioning or spoofed endpoint could
# otherwise dictate an arbitrarily large allocation.
_MAX_LINE = 1 << 20  # 1 MiB; ASCII SCPI responses are a few hundred bytes
_MAX_BLOCK = 64 << 20  # 64 MiB; well above a full 50M-point deep-memory chunk


class ScpiError(Exception):
    pass


def _validate_command(command: str) -> str:
    """Reject anything that can't be exactly one SCPI line.

    SCPI is newline-delimited, so an embedded newline in an interpolated
    parameter would reach the instrument as a *separate command* — turning any
    tool that formats user input into a command string into an arbitrary-SCPI
    escape hatch, bypassing the RIGOL_ENABLE_SCPI_RAW gate. Rejecting all
    control characters (rather than just \\r\\n) keeps this closed even if the
    instrument treats some other byte as a delimiter, and rejecting non-ASCII
    turns what would be an unhandled UnicodeEncodeError into a normal ScpiError.

    Validation happens before any socket work, so a rejected command leaves the
    connection untouched and still in sync.
    """
    command = command.strip()
    if not command.isascii():
        raise ScpiError(f"SCPI commands must be ASCII: {command!r}")
    bad = sorted({c for c in command if ord(c) < 0x20 or ord(c) == 0x7F})
    if bad:
        raise ScpiError(
            f"Refusing to send SCPI containing control characters {bad!r} — "
            f"an embedded newline would be sent as a separate command: {command!r}"
        )
    return command


class ScpiClient:
    """Thread-safe SCPI client over a raw TCP socket."""

    def __init__(self, host: str, port: int = 5555, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    # -- connection management -------------------------------------------

    def connect(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            sock.settimeout(self.timeout)
            self._sock = sock
        except OSError as e:
            # The address goes to the log rather than the exception: the error
            # text reaches the MCP client, and on the unauthenticated HTTP
            # transport that would hand an unauthenticated caller the scope's
            # internal IP. Operators still get the full detail in the logs.
            logger.error("Could not connect to scope at %s:%s — %s", self.host, self.port, e)
            raise ScpiError(
                "Could not connect to the oscilloscope (address and underlying "
                "error are in the server log). Check that RIGOL_HOST is correct, "
                "the scope is on the network, and LAN control is enabled."
            ) from e

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _ensure(self) -> socket.socket:
        if self._sock is None:
            self.connect()
        assert self._sock is not None
        return self._sock

    def _reset_and_raise(self, msg: str, cause: Exception | None = None) -> NoReturn:
        self.close()
        raise ScpiError(msg) from cause

    # -- low-level I/O -----------------------------------------------------

    def _send(self, command: str) -> None:
        command = _validate_command(command)
        sock = self._ensure()
        try:
            sock.sendall(command.encode("ascii") + b"\n")
        except OSError as e:
            self._reset_and_raise(f"Send failed for '{command}': {e}", e)

    def _read_line(self) -> bytes:
        """Read until newline."""
        sock = self._ensure()
        chunks = bytearray()
        try:
            while True:
                b = sock.recv(4096)
                if not b:
                    self._reset_and_raise("Connection closed by instrument.")
                chunks.extend(b)
                if chunks.endswith(b"\n"):
                    break
                if len(chunks) > _MAX_LINE:
                    self._reset_and_raise(
                        f"Response exceeded {_MAX_LINE} bytes with no line "
                        "terminator; abandoning the read."
                    )
        except socket.timeout as e:
            self._reset_and_raise("Timed out waiting for a response.", e)
        return bytes(chunks[:-1])

    def _read_exact(self, n: int) -> bytes:
        sock = self._ensure()
        buf = bytearray()
        try:
            while len(buf) < n:
                b = sock.recv(min(65536, n - len(buf)))
                if not b:
                    self._reset_and_raise("Connection closed mid-transfer.")
                buf.extend(b)
        except socket.timeout as e:
            self._reset_and_raise(
                f"Timed out after receiving {len(buf)}/{n} bytes.", e
            )
        return bytes(buf)

    # -- public API --------------------------------------------------------

    def write(self, command: str) -> None:
        """Send a command that produces no response."""
        with self._lock:
            self._send(command)

    def query(self, command: str) -> str:
        """Send a query and return the ASCII response line."""
        with self._lock:
            self._send(command)
            return self._read_line().decode("ascii", errors="replace").strip()

    def query_binary(self, command: str, timeout: float | None = None) -> bytes:
        """Send a query whose response is a TMC definite-length binary block.

        Returns only the payload (header and trailing terminator stripped).
        """
        with self._lock:
            sock = self._ensure()
            old_timeout = sock.gettimeout()
            if timeout is not None:
                sock.settimeout(timeout)
            try:
                self._send(command)
                head = self._read_exact(1)
                if head != b"#":
                    # Not a block — read rest of line and report it.
                    rest = head + self._read_line()
                    raise ScpiError(
                        f"Expected binary block for '{command}', got: "
                        f"{rest[:120].decode('ascii', errors='replace')!r}"
                    )
                # Both of these are instrument-supplied; a malformed header
                # would otherwise raise a bare ValueError past every caller's
                # ScpiError handling, and an oversized length would be an
                # allocation the remote end gets to choose.
                try:
                    ndigits = int(self._read_exact(1))
                    length = int(self._read_exact(ndigits))
                except ValueError as e:
                    self._reset_and_raise(
                        f"Malformed TMC block header in response to '{command}'.", e
                    )
                if length > _MAX_BLOCK:
                    self._reset_and_raise(
                        f"Refusing a {length}-byte block for '{command}' "
                        f"(limit {_MAX_BLOCK} bytes)."
                    )
                payload = self._read_exact(length)
                # Consume trailing terminator (usually a single \n).
                try:
                    sock.settimeout(0.5)
                    sock.recv(16)
                except OSError:
                    pass
                return payload
            finally:
                try:
                    sock.settimeout(old_timeout)
                except OSError:
                    pass
