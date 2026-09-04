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
    hmcOptimizationsSource = pkgs.fetchFromGitHub {
      owner = "reuben-harris";
      repo = "hmc-optimizations";
      rev = "4e35bccefe6d38b2ba8ac6f805ccdd917187e72c";
      hash = "sha256-J6AFeB7UWYHV7t4/fve6HSzKQOVO8hqtJgFDVohhXIc=";
    };
    protobufJava = pkgs.fetchurl {
      url = "https://repo.maven.apache.org/maven2/com/google/protobuf/protobuf-java/4.35.1/protobuf-java-4.35.1.jar";
      hash = "sha256-pDRboqoAmRL/b5BGf+otEEYFJWtyxQhA118TJWY4pHI=";
    };
    gsonJava = pkgs.fetchurl {
      url = "https://repo.maven.apache.org/maven2/com/google/code/gson/gson/2.14.0/gson-2.14.0.jar";
      hash = "sha256-LL0Rm/GWHCh4gxCWPcgLpl9Yze7B3ROci9sSQPqiw28=";
    };
    clientMod = pkgs.stdenvNoCC.mkDerivation (finalAttrs: {
      pname = "yrush-client-mod";
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
        mkdir -p "$out/share/yrush-client"
        cp build/libs/yrush-client-1.0.0.jar \
          "$out/share/yrush-client/yrush-client.jar"
        runHook postInstall
      '';
    });
    hmcOptimizations = pkgs.stdenvNoCC.mkDerivation (finalAttrs: {
      pname = "hmc-optimizations-fabric";
      version = "0.5.0";
      src = hmcOptimizationsSource;
      sourceRoot = "source/26_2";
      nativeBuildInputs = [pkgs.gradle_9 pkgs.jdk25_headless];
      gradleBuildTask = ":fabric:build";
      gradleUpdateTask = ":fabric:build";

      # The client and optimization projects use the same Minecraft, Loader,
      # Loom, Gradle, and Java versions, so one dependency lock is canonical.
      mitmCache = pkgs.gradle_9.fetchDeps {
        pkg = finalAttrs.finalPackage;
        data = ../deps.json;
      };

      postPatch = ''
        substituteInPlace settings.gradle \
          --replace-fail 'include("fabric", "forge", "neoforge")' 'include("fabric")'
      '';

      __darwinAllowLocalNetworking = true;

      installPhase = ''
        runHook preInstall
        mkdir -p "$out/share/hmc-optimizations"
        cp build/libs/hmc-optimizations-26.2-0.5.0-fabric.jar \
          "$out/share/hmc-optimizations/hmc-optimizations.jar"
        runHook postInstall
      '';
    });
    containerEntrypoint = pkgs.writeShellApplication {
      name = "yrush-client-container";
      runtimeInputs = [pkgs.coreutils pkgs.jdk25_headless];
      text = ''
        runtime_dir="''${YRUSH_CLIENT_RUNTIME:-/runtime}"
        game_dir="$runtime_dir/game"
        readiness_file="''${YRUSH_CLIENT_READINESS_FILE:-$runtime_dir/ready}"
        paper_address="''${YRUSH_PAPER_ADDRESS:-yrush-paper:25565}"
        trainer_bind="''${YRUSH_TRAINER_BIND:-0.0.0.0}"
        trainer_port="''${YRUSH_TRAINER_PORT:-64123}"
        client_xms="''${YRUSH_CLIENT_XMS:-192m}"
        client_xmx="''${YRUSH_CLIENT_XMX:-320m}"
        pod_name="''${POD_NAME:-yrush-client-0}"
        ordinal="''${pod_name##*-}"
        if [[ ! "$ordinal" =~ ^[0-9]+$ ]]; then
          echo "cannot derive StatefulSet ordinal from $pod_name" >&2
          exit 2
        fi
        client_username="''${YRUSH_CLIENT_USERNAME:-yrushbot-$ordinal}"
        if [[ "''${YRUSH_ENTRYPOINT_VALIDATE:-0}" == 1 ]]; then
          printf 'username=%s bind=%s:%s paper=%s readiness=%s\n' \
            "$client_username" "$trainer_bind" "$trainer_port" "$paper_address" "$readiness_file"
          exit 0
        fi

        mkdir -p "$runtime_dir/HeadlessMC" "$game_dir/config" "$game_dir/mods"
        rm -f "$readiness_file" "$readiness_file.tmp"
        cp -f ${headlessMc} "$runtime_dir/headlessmc.jar"
        ln -sfn ${clientMod}/share/yrush-client/yrush-client.jar \
          "$game_dir/mods/yrush-client.jar"
        ln -sfn ${fabricApi} "$game_dir/mods/fabric-api.jar"
        ln -sfn ${hmcOptimizations}/share/hmc-optimizations/hmc-optimizations.jar \
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
        export ALSOFT_DRIVERS=null SDL_AUDIODRIVER=dummy OPENAL_SOFT_LOGLEVEL=0
        cd "$runtime_dir"
        game_args=("$@")
        command_line="launch fabric:26.2 --uid 0.19.3 -offline -lwjgl -keep --jvm \"-Djava.awt.headless=true -Dhmc.optimizations.enabled=true -Dhmc.optimizations.render=true -Dhmc.optimizations.world_render_state=true -Dhmc.optimizations.particles=true -Dhmc.optimizations.sound=true -Dhmc.optimizations.lighting=true -Dhmc.optimizations.animated_textures=true -Dhmc.optimizations.chunk_mesh=true -Dhmc.optimizations.render_buffers=true -Dhmc.optimizations.render_resources=false -Dyrush.client.offline=true -Dyrush.client.bind=$trainer_bind -Dyrush.client.port=$trainer_port -Dyrush.client.server=$paper_address -Dyrush.client.readinessFile=$readiness_file -Xms$client_xms -Xmx$client_xmx\" --game-args \"''${game_args[*]}\""
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
        Entrypoint = ["${containerEntrypoint}/bin/yrush-client-container"];
        WorkingDir = "/runtime";
        Env = [
          "PATH=${pkgs.lib.makeBinPath [pkgs.coreutils pkgs.jdk25_headless]}"
          "YRUSH_CLIENT_RUNTIME=/runtime"
          "YRUSH_CLIENT_READINESS_FILE=/runtime/ready"
          "YRUSH_PAPER_ADDRESS=yrush-paper:25565"
          "YRUSH_TRAINER_BIND=0.0.0.0"
          "YRUSH_TRAINER_PORT=64123"
          "YRUSH_CLIENT_XMS=192m"
          "YRUSH_CLIENT_XMX=320m"
        ];
      };
    };
  in {
    packages = {
      default = clientMod;
      mod = clientMod;
      fabric-api = fabricApi;
      headlessmc = headlessMc;
      hmc-optimizations = hmcOptimizations;
      oci = oci;
      container = oci;
    };

    _module.args.clientArtifacts = {
      inherit
        clientMod
        containerEntrypoint
        fabricApi
        gsonJava
        headlessMc
        hmcOptimizations
        oci
        protobufJava
        ;
    };
  };
}
