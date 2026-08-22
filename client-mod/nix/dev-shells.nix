{inputs, ...}: {
  perSystem = {pkgs, ...}: {
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
      PROTOCOL_DIR = inputs.protocol;
      shellHook = ''
        echo "Fabric 26.2 client shell (Java 25, Loader 0.19.3, Loom 1.17.19)"
      '';
    };
  };
}
