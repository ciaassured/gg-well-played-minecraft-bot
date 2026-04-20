package io.github.ciaassured.yrushbot.client;

import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;

import java.nio.charset.StandardCharsets;

public record YRushTrainingStatePayload(String json) implements CustomPacketPayload {
    public static final Identifier ID = Identifier.fromNamespaceAndPath("yrush", "training_state");
    public static final Type<YRushTrainingStatePayload> TYPE = new Type<>(ID);
    public static final StreamCodec<RegistryFriendlyByteBuf, YRushTrainingStatePayload> CODEC = StreamCodec.of(
        (buffer, payload) -> buffer.writeBytes(payload.json().getBytes(StandardCharsets.UTF_8)),
        buffer -> {
            byte[] bytes = new byte[buffer.readableBytes()];
            buffer.readBytes(bytes);
            return new YRushTrainingStatePayload(new String(bytes, StandardCharsets.UTF_8));
        }
    );

    @Override
    public Type<? extends CustomPacketPayload> type() {
        return TYPE;
    }
}
