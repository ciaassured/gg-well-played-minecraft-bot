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
  in {
    packages = {
      default = serverPackage;
      plugin = plugin;
      paper = paperServer;
    };

    _module.args.serverArtifacts = {
      inherit paperApi paperServer plugin protobufJava serverPackage;
    };
  };
}
