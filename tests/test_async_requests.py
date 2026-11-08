import socket
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from utils.requests.async_tools import check_ipv6_support_async, fetch_first


class AsyncRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_fetch_uses_configured_proxy_explicitly(self):
        response = AsyncMock()
        response.__aenter__.return_value = response
        response.raise_for_status = Mock()
        response.text.return_value = "Demo,http://example.com/live"
        session = Mock()
        session.get.return_value = response

        with patch(
            "utils.requests.async_tools.config",
            SimpleNamespace(
                http_proxy="http://proxy.example.com:7890",
                request_timeout=10,
            ),
        ), patch("utils.requests.async_tools.asyncio.sleep", new=AsyncMock()):
            await fetch_first(
                session,
                ["https://example.com/list.txt"],
                name="https://example.com/list.txt",
            )

        self.assertEqual(
            session.get.call_args.kwargs["proxy"],
            "http://proxy.example.com:7890",
        )

    async def test_ipv6_check_probes_ipv6_family_without_http_or_proxy(self):
        calls = []

        async def fake_open_connection(host, port, family=None):
            calls.append((host, port, family))
            writer = Mock()
            writer.wait_closed = AsyncMock()
            return Mock(), writer

        with patch(
            "utils.requests.async_tools.asyncio.open_connection",
            new=fake_open_connection,
        ):
            self.assertTrue(await check_ipv6_support_async())

        self.assertTrue(calls)
        self.assertTrue(all(family == socket.AF_INET6 for _, _, family in calls))

    async def test_ipv6_check_returns_false_when_every_probe_fails(self):
        async def fake_open_connection(host, port, family=None):
            raise OSError("network is unreachable")

        with patch(
            "utils.requests.async_tools.asyncio.open_connection",
            new=fake_open_connection,
        ):
            self.assertFalse(await check_ipv6_support_async())

    async def test_retry_failure_preserves_root_cause_in_message(self):
        session = Mock()
        session.get.side_effect = TimeoutError("TLS handshake timed out")

        with patch("utils.requests.async_tools.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(Exception) as raised:
                await fetch_first(
                    session,
                    ["https://example.com/list.m3u"],
                    name="https://example.com/list.m3u",
                )

        self.assertIn("TimeoutError", str(raised.exception))
        self.assertIn("TLS handshake timed out", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)


if __name__ == "__main__":
    unittest.main()
