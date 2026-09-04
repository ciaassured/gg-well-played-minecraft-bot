{...}: {
  perSystem = {pkgs, ...}: {
    formatter = pkgs.writeShellApplication {
      name = "format-yrush-protocol";
      runtimeInputs = [pkgs.alejandra pkgs.buf];
      text = ''
        target="''${1:-.}"
        if [[ -d "$target" ]]; then
          find "$target" -type f -name '*.nix' -print0 | xargs -0 -r alejandra --quiet
          find "$target" -type f -name '*.proto' -print0 | xargs -0 -r buf format -w
        else
          case "$target" in
            *.nix) alejandra --quiet "$target" ;;
            *.proto) buf format -w "$target" ;;
          esac
        fi
      '';
    };
  };
}
