from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class DiscoveryError(ValueError):
    """The supplied input cannot identify any replay recordings."""


@dataclass(frozen=True)
class DiscoveredReplay:
    path: Path
    relative: Path


def discover_replays(source: Path) -> tuple[Path, list[DiscoveredReplay]]:
    resolved = source.expanduser().resolve()
    if resolved.is_file():
        if resolved.suffix.lower() != ".mcpr":
            raise DiscoveryError(f"input file is not an .mcpr archive: {resolved}")
        return resolved.parent, [DiscoveredReplay(resolved, Path(resolved.name))]
    if not resolved.is_dir():
        raise DiscoveryError(f"input does not exist: {resolved}")

    replay_root = resolved
    files = sorted(path for path in replay_root.rglob("*.mcpr") if path.is_file())
    if not files:
        raise DiscoveryError(f"no .mcpr files found beneath {replay_root}")
    return replay_root, [
        DiscoveredReplay(path=path.resolve(), relative=path.relative_to(replay_root))
        for path in files
    ]


def default_output_root(source: Path) -> Path:
    resolved = source.expanduser().resolve()
    if resolved.is_file():
        return resolved.parent / "videos"
    return resolved / "videos"
