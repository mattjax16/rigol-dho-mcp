"""Offline scope behavior; no physical instrument is used."""
import asyncio
import unittest
from unittest.mock import Mock, patch

from rigol_dho_mcp import server
from rigol_dho_mcp.scpi import (
    SCOPE_UNAVAILABLE_MESSAGE, ScpiClient, ScpiError, ScopeUnavailable,
)


class AvailabilityTests(unittest.TestCase):
    def test_connection_failure_is_unavailable_and_can_recover(self):
        client = ScpiClient("192.0.2.1")
        sock = Mock()
        sock.recv.return_value = b"RIGOL,DHO814,serial,firmware\n"
        with patch("socket.create_connection", side_effect=[TimeoutError(), sock]):
            with self.assertRaises(ScopeUnavailable) as error:
                client.query("*IDN?")
            self.assertNotIn("192.0.2.1", str(error.exception))
            self.assertIsNone(client._sock)
            self.assertEqual(client.query("*IDN?"), "RIGOL,DHO814,serial,firmware")

    def test_lost_connections_are_reset(self):
        for failure in [b"", ConnectionResetError(), TimeoutError()]:
            with self.subTest(failure=failure):
                client = ScpiClient("192.0.2.1")
                sock = Mock()
                if isinstance(failure, Exception):
                    sock.recv.side_effect = failure
                else:
                    sock.recv.return_value = failure
                client._sock = sock
                with self.assertRaises(ScopeUnavailable):
                    client.query("*IDN?")
                self.assertIsNone(client._sock)
                sock.close.assert_called_once()

    def test_offline_tools_return_normal_results(self):
        client = Mock()
        client.query.side_effect = ScopeUnavailable(SCOPE_UNAVAILABLE_MESSAGE)
        with patch.object(server, "scope", return_value=client):
            self.assertIn("powered off", server.identify())
            for result in [server.get_connection_status(), server.get_status()]:
                self.assertEqual(result["server_status"], "ok")
                self.assertEqual(result["scope_status"], "unavailable")
                self.assertEqual(result["power_state"], "unknown")

    def test_mcp_discovery_and_calls_work_while_offline(self):
        client = Mock()
        client.query.side_effect = ScopeUnavailable(SCOPE_UNAVAILABLE_MESSAGE)

        async def check():
            tools = await server.mcp.list_tools()
            self.assertIn("get_connection_status", {tool.name for tool in tools})
            for name in ["identify", "get_status", "get_connection_status"]:
                result = await server.mcp.call_tool(name, {})
                self.assertIn("unavailable", str(result))

        with patch.object(server, "scope", return_value=client):
            asyncio.run(check())

    def test_online_identity_is_unchanged(self):
        client = Mock()
        client.query.return_value = "RIGOL,DHO814,serial,firmware"
        with patch.object(server, "scope", return_value=client):
            self.assertEqual(server.identify(), client.query.return_value)
            self.assertEqual(server.get_connection_status()["scope_status"], "online")

    def test_other_errors_are_not_reported_as_power_off(self):
        client = Mock()
        client.query.side_effect = ScpiError("Malformed response")
        with patch.object(server, "scope", return_value=client):
            with self.assertRaises(ScpiError):
                server.get_connection_status()

    def test_failed_control_is_not_reported_as_success(self):
        client = Mock()
        client.write.side_effect = ScopeUnavailable(SCOPE_UNAVAILABLE_MESSAGE)
        with patch.object(server, "scope", return_value=client):
            with self.assertRaises(ScopeUnavailable):
                server.run_control("run")


if __name__ == "__main__":
    unittest.main()
