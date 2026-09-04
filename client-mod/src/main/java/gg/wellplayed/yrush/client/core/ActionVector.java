package gg.wellplayed.yrush.client.core;

import java.util.List;

/** The six categorical controls in their versioned wire order. */
public record ActionVector(
    int forwardAxis,
    int strafeAxis,
    boolean jump,
    boolean attack,
    float yawDelta,
    float pitchDelta) {
  public static final int DIMENSIONS = 6;
  public static final int[] CARDINALITIES = {3, 3, 2, 2, 5, 5};
  private static final float[] YAW_DELTAS = {-30.0F, -10.0F, 0.0F, 10.0F, 30.0F};
  private static final float[] PITCH_DELTAS = {-20.0F, -5.0F, 0.0F, 5.0F, 20.0F};

  public static ActionVector fromChoices(List<Integer> choices) {
    if (choices == null || choices.size() != DIMENSIONS) {
      throw new IllegalArgumentException("action must contain exactly six choices");
    }
    for (int index = 0; index < DIMENSIONS; index++) {
      int value = choices.get(index);
      if (value < 0 || value >= CARDINALITIES[index]) {
        throw new IllegalArgumentException("action choice " + index + " is outside its range");
      }
    }
    return new ActionVector(
        choices.get(0) - 1,
        choices.get(1) - 1,
        choices.get(2) == 1,
        choices.get(3) == 1,
        YAW_DELTAS[choices.get(4)],
        PITCH_DELTAS[choices.get(5)]);
  }
}
