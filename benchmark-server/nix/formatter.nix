{...}: {
  perSystem = {pkgs, ...}: {
    formatter = pkgs.writeShellApplication {
      name = "format-jump-benchmark-server";
      runtimeInputs = [pkgs.alejandra pkgs.google-java-format pkgs.ktlint];
      text = ''
        target="''${1:-.}"
        if [[ -d "$target" ]]; then
          find "$target" -type f -name '*.nix' -print0 | xargs -0 -r alejandra --quiet
          find "$target" -type f -name '*.java' -print0 | xargs -0 -r google-java-format -i
          find "$target" -type f \( -name '*.gradle.kts' -o -name 'settings.gradle.kts' \) \
            -print0 | xargs -0 -r ktlint --format
        else
          case "$target" in
            *.nix) alejandra --quiet "$target" ;;
            *.java) google-java-format -i "$target" ;;
            *.gradle.kts) ktlint --format "$target" ;;
          esac
        fi
      '';
    };
  };
}
