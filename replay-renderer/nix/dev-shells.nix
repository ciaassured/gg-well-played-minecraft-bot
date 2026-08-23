{...}: {
  perSystem = {
    pkgs,
    rendererArtifacts,
    ...
  }: {
    devShells.default = pkgs.mkShellNoCC {
      packages = [
        pkgs.alejandra
        pkgs.ffmpeg
        pkgs.git
        pkgs.google-java-format
        pkgs.gradle_9
        pkgs.jdk25
        pkgs.ktlint
        pkgs.python313
        pkgs.ruff
      ];
      JUMP_RENDERER_MINECRAFT = "${rendererArtifacts.minecraftLauncher}/bin/jump-replay-minecraft";
      JUMP_RENDERER_FFPROBE = "${pkgs.ffmpeg}/bin/ffprobe";
      REPLAY_MOD_JAR = rendererArtifacts.replayMod;
      shellHook = ''
        export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
        echo "Replay renderer shell (Minecraft 26.2, Java 25, Replay Mod 2.6.27)"
      '';
    };
  };
}
