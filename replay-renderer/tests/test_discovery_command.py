from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jump_replay_renderer.command import RenderOptions, minecraft_command
from jump_replay_renderer.discovery import default_output_root, discover_replays


class DiscoveryAndCommandTests(unittest.TestCase):
    def test_run_directory_uses_replays_boundary_and_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            captures = run / "replays" / "capture-a"
            captures.mkdir(parents=True)
            (captures / "b.mcpr").touch()
            (captures / "a.mcpr").touch()
            root, found = discover_replays(run)
            self.assertEqual(root, (run / "replays").resolve())
            self.assertEqual(
                [item.relative.as_posix() for item in found],
                ["capture-a/a.mcpr", "capture-a/b.mcpr"],
            )
            self.assertEqual(default_output_root(run), run.resolve() / "videos")

    def test_command_is_argument_safe_and_complete(self) -> None:
        options = RenderOptions(width=320, height=180, fps=10, bitrate=500_000, end_ms=2_000)
        command = minecraft_command(
            Path("/tool with space"),
            Path("/replay with space.mcpr"),
            Path("/out.mp4"),
            Path("/status"),
            options,
        )
        self.assertEqual(command[0], "/tool with space")
        self.assertIn("/replay with space.mcpr", command)
        self.assertEqual(command[-4:], ["--end-ms", "2000", "--camera", "first-person"])

    def test_rejects_odd_mp4_dimensions(self) -> None:
        with self.assertRaisesRegex(ValueError, "even"):
            RenderOptions(width=321, height=180).validate()

    def test_rejects_unknown_camera_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "camera"):
            RenderOptions(camera="underground").validate()


if __name__ == "__main__":
    unittest.main()
