package gg.wellplayed.jump.client.core;

import java.nio.file.Path;

/** Externally supplied addresses and readiness location for one client pod. */
public record ClientConfiguration(
    String trainerBindAddress, int trainerPort, String paperAddress, Path readinessFile) {
  public ClientConfiguration {
    if (trainerBindAddress == null || trainerBindAddress.isBlank()) {
      throw new IllegalArgumentException("trainer bind address must not be blank");
    }
    if (trainerPort < 1 || trainerPort > 65535) {
      throw new IllegalArgumentException("trainer port is outside 1..65535");
    }
    if (paperAddress == null || paperAddress.isBlank()) {
      throw new IllegalArgumentException("Paper address must not be blank");
    }
    if (readinessFile == null) {
      throw new IllegalArgumentException("readiness file must not be null");
    }
  }

  public static ClientConfiguration fromSystemProperties() {
    return new ClientConfiguration(
        System.getProperty("jump.client.bind", "127.0.0.1"),
        Integer.getInteger("jump.client.port", 64123),
        System.getProperty("jump.client.server", "127.0.0.1:25565"),
        Path.of(System.getProperty("jump.client.readinessFile", "ready")));
  }
}
