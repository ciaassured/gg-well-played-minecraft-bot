{inputs, ...}: {
  perSystem = {pkgs, ...}: let
    paperApi = pkgs.fetchurl {
      url = "https://repo.papermc.io/repository/maven-public/io/papermc/paper/paper-api/26.2.build.112-stable/paper-api-26.2.build.112-stable.jar";
      hash = "sha256-ccUINIypXw4zoVnV2lrTUcrqX8hmBnwGrKDWebxQZ2M=";
    };
    paperServer = pkgs.fetchurl {
      url = "https://fill-data.papermc.io/v1/objects/bd3a58cf96874e5ea6643f5f6fe9b4f5bf9e34b795fa078c2f0ee8b98b2f907e/paper-26.2-112.jar";
      hash = "sha256-vTpYz5aHTl6mZD9fb+m09b+eNLeV+geMLw7ouYsvkH4=";
    };
    protobufJava = pkgs.fetchurl {
      url = "https://repo.maven.apache.org/maven2/com/google/protobuf/protobuf-java/4.35.1/protobuf-java-4.35.1.jar";
      hash = "sha256-pDRboqoAmRL/b5BGf+otEEYFJWtyxQhA118TJWY4pHI=";
    };
    plugin = (pkgs.maven.override {jdk_headless = pkgs.jdk25_headless;}).buildMavenPackage {
      pname = "jump-benchmark-paper-plugin";
      version = "1.0.0";
      src = ../.;
      mvnJdk = pkgs.jdk25_headless;
      mvnHash = "sha256-hyp8xWubxNBMP7n+aSe0Rm9RW7jvZQqhlIMhCOys6R0=";
      nativeBuildInputs = [pkgs.protobuf];
      doCheck = false;

      postPatch = ''
        mkdir -p src/main/java
        protoc \
          --proto_path=${inputs.protocol}/proto \
          --java_out=src/main/java \
          ${inputs.protocol}/proto/jump/v1/jump.proto
      '';

      installPhase = ''
        runHook preInstall
        mkdir -p "$out/share/jump-benchmark-server"
        cp target/jump-benchmark-paper-1.0.0.jar \
          "$out/share/jump-benchmark-server/jump-benchmark-paper.jar"
        runHook postInstall
      '';
    };
    serverPackage = pkgs.runCommand "jump-benchmark-server-1.0.0" {} ''
      mkdir -p "$out/share/jump-benchmark-server"
      cp ${paperServer} "$out/share/jump-benchmark-server/paper-26.2-112.jar"
      cp ${plugin}/share/jump-benchmark-server/jump-benchmark-paper.jar \
        "$out/share/jump-benchmark-server/"
    '';
    containerProperties = pkgs.writeText "jump-container-server.properties" ''
      allow-flight=false
      allow-nether=false
      difficulty=peaceful
      enable-command-block=false
      enforce-secure-profile=false
      force-gamemode=true
      gamemode=adventure
      generate-structures=false
      generator-settings={"layers":[{"block":"minecraft:bedrock","height":1}],"biome":"minecraft:plains"}
      level-name=jump-benchmark
      level-type=minecraft:flat
      max-players=@MAX_PLAYERS@
      motd=One-block jump benchmark
      online-mode=false
      pause-when-empty-seconds=-1
      player-idle-timeout=0
      server-port=25565
      simulation-distance=2
      spawn-animals=false
      spawn-monsters=false
      spawn-npcs=false
      spawn-protection=0
      sync-chunk-writes=false
      view-distance=2
      white-list=false
    '';
    containerEntrypoint = pkgs.writeShellApplication {
      name = "jump-server-container";
      runtimeInputs = [pkgs.coreutils pkgs.gnused pkgs.jdk25_headless];
      text = ''
        runtime_dir="''${JUMP_BENCHMARK_SERVER_RUNTIME:-/data}"
        client_count="''${JUMP_CLIENT_COUNT:-1}"
        if [[ ! "$client_count" =~ ^[1-9][0-9]*$ ]]; then
          echo "JUMP_CLIENT_COUNT must be a positive integer" >&2
          exit 2
        fi
        if [[ "''${JUMP_ENTRYPOINT_VALIDATE:-0}" == 1 ]]; then
          printf 'clients=%s heap=%s..%s runtime=%s\n' "$client_count" \
            "''${JUMP_SERVER_XMS:-512m}" "''${JUMP_SERVER_XMX:-1g}" "$runtime_dir"
          exit 0
        fi
        mkdir -p "$runtime_dir/plugins"
        ln -sfn ${serverPackage}/share/jump-benchmark-server/paper-26.2-112.jar \
          "$runtime_dir/paper.jar"
        ln -sfn ${serverPackage}/share/jump-benchmark-server/jump-benchmark-paper.jar \
          "$runtime_dir/plugins/jump-benchmark-paper.jar"
        printf 'eula=true\n' > "$runtime_dir/eula.txt"
        sed "s/@MAX_PLAYERS@/$client_count/" ${containerProperties} \
          > "$runtime_dir/server.properties"
        cd "$runtime_dir"
        exec java -Xms"''${JUMP_SERVER_XMS:-512m}" -Xmx"''${JUMP_SERVER_XMX:-1g}" \
          -jar paper.jar --nogui "$@"
      '';
    };
    oci = pkgs.dockerTools.buildLayeredImage {
      name = "ghcr.io/ciaassured/gg-well-played-minecraft-bot-server";
      tag = "unstable";
      maxLayers = 120;
      contents = [containerEntrypoint pkgs.cacert];
      config = {
        Entrypoint = ["${containerEntrypoint}/bin/jump-server-container"];
        WorkingDir = "/data";
        Env = [
          "JUMP_BENCHMARK_SERVER_RUNTIME=/data"
          "JUMP_CLIENT_COUNT=1"
          "JUMP_SERVER_XMS=512m"
          "JUMP_SERVER_XMX=1g"
        ];
      };
    };
  in {
    packages = {
      default = serverPackage;
      plugin = plugin;
      paper = paperServer;
      oci = oci;
      container = oci;
    };

    _module.args.serverArtifacts = {
      inherit containerEntrypoint oci paperApi paperServer plugin protobufJava serverPackage;
    };
  };
}
