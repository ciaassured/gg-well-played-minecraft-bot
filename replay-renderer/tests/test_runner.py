from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from jump_replay_renderer.command import RenderOptions
from jump_replay_renderer.replay import validate_replay
from jump_replay_renderer.runner import RenderFailure, parse_status, probe_video, render_one


class Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_replay(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metaData.json", json.dumps({"duration": 2_000}))
        archive.writestr("mods.json", "[]")
        archive.writestr("recording.tmcpr", b"packets")


class RunnerTests(unittest.TestCase):
    def test_status_requires_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status"
            path.write_text("message=no state\n", encoding="utf-8")
            with self.assertRaisesRegex(RenderFailure, "no state"):
                parse_status(path)

    @patch("jump_replay_renderer.runner.subprocess.run")
    def test_probe_rejects_ffprobe_failure(self, run: object) -> None:
        run.return_value = Completed(returncode=1, stderr="invalid data")  # type: ignore[attr-defined]
        with self.assertRaisesRegex(RenderFailure, "invalid data"):
            probe_video(Path("/ffprobe"), Path("/video"))

    @patch("jump_replay_renderer.runner.subprocess.run")
    def test_success_moves_only_probed_output(self, run: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay_path = root / "input.mcpr"
            make_replay(replay_path)
            output = root / "final.mp4"

            def fake_run(command: list[str], **kwargs: object) -> Completed:
                if command[0] == "/minecraft":
                    staged = Path(command[command.index("--output") + 1])
                    status = Path(command[command.index("--status") + 1])
                    staged.write_bytes(b"video")
                    status.write_text("state=success\nframes=40\n", encoding="utf-8")
                    return Completed()
                return Completed(
                    stdout=json.dumps(
                        {
                            "format": {"duration": "2.000"},
                            "streams": [
                                {"codec_type": "video", "codec_name": "h264", "nb_frames": "40"}
                            ],
                        }
                    )
                )

            run.side_effect = fake_run  # type: ignore[attr-defined]
            result = render_one(
                validate_replay(replay_path),
                output,
                RenderOptions(end_ms=2_000),
                launcher=Path("/minecraft"),
                ffprobe=Path("/ffprobe"),
                timeout=10,
                environment=os.environ.copy(),
            )
            self.assertEqual(output.read_bytes(), b"video")
            self.assertEqual(result.codec, "h264")
            self.assertEqual(result.frames, 40)


if __name__ == "__main__":
    unittest.main()
