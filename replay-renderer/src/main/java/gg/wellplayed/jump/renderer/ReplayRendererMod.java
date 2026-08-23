package gg.wellplayed.jump.renderer;

import com.replaymod.core.ReplayMod;
import com.replaymod.render.RenderSettings;
import com.replaymod.render.RenderSettings.AntiAliasing;
import com.replaymod.render.RenderSettings.EncodingPreset;
import com.replaymod.render.RenderSettings.RenderMethod;
import com.replaymod.render.rendering.VideoRenderer;
import com.replaymod.replay.ReplayHandler;
import com.replaymod.replay.ReplayModReplay;
import com.replaymod.simplepathing.InterpolatorType;
import com.replaymod.simplepathing.SPTimeline;
import gg.wellplayed.jump.renderer.core.RenderPlan;
import gg.wellplayed.jump.renderer.core.StatusFile;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.CameraType;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.player.Player;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Opens one replay, renders it through Replay Mod, records status, and exits Minecraft. */
public final class ReplayRendererMod implements ClientModInitializer {
  private static final Logger LOGGER = LoggerFactory.getLogger("jump-replay-renderer");
  private static final Duration STARTUP_TIMEOUT = Duration.ofSeconds(90);
  private static final int REPLAY_WARMUP_TICKS = 60;
  private static final double CAMERA_X = 11.5;
  private static final double CAMERA_Y = 304.0;
  private static final double CAMERA_Z = -8.0;
  private static final float CAMERA_YAW = 0.0F;
  private static final float CAMERA_PITCH = 15.0F;

  private final AtomicBoolean renderQueued = new AtomicBoolean();
  private boolean replayStartupCallbackRegistered;
  private boolean replayStartupComplete;
  private boolean replayOpenQueued;
  private int replayReadyTicks;
  private Path input;
  private Path output;
  private StatusFile status;
  private long startupDeadlineNanos;

  @Override
  public void onInitializeClient() {
    String inputProperty = System.getProperty("jump.renderer.input");
    String outputProperty = System.getProperty("jump.renderer.output");
    String statusProperty = System.getProperty("jump.renderer.status");
    if (inputProperty == null && outputProperty == null && statusProperty == null) {
      LOGGER.info("No render job configured; automation remains idle");
      return;
    }

    try {
      input = requireReadableFile(inputProperty, "input replay");
      output = requireOutput(outputProperty);
      status = new StatusFile(requirePath(statusProperty, "status file"));
      status.write("starting", "waiting for Replay Mod startup", 0, 0);
      startupDeadlineNanos = System.nanoTime() + STARTUP_TIMEOUT.toNanos();
    } catch (Throwable failure) {
      failBeforeStartup(failure, statusProperty);
      return;
    }

    ClientTickEvents.END_CLIENT_TICK.register(this::onEndTick);
  }

  private void onEndTick(Minecraft client) {
    if (renderQueued.get()) {
      return;
    }
    if (System.nanoTime() > startupDeadlineNanos) {
      if (renderQueued.compareAndSet(false, true)) {
        fail(new IllegalStateException("timed out waiting for Replay Mod playback"));
      }
      return;
    }

    if (!replayStartupCallbackRegistered) {
      ReplayMod replayMod = ReplayMod.instance;
      if (replayMod == null) {
        return;
      }
      replayStartupCallbackRegistered = true;
      replayMod.runPostStartup(
          () ->
              client.execute(
                  () -> {
                    replayStartupComplete = true;
                    LOGGER.info("Replay Mod post-startup work is complete");
                  }));
      return;
    }
    if (!replayStartupComplete) {
      return;
    }
    if (!replayOpenQueued) {
      ReplayModReplay replayModReplay = ReplayModReplay.instance;
      if (replayModReplay == null) {
        return;
      }
      replayOpenQueued = true;
      try {
        status.write("opening", "opening replay", 0, 0);
        replayModReplay.startReplay(input.toFile());
      } catch (Throwable failure) {
        fail(failure);
      }
      return;
    }

    ReplayModReplay replayModReplay = ReplayModReplay.instance;
    if (replayModReplay == null) {
      return;
    }
    ReplayHandler handler = replayModReplay.getReplayHandler();
    if (handler == null || client.level == null || handler.getCameraEntity() == null) {
      replayReadyTicks = 0;
      return;
    }
    replayReadyTicks++;
    if (replayReadyTicks <= REPLAY_WARMUP_TICKS) {
      if (replayReadyTicks == 1) {
        try {
          status.write("warming", "waiting for replay chunks to settle", 0, 0);
        } catch (Throwable failure) {
          renderQueued.set(true);
          fail(failure);
        }
      }
      if (replayReadyTicks == REPLAY_WARMUP_TICKS) {
        LOGGER.info(
            "Replay ready: camera=({}, {}, {}), players={}, arena floor={}, arena wall={}",
            handler.getCameraEntity().getX(),
            handler.getCameraEntity().getY(),
            handler.getCameraEntity().getZ(),
            client.level.players().size(),
            client.level.getBlockState(new BlockPos(11, 300, 0)),
            client.level.getBlockState(new BlockPos(14, 301, 0)));
      }
      return;
    }
    if (renderQueued.compareAndSet(false, true)) {
      ReplayMod.instance.runLaterWithoutLock(() -> render(handler));
    }
  }

