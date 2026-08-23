from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from jump_replay_renderer.replay import ReplayValidationError, validate_replay


def write_replay(path: Path, *, duration: object = 1_000, omit: str | None = None) -> None:
    entries = {
        "metaData.json": json.dumps({"duration": duration}).encode(),
        "mods.json": b"[]",
        "recording.tmcpr": b"packets",
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in entries.items():
            if name != omit:
                archive.writestr(name, value)


class ReplayValidationTests(unittest.TestCase):
    def test_accepts_complete_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "valid.mcpr"
            write_replay(path, duration=1_234)
            replay = validate_replay(path)
            self.assertEqual(replay.duration_ms, 1_234)
            self.assertEqual(replay.path, path.resolve())

    def test_rejects_missing_recording(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.mcpr"
            write_replay(path, omit="recording.tmcpr")
            with self.assertRaisesRegex(ReplayValidationError, "recording.tmcpr"):
                validate_replay(path)

    def test_rejects_non_positive_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.mcpr"
            write_replay(path, duration=0)
            with self.assertRaisesRegex(ReplayValidationError, "positive integer"):
                validate_replay(path)


if __name__ == "__main__":
    unittest.main()
