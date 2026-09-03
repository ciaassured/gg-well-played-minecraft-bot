package gg.wellplayed.jump.server.core;

import java.util.Objects;

/**
 * Pure authoritative episode state machine; Bukkit integration is deliberately outside this type.
 */
public final class EpisodeController {
  public static final int STABLE_TICKS_REQUIRED = 2;
  public static final int MISSED_JUMP_TICKS = 10;
  public static final int TIME_LIMIT_TICKS = 200;
  // Trainer transports fail independently after five seconds. This secondary guard allows
  // normal batch-coordinator jitter at large pool widths while still bounding orphaned episodes.
  public static final int ACTION_DEADLINE_TICKS = 200;

  private static final double STATIONARY_SPEED_SQUARED = 1.0e-8;
  private static final double NEGLIGIBLE_PROGRESS = 1.0e-3;
  private static final double COLLISION_EPSILON = 1.0e-6;

  public enum Phase {
    IDLE,
    RESETTING,
    READY,
    ACTIVE,
    TERMINAL,
    ABORTED
  }

  public enum EndReason {
    NONE,
    SUCCESS,
    MISSED_JUMP,
    TIME_LIMIT,
    INFRASTRUCTURE_ERROR
  }

  public enum ResetStatus {
    ACCEPTED,
    IDEMPOTENT,
    STALE_REQUEST,
    REQUEST_MISMATCH,
    STALE_EPISODE
  }

  public enum ActionStatus {
    ACCEPTED,
    WRONG_PHASE,
    STALE_OR_DUPLICATE,
    OUT_OF_ORDER
  }

  public record ResetCommand(
      long requestId, String sessionId, long episodeId, long seed, double startingGap) {
    public ResetCommand {
      if (requestId <= 0) {
        throw new IllegalArgumentException("requestId must be positive");
      }
      if (episodeId <= 0) {
        throw new IllegalArgumentException("episodeId must be positive");
      }
      if (sessionId == null || sessionId.isBlank()) {
        throw new IllegalArgumentException("sessionId must not be blank");
      }
      if (startingGap < SeededGap.MIN_GAP || startingGap > SeededGap.MAX_GAP) {
        throw new IllegalArgumentException("startingGap is outside [4, 8]");
      }
    }
  }

  public record Kinematics(
      double laneFront,
      double laneBack,
      double relativeFeetHeight,
      double verticalVelocity,
      double laneVelocity,
      boolean onGround) {}

  public record TickSnapshot(
      Phase phase, EndReason reason, int elapsedTicks, int stuckTicks, boolean finishedNow) {}

  private ResetCommand resetCommand;
  private long lastRequestId;
  private long lastEpisodeId;
  private Phase phase = Phase.IDLE;
  private EndReason endReason = EndReason.NONE;
  private int stableTicks;
  private int elapsedTicks;
  private int stuckTicks;
  private long lastActionSequence;
  private long lastActionServerTick;
  private double lastLaneFront = Double.NaN;

  public ResetStatus requestReset(ResetCommand command) {
    Objects.requireNonNull(command, "command");
    if (resetCommand != null && command.requestId() < lastRequestId) {
      return ResetStatus.STALE_REQUEST;
    }
    if (resetCommand != null && command.requestId() == lastRequestId) {
      return command.equals(resetCommand) ? ResetStatus.IDEMPOTENT : ResetStatus.REQUEST_MISMATCH;
    }
    if (resetCommand != null && command.episodeId() <= lastEpisodeId) {
      return ResetStatus.STALE_EPISODE;
    }

    resetCommand = command;
    lastRequestId = command.requestId();
    lastEpisodeId = command.episodeId();
    phase = Phase.RESETTING;
    endReason = EndReason.NONE;
    stableTicks = 0;
    elapsedTicks = 0;
    stuckTicks = 0;
    lastActionSequence = 0;
    lastActionServerTick = 0;
    lastLaneFront = Double.NaN;
    return ResetStatus.ACCEPTED;
  }