  private void render(ReplayHandler handler) {
    int duration = handler.getReplayDuration();
    int frames = 0;
    VideoRenderer renderer = null;
    try {
      RenderPlan plan = RenderPlan.fromProperties(duration);
      Files.createDirectories(output.getParent());
      Files.deleteIfExists(output);
      status.write("rendering", "Replay Mod is rendering video", plan.timelineDurationMillis(), 0);

      SPTimeline path = new SPTimeline();
      path.setDefaultInterpolatorType(InterpolatorType.LINEAR);
      long timelineEnd = plan.timelineDurationMillis();
      configureCameraPath(path, handler, timelineEnd);
      path.addTimeKeyframe(0L, plan.startMillis());
      path.addTimeKeyframe(timelineEnd, plan.endMillis());
      path.getPositionPath().setActive(true);
      path.getTimePath().setActive(true);
      path.getPositionPath().updateAll();
      path.getTimePath().updateAll();

      String ffmpeg = requirePath(System.getProperty("jump.renderer.ffmpeg"), "FFmpeg").toString();
      RenderSettings settings =
          new RenderSettings(
              RenderMethod.DEFAULT,
              EncodingPreset.MP4_CUSTOM,
              plan.width(),
              plan.height(),
              plan.framesPerSecond(),
              plan.bitRate(),
              output.toFile(),
              false,
              false,
              false,
              false,
              false,
              null,
              360,
              180,
              false,
              false,
              false,
              AntiAliasing.NONE,
              ffmpeg,
              EncodingPreset.MP4_CUSTOM.getValue(),
              true);
      String[] incompatibility = VideoRenderer.checkCompat(settings);
      if (incompatibility != null) {
        throw new IllegalStateException(String.join(" ", incompatibility));
      }

      renderer = new VideoRenderer(settings, handler, path.getTimeline());
      boolean completed = renderer.renderVideo();
      frames = renderer.getFramesDone();
      if (!completed) {
        throw new IllegalStateException("Replay Mod cancelled the render");
      }
      if (!Files.isRegularFile(output) || Files.size(output) == 0) {
        throw new IllegalStateException("Replay Mod returned without a video file");
      }
      handler.endReplay();
      status.write("success", "render completed", plan.timelineDurationMillis(), frames);
      LOGGER.info("Rendered {} frames from {} to {}", frames, input, output);
      stopMinecraft();
    } catch (Throwable failure) {
      if (renderer != null) {
        try {
          renderer.cancel();
        } catch (Throwable cancelFailure) {
          failure.addSuppressed(cancelFailure);
          LOGGER.error("Could not cancel failed video renderer", cancelFailure);
        }
      }
      fail(failure, duration, frames);
    }
  }

  private void fail(Throwable failure) {
    fail(failure, 0, 0);
  }

  private void fail(Throwable failure, int duration, int frames) {
    LOGGER.error("Replay render failed", failure);
    try {
      status.write("failure", describe(failure), duration, frames);
    } catch (Throwable statusFailure) {
      failure.addSuppressed(statusFailure);
      LOGGER.error("Could not write renderer status", statusFailure);
    }
    ReplayModReplay replayModReplay = ReplayModReplay.instance;
    ReplayHandler handler = replayModReplay == null ? null : replayModReplay.getReplayHandler();
    if (handler != null) {
      try {
        handler.endReplay();
      } catch (Throwable closeFailure) {
        failure.addSuppressed(closeFailure);
        LOGGER.error("Could not close failed replay", closeFailure);
      }
    }
    stopMinecraft();
  }

