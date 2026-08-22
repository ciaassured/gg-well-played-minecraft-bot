{
  description = "Versioned Protobuf contract for the one-block jump benchmark";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/ffb3c9b700e759be2ef13237c9d8f953b32a1e46";
    flake-parts.url = "github:hercules-ci/flake-parts/427bf4bd9435fdf21321c8cc628c24efc14c0f7a";
  };

  outputs = inputs @ {flake-parts, ...}:
    flake-parts.lib.mkFlake {inherit inputs;} {
      systems = ["x86_64-linux"];
      imports = [
        ./nix/packages.nix
        ./nix/apps.nix
        ./nix/checks.nix
        ./nix/dev-shells.nix
        ./nix/formatter.nix
      ];
    };
}
