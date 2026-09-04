package gg.wellplayed.yrush.client.core;

/** Unbounded reconnect attempts with an exponentially increasing, capped delay. */
public record ReconnectBackoff(long initialTicks, long maximumTicks) {
  public ReconnectBackoff {
    if (initialTicks <= 0 || maximumTicks < initialTicks) {
      throw new IllegalArgumentException("invalid reconnect backoff bounds");
    }
  }

  public long delayTicks(int attempt) {
    if (attempt < 0) {
      throw new IllegalArgumentException("attempt must be nonnegative");
    }
    if (attempt >= 62) {
      return maximumTicks;
    }
    long multiplier = 1L << attempt;
    if (initialTicks > maximumTicks / multiplier) {
      return maximumTicks;
    }
    return Math.min(maximumTicks, initialTicks * multiplier);
  }
}
