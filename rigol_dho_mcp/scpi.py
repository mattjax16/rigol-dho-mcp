"""Minimal SCPI-over-TCP client for Rigol DHO800/DHO900 oscilloscopes.

The scope exposes a raw SCPI socket on port 5555 (LAN). Commands are
newline-terminated ASCII. Binary responses (screenshots, waveform data)
use the IEEE-488.2 / TMC definite-length block format:

    #<N><LLLL...><payload>\n

where N is the number of ASCII digits that follow, and those digits give
the payload length in bytes.
"""

from __future__ import annotations

import socket
import threading


class ScpiError(Exception):
    pass


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
            raise ScpiError(
                f"Could not connect to {self.host}:{self.port} — {e}. "
                "Check that the scope is on the network and LAN control is enabled."
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

    def _reset_and_raise(self, msg: str, cause: Exception | None = None):
        self.close()
        raise ScpiError(msg) from cause

    # -- low-level I/O -----------------------------------------------------

    def _send(self, command: str) -> None:
        sock = self._ensure()
        try:
            sock.sendall(command.strip().encode("ascii") + b"\n")
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
                ndigits = int(self._read_exact(1))
                length = int(self._read_exact(ndigits))
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
