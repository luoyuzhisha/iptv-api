import asyncio
import unittest
from contextlib import contextmanager
from unittest.mock import PropertyMock, patch

from utils.channel_quality import channel_result_rejection, is_channel_result_valid
from utils.config import config
from utils.speed import get_avg_result, get_live_edge_span, get_result


class HlsFixture:
    def __init__(
            self,
            *,
            segment_size=128 * 1024,
            segment_delay=0.0,
            segment_seconds=1.0,
            target_duration=1.0,
            advance=None,
            master_bandwidth=None,
            endlist=False,
    ):
        self.segment_size = segment_size
        self.segment_delay = segment_delay
        self.segment_seconds = segment_seconds
        self.target_duration = target_duration
        self.advance = advance
        self.master_bandwidth = master_bandwidth
        self.endlist = endlist
        self.sequence = 10
        self.server = None
        self.port = None
        self._advance_task = None

    @property
    def media_path(self) -> str:
        return "/master.m3u8" if self.master_bandwidth else "/media.m3u8"

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.media_path}"

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        if self.advance:
            self._advance_task = asyncio.create_task(self._advance_loop())
        return self

    async def stop(self):
        if self._advance_task:
            self._advance_task.cancel()
            try:
                await self._advance_task
            except asyncio.CancelledError:
                pass
            self._advance_task = None
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _advance_loop(self):
        while True:
            await asyncio.sleep(self.advance)
            self.sequence += 1

    def _playlist(self) -> bytes:
        lines = [
            "#EXTM3U",
            f"#EXT-X-TARGETDURATION:{max(1, round(self.target_duration))}",
            f"#EXT-X-MEDIA-SEQUENCE:{self.sequence}",
        ]
        for index in range(3):
            lines.append(f"#EXTINF:{self.segment_seconds},")
            lines.append(f"/seg{self.sequence + index}.ts")
        if self.endlist:
            lines.append("#EXT-X-ENDLIST")
        return ("\n".join(lines) + "\n").encode()

    def _master(self) -> bytes:
        return (
            "#EXTM3U\n"
            f"#EXT-X-STREAM-INF:BANDWIDTH={self.master_bandwidth},RESOLUTION=1920x1080\n"
            "media.m3u8\n"
        ).encode()

    async def _handle(self, reader, writer):
        try:
            request_line = await reader.readline()
            while await reader.readline() not in {b"\r\n", b"\n", b""}:
                pass
            parts = request_line.split()
            if len(parts) < 2:
                return
            method = parts[0].decode("ascii", "ignore")
            path = parts[1].decode("ascii", "ignore").split("?", 1)[0]
            if path.endswith(".m3u8"):
                body = self._master() if path.endswith("master.m3u8") else self._playlist()
                content_type = "application/vnd.apple.mpegurl"
                await self._respond(writer, method, body, content_type)
                return
            if path.endswith(".ts"):
                if self.segment_delay:
                    await asyncio.sleep(self.segment_delay)
                await self._respond(
                    writer,
                    method,
                    b"x" * self.segment_size,
                    "video/mp2t",
                )
                return
            await self._respond(writer, method, b"", "text/plain", status="404 Not Found")
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _respond(self, writer, method, body, content_type, status="200 OK"):
        headers = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(headers)
        if method != "HEAD":
            writer.write(body)
        await writer.drain()


@contextmanager
def playback_config(headroom=1.5, enabled=True):
    with (
        patch.object(
            type(config), "open_playback_check", new=PropertyMock(return_value=enabled)
        ),
        patch.object(
            type(config), "playback_headroom", new=PropertyMock(return_value=headroom)
        ),
    ):
        yield


class HlsPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixtures = []

    async def asyncTearDown(self):
        for fixture in reversed(self.fixtures):
            await fixture.stop()

    async def start_fixture(self, **kwargs) -> HlsFixture:
        fixture = await HlsFixture(**kwargs).start()
        self.fixtures.append(fixture)
        return fixture

    async def test_complete_segments_from_live_edge_are_measured(self):
        fixture = await self.start_fixture(advance=0.1)

        with playback_config(headroom=1.5):
            result = await get_result(fixture.url(), filter_resolution=False, timeout=8)
            self.assertTrue(is_channel_result_valid(
                result,
                filter_speed=False,
                filter_resolution=False,
            ))

        self.assertTrue(result["playback_checked"])
        self.assertEqual(result["live_edge"], "advanced")
        self.assertTrue(result["newest_segment_ok"])
        self.assertEqual(result["segment_seconds"], 1.0)
        self.assertLess(result["segment_ready_ratio"], 0.5)
        self.assertGreaterEqual(result["headroom"], 1.5)
        self.assertGreater(result["speed"], 0)

    async def test_segment_slower_than_its_duration_reduces_headroom(self):
        fixture = await self.start_fixture(
            segment_seconds=0.5,
            segment_delay=0.8,
            advance=0.1,
        )

        with playback_config(headroom=1.5):
            result = await get_result(fixture.url(), filter_resolution=False, timeout=8)
            self.assertEqual(
                channel_result_rejection(
                    result,
                    filter_speed=False,
                    filter_resolution=False,
                ),
                "playback_low_headroom",
            )

        self.assertTrue(result["playback_checked"])
        self.assertGreater(result["segment_ready_ratio"], 1.0)
        self.assertLess(result["headroom"], 1.5)

    async def test_segment_that_misses_the_duration_margin_is_flagged_early(self):
        fixture = await self.start_fixture(
            segment_seconds=1.0,
            segment_delay=3.2,
            advance=0.1,
        )

        with playback_config(headroom=1.5):
            result = await get_result(fixture.url(), filter_resolution=False, timeout=8)
            self.assertEqual(
                channel_result_rejection(
                    result,
                    filter_speed=False,
                    filter_resolution=False,
                ),
                "playback_low_headroom",
            )

        self.assertGreaterEqual(result["segment_ready_ratio"], 1.3)
        self.assertLess(result["headroom"], 1.0)

    async def test_stalled_live_edge_is_reported(self):
        fixture = await self.start_fixture(advance=0.0)

        with playback_config():
            result = await get_result(fixture.url(), filter_resolution=False, timeout=8)
            self.assertEqual(
                channel_result_rejection(
                    result,
                    filter_speed=False,
                    filter_resolution=False,
                ),
                "playback_live_edge_stalled",
            )

        self.assertEqual(result["live_edge"], "stalled")

    async def test_declared_bandwidth_reduces_headroom(self):
        fixture = await self.start_fixture(
            master_bandwidth=8_000_000,
            segment_size=16 * 1024,
            segment_delay=0.3,
            segment_seconds=1.0,
            advance=0.0,
        )

        with playback_config(headroom=1.5):
            result = await get_result(fixture.url(), filter_resolution=False, timeout=8)

        self.assertEqual(result["stream_bitrate"], 8000.0)
        self.assertLess(result["headroom"], 0.2)
        self.assertEqual(result["live_edge"], "stalled")

    async def test_endlist_playlists_skip_live_edge_tracking(self):
        fixture = await self.start_fixture(segment_seconds=120.0, endlist=True)

        with playback_config(), patch("utils.speed.open_filter_ad", False):
            result = await get_result(fixture.url(), filter_resolution=False, timeout=8)

        self.assertTrue(result["playback_checked"])
        self.assertNotIn("live_edge", result)

    async def test_live_edge_waits_for_a_full_playlist_update_cycle(self):
        # A real live playlist only shifts once per target duration, so an
        # observation window shorter than that reports working streams as
        # stalled.
        fixture = await self.start_fixture(
            target_duration=10.0,
            segment_seconds=10.0,
            advance=10.0,
        )

        with playback_config():
            result = await get_result(fixture.url(), filter_resolution=False, timeout=20)

        self.assertEqual(result["live_edge"], "advanced")
        self.assertTrue(is_channel_result_valid(
            result,
            filter_speed=False,
            filter_resolution=False,
        ))

    async def test_live_edge_span_covers_one_playlist_update(self):
        self.assertGreaterEqual(get_live_edge_span(10.0), 12.5)
        self.assertGreaterEqual(get_live_edge_span(2.0), 3.0)
        self.assertEqual(get_live_edge_span(0), 0.0)

    async def test_playback_check_can_be_disabled(self):
        fixture = await self.start_fixture(advance=0.4)

        with playback_config(enabled=False):
            result = await get_result(fixture.url(), filter_resolution=False, timeout=8)

        self.assertNotIn("playback_checked", result)
        self.assertGreater(result["speed"], 0)


class PlaybackResultTests(unittest.TestCase):
    def test_average_result_keeps_playback_fields(self):
        averaged = get_avg_result([
            {
                "speed": 2.0,
                "delay": 10,
                "resolution": None,
                "playback_checked": True,
                "headroom": 2.2,
                "segment_ready_ratio": 0.4,
                "live_edge": "advanced",
            },
            {"speed": 1.0, "delay": 20, "resolution": None},
        ])

        self.assertTrue(averaged["playback_checked"])
        self.assertEqual(averaged["headroom"], 2.2)
        self.assertEqual(averaged["segment_ready_ratio"], 0.4)
        self.assertEqual(averaged["live_edge"], "advanced")


if __name__ == "__main__":
    unittest.main()
