package gg.wellplayed.jump.renderer.core;

import java.nio.file.Files;
import java.nio.file.Path;

/** Dependency-free assertions used both by Gradle and the Nix check. */
public final class CoreTestMain {
  private CoreTestMain() {}

  public static void main(String[] arguments) throws Exception {
    RenderPlan plan = new RenderPlan(5_000, 1_000, 4_000, 640, 360, 20, 4_000_000);
    assert plan.timelineDurationMillis() == 3_000;
    assert plan.expectedFrames() == 60;
    expectInvalid(() -> new RenderPlan(0, 0, 1, 640, 360, 20, 4_000_000));
    expectInvalid(() -> new RenderPlan(5_000, 1_000, 1_000, 640, 360, 20, 4_000_000));
    expectInvalid(() -> new RenderPlan(5_000, 0, 5_000, 641, 360, 20, 4_000_000));

    Path directory = Files.createTempDirectory("renderer-status-test");
    Path target = directory.resolve("status.txt");
    new StatusFile(target).write("failure", "bad\nvalue=detail", 12, 3);
    String status = Files.readString(target);
    assert status.contains("state=failure\n");
    assert status.contains("message=bad value:detail\n");
    assert !Files.exists(directory.resolve("status.txt.tmp"));
    System.out.println("Replay renderer core assertions passed");
  }

  private static void expectInvalid(Runnable operation) {
    try {
      operation.run();
      throw new AssertionError("expected IllegalArgumentException");
    } catch (IllegalArgumentException expected) {
      // Expected.
    }
  }
}
