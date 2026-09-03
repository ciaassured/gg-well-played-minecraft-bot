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
        runtime_dir="''${JUMP_CLIENT_RUNTIME:-$PWD/client-mod/runtime/client}"
        game_dir="$runtime_dir/game"
        readiness_file="''${JUMP_CLIENT_READINESS_FILE:-$runtime_dir/ready}"
        paper_address="''${JUMP_PAPER_ADDRESS:-127.0.0.1:25565}"
        trainer_bind="''${JUMP_TRAINER_BIND:-127.0.0.1}"
        trainer_port="''${JUMP_TRAINER_PORT:-64123}"
        client_xms="''${JUMP_CLIENT_XMS:-512m}"
        client_xmx="''${JUMP_CLIENT_XMX:-1536m}"
        pod_name="''${POD_NAME:-''${HOSTNAME:-jump-client-0}}"
        ordinal="''${pod_name##*-}"
        if [[ ! "$ordinal" =~ ^[0-9]+$ ]]; then
          echo "cannot derive StatefulSet ordinal from $pod_name" >&2
          exit 2
        fi
        client_username="''${JUMP_CLIENT_USERNAME:-jumpbot-$ordinal}"

        mkdir -p "$runtime_dir/HeadlessMC" "$game_dir/config" "$game_dir/mods"
        rm -f "$readiness_file" "$readiness_file.tmp"
        cp -f ${clientArtifacts.headlessMc} "$runtime_dir/headlessmc.jar"
        ln -sfn ${clientArtifacts.clientMod}/share/jump-benchmark-client/jump-benchmark-client.jar \
          "$game_dir/mods/jump-benchmark-client.jar"
        ln -sfn ${clientArtifacts.fabricApi} "$game_dir/mods/fabric-api.jar"

        java_path="$(command -v java)"
        {
          echo "hmc.mcdir=$runtime_dir/minecraft"
          echo "hmc.gamedir=$game_dir"
          echo "hmc.java.versions=$java_path"
          echo "hmc.offline=true"
          echo "hmc.offline.username=$client_username"
          echo "hmc.assets.dummy=true"
          echo "hmc.jline.enabled=false"
          echo "hmc.fileloglevel=INFO"
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
          echo "renderDistance:2"
          echo "simulationDistance:2"
          echo "soundCategory_master:0.0"
        } > "$game_dir/options.txt"

        export ALSOFT_DRIVERS=null
        export SDL_AUDIODRIVER=dummy
        export OPENAL_SOFT_LOGLEVEL=0
        cd "$runtime_dir"
        game_args=("$@")
        command_line="launch fabric:26.2 --uid 0.19.3 -offline -lwjgl -keep --jvm \"-Djava.awt.headless=true -Djump.client.offline=true -Djump.client.bind=$trainer_bind -Djump.client.port=$trainer_port -Djump.client.server=$paper_address -Djump.client.readinessFile=$readiness_file -Xms$client_xms -Xmx$client_xmx\" --game-args \"''${game_args[*]}\""
        printf '%s\n' "$command_line" | java -Dhmc.fileloglevel=INFO \
          --enable-native-access=ALL-UNNAMED -jar headlessmc.jar
      '';
    };
  in {
    apps.headless = {
      type = "app";
      program = "${launcher}/bin/jump-benchmark-headless";
      meta.description = "Start one persistent Fabric benchmark client";
    };
    apps.default = {
      type = "app";
      program = "${launcher}/bin/jump-benchmark-headless";
      meta.description = "Start one persistent Fabric benchmark client";
    };
  };
}
