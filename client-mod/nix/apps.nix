{...}: {
  perSystem = {
    pkgs,
    clientArtifacts,
    ...
  }: let
    replayConfig = pkgs.writeText "jump-replaymod.json" ''
      {
        "core": {
          "notifications": false,
          "recordingPath": "./replay_recordings/"
        },
        "recording": {
          "recordServer": true,
          "indicator": false,
          "autoStartRecording": true,
          "autoPostProcess": true,
          "renameDialog": false
        }
      }
    '';
    launcher = pkgs.writeShellApplication {
      name = "jump-benchmark-headless";
      runtimeInputs = [pkgs.coreutils pkgs.jdk25_headless];
      text = ''
        runtime_dir="''${JUMP_CLIENT_RUNTIME:-$PWD/client-mod/runtime/client}"
        game_dir="$runtime_dir/game"
        mkdir -p \
          "$runtime_dir/HeadlessMC" \
          "$game_dir/config" \
          "$game_dir/mods" \
          "$game_dir/replay_recordings"

        cp -f ${clientArtifacts.headlessMc} "$runtime_dir/headlessmc.jar"
        ln -sfn ${clientArtifacts.clientMod}/share/jump-benchmark-client/jump-benchmark-client.jar \
          "$game_dir/mods/jump-benchmark-client.jar"
        ln -sfn ${clientArtifacts.fabricApi} "$game_dir/mods/fabric-api.jar"
        ln -sfn ${clientArtifacts.replayMod} "$game_dir/mods/replaymod.jar"
        cp -f ${replayConfig} "$game_dir/config/replaymod.json"

        chmod -R u+w "$runtime_dir"
        java_path="$(command -v java)"
        {
          echo "hmc.mcdir=$runtime_dir/minecraft"
          echo "hmc.gamedir=$game_dir"
          echo "hmc.java.versions=$java_path"
          echo "hmc.offline=true"
          echo "hmc.assets.dummy=true"
          echo "hmc.jline.enabled=false"
          echo "hmc.exit.on.failed.command=true"
          echo "hmc.auto.download.java=false"
          echo "hmc.java.use.current=true"
          echo "hmc.auto.download.specifics=false"
        } > "$runtime_dir/HeadlessMC/config.properties"
        {
          echo "pauseOnLostFocus:false"
          echo "onboardAccessibility:false"
          echo "narrator:0"
          echo "autoJump:false"
          echo "enableVsync:false"
          echo "maxFps:60"
          echo "inactivityFpsLimit:minimized"
          echo "soundCategory_master:0.0"
        } > "$game_dir/options.txt"

        export ALSOFT_DRIVERS=null
        export SDL_AUDIODRIVER=dummy
        export OPENAL_SOFT_LOGLEVEL=0
        cd "$runtime_dir"
        game_args=("$@")
        finalization_timeout="''${JUMP_CLIENT_FINALIZATION_TIMEOUT_MILLIS:-300000}"
        command_line="launch fabric:26.2 --uid 0.19.3 -offline -lwjgl -keep --jvm \"-Djava.awt.headless=true -Djump.client.port=64123 -Djump.client.server=127.0.0.1:25565 -Djump.client.replayDir=$game_dir/replay_recordings -Djump.client.finalizationTimeoutMillis=$finalization_timeout -Xms512m -Xmx2g\" --game-args \"''${game_args[*]}\""
        printf '%s\n' "$command_line" | java --enable-native-access=ALL-UNNAMED -jar headlessmc.jar
      '';
    };
  in {
    apps.headless = {
      type = "app";
      program = "${launcher}/bin/jump-benchmark-headless";
      meta.description = "Start the persistent Replay Mod Fabric benchmark client";
    };
    apps.default = {
      type = "app";
      program = "${launcher}/bin/jump-benchmark-headless";
      meta.description = "Start the persistent Replay Mod Fabric benchmark client";
    };
  };
}
