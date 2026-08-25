{...}: {
  perSystem = {
    pkgs,
    rendererArtifacts,
    ...
  }: {
    checks = {
      package = rendererArtifacts.rendererCli;
      mod-build = rendererArtifacts.rendererMod;

      python-tests =
        pkgs.runCommand "jump-replay-renderer-python-tests" {
          nativeBuildInputs = [pkgs.python313 pkgs.ruff];
          src = ../.;
        } ''
          cp -R "$src" source
          chmod -R u+w source
          cd source
          export PYTHONPATH="$PWD/src"
          python -m unittest discover -s tests -v
          ruff check src tests
          touch "$out"
        '';

      core-tests =
        pkgs.runCommand "jump-replay-renderer-core-tests" {
          nativeBuildInputs = [pkgs.jdk25_headless];
          src = ../.;
        } ''
          cp -R "$src" source
          chmod -R u+w source
          cd source
          mkdir -p classes
          find \
            src/main/java/gg/wellplayed/jump/renderer/core \
            src/test/java/gg/wellplayed/jump/renderer/core \
            -type f -name '*.java' | sort > sources.txt
          javac --release 25 -d classes @sources.txt
          java -ea -cp classes gg.wellplayed.jump.renderer.core.CoreTestMain
          touch "$out"
        '';

      startup-configuration = pkgs.runCommand "jump-replay-renderer-startup-configuration" {} ''
        grep -q 'runPostStartup' ${../src/main/java/gg/wellplayed/jump/renderer/ReplayRendererMod.java}
        grep -q 'replaySceneReady' ${../src/main/java/gg/wellplayed/jump/renderer/ReplayRendererMod.java}
        grep -q 'SHORT_REPLAY_SETTLE_MILLIS' ${../src/main/java/gg/wellplayed/jump/renderer/ReplayRendererMod.java}
        grep -q 'trim=start_frame=' ${../src/main/java/gg/wellplayed/jump/renderer/ReplayRendererMod.java}
        grep -q 'setDefaultInterpolatorType(InterpolatorType.LINEAR)' ${../src/main/java/gg/wellplayed/jump/renderer/ReplayRendererMod.java}
        grep -q 'CameraType.FIRST_PERSON' ${../src/main/java/gg/wellplayed/jump/renderer/ReplayRendererMod.java}
        grep -q 'target.getId()' ${../src/main/java/gg/wellplayed/jump/renderer/ReplayRendererMod.java}
        grep -q 'VideoRenderer' ${../src/main/java/gg/wellplayed/jump/renderer/ReplayRendererMod.java}
        grep -q 'MP4_CUSTOM' ${../src/main/java/gg/wellplayed/jump/renderer/ReplayRendererMod.java}
        grep -q '"replaymod": "26.2-2.6.27"' ${../src/main/resources/fabric.mod.json}
        grep -q 'ffprobe' ${../src/jump_replay_renderer/runner.py}
        grep -q 'hmc.offline=true' ${./packages.nix}
        grep -q 'hmc.check.xvfb=true' ${./packages.nix}
        grep -q -- '--width \$width --height \$height' ${./packages.nix}
        grep -q -- '-Djump.renderer.camera=\$camera' ${./packages.nix}
        grep -q 'ALSOFT_DRIVERS=null' ${./packages.nix}
        grep -q 'gamma:1.0' ${./packages.nix}
        grep -q 'LIBGL_ALWAYS_SOFTWARE=1' ${./packages.nix}
        grep -q 'pkgs.mesa' ${./packages.nix}
        grep -q 'xvfb-run' ${./packages.nix}
        ! grep -q -- '-lwjgl' ${./packages.nix}
        touch "$out"
      '';
    };
  };
}
