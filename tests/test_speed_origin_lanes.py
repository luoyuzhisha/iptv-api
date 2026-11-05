import asyncio
import unittest
from collections import defaultdict
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from utils.speed import resolve_origin


@asynccontextmanager
async def speed_session(_concurrency):
    yield object()


def speed_test_config(concurrency=4, timeout=1):
    return SimpleNamespace(
        open_ipv6=False,
        speed_test_mode="full",
        open_full_speed_test=True,
        open_filter_resolution=False,
        open_stream_screenshot=False,
        performance_settings=SimpleNamespace(
            speed_test_concurrency=concurrency,
            probe_concurrency=1,
        ),
        speed_test_target=1,
        speed_test_timeout=timeout,
    )


class MemoryArtifactWriter:
    def __init__(self, *_args, **_kwargs):
        self.count = 0

    def write(self, _record):
        self.count += 1

    def close(self):
        pass


class RedirectFixture:
    def __init__(self):
        self.target_server = None
        self.entry_server = None
        self.target_port = None
        self.entry_port = None

    async def start(self):
        self.target_server = await asyncio.start_server(
            self._handle_target, "127.0.0.1", 0
        )
        self.target_port = self.target_server.sockets[0].getsockname()[1]
        self.entry_server = await asyncio.start_server(
            self._handle_entry, "127.0.0.1", 0
        )
        self.entry_port = self.entry_server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        for server in (self.entry_server, self.target_server):
            if server:
                server.close()
                await server.wait_closed()

    def url(self):
        return f"http://127.0.0.1:{self.entry_port}/live.m3u8"

    async def _handle_entry(self, reader, writer):
        await self._read_request(reader)
        response = (
            "HTTP/1.1 302 Found\r\n"
            f"Location: http://127.0.0.1:{self.target_port}/media.m3u8\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(response.encode())
        await writer.drain()
        writer.close()

    async def _handle_target(self, reader, writer):
        await self._read_request(reader)
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/vnd.apple.mpegurl\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(response.encode())
        await writer.drain()
        writer.close()

    @staticmethod
    async def _read_request(reader):
        await reader.readline()
        while await reader.readline() not in {b"\r\n", b"\n", b""}:
            pass


class ResolveOriginTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_the_origin_that_serves_the_redirect_target(self):
        fixture = await RedirectFixture().start()
        try:
            origin = await resolve_origin(fixture.url(), timeout=1.0)
        finally:
            await fixture.stop()

        self.assertEqual(origin, f"127.0.0.1:{fixture.target_port}")

    async def test_unreachable_entry_keeps_its_own_origin(self):
        origin = await resolve_origin("http://127.0.0.1:9/live.m3u8", timeout=0.2)

        self.assertEqual(origin, "127.0.0.1:9")


class OriginLaneTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_origin_candidates_never_run_concurrently(self):
        from utils.channel import test_speed

        url_origins = {
            "http://a.test/1": "media.test:80",
            "http://a.test/2": "media.test:80",
            "http://a.test/3": "media.test:80",
            "http://b.test/1": "other.test:80",
        }
        active = defaultdict(int)
        peak = defaultdict(int)
        measured = []

        async def fake_resolve(url, **_kwargs):
            return url_origins[url]

        async def fake_get_speed(info, **_kwargs):
            origin = url_origins[info["url"]]
            measured.append(origin)
            active[origin] += 1
            peak[origin] = max(peak[origin], active[origin])
            await asyncio.sleep(0.05)
            active[origin] -= 1
            return {"speed": 0, "delay": -1, "resolution": None}

        channel_data = {
            "Test": {
                "Channel": [
                    {
                        "url": url,
                        "host": url.split("/")[2],
                        "resolution": None,
                        "ipv_type": "ipv4",
                        "origin": "subscribe",
                    }
                    for url in url_origins
                ]
            }
        }

        with (
            patch("utils.channel.config", speed_test_config()),
            patch("utils.channel.ArtifactWriter", MemoryArtifactWriter),
            patch("utils.channel.create_speed_test_session", speed_session),
            patch("utils.channel.resolve_origin", fake_resolve),
            patch("utils.channel.get_speed", fake_get_speed),
            patch("utils.channel.mark_url_bad"),
            patch("utils.channel.mark_url_good"),
        ):
            result = await test_speed(channel_data)

        self.assertEqual(peak["media.test:80"], 1)
        self.assertEqual(len(measured), len(url_origins))
        self.assertEqual(len(result["Test"]["Channel"]), len(url_origins))

    async def test_candidates_without_a_shared_host_skip_origin_resolution(self):
        from utils.channel import test_speed

        resolved = []

        async def fake_resolve(url, **_kwargs):
            resolved.append(url)
            return "media.test:80"

        async def fake_get_speed(_info, **_kwargs):
            return {"speed": 0, "delay": -1, "resolution": None}

        channel_data = {
            "Test": {
                "Channel": [
                    {
                        "url": "http://a.test/1",
                        "host": "a.test",
                        "resolution": None,
                        "ipv_type": "ipv4",
                        "origin": "subscribe",
                    }
                ]
            }
        }

        with (
            patch("utils.channel.config", speed_test_config()),
            patch("utils.channel.ArtifactWriter", MemoryArtifactWriter),
            patch("utils.channel.create_speed_test_session", speed_session),
            patch("utils.channel.resolve_origin", fake_resolve),
            patch("utils.channel.get_speed", fake_get_speed),
            patch("utils.channel.mark_url_bad"),
            patch("utils.channel.mark_url_good"),
        ):
            await test_speed(channel_data)

        self.assertEqual(resolved, [])


if __name__ == "__main__":
    unittest.main()
