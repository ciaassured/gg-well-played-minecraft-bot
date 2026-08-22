package gg.wellplayed.jump.client.core;

import gg.wellplayed.jump.protocol.v1.Action;

/** Logical input latch; the Fabric adapter mirrors this state into Minecraft key mappings. */
public final class ControlledInputs {
  private boolean forward;
  private boolean jump;

  public void apply(Action action) {
    if (action != Action.ACTION_NOOP && action != Action.ACTION_JUMP) {
      throw new IllegalArgumentException("unsupported benchmark action");
    }
    forward = true;
    jump = action == Action.ACTION_JUMP;
  }

  public void finishTick() {
    jump = false;
  }

  public void releaseAll() {
    forward = false;
    jump = false;
  }

  public boolean forward() {
    return forward;
  }

  public boolean jump() {
    return jump;
  }
}