  private static void failBeforeStartup(Throwable failure, String statusProperty) {
    LOGGER.error("Invalid replay render configuration", failure);
    if (statusProperty != null && !statusProperty.isBlank()) {
      try {
        new StatusFile(Path.of(statusProperty)).write("failure", describe(failure), 0, 0);
      } catch (Throwable statusFailure) {
        LOGGER.error("Could not write renderer status", statusFailure);
      }
    }
    stopMinecraft();
  }

  private static Path requireReadableFile(String value, String name) {
    Path path = requirePath(value, name).toAbsolutePath().normalize();
    if (!Files.isRegularFile(path) || !Files.isReadable(path)) {
      throw new IllegalArgumentException(name + " is not a readable file: " + path);
    }
    return path;
  }

  private static Path requireOutput(String value) {
    Path path = requirePath(value, "output video").toAbsolutePath().normalize();
    if (path.getParent() == null || !path.getFileName().toString().endsWith(".mp4")) {
      throw new IllegalArgumentException("output video must have an .mp4 filename");
    }
    return path;
  }

  private static Path requirePath(String value, String name) {
    if (value == null || value.isBlank()) {
      throw new IllegalArgumentException(name + " was not configured");
    }
    return Path.of(value);
  }

  private static double doubleProperty(String name, double fallback) {
    String value = System.getProperty(name);
    if (value == null || value.isBlank()) {
      return fallback;
    }
    try {
      return Double.parseDouble(value);
    } catch (NumberFormatException failure) {
      throw new IllegalArgumentException(name + " must be a number", failure);
    }
  }

  private static void configureCameraPath(
      SPTimeline path, ReplayHandler handler, long timelineEnd) {
    String mode = System.getProperty("jump.renderer.camera", "first-person");
    if (mode.equals("fixed")) {
      double cameraX = doubleProperty("jump.renderer.cameraX", CAMERA_X);
      double cameraY = doubleProperty("jump.renderer.cameraY", CAMERA_Y);
      double cameraZ = doubleProperty("jump.renderer.cameraZ", CAMERA_Z);
      float cameraYaw = (float) doubleProperty("jump.renderer.cameraYaw", CAMERA_YAW);
      float cameraPitch = (float) doubleProperty("jump.renderer.cameraPitch", CAMERA_PITCH);
      path.addPositionKeyframe(0L, cameraX, cameraY, cameraZ, cameraYaw, cameraPitch, 0.0F, -1);
      path.addPositionKeyframe(
          timelineEnd, cameraX, cameraY, cameraZ, cameraYaw, cameraPitch, 0.0F, -1);
      LOGGER.info("Rendering with fixed camera at ({}, {}, {})", cameraX, cameraY, cameraZ);
      return;
    }

    CameraType cameraType =
        switch (mode) {
          case "first-person" -> CameraType.FIRST_PERSON;
          case "third-person" -> CameraType.THIRD_PERSON_BACK;
          default ->
              throw new IllegalArgumentException(
                  "jump.renderer.camera must be first-person, third-person, or fixed");
        };
    Minecraft client = Minecraft.getInstance();
    if (client == null || client.level == null) {
      throw new IllegalStateException("replay world is unavailable while selecting its player");
    }
    List<Player> recordedPlayers =
        client.level.players().stream()
            .filter(player -> player != handler.getCameraEntity())
            .map(player -> (Player) player)
            .toList();
    if (recordedPlayers.size() != 1) {
      throw new IllegalStateException(
          "expected exactly one recorded player, found " + recordedPlayers.size());
    }
    Player target = recordedPlayers.getFirst();
    client.options.setCameraType(cameraType);
    path.addPositionKeyframe(
        0L,
        target.getX(),
        target.getY(),
        target.getZ(),
        target.getYRot(),
        target.getXRot(),
        0.0F,
        target.getId());
    path.addPositionKeyframe(
        timelineEnd,
        target.getX(),
        target.getY(),
        target.getZ(),
        target.getYRot(),
        target.getXRot(),
        0.0F,
        target.getId());
    LOGGER.info(
        "Rendering {} camera following recorded player {} (entity {})",
        mode,
        target.getName().getString(),
        target.getId());
  }

  private static String describe(Throwable failure) {
    String message = failure.getMessage();
    return failure.getClass().getSimpleName()
        + (message == null || message.isBlank() ? "" : ": " + message);
  }

  private static void stopMinecraft() {
    Minecraft client = Minecraft.getInstance();
    if (client != null) {
      client.stop();
    }
  }
}
