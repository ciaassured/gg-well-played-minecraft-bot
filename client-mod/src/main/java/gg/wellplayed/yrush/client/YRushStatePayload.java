package gg.wellplayed.yrush.client;

import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;

/** Raw UTF-8 JSON carried by YRush's versioned plugin-message channel. */
public record YRushStatePayload(byte[] data) implements CustomPacketPayload {
  public static final int MAX_BYTES = 65_536;
  public static final Type<YRushStatePayload> TYPE =
      new Type<>(Identifier.fromNamespaceAndPath("yrush", "bot_state"));
  public static final StreamCodec<RegistryFriendlyByteBuf, YRushStatePayload> CODEC =
      CustomPacketPayload.codec(YRushStatePayload::write, YRushStatePayload::new);

  public YRushStatePayload {
    if (data.length == 0 || data.length > MAX_BYTES) {
      throw new IllegalArgumentException("YRush state payload is empty or too large");
    }
    data = data.clone();
  }

  private YRushStatePayload(RegistryFriendlyByteBuf buffer) {
    this(read(buffer));
  }

  private void write(RegistryFriendlyByteBuf buffer) {
    buffer.writeBytes(data);
  }

  private static byte[] read(RegistryFriendlyByteBuf buffer) {
    int size = buffer.readableBytes();
    if (size <= 0 || size > MAX_BYTES) {
      throw new IllegalArgumentException("YRush state payload is empty or too large");
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
