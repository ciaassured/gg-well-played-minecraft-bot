{inputs, ...}: {
  perSystem = {
    pkgs,
    serverArtifacts,
    ...
  }: {
    devShells.default = pkgs.mkShellNoCC {
      packages = [
        pkgs.alejandra
        pkgs.git
        pkgs.google-java-format
        pkgs.gradle_9
        pkgs.jdk25
        pkgs.ktlint
        pkgs.protobuf
      ];
      PAPER_API_JAR = serverArtifacts.paperApi;
      PROTOBUF_JAVA_JAR = serverArtifacts.protobufJava;
      PROTOCOL_DIR = inputs.protocol;
      shellHook = ''
        echo "Paper 26.2 benchmark shell (Java 25, Gradle 9, protoc)"
      '';
    };
  };
}
