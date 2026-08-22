{...}: {
  perSystem = {pkgs, ...}: {
    formatter = pkgs.writeShellApplication {
      name = "format-minecraft-jump-trainer";
      runtimeInputs = [pkgs.alejandra pkgs.ruff pkgs.taplo];
      text = ''
        target="''${1:-.}"
        if [[ -d "$target" ]]; then
          find "$target" -type f -name '*.nix' \
            -not -path '*/.generated/*' -not -path '*/runs/*' \
            -print0 | xargs -0 -r alejandra --quiet
          find "$target" -type f -name '*.py' \
            -not -path '*/.generated/*' -not -path '*/runs/*' \
            -print0 | xargs -0 -r ruff format
          find "$target" -type f -name '*.toml' \
            -not -path '*/.generated/*' -not -path '*/runs/*' \
            -print0 | xargs -0 -r taplo format
        else
          case "$target" in
            *.nix) alejandra --quiet "$target" ;;
            *.py) ruff format "$target" ;;
            *.toml) taplo format "$target" ;;
          esac
        fi
      '';
    };
  };
}
