package gg.wellplayed.jump.renderer.core;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

/** Writes a tiny atomic status contract consumed by the outer renderer CLI. */
public final class StatusFile {
  private final Path path;

  public StatusFile(Path path) {
    this.path = path.toAbsolutePath().normalize();
  }

  public void write(String state, String message, int durationMillis, int frames)
      throws IOException {
    Path parent = path.getParent();
    if (parent != null) {
      Files.createDirectories(parent);
    }
    String body =
        "state="
            + clean(state)
            + "\nmessage="
            + clean(message)
            + "\nduration_ms="
            + durationMillis
            + "\nframes="
            + frames
            + "\n";
    Path temporary = path.resolveSibling(path.getFileName() + ".tmp");
    Files.writeString(temporary, body, StandardCharsets.UTF_8);
    try {
      Files.move(
          temporary, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    } catch (IOException unsupportedAtomicMove) {
      Files.move(temporary, path, StandardCopyOption.REPLACE_EXISTING);
    }
  }

  private static String clean(String value) {
    return value == null ? "" : value.replace('\n', ' ').replace('\r', ' ').replace('=', ':');
  }
}
