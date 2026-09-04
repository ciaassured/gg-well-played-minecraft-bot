package gg.wellplayed.yrush.client.core;

import java.nio.file.Path;

/** Externally supplied addresses and readiness location for one persistent client. */
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
        System.getProperty("yrush.client.bind", "127.0.0.1"),
        Integer.getInteger("yrush.client.port", 64123),
        System.getProperty("yrush.client.server", "127.0.0.1:25565"),
        Path.of(System.getProperty("yrush.client.readinessFile", "ready")));
  }
}
