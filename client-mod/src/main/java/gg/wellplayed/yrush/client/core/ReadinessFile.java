package gg.wellplayed.yrush.client.core;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

/** Atomically publishes readiness only after Minecraft and the trainer listener are ready. */
public final class ReadinessFile {
  private final Path path;

  public ReadinessFile(Path path) {
    this.path = path.toAbsolutePath().normalize();
  }

  public void markReady(String content) {
    try {
      Path parent = path.getParent();
      if (parent != null) {
        Files.createDirectories(parent);
      }
      Path temporary = path.resolveSibling(path.getFileName() + ".tmp");
      Files.writeString(temporary, content, StandardCharsets.UTF_8);
      try {
        Files.move(
            temporary, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
      } catch (AtomicMoveNotSupportedException ignored) {
        Files.move(temporary, path, StandardCopyOption.REPLACE_EXISTING);
      }
    } catch (IOException exception) {
      throw new IllegalStateException("cannot publish readiness file " + path, exception);
    }
  }

  public void remove() {
    try {
      Files.deleteIfExists(path);
      Files.deleteIfExists(path.resolveSibling(path.getFileName() + ".tmp"));
    } catch (IOException exception) {
      throw new IllegalStateException("cannot remove readiness file " + path, exception);
    }
  }
}
