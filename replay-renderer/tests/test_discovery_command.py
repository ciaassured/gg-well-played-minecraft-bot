from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jump_replay_renderer.command import RenderOptions, minecraft_command
from jump_replay_renderer.discovery import default_output_root, discover_replays


class DiscoveryAndCommandTests(unittest.TestCase):
    def test_supplied_directory_is_the_only_discovery_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            captures = run / "replays" / "capture-a"
            captures.mkdir(parents=True)
            (captures / "b.mcpr").touch()
            (captures / "a.mcpr").touch()
            outside = run / "outside.mcpr"
            outside.touch()
            root, found = discover_replays(run)
            self.assertEqual(root, run.resolve())
            self.assertEqual(
                [item.relative.as_posix() for item in found],
                ["outside.mcpr", "replays/capture-a/a.mcpr", "replays/capture-a/b.mcpr"],
            )
            self.assertEqual(default_output_root(run), run.resolve() / "videos")

    def test_file_input_uses_a_sibling_videos_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            replay = Path(directory) / "episode.mcpr"
            replay.touch()
            root, found = discover_replays(replay)
            self.assertEqual(root, replay.parent.resolve())
            self.assertEqual(found[0].relative, Path("episode.mcpr"))
            self.assertEqual(default_output_root(replay), replay.parent.resolve() / "videos")

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
