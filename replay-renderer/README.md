# Replay renderer

This project owns validation and unattended video rendering of finalized Replay
Mod recordings. It loads the pinned Minecraft 26.2, Fabric Loader 0.19.3, Java
25, HeadlessMC 2.10.0, and Replay Mod 2.6.27 runtime, drives Replay Mod's native
camera-path renderer, encodes H.264 MP4 with FFmpeg, and verifies every result
with `ffprobe`. It does not train models, control live benchmark episodes, or
capture replays.

Rendering uses Mesa's software OpenGL implementation inside an isolated Xvfb
display, so it does not need a visible desktop or a host GPU.

```console
nix develop ./replay-renderer
nix build ./replay-renderer
nix flake check ./replay-renderer
(cd replay-renderer && nix fmt)
nix run ./replay-renderer#render -- <replay-or-run-directory>
```

For a training run, recordings are discovered below `<run>/replays` and videos
are written below `<run>/videos` with the same relative names. For one `.mcpr`,
the output goes into a sibling `videos/` directory. Each invocation validates
ZIP integrity, required Replay Mod members, and metadata before starting
Minecraft. Successful output must contain a playable video stream with positive
duration; all results and failures are recorded in `render-manifest.json`.

The first run downloads the pinned Minecraft runtime into
`replay-renderer/runtime`. Set `JUMP_RENDERER_RUNTIME` to move that mutable cache.
Rendering is sequential and follows the one recorded benchmark player in first
person by default using Replay Mod spectator keyframes, so the view cannot be
clipped into a hard-coded world position. `--camera third-person` selects the
vanilla following view, while `--camera fixed` retains the diagnostic fixed
arena camera. Optional `--width`, `--height`, `--fps`, `--bitrate`,
`--start-ms`, `--end-ms`, and `--output-dir` flags are also available; the
documented command renders every retained replay in full.
