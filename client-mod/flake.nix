{
  description = "Fabric 26.2 tick-synchronous one-block jump client";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/ffb3c9b700e759be2ef13237c9d8f953b32a1e46";
    flake-parts.url = "github:hercules-ci/flake-parts/427bf4bd9435fdf21321c8cc628c24efc14c0f7a";
    protocol = {
      url = "path:../protocol";
      flake = false;
    };
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
