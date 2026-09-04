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
        "build    Link the Nix-built OCI archive to OUTPUT." \
        "load     Load the archive into containers-storage (Podman) as TAG." \
        "publish  Publish the archive to its fixed GHCR repository as TAG." \
        "" \
        "Set YRUSH_LOCAL_IMAGE_TRANSPORT=docker-daemon to load into Docker."
    }

    validate_tag() {
      local tag="$1"
      if [[ ! "$tag" =~ ^[[:alnum:]_][[:alnum:]_.-]{0,127}$ ]]; then
        echo "invalid OCI image tag: $tag" >&2
        exit 2
      fi
    }

    action="''${1:-build}"
    if (( $# > 0 )); then
      shift
    fi

    case "$action" in
      build)
        if (( $# > 1 )); then
          usage >&2
          exit 2
        fi
        output="''${1:-result-$component-image}"
        if [[ -z "$output" || "$output" == / ]]; then
          echo "refusing invalid image archive output: $output" >&2
          exit 2
        fi
        if [[ -d "$output" && ! -L "$output" ]]; then
          echo "image archive output is an existing directory: $output" >&2
          exit 2
        fi
        mkdir -p -- "$(dirname -- "$output")"
        ln -sfnT -- "$image_archive" "$output"
        printf '%s -> %s\n' "$output" "$image_archive"
        ;;
      load)
        if (( $# > 1 )); then
          usage >&2
          exit 2
        fi
        tag="''${1:-dev}"
        validate_tag "$tag"
        local_transport="''${YRUSH_LOCAL_IMAGE_TRANSPORT:-containers-storage}"
        case "$local_transport" in
          containers-storage|docker-daemon) ;;
          *)
            echo "YRUSH_LOCAL_IMAGE_TRANSPORT must be containers-storage or docker-daemon" >&2
            exit 2
            ;;
        esac
        if [[ "$local_transport" == containers-storage ]]; then
          podman load --input "$image_archive"
          podman tag "$image_name:unstable" "$image_name:$tag"
        else
          destination="$local_transport:$image_name:$tag"
          skopeo copy --retry-times 3 "docker-archive:$image_archive" "$destination"
        fi
        printf 'loaded %s:%s via %s\n' "$image_name" "$tag" "$local_transport"
        ;;
      publish)
        if (( $# != 1 )); then
          usage >&2
          exit 2
        fi
        tag="$1"
        validate_tag "$tag"
        if [[ -z "''${GHCR_USER:-}" || -z "''${GHCR_TOKEN:-}" ]]; then
          echo "publish requires GHCR_USER and GHCR_TOKEN" >&2
          exit 2
        fi
        registry="''${image_name%%/*}"
        auth_dir="$(mktemp -d -t yrush-image-auth.XXXXXX)"
        auth_file="$auth_dir/auth.json"
        cleanup() {
          rm -f -- "$auth_file"
          rmdir -- "$auth_dir" 2>/dev/null || true
        }
        trap cleanup EXIT
        printf '%s' "$GHCR_TOKEN" | skopeo login \
          --authfile "$auth_file" \
          --username "$GHCR_USER" \
          --password-stdin \
          "$registry" >/dev/null
        skopeo copy --retry-times 3 \
          --authfile "$auth_file" \
          "docker-archive:$image_archive" \
          "docker://$image_name:$tag"
        printf 'published %s:%s\n' "$image_name" "$tag"
        ;;
      help|-h|--help)
        usage
        ;;
      *)
        echo "unknown image action: $action" >&2
        usage >&2
        exit 2
        ;;
    esac
  '';
}
