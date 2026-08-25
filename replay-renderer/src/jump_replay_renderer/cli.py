from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .command import RenderOptions
from .discovery import DiscoveryError, default_output_root, discover_replays
from .replay import ReplayValidationError, validate_replay
from .runner import RenderFailure, render_one


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jump-replay-renderer",
        description="Validate Replay Mod captures and render each to a playable MP4.",
    )
    parser.add_argument(
        "source", type=Path, help="one .mcpr file or the exact directory to search recursively"
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--bitrate", type=int, default=4_000_000)
    parser.add_argument("--start-ms", type=int, default=0)
    parser.add_argument("--end-ms", type=int, default=-1)
    parser.add_argument(
        "--camera",
        choices=("first-person", "third-person", "fixed"),
        default="first-person",
        help="follow the recorded player or use the diagnostic fixed camera",
    )
    parser.add_argument("--timeout", type=float, default=1_800.0)
    return parser


def _required_tool(variable: str) -> Path:
    value = os.environ.get(variable)
    if not value:
        raise RenderFailure(f"{variable} is not configured; use the Nix app or development shell")
    path = Path(value)
    if not path.is_file():
        raise RenderFailure(f"configured tool does not exist: {path}")
    return path


def _manifest_path(output_root: Path) -> Path:
    return output_root / "render-manifest.json"


def main(arguments: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(arguments)
    options = RenderOptions(
        width=args.width,
        height=args.height,
        fps=args.fps,
        bitrate=args.bitrate,
        start_ms=args.start_ms,
        end_ms=args.end_ms,
        camera=args.camera,
    )
    try:
        options.validate()
        replay_root, discovered = discover_replays(args.source)
        output_root = (
            args.output_dir.expanduser().resolve()
            if args.output_dir is not None
            else default_output_root(args.source)
        )
        launcher = _required_tool("JUMP_RENDERER_MINECRAFT")
        ffprobe = _required_tool("JUMP_RENDERER_FFPROBE")
    except (ValueError, DiscoveryError, RenderFailure) as error:
        parser.error(str(error))

    output_root.mkdir(parents=True, exist_ok=True)
    successes: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for item in discovered:
        relative = item.relative.with_suffix(".mp4")
        destination = output_root / relative
        try:
            replay = validate_replay(item.path)
            effective_end = options.end_ms
            if effective_end < 0:
                effective_end = replay.duration_ms
            if effective_end > replay.duration_ms:
                raise ReplayValidationError(
                    f"requested end {effective_end}ms exceeds replay duration "
                    f"{replay.duration_ms}ms"
                )
            print(f"rendering {item.path} -> {destination}", flush=True)
            result = render_one(
                replay,
                destination,
                RenderOptions(
                    width=options.width,
                    height=options.height,
                    fps=options.fps,
                    bitrate=options.bitrate,
                    start_ms=options.start_ms,
                    end_ms=effective_end,
                    camera=options.camera,
                ),
                launcher=launcher,
                ffprobe=ffprobe,
                timeout=args.timeout,
            )
            successes.append(result.as_json())
            print(
                f"rendered {destination} ({result.codec}, "
                f"{result.video_duration_seconds:.3f}s, {result.size_bytes} bytes)",
                flush=True,
            )
        except (ReplayValidationError, RenderFailure, ValueError) as error:
            failures.append({"replay": str(item.path), "error": str(error)})
            print(f"failed {item.path}: {error}", file=sys.stderr, flush=True)

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(args.source.expanduser().resolve()),
        "replay_root": str(replay_root),
        "output_root": str(output_root),
        "settings": {
            "width": options.width,
            "height": options.height,
            "fps": options.fps,
            "bitrate": options.bitrate,
            "start_ms": options.start_ms,
            "end_ms": options.end_ms,
            "camera": options.camera,
        },
        "renders": successes,
        "failures": failures,
    }
    manifest_path = _manifest_path(output_root)
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_path)
    if failures:
        print(f"{len(failures)} replay(s) failed; see {manifest_path}", file=sys.stderr)
        return 1
    print(f"rendered {len(successes)} replay(s); manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
