package gg.wellplayed.jump.client.core;

/** Deterministic projection of raw client kinematics onto the benchmark lane. */
public final class ObservationMath {
  private static final double STABLE_POSITION_EPSILON = 0.03;
  private static final double STABLE_SPEED_SQUARED = 1.0e-6;

  private ObservationMath() {}

  public record Sample(
      double signedWallDistance,
      double relativeFeetHeight,
      double verticalVelocity,
      double laneVelocity,
      boolean onGround) {}

  public static Sample sample(
      double minX,
      double minY,
      double minZ,
      double maxX,
      double maxZ,
      double velocityX,
      double velocityY,
      double velocityZ,
      boolean onGround,
      double wallNearCoordinate,
      double laneDirectionX,
      double laneDirectionZ,
      double standingFeetY) {
    requireLane(laneDirectionX, laneDirectionZ);
    double centerX = (minX + maxX) * 0.5;
    double centerZ = (minZ + maxZ) * 0.5;
    double halfX = (maxX - minX) * 0.5;
    double halfZ = (maxZ - minZ) * 0.5;
    double front =
        centerX * laneDirectionX
            + centerZ * laneDirectionZ
            + halfX * Math.abs(laneDirectionX)
            + halfZ * Math.abs(laneDirectionZ);
    double laneVelocity = velocityX * laneDirectionX + velocityZ * laneDirectionZ;
    return new Sample(
        wallNearCoordinate - front, minY - standingFeetY, velocityY, laneVelocity, onGround);
  }

  public static boolean resetStateMatches(
      Sample sample, double startingGap, double horizontalSpeedSquared) {
    return sample.onGround()
        && Math.abs(sample.signedWallDistance() - startingGap) <= STABLE_POSITION_EPSILON
        && Math.abs(sample.relativeFeetHeight()) <= STABLE_POSITION_EPSILON
        && horizontalSpeedSquared <= STABLE_SPEED_SQUARED;
  }

  private static void requireLane(double x, double z) {
    double length = Math.hypot(x, z);
    if (!Double.isFinite(length) || Math.abs(length - 1.0) > 1.0e-6) {
      throw new IllegalArgumentException("lane direction must be a finite unit vector");
    }
  }
}