  /** Returns true only on the tick that reset stability becomes ready. */
  public boolean observeResetStability(boolean onGround, double horizontalSpeedSquared) {
    if (phase != Phase.RESETTING) {
      return false;
    }
    if (onGround && horizontalSpeedSquared <= STATIONARY_SPEED_SQUARED) {
      stableTicks++;
    } else {
      stableTicks = 0;
    }
    if (stableTicks < STABLE_TICKS_REQUIRED) {
      return false;
    }
    phase = Phase.READY;
    return true;
  }

  public ActionStatus acceptAction(long observationSequence, long actionSequence, long serverTick) {
    if (phase != Phase.READY && phase != Phase.ACTIVE) {
      return ActionStatus.WRONG_PHASE;
    }
    if (actionSequence <= lastActionSequence) {
      return ActionStatus.STALE_OR_DUPLICATE;
    }
    if (actionSequence != lastActionSequence + 1 || observationSequence != actionSequence - 1) {
      return ActionStatus.OUT_OF_ORDER;
    }
    lastActionSequence = actionSequence;
    lastActionServerTick = serverTick;
    if (phase == Phase.READY) {
      phase = Phase.ACTIVE;
    }
    return ActionStatus.ACCEPTED;
  }

  public TickSnapshot tick(long serverTick, Kinematics kinematics, ArenaGeometry geometry) {
    Objects.requireNonNull(kinematics, "kinematics");
    Objects.requireNonNull(geometry, "geometry");
    if (phase != Phase.ACTIVE) {
      return snapshot(false);
    }
    if (lastActionSequence <= elapsedTicks
        && serverTick - lastActionServerTick > ACTION_DEADLINE_TICKS) {
      return finish(Phase.ABORTED, EndReason.INFRASTRUCTURE_ERROR);
    }
    if (lastActionSequence <= elapsedTicks) {
      return snapshot(false);
    }

    elapsedTicks++;
    if (kinematics.onGround()
        && Math.abs(kinematics.relativeFeetHeight()) < 0.05
        && kinematics.laneBack() > geometry.wallFar() + COLLISION_EPSILON) {
      return finish(Phase.TERMINAL, EndReason.SUCCESS);
    }

    double progress =
        Double.isNaN(lastLaneFront)
            ? Double.POSITIVE_INFINITY
            : kinematics.laneFront() - lastLaneFront;
    boolean againstWall =
        kinematics.onGround()
            && Math.abs(kinematics.relativeFeetHeight()) < 0.05
            && kinematics.laneFront() >= geometry.wallNear() - 0.02
            && kinematics.laneBack() < geometry.wallFar()
            && Math.abs(progress) < NEGLIGIBLE_PROGRESS;
    stuckTicks = againstWall ? stuckTicks + 1 : 0;
    lastLaneFront = kinematics.laneFront();

    if (stuckTicks >= MISSED_JUMP_TICKS) {
      return finish(Phase.TERMINAL, EndReason.MISSED_JUMP);
    }
    if (elapsedTicks >= TIME_LIMIT_TICKS) {
      return finish(Phase.TERMINAL, EndReason.TIME_LIMIT);
    }
    return snapshot(false);
  }

  public TickSnapshot abortInfrastructure() {
    if (phase == Phase.TERMINAL || phase == Phase.ABORTED || phase == Phase.IDLE) {
      return snapshot(false);
    }
    return finish(Phase.ABORTED, EndReason.INFRASTRUCTURE_ERROR);
  }

  private TickSnapshot finish(Phase terminalPhase, EndReason reason) {
    phase = terminalPhase;
    endReason = reason;
    return snapshot(true);
  }

  public TickSnapshot snapshot(boolean finishedNow) {
    return new TickSnapshot(phase, endReason, elapsedTicks, stuckTicks, finishedNow);
  }

  public ResetCommand resetCommand() {
    return resetCommand;
  }

  public Phase phase() {
    return phase;
  }

  public int elapsedTicks() {
    return elapsedTicks;
  }
}
