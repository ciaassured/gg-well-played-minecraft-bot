{...}: {
  perSystem = {pkgs, ...}: let
    fabricApi = pkgs.fetchurl {
      url = "https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/0.155.2+26.2/fabric-api-0.155.2+26.2.jar";
      hash = "sha256-1lGMdwAky+ilViSPFvzbuRxqYvUCJ6bDuugZBRHiwbg=";
    };
    replayMod = pkgs.fetchurl {
      url = "https://www.replaymod.com/download/download_new.php?version=26.2-2.6.27";
      hash = "sha256-aKGLtyXqZcumIXpqk6/YVRuWnDbIeNRf6/5FTSRYaZk=";
      name = "replaymod-26.2-2.6.27.jar";
    };
    headlessMc = pkgs.fetchurl {
      url = "https://github.com/3arthqu4ke/HeadlessMc/releases/download/2.10.0/headlessmc-launcher-wrapper-2.10.0.jar";
      hash = "sha256-v4DYRRbu65pR+jWJTERmsUbVXQFGzW0q3En90jFlRTY=";
    };
    rendererModSource = pkgs.lib.cleanSourceWith {
      src = ../.;
      filter = path: type: let
        relative = pkgs.lib.removePrefix (toString ../. + "/") (toString path);
      in
        type
        == "directory"
        || pkgs.lib.hasPrefix "src/main/" relative
        || pkgs.lib.hasPrefix "src/test/java/" relative
        || builtins.elem relative [
          "build.gradle.kts"
          "gradle.properties"
          "settings.gradle.kts"
        ];
    };
    rendererMod = pkgs.stdenvNoCC.mkDerivation (finalAttrs: {
      pname = "jump-replay-renderer-mod";
      version = "1.0.0";
      src = rendererModSource;

      nativeBuildInputs = [pkgs.gradle_9 pkgs.jdk25_headless];
      gradleFlags = ["-PreplayModJar=${replayMod}"];
      gradleBuildTask = "assemble";
      gradleUpdateTask = "assemble";

      mitmCache = pkgs.gradle_9.fetchDeps {
        pkg = finalAttrs.finalPackage;
        data = ../deps.json;
      };

      __darwinAllowLocalNetworking = true;

      installPhase = ''
        runHook preInstall
        mkdir -p "$out/share/jump-replay-renderer"
        cp build/libs/jump-replay-renderer-mod-1.0.0.jar \
          "$out/share/jump-replay-renderer/renderer-mod.jar"
        runHook postInstall
      '';
    });
    replayConfig = pkgs.writeText "jump-renderer-replaymod.json" ''
      {
        "core": {
          "notifications": false
        },
        "recording": {
          "recordServer": false,
          "autoStartRecording": false,
          "indicator": false,
          "renameDialog": false
        }
      }
    '';
    minecraftRuntimeLibraries = [
      (pkgs.lib.getLib pkgs.stdenv.cc.cc)
      pkgs.alsa-lib
      pkgs.glfw3-minecraft
      pkgs.libGL
      pkgs.libjack2
      pkgs.libpulseaudio
      pkgs.libx11
      pkgs.libxcursor
      pkgs.libxext
      pkgs.libxrandr
      pkgs.libxxf86vm
      pkgs.mesa
      pkgs.openal
      pkgs.pipewire
      pkgs.udev
      pkgs.vulkan-loader
    ];
    minecraftLibraryPath = "${pkgs.addDriverRunpath.driverLink}/lib:${pkgs.lib.makeLibraryPath minecraftRuntimeLibraries}";
    minecraftLauncher = pkgs.writeShellApplication {
      name = "jump-replay-minecraft";
      runtimeInputs = [
        pkgs.coreutils
        pkgs.ffmpeg
        pkgs.findutils
        pkgs.jdk25_headless
        pkgs.pciutils
        pkgs.util-linux
        pkgs.xrandr
        pkgs.xvfb-run
      ];
      text = ''
        input=""
        output=""
        status=""
        width=640
        height=360
        fps=20
        bitrate=4000000
        start_ms=0
        end_ms=-1
        camera=first-person
        camera_x="''${JUMP_RENDERER_CAMERA_X:-20.0}"
        camera_y="''${JUMP_RENDERER_CAMERA_Y:-66.5}"
        camera_z="''${JUMP_RENDERER_CAMERA_Z:-0.5}"
        camera_yaw="''${JUMP_RENDERER_CAMERA_YAW:-90.0}"
        camera_pitch="''${JUMP_RENDERER_CAMERA_PITCH:-15.0}"

        while (($#)); do
          case "$1" in
            --input) input="''${2:?--input requires a path}"; shift 2 ;;
            --output) output="''${2:?--output requires a path}"; shift 2 ;;
            --status) status="''${2:?--status requires a path}"; shift 2 ;;
            --width) width="''${2:?--width requires a value}"; shift 2 ;;
            --height) height="''${2:?--height requires a value}"; shift 2 ;;
            --fps) fps="''${2:?--fps requires a value}"; shift 2 ;;
            --bitrate) bitrate="''${2:?--bitrate requires a value}"; shift 2 ;;
            --start-ms) start_ms="''${2:?--start-ms requires a value}"; shift 2 ;;
            --end-ms) end_ms="''${2:?--end-ms requires a value}"; shift 2 ;;
            --camera) camera="''${2:?--camera requires a value}"; shift 2 ;;
            *) echo "unknown renderer argument: $1" >&2; exit 2 ;;
          esac
        done

        if [[ -z "$input" || -z "$output" || -z "$status" ]]; then
          echo "--input, --output, and --status are required" >&2
          exit 2
        fi
        if [[ "$camera" != first-person && "$camera" != third-person && "$camera" != fixed ]]; then
          echo "--camera must be first-person, third-person, or fixed" >&2
          exit 2
        fi

        input="$(realpath "$input")"
        output="$(realpath -m "$output")"
        status="$(realpath -m "$status")"
        runtime_dir="''${JUMP_RENDERER_RUNTIME:-$PWD/replay-renderer/runtime}"
        mkdir -p "$runtime_dir" "$(dirname "$output")" "$(dirname "$status")"

        exec 9>"$runtime_dir/render.lock"
        flock 9

        game_dir="$runtime_dir/game"
        job_dir="$(mktemp -d "$runtime_dir/render-job.XXXXXX")"
        trap 'rm -rf "$job_dir"' EXIT
        mkdir -p "$runtime_dir/HeadlessMC" "$game_dir/config" "$game_dir/mods"
        cp "$input" "$job_dir/input.mcpr"
        cp -f ${headlessMc} "$runtime_dir/headlessmc.jar"
        cp -f ${replayConfig} "$game_dir/config/replaymod.json"
        ln -sfn ${rendererMod}/share/jump-replay-renderer/renderer-mod.jar \
          "$game_dir/mods/jump-replay-renderer.jar"
        ln -sfn ${fabricApi} "$game_dir/mods/fabric-api.jar"
        ln -sfn ${replayMod} "$game_dir/mods/replaymod.jar"

        chmod -R u+w "$runtime_dir"
        java_path="$(command -v java)"
        {
          echo "hmc.mcdir=$runtime_dir/minecraft"
          echo "hmc.gamedir=$game_dir"
          echo "hmc.java.versions=$java_path"
          echo "hmc.offline=true"
          echo "hmc.check.xvfb=true"
          echo "hmc.assets.dummy=true"
          echo "hmc.jline.enabled=false"
          echo "hmc.exit.on.failed.command=true"
          echo "hmc.auto.download.java=false"
          echo "hmc.java.use.current=true"
          echo "hmc.auto.download.specifics=false"
        } > "$runtime_dir/HeadlessMC/config.properties"
        {
          echo "pauseOnLostFocus:false"
          echo "onboardAccessibility:false"
          echo "narrator:0"
          echo "enableVsync:false"
          echo "maxFps:20"
          echo "gamma:1.0"
          echo "soundCategory_master:0.0"
        } > "$game_dir/options.txt"

        export ALSOFT_DRIVERS=null
        export SDL_AUDIODRIVER=dummy
        export OPENAL_SOFT_LOGLEVEL=0
        export LD_LIBRARY_PATH=${minecraftLibraryPath}
        export LIBGL_ALWAYS_SOFTWARE=1
        export LIBGL_DRIVERS_PATH=${pkgs.mesa}/lib/dri
        export MESA_LOADER_DRIVER_OVERRIDE=llvmpipe
        cd "$runtime_dir"
        command_line="launch fabric:26.2 --uid 0.19.3 -offline -keep --jvm \"-Djava.awt.headless=true -Djump.renderer.input=$job_dir/input.mcpr -Djump.renderer.output=$job_dir/output.mp4 -Djump.renderer.status=$job_dir/status.txt -Djump.renderer.ffmpeg=${pkgs.ffmpeg}/bin/ffmpeg -Djump.renderer.width=$width -Djump.renderer.height=$height -Djump.renderer.fps=$fps -Djump.renderer.bitrate=$bitrate -Djump.renderer.startMillis=$start_ms -Djump.renderer.endMillis=$end_ms -Djump.renderer.camera=$camera -Djump.renderer.cameraX=$camera_x -Djump.renderer.cameraY=$camera_y -Djump.renderer.cameraZ=$camera_z -Djump.renderer.cameraYaw=$camera_yaw -Djump.renderer.cameraPitch=$camera_pitch -Xms512m -Xmx3g\" --game-args \"--width $width --height $height\""
        set +e
        printf '%s\n' "$command_line" \
          | xvfb-run -a -s "-screen 0 ''${width}x''${height}x24 +extension GLX +render -noreset" \
            java --enable-native-access=ALL-UNNAMED -jar headlessmc.jar
        minecraft_exit=$?
        set -e

        if [[ -f "$job_dir/output.mp4" ]]; then
          cp -f "$job_dir/output.mp4" "$output"
        fi
        if [[ -f "$job_dir/status.txt" ]]; then
          cp -f "$job_dir/status.txt" "$status"
        fi
        exit "$minecraft_exit"
      '';
    };
    rendererCli = pkgs.python313Packages.buildPythonApplication {
      pname = "jump-replay-renderer";
      version = "1.0.0";
      src = ../.;
      pyproject = true;
      build-system = [pkgs.python313Packages.setuptools];
      nativeBuildInputs = [pkgs.makeWrapper];
      doCheck = false;
      pythonImportsCheck = ["jump_replay_renderer"];
      postFixup = ''
        wrapProgram "$out/bin/jump-replay-renderer" \
          --set-default JUMP_RENDERER_MINECRAFT ${minecraftLauncher}/bin/jump-replay-minecraft \
          --set-default JUMP_RENDERER_FFPROBE ${pkgs.ffmpeg}/bin/ffprobe
      '';
    };
  in {
    packages = {
      default = rendererCli;
      cli = rendererCli;
      mod = rendererMod;
      runtime = minecraftLauncher;
      fabric-api = fabricApi;
      replay-mod = replayMod;
      headlessmc = headlessMc;
    };

    _module.args.rendererArtifacts = {
      inherit fabricApi headlessMc minecraftLauncher rendererCli rendererMod replayMod;
    };
  };
}
