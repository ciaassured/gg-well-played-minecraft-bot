package gg.wellplayed.yrush.client.core;

/** Logical input latch mirrored into Minecraft key mappings by the Fabric adapter. */
public final class ControlledInputs {
  private int forwardAxis;
  private int strafeAxis;
  private boolean jump;
  private boolean attack;

  public void apply(ActionVector action) {
    forwardAxis = action.forwardAxis();
    strafeAxis = action.strafeAxis();
    jump = action.jump();
    attack = action.attack();
  }

  public void releaseAll() {
    forwardAxis = 0;
    strafeAxis = 0;
    jump = false;
    attack = false;
  }

  public boolean forward() {
    return forwardAxis > 0;
  }

  public boolean backward() {
    return forwardAxis < 0;
  }

  public boolean left() {
    return strafeAxis < 0;
  }

  public boolean right() {
    return strafeAxis > 0;
  }

  public boolean jump() {
    return jump;
  }

  public boolean attack() {
    return attack;
  }

  public boolean sprint() {
    return forwardAxis > 0;
  }
}
