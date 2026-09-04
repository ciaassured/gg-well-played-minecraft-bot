{...}: {
  perSystem = {pkgs, ...}: {
    formatter = pkgs.writeShellApplication {
      name = "format-yrush-server";
      runtimeInputs = [pkgs.alejandra];
      text = ''
        target="''${1:-.}"
        if [[ -d "$target" ]]; then
          find "$target" -type f -name '*.nix' -print0 | xargs -0 -r alejandra --quiet
        elif [[ "$target" == *.nix ]]; then
          alejandra --quiet "$target"
        fi
      '';
    };
  };
}
