package gg.wellplayed.yrush.client.core;

/** Nearest-cardinal egocentric axes and deterministic 5-cubed voxel ordering. */
public final class VoxelOrientation {
  public static final int RADIUS = 2;
  public static final int BLOCK_COUNT = 125;
  public static final int PROPERTIES_PER_BLOCK = 4;
  public static final int ENCODED_BYTES = BLOCK_COUNT * PROPERTIES_PER_BLOCK;

  public record Axes(int forwardX, int forwardZ, int rightX, int rightZ, double yawResidual) {
    public Offset offset(int localRight, int localUp, int localForward) {
      return new Offset(
          localForward * forwardX + localRight * rightX,
          localUp,
          localForward * forwardZ + localRight * rightZ);
    }

    public double forwardVelocity(double x, double z) {
      double radians = Math.toRadians(yawResidual);
      double exactForwardX = forwardX * Math.cos(radians) + rightX * Math.sin(radians);
      double exactForwardZ = forwardZ * Math.cos(radians) + rightZ * Math.sin(radians);
      return x * exactForwardX + z * exactForwardZ;
    }

    public double strafeVelocity(double x, double z) {
      double radians = Math.toRadians(yawResidual);
      double exactRightX = rightX * Math.cos(radians) - forwardX * Math.sin(radians);
      double exactRightZ = rightZ * Math.cos(radians) - forwardZ * Math.sin(radians);
      return x * exactRightX + z * exactRightZ;
    }
  }

  public record Offset(int x, int y, int z) {}

  public record BlockProperties(
      boolean collision, boolean fluid, boolean hazard, boolean breakable) {}

  @FunctionalInterface
  public interface BlockSampler {
    BlockProperties sample(int x, int y, int z);
  }

  private VoxelOrientation() {}

  public static Axes fromYaw(double yawDegrees) {
    if (!Double.isFinite(yawDegrees)) {
      throw new IllegalArgumentException("yaw must be finite");
    }
    double normalized = normalizeDegrees(yawDegrees);
    int quarter = Math.floorMod((int) Math.floor((normalized + 45.0) / 90.0), 4);
    double cardinal = quarter * 90.0;
    double residual = normalizeDegrees(normalized - cardinal);
    return switch (quarter) {
      case 0 -> new Axes(0, 1, -1, 0, residual);
      case 1 -> new Axes(-1, 0, 0, -1, residual);
      case 2 -> new Axes(0, -1, 1, 0, residual);
      case 3 -> new Axes(1, 0, 0, 1, residual);
      default -> throw new AssertionError("quarter rotation escaped 0..3");
    };
  }

  public static byte[] encode(
      int feetX, int feetY, int feetZ, double yawDegrees, BlockSampler sampler) {
    if (sampler == null) {
      throw new IllegalArgumentException("block sampler must not be null");
    }
    Axes axes = fromYaw(yawDegrees);
    byte[] result = new byte[ENCODED_BYTES];
    int index = 0;
    for (int localUp = -RADIUS; localUp <= RADIUS; localUp++) {
      for (int localForward = -RADIUS; localForward <= RADIUS; localForward++) {
        for (int localRight = -RADIUS; localRight <= RADIUS; localRight++) {
          Offset offset = axes.offset(localRight, localUp, localForward);
          BlockProperties block =
              sampler.sample(feetX + offset.x(), feetY + offset.y(), feetZ + offset.z());
          result[index++] = flag(block.collision());
          result[index++] = flag(block.fluid());
          result[index++] = flag(block.hazard());
          result[index++] = flag(block.breakable());
        }
      }
    }
    return result;
  }

  private static byte flag(boolean value) {
    return (byte) (value ? 1 : 0);
  }

  private static double normalizeDegrees(double value) {
    double normalized = value % 360.0;
    if (normalized >= 180.0) {
      normalized -= 360.0;
    } else if (normalized < -180.0) {
      normalized += 360.0;
    }
    return normalized;
  }
}
