from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ReplayValidationError(ValueError):
    """A path is not a complete, readable Replay Mod recording."""


@dataclass(frozen=True)
class ReplayInfo:
    path: Path
    duration_ms: int
    metadata: dict[str, Any]


REQUIRED_ENTRIES = frozenset({"metaData.json", "mods.json", "recording.tmcpr"})


def validate_replay(path: Path) -> ReplayInfo:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() != ".mcpr":
        raise ReplayValidationError(f"not an .mcpr file: {resolved}")
    if not resolved.is_file():
        raise ReplayValidationError(f"replay does not exist: {resolved}")
    try:
        with zipfile.ZipFile(resolved) as archive:
            names = set(archive.namelist())
            missing = REQUIRED_ENTRIES - names
            if missing:
                raise ReplayValidationError(
                    f"replay is missing required entries: {', '.join(sorted(missing))}"
                )
            corrupt = archive.testzip()
            if corrupt is not None:
                raise ReplayValidationError(f"replay has a corrupt ZIP member: {corrupt}")
            if archive.getinfo("recording.tmcpr").file_size <= 0:
                raise ReplayValidationError("recording.tmcpr is empty")
            metadata = json.loads(archive.read("metaData.json"))
    except ReplayValidationError:
        raise
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReplayValidationError(f"invalid replay archive: {error}") from error
    if not isinstance(metadata, dict):
        raise ReplayValidationError("metaData.json must contain an object")
    duration = metadata.get("duration")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        raise ReplayValidationError("replay metadata duration must be a positive integer")
    return ReplayInfo(path=resolved, duration_ms=duration, metadata=metadata)
