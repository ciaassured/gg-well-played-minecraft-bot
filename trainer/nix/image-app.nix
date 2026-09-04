{
  pkgs,
  commandName,
  component,
  imageArchive,
  imageName,
}:
pkgs.writeShellApplication {
  name = commandName;
  runtimeInputs = [pkgs.coreutils pkgs.podman pkgs.skopeo];
  text = ''
    command_name=${pkgs.lib.escapeShellArg commandName}
    component=${pkgs.lib.escapeShellArg component}
    image_archive=${pkgs.lib.escapeShellArg "${imageArchive}"}
    image_name=${pkgs.lib.escapeShellArg imageName}

    usage() {
      printf '%s\n' \
        "Usage:" \
        "  $command_name [build [OUTPUT]]" \
        "  $command_name load [TAG]" \
        "  $command_name publish TAG" \
        "" \
        "Set YRUSH_LOCAL_IMAGE_TRANSPORT=docker-daemon to load into Docker."
    }

    validate_tag() {
      local tag="$1"
      [[ "$tag" =~ ^[[:alnum:]_][[:alnum:]_.-]{0,127}$ ]] || {
        echo "invalid OCI image tag: $tag" >&2
        exit 2
      }
    }

    action="''${1:-build}"
    (( $# == 0 )) || shift
    case "$action" in
      build)
        (( $# <= 1 )) || { usage >&2; exit 2; }
        output="''${1:-result-$component-image}"
        [[ -n "$output" && "$output" != / ]] || {
          echo "refusing invalid image archive output: $output" >&2
          exit 2
        }
        [[ ! -d "$output" || -L "$output" ]] || {
          echo "image archive output is an existing directory: $output" >&2
          exit 2
        }
        mkdir -p -- "$(dirname -- "$output")"
        ln -sfnT -- "$image_archive" "$output"
        printf '%s -> %s\n' "$output" "$image_archive"
        ;;
      load)
        (( $# <= 1 )) || { usage >&2; exit 2; }
        tag="''${1:-dev}"
        validate_tag "$tag"
        transport="''${YRUSH_LOCAL_IMAGE_TRANSPORT:-containers-storage}"
        case "$transport" in
          containers-storage)
            podman load --input "$image_archive"
            podman tag "$image_name:unstable" "$image_name:$tag"
            ;;
          docker-daemon)
            skopeo copy --retry-times 3 \
              "docker-archive:$image_archive" "$transport:$image_name:$tag"
            ;;
          *)
            echo "YRUSH_LOCAL_IMAGE_TRANSPORT must be containers-storage or docker-daemon" >&2
            exit 2
            ;;
        esac
        printf 'loaded %s:%s via %s\n' "$image_name" "$tag" "$transport"
        ;;
      publish)
        (( $# == 1 )) || { usage >&2; exit 2; }
        tag="$1"
        validate_tag "$tag"
        [[ -n "''${GHCR_USER:-}" && -n "''${GHCR_TOKEN:-}" ]] || {
          echo "publish requires GHCR_USER and GHCR_TOKEN" >&2
          exit 2
        }
        registry="''${image_name%%/*}"
        auth_dir="$(mktemp -d -t yrush-image-auth.XXXXXX)"
        auth_file="$auth_dir/auth.json"
        cleanup() {
          rm -f -- "$auth_file"
          rmdir -- "$auth_dir" 2>/dev/null || true
        }
        trap cleanup EXIT
        printf '%s' "$GHCR_TOKEN" | skopeo login \
          --authfile "$auth_file" --username "$GHCR_USER" --password-stdin \
          "$registry" >/dev/null
        skopeo copy --retry-times 3 --authfile "$auth_file" \
          "docker-archive:$image_archive" "docker://$image_name:$tag"
        printf 'published %s:%s\n' "$image_name" "$tag"
        ;;
      help|-h|--help) usage ;;
      *)
        echo "unknown image action: $action" >&2
        usage >&2
        exit 2
        ;;
    esac
  '';
}
