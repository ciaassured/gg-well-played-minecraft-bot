package gg.wellplayed.jump.server.core;

/** Stable SplitMix64 mapping from an episode seed to a continuous gap in [4, 8]. */
public final class SeededGap {
  public static final double MIN_GAP = 4.0;
  public static final double MAX_GAP = 8.0;

  private SeededGap() {}

  public static double fromSeed(long seed) {
    long mixed = mix64(seed + 0x9E3779B97F4A7C15L);
    double unit = (mixed >>> 11) * 0x1.0p-53;
    return MIN_GAP + unit * (MAX_GAP - MIN_GAP);
  }

  private static long mix64(long value) {
    value = (value ^ (value >>> 30)) * 0xBF58476D1CE4E5B9L;
    value = (value ^ (value >>> 27)) * 0x94D049BB133111EBL;
    return value ^ (value >>> 31);
  }
}
