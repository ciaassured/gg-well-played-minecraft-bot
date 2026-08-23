package gg.wellplayed.jump.server.core;

/** Fixed geometry for the three-wide, positive-X benchmark lane. */
public record ArenaGeometry(
    int floorMinX,
    int floorMaxX,
    int floorY,
    int laneMinZ,
    int laneMaxZ,
    int wallX,
    double playerWidth) {
  // A sky platform avoids dependence on the randomly generated terrain at the world origin while
  // retaining normal Overworld movement, gravity, lighting, and tick behavior.
  public static final ArenaGeometry STANDARD = new ArenaGeometry(0, 23, 300, -1, 1, 14, 0.6);

  public ArenaGeometry {
    if (floorMaxX - floorMinX + 1 < 20) {
      throw new IllegalArgumentException("lane must be at least 20 blocks long");
    }
    if (laneMaxZ - laneMinZ + 1 != 3) {
      throw new IllegalArgumentException("lane must be exactly three blocks wide");
    }
    if (wallX <= floorMinX || wallX + 4 > floorMaxX) {
      throw new IllegalArgumentException("wall must leave at least four landing blocks");
    }
  }

  public double standingFeetY() {
    return floorY + 1.0;
  }

  public double wallNear() {
    return wallX;
  }

  public double wallFar() {
    return wallX + 1.0;
  }

  public double spawnCenterX(double gap) {
    return wallNear() - gap - playerWidth / 2.0;
  }

  public double landingLength() {
    return floorMaxX - wallFar() + 1.0;
  }

  public int endBarrierX() {
    return floorMaxX + 1;
  }
}
