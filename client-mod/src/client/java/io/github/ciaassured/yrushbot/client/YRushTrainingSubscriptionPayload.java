package io.github.ciaassured.yrushbot.client;

import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;

public record YRushTrainingSubscriptionPayload(String command) implements CustomPacketPayload {
    public static final Type<YRushTrainingSubscriptionPayload> TYPE = new Type<>(YRushTrainingStatePayload.ID);
    public static final YRushTrainingSubscriptionPayload SUBSCRIBE = new YRushTrainingSubscriptionPayload("subscribe");
    public static final YRushTrainingSubscriptionPayload UNSUBSCRIBE = new YRushTrainingSubscriptionPayload("unsubscribe");
    public static final StreamCodec<RegistryFriendlyByteBuf, YRushTrainingSubscriptionPayload> CODEC = StreamCodec.of(
        (buffer, payload) -> buffer.writeUtf(payload.command()),
        buffer -> new YRushTrainingSubscriptionPayload(buffer.readUtf())
    );

    @Override
    public Type<? extends CustomPacketPayload> type() {
        return TYPE;
    }
}
