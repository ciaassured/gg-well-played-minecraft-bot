{...}: {
  perSystem = {rendererArtifacts, ...}: {
    apps.render = {
      type = "app";
      program = "${rendererArtifacts.rendererCli}/bin/jump-replay-renderer";
      meta.description = "Validate and render Replay Mod recordings to playable MP4 files";
    };
    apps.default = {
      type = "app";
      program = "${rendererArtifacts.rendererCli}/bin/jump-replay-renderer";
      meta.description = "Validate and render Replay Mod recordings to playable MP4 files";
    };
  };
}
