from typing import TypedDict, Literal, Union, NotRequired

OriginType = Literal["hls", "local", "whitelist", "subscribe"]
IPvType = Literal["ipv4", "ipv6", None]


class ChannelData(TypedDict):
    """
    Channel data types, including url, date, resolution, origin and ipv_type
    """
    id: str
    url: str
    host: str
    date: NotRequired[str | None]
    resolution: NotRequired[str | None]
    video_codec: NotRequired[str | None]
    audio_codec: NotRequired[str | None]
    fps: NotRequired[float | None]
    origin: OriginType
    ipv_type: IPvType
    location: NotRequired[str | None]
    isp: NotRequired[str | None]
    headers: NotRequired[dict[str, str] | None]
    catchup: NotRequired[dict[str, str] | None]
    tvg_logo: NotRequired[str | None]
    extra_info: NotRequired[str]
    supply: NotRequired[bool]


CategoryChannelData = dict[str, dict[str, list[ChannelData]]]


class TestResult(TypedDict):
    """
    Test result types, including speed, delay, resolution
    """
    speed: int | float | None
    delay: int | float | None
    resolution: NotRequired[str | None]
    video_codec: NotRequired[str | None]
    audio_codec: NotRequired[str | None]
    fps: NotRequired[float | None]
    playback_checked: NotRequired[bool]
    playback_problem: NotRequired[str | None]
    playback_redirected: NotRequired[bool]
    playback_redirect_target: NotRequired[str | None]
    headroom: NotRequired[float | None]
    stream_bitrate: NotRequired[float | None]
    segment_seconds: NotRequired[float | None]
    segment_download_seconds: NotRequired[float | None]
    segment_ready_ratio: NotRequired[float | None]
    live_edge: NotRequired[str | None]
    newest_segment_ok: NotRequired[bool]


TestResultCacheData = dict[str, list[TestResult]]

ChannelTestResult = Union[ChannelData, TestResult]

WhitelistMaps = tuple[dict[str, list[str]], dict[str, list[str]]]
