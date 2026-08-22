{inputs, ...}: {
  perSystem = {
    pkgs,
    serverArtifacts,
    ...
  }: {
    checks = {
      core-tests =
        pkgs.runCommand "jump-server-core-tests" {
          nativeBuildInputs = [pkgs.jdk25_headless];
          src = ../.;
        } ''
          cp -R "$src" source
          chmod -R u+w source
          cd source
          mkdir classes
          find src/main/java/gg/wellplayed/jump/server/core \
            src/test/java/gg/wellplayed/jump/server/core \
            -type f -name '*.java' | sort > sources.txt
          javac --release 25 -d classes @sources.txt
          java -ea -cp classes gg.wellplayed.jump.server.core.CoreTestMain
          touch "$out"
        '';

      protocol-generation =
        pkgs.runCommand "jump-server-protocol-generation" {
          nativeBuildInputs = [pkgs.jdk25_headless pkgs.protobuf];
        } ''
          mkdir -p generated/classes
          protoc \
            --proto_path=${inputs.protocol}/proto \
            --java_out=generated \
            ${inputs.protocol}/proto/jump/v1/jump.proto
          test -f generated/gg/wellplayed/jump/protocol/v1/WireMessage.java
          grep -q 'class ResetRequest' \
            generated/gg/wellplayed/jump/protocol/v1/ResetRequest.java
          touch "$out"
        '';

      plugin-build = serverArtifacts.plugin;

      protobuf-isolation =
        pkgs.runCommand "jump-server-protobuf-isolation" {
          nativeBuildInputs = [pkgs.jdk25_headless];
        } ''
          jar tf \
            ${serverArtifacts.plugin}/share/jump-benchmark-server/jump-benchmark-paper.jar \
            > contents.txt
          grep -q \
            '^gg/wellplayed/jump/server/internal/protobuf/MessageLite.class$' \
            contents.txt
          if grep -q '^com/google/protobuf/' contents.txt; then
            echo "unrelocated protobuf classes would collide with Paper" >&2
            exit 1
          fi
          touch "$out"
        '';
    };
  };
}
