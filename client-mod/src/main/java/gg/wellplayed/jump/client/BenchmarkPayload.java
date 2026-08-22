package gg.wellplayed.jump.client;

import gg.wellplayed.jump.client.core.FramedProtobuf;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;

/** Raw protobuf carried inside Minecraft's already-framed custom payload packet. */
public record BenchmarkPayload(byte[] data) implements CustomPacketPayload {
  public static final Type<BenchmarkPayload> TYPE =
      new Type<>(Identifier.fromNamespaceAndPath("jump", "control"));
  public static final StreamCodec<RegistryFriendlyByteBuf, BenchmarkPayload> CODEC =
      CustomPacketPayload.codec(BenchmarkPayload::write, BenchmarkPayload::new);

  public BenchmarkPayload {
    if (data.length == 0 || data.length > FramedProtobuf.MAX_MESSAGE_BYTES) {
      throw new IllegalArgumentException("benchmark payload is empty or exceeds 1 MiB");
    }
    data = data.clone();
  }

  private BenchmarkPayload(RegistryFriendlyByteBuf buffer) {
    this(read(buffer));
  }

  private void write(RegistryFriendlyByteBuf buffer) {
    buffer.writeBytes(data);
  }

  private static byte[] read(RegistryFriendlyByteBuf buffer) {
    int size = buffer.readableBytes();
    if (size <= 0 || size > FramedProtobuf.MAX_MESSAGE_BYTES) {
      throw new IllegalArgumentException("benchmark payload is empty or exceeds 1 MiB");
    }
    byte[] bytes = new byte[size];
    buffer.readBytes(bytes);
    return bytes;
  }

  @Override
  public byte[] data() {
    return data.clone();
  }

  @Override
  public Type<? extends CustomPacketPayload> type() {
    return TYPE;
  }
}
