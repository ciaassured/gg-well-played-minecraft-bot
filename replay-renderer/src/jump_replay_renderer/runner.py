from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .command import RenderOptions, minecraft_command
from .replay import ReplayInfo


class RenderFailure(RuntimeError):
    """Minecraft, Replay Mod, or FFmpeg did not produce a valid video."""


@dataclass(frozen=True)
class RenderResult:
    replay: str
    video: str
    replay_duration_ms: int
    video_duration_seconds: float
    frames: int
    codec: str
    size_bytes: int

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def parse_status(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RenderFailure("Minecraft exited without a renderer status file")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = raw_line.partition("=")
        if separator:
            values[key] = value
    if "state" not in values:
        raise RenderFailure("renderer status file has no state")
    return values


def probe_video(ffprobe: Path, video: Path, timeout: float = 30.0) -> tuple[float, int, str]:
    try:
        completed = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_name,codec_type,nb_frames",
                "-of",
                "json",
                str(video),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RenderFailure(f"could not run ffprobe: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit {completed.returncode}"
        raise RenderFailure(f"ffprobe rejected rendered video: {detail}")
    try:
        payload = json.loads(completed.stdout)
        duration = float(payload["format"]["duration"])
        video_stream = next(
            stream for stream in payload["streams"] if stream.get("codec_type") == "video"
        )
        codec = str(video_stream["codec_name"])
        raw_frames = video_stream.get("nb_frames", "0")
        frames = int(raw_frames) if str(raw_frames).isdigit() else 0
    except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError) as error:
        raise RenderFailure("ffprobe did not report a playable video stream") from error
    if duration <= 0 or not codec:
        raise RenderFailure("rendered video has no playable duration or codec")
    return duration, frames, codec


def render_one(
    replay: ReplayInfo,
    output: Path,
    options: RenderOptions,
    *,
    launcher: Path,
    ffprobe: Path,
    timeout: float,
    environment: dict[str, str] | None = None,
) -> RenderResult:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="jump-render-", dir=output.parent) as temporary_directory:
        temporary = Path(temporary_directory)
        staged_output = temporary / "render.mp4"
        status_path = temporary / "status.txt"
        command = minecraft_command(launcher, replay.path, staged_output, status_path, options)
        try:
            completed = subprocess.run(
                command,
                check=False,
                text=True,
                timeout=timeout,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RenderFailure(f"could not complete Minecraft render: {error}") from error

        status = parse_status(status_path)
        if status["state"] != "success":
            detail = status.get("message", "unknown Replay Mod failure")
            raise RenderFailure(f"Replay Mod render failed: {detail}")
        if completed.returncode != 0:
            raise RenderFailure(f"Minecraft renderer exited with status {completed.returncode}")
        if not staged_output.is_file() or staged_output.stat().st_size <= 0:
            raise RenderFailure("renderer reported success without an MP4 file")

        duration, probe_frames, codec = probe_video(ffprobe, staged_output)
        status_frames = int(status.get("frames", "0"))
        frames = probe_frames or status_frames
        os.replace(staged_output, output)
    return RenderResult(
        replay=str(replay.path),
        video=str(output),
        replay_duration_ms=replay.duration_ms,
        video_duration_seconds=duration,
        frames=frames,
        codec=codec,
        size_bytes=output.stat().st_size,
    )
