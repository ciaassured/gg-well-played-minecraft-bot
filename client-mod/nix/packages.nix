{inputs, ...}: {
  perSystem = {pkgs, ...}: let
    fabricApi = pkgs.fetchurl {
      url = "https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/0.155.2+26.2/fabric-api-0.155.2+26.2.jar";
      hash = "sha256-1lGMdwAky+ilViSPFvzbuRxqYvUCJ6bDuugZBRHiwbg=";
    };
    headlessMc = pkgs.fetchurl {
      url = "https://github.com/3arthqu4ke/HeadlessMc/releases/download/2.10.0/headlessmc-launcher-wrapper-2.10.0.jar";
      hash = "sha256-v4DYRRbu65pR+jWJTERmsUbVXQFGzW0q3En90jFlRTY=";
    };
    protobufJava = pkgs.fetchurl {
      url = "https://repo.maven.apache.org/maven2/com/google/protobuf/protobuf-java/4.35.1/protobuf-java-4.35.1.jar";
      hash = "sha256-pDRboqoAmRL/b5BGf+otEEYFJWtyxQhA118TJWY4pHI=";
    };
    clientMod = pkgs.stdenvNoCC.mkDerivation (finalAttrs: {
      pname = "jump-benchmark-client-mod";
      version = "1.0.0";
      src = ../.;

      nativeBuildInputs = [pkgs.gradle_9 pkgs.jdk25_headless pkgs.protobuf];
      gradleFlags = ["-PprotocolDir=${inputs.protocol}"];
      gradleBuildTask = "assemble";
      gradleUpdateTask = "assemble";

      mitmCache = pkgs.gradle_9.fetchDeps {
        pkg = finalAttrs.finalPackage;
        data = ../deps.json;
      };

      __darwinAllowLocalNetworking = true;

      installPhase = ''
        runHook preInstall
        mkdir -p "$out/share/jump-benchmark-client"
        cp build/libs/jump-benchmark-client-1.0.0.jar \
          "$out/share/jump-benchmark-client/jump-benchmark-client.jar"
        runHook postInstall
      '';
    });
    containerEntrypoint = pkgs.writeShellApplication {
      name = "jump-client-container";
      runtimeInputs = [pkgs.coreutils pkgs.jdk25_headless];
      text = ''
        runtime_dir="''${JUMP_CLIENT_RUNTIME:-/runtime}"
        game_dir="$runtime_dir/game"
        readiness_file="''${JUMP_CLIENT_READINESS_FILE:-$runtime_dir/ready}"
        paper_address="''${JUMP_PAPER_ADDRESS:-jump-paper:25565}"
        trainer_bind="''${JUMP_TRAINER_BIND:-0.0.0.0}"
        trainer_port="''${JUMP_TRAINER_PORT:-64123}"
        client_xms="''${JUMP_CLIENT_XMS:-512m}"
        client_xmx="''${JUMP_CLIENT_XMX:-1536m}"
        pod_name="''${POD_NAME:-jump-client-0}"
        ordinal="''${pod_name##*-}"
        if [[ ! "$ordinal" =~ ^[0-9]+$ ]]; then
          echo "cannot derive StatefulSet ordinal from $pod_name" >&2
          exit 2
        fi
        client_username="''${JUMP_CLIENT_USERNAME:-jumpbot-$ordinal}"
        if [[ "''${JUMP_ENTRYPOINT_VALIDATE:-0}" == 1 ]]; then
          printf 'username=%s bind=%s:%s paper=%s readiness=%s\n' \
            "$client_username" "$trainer_bind" "$trainer_port" "$paper_address" "$readiness_file"
          exit 0
        fi

        mkdir -p "$runtime_dir/HeadlessMC" "$game_dir/config" "$game_dir/mods"
        rm -f "$readiness_file" "$readiness_file.tmp"
        cp -f ${headlessMc} "$runtime_dir/headlessmc.jar"
        ln -sfn ${clientMod}/share/jump-benchmark-client/jump-benchmark-client.jar \
          "$game_dir/mods/jump-benchmark-client.jar"
        ln -sfn ${fabricApi} "$game_dir/mods/fabric-api.jar"
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
        export ALSOFT_DRIVERS=null SDL_AUDIODRIVER=dummy OPENAL_SOFT_LOGLEVEL=0
        cd "$runtime_dir"
        game_args=("$@")
        command_line="launch fabric:26.2 --uid 0.19.3 -offline -lwjgl -keep --jvm \"-Djava.awt.headless=true -Djump.client.offline=true -Djump.client.bind=$trainer_bind -Djump.client.port=$trainer_port -Djump.client.server=$paper_address -Djump.client.readinessFile=$readiness_file -Xms$client_xms -Xmx$client_xmx\" --game-args \"''${game_args[*]}\""
        printf '%s\n' "$command_line" | java -Dhmc.fileloglevel=INFO \
          --enable-native-access=ALL-UNNAMED -jar headlessmc.jar
      '';
    };
    oci = pkgs.dockerTools.buildLayeredImage {
      name = "ghcr.io/ciaassured/gg-well-played-minecraft-bot-client";
      tag = "unstable";
      maxLayers = 120;
      contents = [containerEntrypoint pkgs.cacert];
      config = {
        Entrypoint = ["${containerEntrypoint}/bin/jump-client-container"];
        WorkingDir = "/runtime";
        Env = [
          "JUMP_CLIENT_RUNTIME=/runtime"
          "JUMP_CLIENT_READINESS_FILE=/runtime/ready"
          "JUMP_PAPER_ADDRESS=jump-paper:25565"
          "JUMP_TRAINER_BIND=0.0.0.0"
          "JUMP_TRAINER_PORT=64123"
          "JUMP_CLIENT_XMS=512m"
          "JUMP_CLIENT_XMX=1536m"
        ];
      };
    };
  in {
    packages = {
      default = clientMod;
      mod = clientMod;
      fabric-api = fabricApi;
      headlessmc = headlessMc;
      oci = oci;
      container = oci;
    };

    _module.args.clientArtifacts = {
      inherit clientMod containerEntrypoint fabricApi headlessMc oci protobufJava;
    };
  };
}
