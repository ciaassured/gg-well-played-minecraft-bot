{...}: {
  perSystem = {pkgs, ...}: {
    formatter = pkgs.writeShellApplication {
      name = "format-jump-replay-renderer";
      runtimeInputs = [pkgs.alejandra pkgs.google-java-format pkgs.ktlint pkgs.ruff];
      text = ''
        target="''${1:-.}"
        if [[ -d "$target" ]]; then
          find "$target" -type f -name '*.nix' \
            -not -path '*/build/*' -not -path '*/runtime/*' -not -path '*/.gradle/*' \
            -print0 | xargs -0 -r alejandra --quiet
          find "$target" -type f -name '*.java' \
            -not -path '*/build/*' -not -path '*/runtime/*' -not -path '*/.gradle/*' \
            -print0 | xargs -0 -r google-java-format -i
          find "$target" -type f \( -name '*.gradle.kts' -o -name 'settings.gradle.kts' \) \
            -not -path '*/build/*' -not -path '*/runtime/*' -not -path '*/.gradle/*' \
            -print0 | xargs -0 -r ktlint --format
          ruff format "$target/src" "$target/tests"
        else
          case "$target" in
            *.nix) alejandra --quiet "$target" ;;
            *.java) google-java-format -i "$target" ;;
            *.gradle.kts) ktlint --format "$target" ;;
            *.py) ruff format "$target" ;;
          esac
        fi
      '';
    };
  };
}
