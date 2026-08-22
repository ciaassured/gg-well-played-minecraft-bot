package gg.wellplayed.jump.client.core;

import gg.wellplayed.jump.protocol.v1.WireMessage;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/** Size-limited four-byte big-endian framing for the loopback TCP transport. */
public final class FramedProtobuf {
  public static final int MAX_MESSAGE_BYTES = 1024 * 1024;

  private FramedProtobuf() {}

  public static WireMessage read(InputStream input) throws IOException {
    DataInputStream data = new DataInputStream(input);
    final int size;
    try {
      size = data.readInt();
    } catch (EOFException exception) {
      throw exception;
    }
    if (size <= 0 || size > MAX_MESSAGE_BYTES) {
      throw new IOException("invalid protobuf frame length: " + Integer.toUnsignedString(size));
    }
    byte[] payload = data.readNBytes(size);
    if (payload.length != size) {
      throw new EOFException("protobuf frame ended early");
    }
    return WireMessage.parseFrom(payload);
  }

  public static void write(OutputStream output, WireMessage message) throws IOException {
    byte[] payload = message.toByteArray();
    if (payload.length == 0 || payload.length > MAX_MESSAGE_BYTES) {
      throw new IOException("protobuf payload is empty or exceeds 1 MiB");
    }
    DataOutputStream data = new DataOutputStream(output);
    data.writeInt(payload.length);
    data.write(payload);
    data.flush();
  }
}
