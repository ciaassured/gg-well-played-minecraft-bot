from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RenderOptions:
    width: int = 640
    height: int = 360
    fps: int = 20
    bitrate: int = 4_000_000
    start_ms: int = 0
    end_ms: int = -1

    def validate(self) -> None:
        if not 160 <= self.width <= 7680 or not 90 <= self.height <= 4320:
            raise ValueError("video dimensions are outside the supported range")
        if self.width % 2 or self.height % 2:
            raise ValueError("MP4 width and height must be even")
        if not 1 <= self.fps <= 120:
            raise ValueError("frame rate must be between 1 and 120")
        if not 100_000 <= self.bitrate <= 200_000_000:
            raise ValueError("bitrate is outside the supported range")
        if self.start_ms < 0 or (self.end_ms >= 0 and self.end_ms <= self.start_ms):
            raise ValueError("render time range is invalid")


def minecraft_command(
    launcher: Path,
    replay: Path,
    output: Path,
    status: Path,
    options: RenderOptions,
) -> list[str]:
    options.validate()
    return [
        str(launcher),
        "--input",
        str(replay),
        "--output",
        str(output),
        "--status",
        str(status),
        "--width",
        str(options.width),
        "--height",
        str(options.height),
        "--fps",
        str(options.fps),
        "--bitrate",
        str(options.bitrate),
        "--start-ms",
        str(options.start_ms),
        "--end-ms",
        str(options.end_ms),
    ]
