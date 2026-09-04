{...}: {
  perSystem = {pkgs, ...}: {
    devShells.default = pkgs.mkShellNoCC {
      packages = [
        pkgs.alejandra
        pkgs.buf
        pkgs.git
        pkgs.protobuf
        pkgs.python3
      ];
      shellHook = ''
        echo "YRush protocol development shell (Buf + protoc)"
      '';
    };
  };
}
