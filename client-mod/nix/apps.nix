{...}: {
  perSystem = {
    pkgs,
    clientArtifacts,
    ...
  }: let
    launcher = pkgs.writeShellApplication {
      name = "jump-benchmark-headless";
      runtimeInputs = [pkgs.coreutils pkgs.jdk25_headless];
      text = ''
        mode="training"
        passthrough=()
        while (($#)); do
          case "$1" in
            --mode)
              if (($# < 2)); then
                echo "--mode requires training or recording" >&2
                exit 2
              fi
              mode="$2"
              shift 2
              ;;
            --mode=*)
              mode="''${1#--mode=}"
              shift
              ;;
            *)
              passthrough+=("$1")
              shift
              ;;
          esac
        done
        if [[ "$mode" != training && "$mode" != recording ]]; then
          echo "--mode must be training or recording" >&2
          exit 2
        fi

        runtime_dir="''${JUMP_CLIENT_RUNTIME:-$PWD/client-mod/runtime/$mode}"
        game_dir="$runtime_dir/game"
        mkdir -p "$runtime_dir/HeadlessMC" "$game_dir/mods" "$game_dir/replay_recordings"

        cp -f ${clientArtifacts.headlessMc} "$runtime_dir/headlessmc.jar"
        ln -sfn ${clientArtifacts.clientMod}/share/jump-benchmark-client/jump-benchmark-client.jar \
          "$game_dir/mods/jump-benchmark-client.jar"
        ln -sfn ${clientArtifacts.fabricApi} "$game_dir/mods/fabric-api.jar"
        if [[ "$mode" == recording ]]; then
          ln -sfn ${clientArtifacts.replayMod} "$game_dir/mods/replaymod.jar"
        else
          rm -f "$game_dir/mods/replaymod.jar"
        fi

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
          echo "maxFps:20"
          echo "soundCategory_master:0.0"
        } > "$game_dir/options.txt"

        export ALSOFT_DRIVERS=null
        export SDL_AUDIODRIVER=dummy
        export OPENAL_SOFT_LOGLEVEL=0
        cd "$runtime_dir"
        command_line="launch fabric:26.2 --uid 0.19.3 -offline -lwjgl -keep --jvm \"-Djava.awt.headless=true -Djump.client.mode=$mode -Djump.client.port=64123 -Xms512m -Xmx2g\" --game-args \"--quickPlayMultiplayer 127.0.0.1:25565''${passthrough[*]:+ ''${passthrough[*]}}\""
        printf '%s\n' "$command_line" | java --enable-native-access=ALL-UNNAMED -jar headlessmc.jar
      '';
    };
  in {
    apps.headless = {
      type = "app";
      program = "${launcher}/bin/jump-benchmark-headless";
      meta.description = "Start the isolated Fabric client in training or recording mode";
    };
    apps.default = {
      type = "app";
      program = "${launcher}/bin/jump-benchmark-headless";
      meta.description = "Start the isolated Fabric client in training mode";
    };
  };
}
