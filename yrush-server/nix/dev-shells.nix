{...}: {
  perSystem = {pkgs, ...}: {
    devShells.default = pkgs.mkShellNoCC {
      packages = [
        pkgs.alejandra
        pkgs.git
        pkgs.jdk25
        pkgs.shellcheck
      ];
      shellHook = ''
        echo "Paper 26.2 and YRush v1.3.1 packaging shell (Java 25)"
      '';
    };
  };
}
