package gg.wellplayed.jump.renderer.core;

/** Validated immutable parameters for one Replay Mod render. */
public record RenderPlan(
    int replayDurationMillis,
    int startMillis,
    int endMillis,
    int width,
    int height,
    int framesPerSecond,
    int bitRate) {
  public RenderPlan {
    if (replayDurationMillis <= 0) {
      throw new IllegalArgumentException("replay duration must be positive");
    }
    if (startMillis < 0 || startMillis >= replayDurationMillis) {
      throw new IllegalArgumentException("start time is outside the replay");
    }
    if (endMillis <= startMillis || endMillis > replayDurationMillis) {
      throw new IllegalArgumentException("end time is outside the replay");
    }
    if (width < 160 || height < 90 || width > 7680 || height > 4320) {
      throw new IllegalArgumentException("video dimensions are unsupported");
    }
    if ((width & 1) != 0 || (height & 1) != 0) {
      throw new IllegalArgumentException("MP4 dimensions must be even");
    }
    if (framesPerSecond < 1 || framesPerSecond > 120) {
      throw new IllegalArgumentException("frame rate is unsupported");
    }
    if (bitRate < 100_000 || bitRate > 200_000_000) {
      throw new IllegalArgumentException("bit rate is unsupported");
    }
  }

  public static RenderPlan fromProperties(int replayDurationMillis) {
    int requestedEnd = Integer.getInteger("jump.renderer.endMillis", -1);
    int end = requestedEnd < 0 ? replayDurationMillis : requestedEnd;
    return new RenderPlan(
        replayDurationMillis,
        Integer.getInteger("jump.renderer.startMillis", 0),
        end,
        Integer.getInteger("jump.renderer.width", 640),
        Integer.getInteger("jump.renderer.height", 360),
        Integer.getInteger("jump.renderer.fps", 20),
        Integer.getInteger("jump.renderer.bitrate", 4_000_000));
  }

  public int timelineDurationMillis() {
    return endMillis - startMillis;
  }

  public int expectedFrames() {
    return Math.max(1, (int) Math.ceil(timelineDurationMillis() * framesPerSecond / 1000.0));
  }
}
