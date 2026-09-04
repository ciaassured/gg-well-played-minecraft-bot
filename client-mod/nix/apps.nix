{...}: {
  perSystem = {
    pkgs,
    clientArtifacts,
    ...
  }: let
    launcher = pkgs.writeShellApplication {
      name = "yrush-headless";
      runtimeInputs = [pkgs.coreutils pkgs.jdk25_headless];
      text = ''
        runtime_dir="''${YRUSH_CLIENT_RUNTIME:-$PWD/client-mod/runtime/client}"
        game_dir="$runtime_dir/game"
        readiness_file="''${YRUSH_CLIENT_READINESS_FILE:-$runtime_dir/ready}"
        paper_address="''${YRUSH_PAPER_ADDRESS:-127.0.0.1:25565}"
        trainer_bind="''${YRUSH_TRAINER_BIND:-127.0.0.1}"
        trainer_port="''${YRUSH_TRAINER_PORT:-64123}"
        client_xms="''${YRUSH_CLIENT_XMS:-192m}"
        client_xmx="''${YRUSH_CLIENT_XMX:-320m}"
        pod_name="''${POD_NAME:-yrush-client-0}"
        ordinal="''${pod_name##*-}"
        client_username="''${YRUSH_CLIENT_USERNAME:-}"
        if [[ -z "$client_username" && ! "$ordinal" =~ ^[0-9]+$ ]]; then
          echo "cannot derive StatefulSet ordinal from $pod_name" >&2
          exit 2
        fi
        client_username="''${client_username:-yrushbot-$ordinal}"

        mkdir -p "$runtime_dir/HeadlessMC" "$game_dir/config" "$game_dir/mods"
        rm -f "$readiness_file" "$readiness_file.tmp"
        cp -f ${clientArtifacts.headlessMc} "$runtime_dir/headlessmc.jar"
        ln -sfn ${clientArtifacts.clientMod}/share/yrush-client/yrush-client.jar \
          "$game_dir/mods/yrush-client.jar"
        ln -sfn ${clientArtifacts.fabricApi} "$game_dir/mods/fabric-api.jar"
        ln -sfn ${clientArtifacts.hmcOptimizations}/share/hmc-optimizations/hmc-optimizations.jar \
          "$game_dir/mods/hmc-optimizations.jar"

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
          echo "simulationDistance:5"
          echo "soundCategory_master:0.0"
        } > "$game_dir/options.txt"

        export ALSOFT_DRIVERS=null
        export SDL_AUDIODRIVER=dummy
        export OPENAL_SOFT_LOGLEVEL=0
        cd "$runtime_dir"
        game_args=("$@")
        command_line="launch fabric:26.2 --uid 0.19.3 -offline -lwjgl -keep --jvm \"-Djava.awt.headless=true -Dhmc.optimizations.enabled=true -Dhmc.optimizations.render=true -Dhmc.optimizations.world_render_state=true -Dhmc.optimizations.particles=true -Dhmc.optimizations.sound=true -Dhmc.optimizations.lighting=true -Dhmc.optimizations.animated_textures=true -Dhmc.optimizations.chunk_mesh=true -Dhmc.optimizations.render_buffers=true -Dhmc.optimizations.render_resources=false -Dyrush.client.offline=true -Dyrush.client.bind=$trainer_bind -Dyrush.client.port=$trainer_port -Dyrush.client.server=$paper_address -Dyrush.client.readinessFile=$readiness_file -Xms$client_xms -Xmx$client_xmx\" --game-args \"''${game_args[*]}\""
        printf '%s\n' "$command_line" | java -Dhmc.fileloglevel=INFO \
          --enable-native-access=ALL-UNNAMED -jar headlessmc.jar
      '';
    };
    imageCommand = import ./image-app.nix {
      inherit pkgs;
      commandName = "yrush-client-image";
      component = "client";
      imageArchive = clientArtifacts.oci;
      imageName = "ghcr.io/ciaassured/gg-well-played-minecraft-bot-client";
    };
  in {
    apps.headless = {
      type = "app";
      program = "${launcher}/bin/yrush-headless";
      meta.description = "Start one persistent Fabric YRush client";
    };
    apps.default = {
      type = "app";
      program = "${launcher}/bin/yrush-headless";
      meta.description = "Start one persistent Fabric YRush client";
    };
    apps.image = {
      type = "app";
      program = "${imageCommand}/bin/yrush-client-image";
      meta.description = "Build, load, or publish the HeadlessMC client OCI image";
    };
  };
}
