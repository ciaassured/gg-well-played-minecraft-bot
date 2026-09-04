package gg.wellplayed.yrush.client.core;

import gg.wellplayed.yrush.protocol.v1.ErrorCode;

/** A peer message or YRush lifecycle packet violated the versioned contract. */
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
