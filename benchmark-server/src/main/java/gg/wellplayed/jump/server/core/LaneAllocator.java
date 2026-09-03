package gg.wellplayed.jump.server.core;

import java.util.BitSet;

/** Allocates the lowest free nonnegative lane without imposing a maximum. */
public final class LaneAllocator {
  private final BitSet allocated = new BitSet();

  public int acquire() {
    int ordinal = allocated.nextClearBit(0);
    allocated.set(ordinal);
    return ordinal;
  }

  public void release(int ordinal) {
    if (ordinal < 0) {
      throw new IllegalArgumentException("lane ordinal must be nonnegative");
    }
    if (!allocated.get(ordinal)) {
      throw new IllegalStateException("lane is not allocated: " + ordinal);
    }
    allocated.clear(ordinal);
  }

  public boolean allocated(int ordinal) {
    return ordinal >= 0 && allocated.get(ordinal);
  }

  public int size() {
    return allocated.cardinality();
  }
}
