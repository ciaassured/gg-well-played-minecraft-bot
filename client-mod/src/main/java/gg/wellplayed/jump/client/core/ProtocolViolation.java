package gg.wellplayed.jump.client.core;

import gg.wellplayed.jump.protocol.v1.ErrorCode;

/** A peer message violated the versioned sequencing contract. */
public final class ProtocolViolation extends Exception {
  private final ErrorCode code;

  public ProtocolViolation(ErrorCode code, String message) {
    super(message);
    this.code = code;
  }

  public ErrorCode code() {
    return code;
  }
}
