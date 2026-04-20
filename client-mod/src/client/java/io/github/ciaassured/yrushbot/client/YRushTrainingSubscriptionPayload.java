package io.github.ciaassured.yrushbot.client;

import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;

import java.nio.charset.StandardCharsets;

public record YRushTrainingSubscriptionPayload(String command) implements CustomPacketPayload {
    public static final Type<YRushTrainingSubscriptionPayload> TYPE = new Type<>(YRushTrainingStatePayload.ID);
    public static final YRushTrainingSubscriptionPayload SUBSCRIBE = new YRushTrainingSubscriptionPayload("subscribe");
    public static final YRushTrainingSubscriptionPayload UNSUBSCRIBE = new YRushTrainingSubscriptionPayload("unsubscribe");
    public static final StreamCodec<RegistryFriendlyByteBuf, YRushTrainingSubscriptionPayload> CODEC = StreamCodec.of(
        (buffer, payload) -> buffer.writeBytes(payload.command().getBytes(StandardCharsets.UTF_8)),
        buffer -> {
            byte[] bytes = new byte[buffer.readableBytes()];
            buffer.readBytes(bytes);
            return new YRushTrainingSubscriptionPayload(new String(bytes, StandardCharsets.UTF_8));
        }
    );

    @Override
    public Type<? extends CustomPacketPayload> type() {
        return TYPE;
    }
}
